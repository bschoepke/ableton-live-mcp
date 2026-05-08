from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from bridge import AbletonBridgeClient, AbletonBridgeError
from install_remote_script import remote_script_status


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate the Ableton Live MCP install and connection.")
    parser.add_argument("--target-dir", type=Path, default=None, help="Ableton Remote Scripts directory to check.")
    parser.add_argument("--skip-live", action="store_true", help="Only validate local Remote Script freshness.")
    parser.add_argument("--allow-stale-remote-script", action="store_true", help="Do not fail when the installed Remote Script differs from this checkout.")
    args = parser.parse_args(argv)

    results = {"remote_script": remote_script_status(target_dir=args.target_dir)}
    remote_ok = bool(results["remote_script"].get("current"))
    if not remote_ok and not args.allow_stale_remote_script:
        print(json.dumps(results, indent=2, sort_keys=True))
        print("Ableton Live MCP validation failed: installed Remote Script is missing or stale", file=sys.stderr)
        return 1
    if args.skip_live:
        print(json.dumps(results, indent=2, sort_keys=True))
        return 0

    client = AbletonBridgeClient()
    checks = [
        ("ping", "ping", {}),
        ("song", "get", {"ref": {"path": "live_set"}, "properties": ["tempo", "signature_numerator", "signature_denominator"], "timeout": 45}),
        ("application", "eval", {"expr": "app.get_major_version() if hasattr(app, 'get_major_version') else app.get_version_string().split('.')[0]", "timeout": 45}),
    ]
    try:
        for name, method, params in checks:
            results[name] = client.request(method, params)
    except AbletonBridgeError as exc:
        print(f"Ableton Live MCP validation failed: {exc}", file=sys.stderr)
        return 1
    runtime_ok, runtime_reason = _check_running_remote_script(results)
    results["remote_script"]["runtime_current"] = runtime_ok
    if runtime_reason:
        results["remote_script"]["runtime_mismatch"] = runtime_reason
    if not runtime_ok and not args.allow_stale_remote_script:
        print(json.dumps(results, indent=2, sort_keys=True))
        print("Ableton Live MCP validation failed: running Remote Script is stale or unverified", file=sys.stderr)
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


if __name__ == "__main__":
    raise SystemExit(main())
