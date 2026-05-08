from __future__ import annotations

import json
import os
import select
import socket
import itertools
import threading
import time
from dataclasses import dataclass
from typing import Any

DEFAULT_MAIN_THREAD_TIMEOUT = 30.0


class AbletonBridgeError(RuntimeError):
    pass


class _BridgeReadTimeout(OSError):
    pass


@dataclass(frozen=True)
class BridgeConfig:
    host: str = "127.0.0.1"
    port: int = 8765
    timeout: float = 30.0
    connect_timeout: float = 2.0
    idle_timeout: float = 8.0
    max_response_bytes: int = 8 * 1024 * 1024

    @classmethod
    def from_env(cls) -> "BridgeConfig":
        return cls(
            host=os.environ.get("ABLETON_MCP_HOST", cls.host),
            port=int(os.environ.get("ABLETON_MCP_PORT", str(cls.port))),
            timeout=float(os.environ.get("ABLETON_MCP_TIMEOUT", str(cls.timeout))),
            connect_timeout=float(os.environ.get("ABLETON_MCP_CONNECT_TIMEOUT", str(cls.connect_timeout))),
            idle_timeout=float(os.environ.get("ABLETON_MCP_IDLE_TIMEOUT", str(cls.idle_timeout))),
            max_response_bytes=int(os.environ.get("ABLETON_MCP_MAX_RESPONSE_BYTES", str(cls.max_response_bytes))),
        )


class AbletonBridgeClient:
    def __init__(self, config: BridgeConfig | None = None) -> None:
        self.config = config or BridgeConfig.from_env()
        self._ids = itertools.count(1)
        self._sock: socket.socket | None = None
        self._lock = threading.Lock()
        self._last_used = 0.0

    def request(self, method: str, params: dict[str, Any] | None = None) -> Any:
        params = params or {}
        payload = {
            "jsonrpc": "2.0",
            "id": next(self._ids),
            "method": method,
            "params": params,
        }
        with self._lock:
            try:
                response = self._send(payload)
            except _BridgeReadTimeout as exc:
                self.close()
                raise AbletonBridgeError(self._timeout_message(method, params)) from exc
            except OSError:
                self.close()
                try:
                    response = self._send(payload)
                except _BridgeReadTimeout as exc:
                    self.close()
                    raise AbletonBridgeError(self._timeout_message(method, params)) from exc
                except OSError as exc:
                    self.close()
                    raise AbletonBridgeError(f"Could not connect to Ableton bridge at {self.config.host}:{self.config.port}: {exc}") from exc
        message = json.loads(response.decode("utf-8"))
        if self._is_stale_idle_timeout(message, payload["id"]):
            self.close()
            with self._lock:
                try:
                    response = self._send(payload)
                except _BridgeReadTimeout as exc:
                    self.close()
                    raise AbletonBridgeError(self._timeout_message(method, params)) from exc
                except OSError as exc:
                    self.close()
                    raise AbletonBridgeError(f"Could not connect to Ableton bridge at {self.config.host}:{self.config.port}: {exc}") from exc
            message = json.loads(response.decode("utf-8"))
        if "error" in message:
            err = message["error"]
            detail = err.get("data") if os.environ.get("ABLETON_MCP_TRACEBACK") else ""
            suffix = f": {detail}" if detail else ""
            raise AbletonBridgeError(f"{err.get('code', -32000)} {err.get('message', 'Bridge error')}{suffix}")
        return message.get("result")

    def close(self) -> None:
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None

    def _send(self, payload: dict[str, Any]) -> bytes:
        sock = self._socket()
        params = payload.get("params") or {}
        request_timeout = self._request_timeout(params if isinstance(params, dict) else {})
        sock.settimeout(request_timeout)
        line = (json.dumps(payload, separators=(",", ":")) + "\n").encode("utf-8")
        sock.sendall(line)
        response = self._read_line(sock, self.config.max_response_bytes, time.monotonic() + request_timeout)
        self._last_used = time.monotonic()
        return response

    def _request_timeout(self, params: dict[str, Any]) -> float:
        request_timeout = self.config.timeout
        if params.get("timeout") is not None:
            request_timeout = max(request_timeout, effective_main_thread_timeout(params) + 1.0)
        return request_timeout

    def _timeout_message(self, method: str, params: dict[str, Any]) -> str:
        request_timeout = self._request_timeout(params)
        return (
            f"Ableton bridge request {method!r} timed out after {request_timeout:g}s waiting for a response. "
            "The request was sent, so it was not retried automatically."
        )

    def _socket(self) -> socket.socket:
        if self._sock is not None and self.config.idle_timeout > 0:
            if time.monotonic() - self._last_used >= self.config.idle_timeout:
                self.close()
        if self._sock is None:
            self._sock = socket.create_connection((self.config.host, self.config.port), self.config.connect_timeout)
            self._sock.settimeout(self.config.timeout)
            self._last_used = time.monotonic()
        return self._sock

    @staticmethod
    def _is_stale_idle_timeout(message: dict[str, Any], request_id: int) -> bool:
        if message.get("id") == request_id:
            return False
        error = message.get("error")
        if not isinstance(error, dict):
            return False
        return message.get("id") is None and str(error.get("message", "")).lower() == "timed out"

    @staticmethod
    def _read_line(sock: socket.socket, max_bytes: int = 8 * 1024 * 1024, deadline: float | None = None) -> bytes:
        chunks: list[bytes] = []
        total = 0
        while True:
            if deadline is not None and _can_select(sock):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise _BridgeReadTimeout("timed out waiting for Ableton bridge response")
                ready, _, _ = select.select([sock], [], [], min(remaining, 0.25))
                if not ready:
                    continue
            try:
                chunk = sock.recv(4096)
            except socket.timeout as exc:
                raise _BridgeReadTimeout("timed out waiting for Ableton bridge response") from exc
            if not chunk:
                break
            total += len(chunk)
            if max_bytes >= 0 and total > max_bytes:
                raise OSError(f"Ableton bridge response exceeds {max_bytes} bytes")
            chunks.append(chunk)
            if b"\n" in chunk:
                break
        data = b"".join(chunks)
        if not data:
            raise OSError("No response from Ableton bridge")
        return data.split(b"\n", 1)[0]


def _can_select(sock: socket.socket) -> bool:
    try:
        return int(sock.fileno()) >= 0
    except Exception:
        return False


def effective_main_thread_timeout(params: dict[str, Any]) -> float:
    timeout = float(params.get("timeout") or DEFAULT_MAIN_THREAD_TIMEOUT)
    if not (params.get("strict_timeout") or params.get("timeout_strict")):
        timeout = max(timeout, DEFAULT_MAIN_THREAD_TIMEOUT)
    return timeout
