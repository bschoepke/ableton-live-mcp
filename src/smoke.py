from __future__ import annotations

import argparse
import json
import math
import struct
import sys
import tempfile
import wave
from pathlib import Path
from typing import Any

from bridge import AbletonBridgeClient, AbletonBridgeError
from debug import require_debug_cli
from similar_sounds import find_similar_sounds


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


def run_core_regression(
    client: AbletonBridgeClient | None = None,
    *,
    similar_finder=find_similar_sounds,
    audio_file: Path | None = None,
    compact: bool = True,
) -> tuple[int, dict[str, Any]]:
    client = client or AbletonBridgeClient()
    checks: list[dict[str, Any]] = []
    prefix = "MCP Regression "

    def call(name: str, method: str, params: dict[str, Any]) -> Any:
        check = _call(client, name, method, params)
        checks.append(check)
        if not check["ok"]:
            raise RuntimeError("%s failed: %s" % (name, check.get("error")))
        return check["result"]

    try:
        call("ping_current_runtime", "ping", {"timeout": 10})
        setup = call("create_regression_tracks", "exec", {"code": _core_regression_setup_code(prefix), "timeout": 15})
        notes = [
            {"pitch": 60, "start_time": 0.0, "duration": 0.45, "velocity": 96},
            {"pitch": 64, "start_time": 0.5, "duration": 0.45, "velocity": 92},
            {"pitch": 67, "start_time": 1.0, "duration": 0.45, "velocity": 100},
            {"pitch": 72, "start_time": 1.5, "duration": 0.45, "velocity": 88},
        ]
        call("add_midi_clip_notes", "clip_add_notes", {
            "ref": {"path": setup["midi_path"] + " clip_slots 0"},
            "create_clip_length": 4.0,
            "clip_name": prefix + "MIDI Clip",
            "notes": notes,
            "timeout": 15,
        })
        readback = call("read_back_midi_clip_notes", "clip_notes", {
            "ref": {"path": setup["midi_path"] + " clip_slots 0 clip"},
            "limit": 16,
            "timeout": 15,
        })
        if len(readback.get("notes", [])) < len(notes):
            raise RuntimeError("MIDI note readback returned too few notes")

        search = call("search_library_audio_device", "browser_search", {
            "query": "Limiter",
            "roots": ["audio_effects"],
            "limit": 8,
            "max_depth": 5,
            "max_visited": 8000,
            "loadable_only": True,
            "stop_on_limit": True,
            "timeout": 20,
        })
        limiter = _pick_browser_result(search, "Limiter")
        call("load_library_device_to_audio_track", "browser_load", {
            "item": {"id": limiter["id"]},
            "target_track": {"path": setup["audio_path"]},
            "timeout": 20,
        })

        wav_path = audio_file or _write_regression_wav()
        call("create_audio_clip_from_file", "track_create_audio_clip", {
            "ref": {"path": setup["audio_path"]},
            "file_path": str(wav_path),
            "destination_time": 0.0,
            "name": prefix + "Audio File",
            "timeout": 20,
        })
        summary = call("verify_regression_track_summary", "set_summary", {
            "track_query": prefix,
            "track_limit": 4,
            "clip_slot_limit": 2,
            "device_limit": 8,
            "arrangement_clip_limit": 8,
            "timeout": 15,
        })
        _verify_core_regression_summary(summary, prefix)
        similar = _find_first_similar(similar_finder)
        checks.append(_ok("find_similar_sounds", {
            "database": similar["result"].get("database"),
            "query": similar["query"],
            "base": (similar["result"].get("base") or {}).get("name"),
            "result_count": len(similar["result"].get("results", [])),
        }))
    except Exception as exc:
        checks.append(_fail("core_regression_assertion", exc))

    hard_failures = [check for check in checks if not check["ok"]]
    output = {
        "ok": not hard_failures,
        "destructive": True,
        "checks": [_compact_core_regression_check(check) for check in checks] if compact else checks,
        "summary": {
            "total": len(checks),
            "passed": sum(1 for check in checks if check["ok"]),
            "failed": sum(1 for check in checks if not check["ok"]),
            "hard_failed": len(hard_failures),
        },
    }
    return (0 if output["ok"] else 1), output


