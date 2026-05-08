from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import agent_m4l
from bridge import AbletonBridgeClient, AbletonBridgeError
from install_remote_script import remote_script_status


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate the Ableton Live MCP install and connection.")
    parser.add_argument("--target-dir", type=Path, default=None, help="Ableton Remote Scripts directory to check.")
    parser.add_argument("--skip-live", action="store_true", help="Only validate local Remote Script freshness.")
    parser.add_argument("--allow-stale-remote-script", action="store_true", help="Do not fail when the installed or running Remote Script differs from this checkout.")
    parser.add_argument("--allow-stale-m4l-host", action="store_true", help="Do not fail when generated Agent M4L host companion JS copies are stale.")
    parser.add_argument("--timeout", type=float, default=45.0, help="Seconds to wait for Live checks.")
    parser.add_argument("--strict-timeout", action="store_true", help="Do not clamp short Live check timeouts to the default main-thread wait.")
    args = parser.parse_args(argv)

    results = {
        "remote_script": remote_script_status(target_dir=args.target_dir),
        "m4l_host": agent_m4l.agent_m4l_host_status(),
    }
    remote_ok = bool(results["remote_script"].get("current"))
    m4l_host_ok = bool(results["m4l_host"].get("current"))
    if not remote_ok and not args.allow_stale_remote_script:
        print(json.dumps(results, indent=2, sort_keys=True))
        print("Ableton Live MCP validation failed: installed Remote Script is missing or stale", file=sys.stderr)
        return 1
    if not m4l_host_ok and not args.allow_stale_m4l_host:
        print(json.dumps(results, indent=2, sort_keys=True))
        print("Ableton Live MCP validation failed: generated Agent M4L host files are missing or stale", file=sys.stderr)
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
    expected = (results.get("remote_script") or {}).get("source_bridge_sha256")
    runtime = ((results.get("ping") or {}).get("remote_script") or {})
    actual = runtime.get("bridge_sha256")
    if not expected:
        return False, "missing_source_bridge_hash"
    if not actual:
        return False, "missing_runtime_bridge_hash"
    if actual != expected:
        return False, "bridge_hash_mismatch"
    return True, ""


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
    if "timed out waiting for live main thread" in lower:
        if bridge_status and bridge_status.get("server_thread_responsive"):
            return (
                "live_main_thread_hung",
                "The Remote Script socket thread is responsive, but Live's main thread did not execute scheduled work. Stop sending Live API mutations; save/recover the set if possible, then restart Ableton Live or reload the Control Surface with user authorization and rerun validation.",
            )
        return (
            "live_main_thread_timeout",
            "Check Ableton Live for modal dialogs, permission prompts, browser/indexing stalls, or heavy UI work; resolve the blocker, then rerun validation before sending more mutations.",
        )
    if "timed out" in lower:
        return (
            "bridge_response_timeout",
            "Check Ableton Live for modal dialogs or UI stalls. In stressed sets, retry with a longer timeout only after confirming no modal is blocking Live.",
        )
    return (
        "live_check_failed",
        "Start Ableton Live and select or reload the Ableton_Live_MCP Control Surface; if the bridge remains unresponsive, restart Ableton Live.",
    )


if __name__ == "__main__":
    raise SystemExit(main())
