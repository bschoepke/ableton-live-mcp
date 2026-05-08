from __future__ import annotations

import argparse
import importlib.util
import json
import platform
import sys
from pathlib import Path
from typing import Any, Callable

import agent_m4l
from bridge import AbletonBridgeClient, AbletonBridgeError
from install_remote_script import remote_script_status


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate the Ableton Live MCP install and connection.")
    parser.add_argument("--target-dir", type=Path, default=None, help="Ableton Remote Scripts directory to check.")
    parser.add_argument("--skip-live", action="store_true", help="Only validate local Remote Script freshness.")
    parser.add_argument("--allow-stale-remote-script", action="store_true", help="Do not fail when the installed or running Remote Script differs from this checkout.")
    parser.add_argument("--allow-stale-m4l-host", action="store_true", help="Do not fail when generated Agent M4L host companion JS copies are stale.")
    parser.add_argument("--allow-missing-visual-capture", action="store_true", help="Do not fail when Ableton-only visual capture dependencies are unavailable.")
    parser.add_argument("--timeout", type=float, default=45.0, help="Seconds to wait for Live checks.")
    parser.add_argument("--strict-timeout", action="store_true", help="Do not clamp short Live check timeouts to the default main-thread wait.")
    args = parser.parse_args(argv)

    results = {
        "mcp_tools": mcp_tool_schema_status(),
        "remote_script": remote_script_status(target_dir=args.target_dir),
        "m4l_host": agent_m4l.agent_m4l_host_status(),
        "visual_capture": visual_capture_dependency_status(),
    }
    mcp_tools_ok = bool(results["mcp_tools"].get("ok"))
    remote_ok = bool(results["remote_script"].get("current"))
    m4l_host_ok = bool(results["m4l_host"].get("current"))
    visual_ok = bool(results["visual_capture"].get("ok"))
    if not mcp_tools_ok:
        print(json.dumps(results, indent=2, sort_keys=True))
        print("Ableton Live MCP validation failed: local MCP tool schemas are stale or incomplete", file=sys.stderr)
        return 1
    if not remote_ok and not args.allow_stale_remote_script:
        print(json.dumps(results, indent=2, sort_keys=True))
        print("Ableton Live MCP validation failed: installed Remote Script is missing or stale", file=sys.stderr)
        return 1
    if not m4l_host_ok and not args.allow_stale_m4l_host:
        print(json.dumps(results, indent=2, sort_keys=True))
        print("Ableton Live MCP validation failed: generated Agent M4L host files are missing or stale", file=sys.stderr)
        return 1
    if not visual_ok and not args.allow_missing_visual_capture:
        print(json.dumps(results, indent=2, sort_keys=True))
        print("Ableton Live MCP validation failed: Ableton-only visual capture dependencies are unavailable", file=sys.stderr)
        return 1
    if args.skip_live:
        print(json.dumps(results, indent=2, sort_keys=True))
        return 0

    client = AbletonBridgeClient()
    live_timeout = float(args.timeout)
    live_controls = {"timeout": live_timeout}
    if args.strict_timeout:
        live_controls["strict_timeout"] = True
    checks = [
        ("ping", "ping", dict(live_controls)),
        ("song", "get", {"ref": {"path": "live_set"}, "properties": ["tempo", "signature_numerator", "signature_denominator"], **live_controls}),
        ("application", "eval", {"expr": "app.get_major_version() if hasattr(app, 'get_major_version') else app.get_version_string().split('.')[0]", **live_controls}),
    ]
    batch_params = {
        "operations": [{"method": method, "params": params} for _name, method, params in checks],
        "timeout": live_timeout,
    }
    if args.strict_timeout:
        batch_params["strict_timeout"] = True
    try:
        batch = client.request("batch", batch_params)
        for (name, method, _params), item in zip(checks, batch):
            if not item.get("ok"):
                raise AbletonBridgeError("%s validation failed: %s" % (method, item.get("error", "unknown error")))
            results[name] = item.get("result")
    except AbletonBridgeError as exc:
        bridge_status = _probe_bridge_status(live_timeout)
        if bridge_status:
            results["bridge_status"] = bridge_status
        failure_type, next_action = _live_failure_diagnostics(exc, bridge_status)
        results["live_error"] = str(exc)
        results["live_failure_type"] = failure_type
        results["remote_script"]["runtime_current"] = False
        results["remote_script"]["runtime_mismatch"] = failure_type
        results["remote_script"]["runtime_reload_required"] = True
        results["remote_script"]["runtime_next_action"] = next_action
        print(json.dumps(results, indent=2, sort_keys=True))
        print(f"Ableton Live MCP validation failed: {exc}", file=sys.stderr)
        return 1
    runtime_ok, runtime_reason = _check_running_remote_script(results)
    results["remote_script"]["runtime_current"] = runtime_ok
    if runtime_reason:
        results["remote_script"]["runtime_mismatch"] = runtime_reason
        results["remote_script"]["runtime_reload_required"] = True
        results["remote_script"]["runtime_next_action"] = "Reload the Ableton_Live_MCP Control Surface or restart Ableton Live, then rerun ableton-live-mcp-validate."
    if not runtime_ok and not args.allow_stale_remote_script:
        print(json.dumps(results, indent=2, sort_keys=True))
        print("Ableton Live MCP validation failed: running Remote Script is stale or unverified; reload the Ableton_Live_MCP Control Surface or restart Ableton Live", file=sys.stderr)
        return 1
    print(json.dumps(results, indent=2, sort_keys=True))
    return 0


