from __future__ import annotations

import json
import os
import socket
import itertools
import threading
from dataclasses import dataclass
from typing import Any


class AbletonBridgeError(RuntimeError):
    pass


@dataclass(frozen=True)
class BridgeConfig:
    host: str = "127.0.0.1"
    port: int = 8765
    timeout: float = 10.0
    max_response_bytes: int = 8 * 1024 * 1024


class AbletonBridgeClient:
    def __init__(self, config: BridgeConfig | None = None) -> None:
        self.config = config or BridgeConfig()
        self._ids = itertools.count(1)
        self._sock: socket.socket | None = None
        self._lock = threading.Lock()

    def request(self, method: str, params: dict[str, Any] | None = None) -> Any:
        payload = {
            "jsonrpc": "2.0",
            "id": next(self._ids),
            "method": method,
            "params": params or {},
        }
        with self._lock:
            try:
                response = self._send(payload)
            except OSError:
                self.close()
                try:
                    response = self._send(payload)
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
        line = (json.dumps(payload, separators=(",", ":")) + "\n").encode("utf-8")
        sock.sendall(line)
        return self._read_line(sock, self.config.max_response_bytes)

    def _socket(self) -> socket.socket:
        if self._sock is None:
            self._sock = socket.create_connection((self.config.host, self.config.port), self.config.timeout)
            self._sock.settimeout(self.config.timeout)
        return self._sock

    @staticmethod
    def _read_line(sock: socket.socket, max_bytes: int = 8 * 1024 * 1024) -> bytes:
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = sock.recv(4096)
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