def _compact_core_regression_check(check: dict[str, Any]) -> dict[str, Any]:
    result = check.get("result")
    compact = {key: value for key, value in check.items() if key != "result"}
    if not isinstance(result, dict):
        return compact
    name = str(check.get("name") or "")
    if name == "ping_current_runtime":
        remote = result.get("remote_script") or {}
        compact["version"] = result.get("version")
        compact["runtime_version"] = remote.get("runtime_version")
    elif name == "read_back_midi_clip_notes":
        compact["note_count"] = result.get("note_count")
        if compact["note_count"] is None and isinstance(result.get("notes"), list):
            compact["note_count"] = len(result["notes"])
    elif name == "search_library_audio_device":
        results = result.get("results") or []
        compact["result_count"] = len(results)
        if results:
            compact["first_result"] = results[0].get("name")
    elif name == "create_audio_clip_from_file":
        compact["clip_name"] = (result.get("clip") or {}).get("name")
    elif name == "verify_regression_track_summary":
        compact["track_count"] = len(result.get("tracks") or [])
        compact["set_signature"] = result.get("set_signature")
    elif name == "find_similar_sounds":
        compact.update(result)
    return compact


def _core_regression_setup_code(prefix: str) -> str:
    return f'''
prefix = {json.dumps(prefix)}
for index in reversed(range(len(song.tracks))):
    try:
        if str(song.tracks[index].name).startswith(prefix):
            song.delete_track(index)
    except Exception:
        pass
midi_index = len(song.tracks)
song.create_midi_track(midi_index)
midi_track = song.tracks[midi_index]
midi_track.name = prefix + "MIDI Clip"
audio_index = len(song.tracks)
song.create_audio_track(audio_index)
audio_track = song.tracks[audio_index]
audio_track.name = prefix + "Audio Device Clip"
result = {{
    "midi_index": midi_index,
    "audio_index": audio_index,
    "midi_path": "live_set tracks %s" % midi_index,
    "audio_path": "live_set tracks %s" % audio_index,
}}
'''.strip()


def _pick_browser_result(search: dict[str, Any], preferred_name: str) -> dict[str, Any]:
    results = search.get("results") or []
    if not results:
        raise RuntimeError("No loadable %s browser item found" % preferred_name)
    return next((item for item in results if item.get("name") == preferred_name and item.get("is_loadable")), results[0])


def _verify_core_regression_summary(summary: dict[str, Any], prefix: str) -> None:
    tracks = summary.get("tracks", [])
    if not any(track.get("name") == prefix + "MIDI Clip" and track.get("clips") for track in tracks):
        raise RuntimeError("MIDI regression track/clip missing from summary")
    audio_tracks = [track for track in tracks if track.get("name") == prefix + "Audio Device Clip"]
    if not audio_tracks:
        raise RuntimeError("Audio regression track missing from summary")
    audio_track = audio_tracks[0]
    if not any("Limiter" in device.get("name", "") for device in audio_track.get("devices", [])):
        raise RuntimeError("Loaded Limiter not found on audio regression track")
    if not any(clip.get("name") == prefix + "Audio File" for clip in audio_track.get("arrangement_clips", [])):
        raise RuntimeError("Audio file clip not found in arrangement summary")


def _find_first_similar(similar_finder) -> dict[str, Any]:
    errors = []
    for query in ("kick", "snare", "hat", "bass", "loop", "drum"):
        try:
            result = similar_finder({"query": query, "limit": 3})
        except Exception as exc:
            errors.append({"query": query, "error": str(exc)})
            continue
        if result.get("results"):
            return {"query": query, "result": result}
    raise RuntimeError("find_similar_sounds failed for fallback queries: %s" % json.dumps(errors, separators=(",", ":")))


def _write_regression_wav() -> Path:
    path = Path(tempfile.gettempdir()) / "ableton_mcp_regression_tone.wav"
    sample_rate = 44100
    with wave.open(str(path), "w") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        frames = []
        for index in range(sample_rate):
            envelope = min(1.0, index / 2205) * min(1.0, (sample_rate - index) / 2205)
            value = 0.28 * envelope * math.sin(2 * math.pi * 330 * index / sample_rate)
            frames.append(struct.pack("<h", int(value * 32767)))
        wav.writeframes(b"".join(frames))
    return path


def main() -> int:
    if not require_debug_cli("ableton-live-mcp smoke"):
        return 2
    parser = argparse.ArgumentParser(description="Run Ableton Live MCP smoke checks.")
    parser.add_argument("--core-regression", action="store_true", help="Run destructive checks for library device load, MIDI clips, audio import, and similar sounds.")
    parser.add_argument("--detail", action="store_true", help="Include full regression result payloads instead of compact evidence.")
    parser.add_argument("--yes", action="store_true", help="Required with --core-regression because it modifies the open Live set.")
    args = parser.parse_args()
    if args.core_regression and not args.yes:
        print("Refusing to run destructive core regression without --yes.", file=sys.stderr)
        return 2
    try:
        code, output = run_core_regression(compact=not args.detail) if args.core_regression else run_smoke()
    except AbletonBridgeError as exc:
        print(f"Ableton Live MCP smoke failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(output, indent=2, sort_keys=True))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