def _check_running_remote_script(results: dict) -> tuple[bool, str]:
    remote_script = results.get("remote_script") or {}
    expected_version = remote_script.get("source_runtime_version")
    expected_code = remote_script.get("source_runtime_code_sha256")
    expected = remote_script.get("source_bridge_sha256")
    runtime = ((results.get("ping") or {}).get("remote_script") or {})
    actual_version = runtime.get("runtime_version")
    actual_code = runtime.get("runtime_code_sha256")
    actual = runtime.get("bridge_sha256")
    if expected_version:
        if not actual_version:
            return False, "missing_runtime_version"
        if actual_version != expected_version:
            return False, "runtime_version_mismatch"
    if expected_code:
        if not actual_code:
            return False, "missing_runtime_code_hash"
        if actual_code != expected_code:
            return False, "runtime_code_hash_mismatch"
    if not expected:
        return False, "missing_source_bridge_hash"
    if not actual:
        return False, "missing_runtime_bridge_hash"
    if actual != expected:
        return False, "bridge_hash_mismatch"
    return True, ""


def mcp_tool_schema_status() -> dict:
    class _NoopBridge:
        def request(self, _method: str, _params: dict) -> Any:
            raise RuntimeError("schema inspection should not call Live")

    try:
        from server import make_server

        server = make_server(_NoopBridge())
        tools = {name: tool.as_mcp().get("inputSchema", {}) for name, tool in server.tools.items()}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}

    expectations = {
        "live_agent_audio_tap": {
            "properties": ["command", "path", "id", "udp"],
            "required": ["command"],
            "enums": {"command": ["open", "start", "stop", "status"]},
        },
        "live_transport": {
            "properties": ["action", "time", "timeout", "strict_timeout"],
        },
        "live_ping": {
            "properties": ["timeout"],
        },
    }
    checks = []
    for name, expected in expectations.items():
        schema = tools.get(name) or {}
        properties = schema.get("properties") or {}
        missing_properties = [item for item in expected.get("properties", []) if item not in properties]
        missing_required = [item for item in expected.get("required", []) if item not in schema.get("required", [])]
        enum_mismatches = {}
        for prop, expected_enum in expected.get("enums", {}).items():
            actual_enum = properties.get(prop, {}).get("enum")
            if actual_enum != expected_enum:
                enum_mismatches[prop] = {"expected": expected_enum, "actual": actual_enum}
        ok = not missing_properties and not missing_required and not enum_mismatches
        checks.append({
            "tool": name,
            "ok": ok,
            "missing_properties": missing_properties,
            "missing_required": missing_required,
            "enum_mismatches": enum_mismatches,
        })
    failed = [item for item in checks if not item.get("ok")]
    result = {
        "ok": not failed,
        "checked": len(checks),
        "checks": checks,
    }
    if failed:
        result["next_action"] = "Restart the MCP server/client so it advertises the current local tool schemas."
    return result


