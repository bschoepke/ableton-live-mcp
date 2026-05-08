from __future__ import annotations

import json
import sys
from typing import Any

from bridge import AbletonBridgeClient, AbletonBridgeError
from debug import require_debug_cli


def _ok(name: str, result: Any) -> dict[str, Any]:
    return {"name": name, "ok": True, "result": result}


def _fail(name: str, exc: Exception) -> dict[str, Any]:
    return {"name": name, "ok": False, "error": str(exc)}


def _call(client: AbletonBridgeClient, name: str, method: str, params: dict[str, Any]) -> dict[str, Any]:
    try:
        return _ok(name, client.request(method, params))
    except Exception as exc:
        return _fail(name, exc)


def run_smoke(client: AbletonBridgeClient | None = None) -> tuple[int, dict[str, Any]]:
    client = client or AbletonBridgeClient()
    checks: list[dict[str, Any]] = []

    checks.append(_call(client, "ping", "ping", {}))
    checks.append(_call(client, "song_summary", "get", {
        "ref": {"path": "live_set"},
        "properties": ["tempo", "signature_numerator", "signature_denominator", "current_song_time"],
        "children": {"tracks": 8, "scenes": 8, "return_tracks": 4},
    }))
    checks.append(_call(client, "track_children_limit", "children", {
        "ref": {"path": "live_set"},
        "child": "tracks",
        "limit": 3,
    }))
    checks.append(_call(client, "full_object_eval", "eval", {
        "expr": "sorted([name for name in dir(song) if 'track' in name.lower() or 'scene' in name.lower()])[:40]",
    }))
    checks.append(_call(client, "exec_summary", "exec", {
        "code": "result = {'tracks': len(song.tracks), 'scenes': len(song.scenes), 'tempo': song.tempo}",
    }))
    checks.append(_call(client, "batch_roundtrip", "batch", {
        "operations": [
            {"method": "get", "params": {"ref": {"path": "live_set"}, "properties": ["tempo"]}},
            {"method": "children", "params": {"ref": {"path": "live_set"}, "child": "tracks", "limit": 2}},
            {"method": "eval", "params": {"expr": "app.get_version_string() if hasattr(app, 'get_version_string') else 'unknown'"}},
            {"method": "exec", "params": {"code": "result = len(song.tracks)"}},
        ],
    }))
    checks.append(_call(client, "agent_m4l_command_file_update", "agent_m4l_device", {
        "role": "audio_effect",
        "instance_id": "Smoke M4L Direct",
        "command": "update",
        "load": False,
        "udp": False,
        "id": "smoke-m4l-update",
        "patch": {
            "device_width": 280,
            "device_height": 130,
            "objects": [{"id": "probe_value", "text": "flonum", "presentation_rect": [12, 12, 80, 22]}],
            "connections": [],
        },
    }))
    checks.append(_call(client, "browser_roots", "browser_roots", {}))
    checks.append(_call(client, "browser_instrument_search", "browser_search", {
        "query": "drum",
        "roots": ["instruments", "drums"],
        "limit": 5,
        "max_depth": 4,
    }))
    checks.append(_call(client, "browser_plugin_search", "browser_search", {
        "query": "",
        "roots": ["plugins"],
        "limit": 5,
        "max_depth": 4,
        "include_folders": True,
        "loadable_only": False,
    }))
    checks.append(_call(client, "observe_add", "observe", {
        "ref": {"path": "live_set"},
        "property": "tempo",
        "enabled": True,
    }))
    checks.append(_call(client, "observe_remove", "observe", {
        "ref": {"path": "live_set"},
        "property": "tempo",
        "enabled": False,
    }))
    checks.append(_call(client, "events_drain", "events", {"limit": 10}))

    hard_failures = [
        check for check in checks
        if not check["ok"] and check["name"] not in {"browser_plugin_search"}
    ]
    output = {
        "ok": not hard_failures,
        "checks": checks,
        "summary": {
            "total": len(checks),
            "passed": sum(1 for check in checks if check["ok"]),
            "failed": sum(1 for check in checks if not check["ok"]),
            "hard_failed": len(hard_failures),
        },
    }
    return (0 if output["ok"] else 1), output


def main() -> int:
    if not require_debug_cli("ableton-live-mcp smoke"):
        return 2
    try:
        code, output = run_smoke()
    except AbletonBridgeError as exc:
        print(f"Ableton Live MCP smoke failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(output, indent=2, sort_keys=True))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
