from __future__ import annotations

import argparse
import hashlib
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
        "m4l_host": agent_m4l_host_status(),
    }
    remote_ok = bool(results["remote_script"].get("current"))
    m4l_host_ok = bool(results["m4l_host"].get("current"))
    if not remote_ok and not args.allow_stale_remote_script:
        print(json.dumps(results, indent=2, sort_keys=True))
        print("Ableton Live MCP validation failed: installed Remote Script is missing or stale", file=sys.stderr)
        return 1
    if not m4l_host_ok and not args.allow_stale_m4l_host:
        print(json.dumps(results, indent=2, sort_keys=True))
        print("Ableton Live MCP validation failed: generated Agent M4L host companion JS is missing or stale", file=sys.stderr)
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
        results["live_error"] = str(exc)
        results["remote_script"]["runtime_current"] = False
        results["remote_script"]["runtime_mismatch"] = "live_check_failed"
        results["remote_script"]["runtime_reload_required"] = True
        results["remote_script"]["runtime_next_action"] = "Start Ableton Live and select or reload the Ableton_Live_MCP Control Surface; if the bridge remains unresponsive, restart Ableton Live."
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


def agent_m4l_host_status() -> dict:
    source = Path(agent_m4l.HOST_JS)
    source_hash = _sha256(source) if source.is_file() else None
    targets = _agent_m4l_host_targets()
    checked = []
    missing = []
    stale = []
    for label, path in targets:
        target_hash = _sha256(path) if path.is_file() else None
        current = bool(source_hash and target_hash == source_hash)
        item = {
            "label": label,
            "path": str(path),
            "installed": path.is_file(),
            "current": current,
            "target_sha256": target_hash,
        }
        checked.append(item)
        if not path.is_file():
            missing.append(str(path))
        elif not current:
            stale.append(str(path))
    return {
        "source": str(source),
        "source_sha256": source_hash,
        "current": bool(source_hash) and not missing and not stale,
        "targets_checked": len(checked),
        "targets": checked,
        "missing": missing,
        "stale": stale,
    }


def _agent_m4l_host_targets() -> list[tuple[str, Path]]:
    targets: list[tuple[str, Path]] = []
    generated_dir = Path(agent_m4l.GENERATED_DIR)
    if _contains_agent_m4l_devices(generated_dir):
        targets.append(("generated", generated_dir / "agent_m4l_host.js"))
    for role in agent_m4l.ROLE_PRESETS:
        folder = agent_m4l.install_folder(role)
        if _contains_agent_m4l_devices(folder):
            targets.append((role, folder / "agent_m4l_host.js"))
    return targets


def _contains_agent_m4l_devices(folder: Path) -> bool:
    try:
        return folder.is_dir() and any(folder.glob("AgentM4L_*.amxd"))
    except Exception:
        return False


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


if __name__ == "__main__":
    raise SystemExit(main())