def visual_capture_dependency_status(
    system: str | None = None,
    finder: Callable[[str], object | None] | None = None,
) -> dict:
    system = system or platform.system()
    finder = finder or importlib.util.find_spec
    supported = system in ("Darwin", "Windows")
    dependencies = [
        _visual_dependency("Pillow", "PIL.Image", True, finder),
    ]
    if system == "Darwin":
        dependencies.append(_visual_dependency("pyobjc-framework-Quartz", "Quartz", True, finder))
    elif system == "Windows":
        dependencies.append(_visual_dependency("windows-capture", "windows_capture", True, finder))
    missing = [item["package"] for item in dependencies if item.get("required") and not item.get("available")]
    ok = supported and not missing
    result = {
        "ok": ok,
        "platform": system,
        "supported_platform": supported,
        "dependencies": dependencies,
    }
    if not supported:
        result["next_action"] = "Ableton visual capture currently supports macOS and Windows only."
    elif missing:
        result["next_action"] = 'Install visual capture dependencies with python -m pip install -e ".[visual]" or ".[dev]".'
        result["missing"] = missing
    return result


def _visual_dependency(package: str, module: str, required: bool, finder: Callable[[str], object | None]) -> dict:
    return {
        "package": package,
        "module": module,
        "required": required,
        "available": finder(module) is not None,
    }


def _probe_bridge_status(timeout: float) -> dict:
    probe_timeout = max(1.0, min(float(timeout), 3.0))
    try:
        status = AbletonBridgeClient().request("bridge_status", {"timeout": probe_timeout, "strict_timeout": True})
        if isinstance(status, dict):
            return status
        return {"ok": True, "result": status}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def _live_failure_diagnostics(exc: Exception, bridge_status: dict | None = None) -> tuple[str, str]:
    message = str(exc)
    lower = message.lower()
    bridge_unresponsive = _bridge_status_probe_unresponsive(bridge_status)
    if "connection refused" in lower:
        return (
            "bridge_not_listening",
            "Start Ableton Live and select or reload the Ableton_Live_MCP Control Surface; the localhost bridge is not listening.",
        )
    if "could not connect" in lower and "timed out" in lower:
        return (
            "bridge_connect_timeout",
            "Confirm Ableton Live is running and the Ableton_Live_MCP Control Surface is selected; reload the Control Surface if the bridge never starts listening.",
        )
    if "timed out waiting for live main thread" in lower or "stall cooldown" in lower:
        if bridge_status and bridge_status.get("server_thread_responsive"):
            return (
                "live_main_thread_hung",
                "The bridge socket thread is responsive, but Live's main thread did not execute scheduled work or is in a protective stall cooldown. Stop sending Live API mutations; save/recover the set if possible, then restart Ableton Live or reload the Control Surface with user authorization and rerun validation.",
            )
        if bridge_unresponsive:
            return (
                "live_process_unresponsive",
                "The Live API check timed out and the socket-thread bridge_status probe also timed out. Treat Ableton Live/Max as wedged: stop sending MCP calls, preserve diagnostics such as visual capture or an OS process sample, and recover/restart/reload Live only with user authorization before validating again.",
            )
        return (
            "live_main_thread_timeout",
            "Check Ableton Live for modal dialogs, permission prompts, browser/indexing stalls, heavy UI work, or a client-side stall cooldown after a sent timeout; resolve the blocker, then rerun validation before sending more mutations.",
        )
    if "timed out" in lower:
        if bridge_unresponsive:
            return (
                "bridge_unresponsive",
                "The bridge request timed out and the socket-thread bridge_status probe also timed out. Treat the Live/Max process or Remote Script bridge as wedged; stop retrying Live API calls, preserve diagnostics, and recover/restart/reload Live only with user authorization before validating again.",
            )
        return (
            "bridge_response_timeout",
            "Check Ableton Live for modal dialogs or UI stalls. In stressed sets, retry with a longer timeout only after confirming no modal is blocking Live.",
        )
    return (
        "live_check_failed",
        "Start Ableton Live and select or reload the Ableton_Live_MCP Control Surface; if the bridge remains unresponsive, restart Ableton Live.",
    )


def _bridge_status_probe_unresponsive(bridge_status: dict | None) -> bool:
    if not isinstance(bridge_status, dict):
        return False
    if bridge_status.get("server_thread_responsive"):
        return False
    if bridge_status.get("ok") is not False:
        return False
    text = str(bridge_status.get("error") or "").lower()
    return "timed out" in text


if __name__ == "__main__":
    raise SystemExit(main())
