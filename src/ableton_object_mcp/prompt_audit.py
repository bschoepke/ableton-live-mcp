from __future__ import annotations

import argparse
import json
import math
import statistics
import struct
import sys
import tempfile
import time
import wave
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .bridge import AbletonBridgeClient, AbletonBridgeError


Scenario = Callable[[AbletonBridgeClient], dict[str, Any]]


def _response_size(value: Any) -> int:
    return len(json.dumps(value, separators=(",", ":"), sort_keys=True).encode("utf-8"))


def _call(client: AbletonBridgeClient, method: str, params: dict[str, Any], name: str) -> dict[str, Any]:
    start = time.perf_counter()
    result = client.request(method, params)
    elapsed_ms = (time.perf_counter() - start) * 1000
    return {
        "name": name,
        "method": method,
        "ms": round(elapsed_ms, 3),
        "bytes": _response_size(result),
        "result": result,
    }


def _scenario_make_edm_song(client: AbletonBridgeClient) -> dict[str, Any]:
    calls = [
        _call(client, "batch", {
            "continue_on_error": True,
            "operations": [
                {"method": "browser_search", "params": {"query": "Drum Rack", "roots": ["instruments"], "limit": 1, "stop_on_limit": True}},
                {"method": "browser_search", "params": {"query": "bass", "roots": ["sounds", "instruments"], "limit": 3, "max_depth": 5, "max_visited": 3000}},
                {"method": "browser_search", "params": {"query": "lead", "roots": ["sounds", "instruments"], "limit": 3, "max_depth": 5, "max_visited": 3000}},
                {"method": "browser_search", "params": {"query": "limiter", "roots": ["audio_effects"], "limit": 1, "stop_on_limit": True}},
            ],
        }, "discover_library_candidates"),
        _call(client, "exec", {"code": _EDM_CREATION_CODE, "timeout": 10}, "create_four_track_edm_arrangement"),
    ]
    return _scenario_result("make_edm_song", "Make me an EDM song", calls)


def _scenario_library_sample_track(client: AbletonBridgeClient) -> dict[str, Any]:
    calls = [
        _call(client, "browser_search", {
            "query": "cowbell",
            "roots": ["drums", "samples", "user_library"],
            "limit": 1,
            "max_depth": 7,
            "max_visited": 8000,
            "stop_on_limit": True,
            "stop_score": 1,
        }, "find_first_good_cowbell"),
        _call(client, "exec", {"code": _CREATE_SAMPLE_TRACK_CODE, "timeout": 5}, "create_sample_target_track"),
    ]
    results = calls[0]["result"].get("results", []) if isinstance(calls[0]["result"], dict) else []
    if not results:
        return _scenario_result("library_sample_track", "Use an installed cowbell sample", calls, skipped="no cowbell sample found")
    track_path = calls[1]["result"]["track_path"]
    calls.append(_call(client, "browser_load", {"item": {"id": results[0]["id"]}, "target_track": {"path": track_path}}, "load_sample_into_track"))
    calls.append(_call(client, "exec", {"code": _ADD_SAMPLE_NOTES_CODE % track_path, "timeout": 5}, "add_sample_midi_hook"))
    return _scenario_result("library_sample_track", "Use an installed cowbell sample", calls)


def _scenario_existing_project_edit(client: AbletonBridgeClient) -> dict[str, Any]:
    calls = [
        _call(client, "exec", {"code": _EXISTING_EDIT_SETUP_CODE, "timeout": 5}, "seed_existing_midi_arrangement_clip"),
        _call(client, "set_summary", {"track_query": "Audit Existing MIDI", "track_limit": 4, "clip_slot_limit": 2, "device_limit": 2, "arrangement_clip_limit": 8}, "summarize_existing_project"),
    ]
    clip_id = None
    for track in calls[1]["result"].get("tracks", []):
        for clip in track.get("arrangement_clips") or []:
            if clip.get("name") == "MCP Prompt Audit Existing":
                clip_id = clip["id"]
    if clip_id is None:
        raise RuntimeError("Prompt audit setup clip was not found in set_summary")
    calls.append(_call(client, "clip_notes", {"ref": {"id": clip_id}, "limit": 16}, "inspect_existing_midi_notes"))
    updates = [{"note_id": note["note_id"], "velocity": min(127, note["velocity"] + 12)} for note in calls[-1]["result"].get("notes", [])[:8]]
    calls.append(_call(client, "clip_update_notes", {"ref": {"id": clip_id}, "updates": updates}, "humanize_existing_midi_notes"))
    return _scenario_result("existing_project_midi_edit", "Edit notes in an existing Arrangement clip", calls)


def _scenario_audio_warp_edit(client: AbletonBridgeClient) -> dict[str, Any]:
    wav_path = _write_probe_wav()
    setup_code = _AUDIO_WARP_SETUP_CODE % str(wav_path)
    calls = [
        _call(client, "exec", {"code": setup_code, "timeout": 5}, "seed_existing_audio_clip"),
        _call(client, "set_summary", {"track_query": "Audit Existing Audio", "track_limit": 4, "clip_slot_limit": 0, "device_limit": 0, "arrangement_clip_limit": 8}, "summarize_audio_arrangement_clip"),
    ]
    clip_id = None
    for track in calls[1]["result"].get("tracks", []):
        for clip in track.get("arrangement_clips") or []:
            if clip.get("name") == "MCP Prompt Audit Warp":
                clip_id = clip["id"]
    if clip_id is None:
        raise RuntimeError("Prompt audit audio clip was not found in set_summary")
    calls.append(_call(client, "clip_warp_markers", {"ref": {"id": clip_id}, "limit": 16}, "inspect_audio_warp_markers"))
    calls.append(_call(client, "clip_warp_markers", {
        "ref": {"id": clip_id},
        "warping": True,
        "add_markers": [{"sample_time": 1.0, "beat_time": 1.0}],
        "limit": 16,
    }, "add_audio_warp_marker"))
    return _scenario_result("audio_warp_edit", "Edit warp markers in an existing audio clip", calls)


def _scenario_plugin_discovery(client: AbletonBridgeClient) -> dict[str, Any]:
    calls = [_call(client, "browser_search", {
        "query": "",
        "roots": ["plugins"],
        "limit": 12,
        "max_depth": 4,
        "max_visited": 2500,
        "include_folders": True,
        "loadable_only": False,
    }, "discover_installed_plugins")]
    return _scenario_result("plugin_discovery", "Discover installed third-party plugins", calls)


def _scenario_result(name: str, prompt: str, calls: list[dict[str, Any]], skipped: str | None = None) -> dict[str, Any]:
    timings = [call["ms"] for call in calls]
    sizes = [call["bytes"] for call in calls]
    return {
        "name": name,
        "prompt": prompt,
        "ok": skipped is None,
        "skipped": skipped is not None,
        "reason": skipped,
        "calls": [{key: value for key, value in call.items() if key != "result"} for call in calls],
        "total_ms": round(sum(timings), 3),
        "median_call_ms": round(statistics.median(timings), 3) if timings else None,
        "total_bytes": sum(sizes),
        "max_call_bytes": max(sizes) if sizes else None,
    }


def _write_probe_wav() -> Path:
    handle = tempfile.NamedTemporaryFile(prefix="ableton_mcp_prompt_audit_", suffix=".wav", delete=False)
    path = Path(handle.name)
    handle.close()
    sample_rate = 44100
    with wave.open(str(path), "w") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        frames = []
        for index in range(sample_rate * 2):
            value = 0.35 * math.sin(2 * math.pi * 220 * index / sample_rate)
            frames.append(struct.pack("<h", int(value * 32767)))
        wav.writeframes(b"".join(frames))
    return path


def run_prompt_audit(client: AbletonBridgeClient | None = None, *, include_optional: bool = True) -> tuple[int, dict[str, Any]]:
    client = client or AbletonBridgeClient()
    scenarios: list[tuple[Scenario, bool]] = [
        (_scenario_make_edm_song, False),
        (_scenario_library_sample_track, True),
        (_scenario_existing_project_edit, False),
        (_scenario_audio_warp_edit, False),
        (_scenario_plugin_discovery, True),
    ]
    checks = []
    for scenario, optional in scenarios:
        if optional and not include_optional:
            continue
        try:
            checks.append(scenario(client))
        except Exception as exc:
            checks.append({"name": scenario.__name__.replace("_scenario_", ""), "ok": False, "optional": optional, "error": str(exc)})
    hard_failures = [check for check in checks if not check["ok"] and not check.get("optional") and not check.get("skipped")]
    totals = [check["total_ms"] for check in checks if check.get("total_ms") is not None]
    output = {
        "ok": not hard_failures,
        "destructive": True,
        "checks": checks,
        "summary": {
            "total": len(checks),
            "passed": sum(1 for check in checks if check["ok"]),
            "skipped": sum(1 for check in checks if check.get("skipped")),
            "failed": sum(1 for check in checks if not check["ok"] and not check.get("skipped")),
            "hard_failed": len(hard_failures),
            "median_scenario_ms": round(statistics.median(totals), 3) if totals else None,
            "max_scenario_ms": max(totals) if totals else None,
        },
    }
    return (0 if output["ok"] else 1), output


def main() -> int:
    parser = argparse.ArgumentParser(description="Run destructive real-prompt Ableton Object MCP workflow audits.")
    parser.add_argument("--yes", action="store_true", help="Required: acknowledge that this modifies the open Live set.")
    parser.add_argument("--no-optional", action="store_true", help="Skip library/plugin-dependent optional scenarios.")
    args = parser.parse_args()
    if not args.yes:
        print("Refusing to run destructive prompt audit without --yes.", file=sys.stderr)
        return 2
    try:
        code, output = run_prompt_audit(include_optional=not args.no_optional)
    except AbletonBridgeError as exc:
        print(f"Ableton Object MCP prompt audit failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(output, indent=2, sort_keys=True))
    return code


_EDM_CREATION_CODE = r'''
song.tempo = 128
names = ["Audit Drums", "Audit Bass", "Audit Lead", "Audit Hook"]
patterns = {
    "Audit Drums": [(36, 0, 0.25, 112), (42, 0.5, 0.1, 78), (38, 1, 0.25, 96), (42, 1.5, 0.1, 78), (36, 2, 0.25, 112), (42, 2.5, 0.1, 78), (38, 3, 0.25, 96), (42, 3.5, 0.1, 78)],
    "Audit Bass": [(36, 0, 0.5, 100), (36, 0.75, 0.25, 88), (39, 1.5, 0.5, 98), (34, 2.5, 0.5, 96)],
    "Audit Lead": [(72, 0, 0.25, 86), (76, 0.5, 0.25, 82), (79, 1, 0.5, 88), (83, 2, 0.25, 90), (79, 2.5, 0.25, 84), (76, 3, 0.5, 82)],
    "Audit Hook": [(60, 0, 0.5, 92), (67, 0.75, 0.25, 86), (69, 1, 0.5, 88), (72, 2, 0.75, 94), (71, 3, 0.5, 85)],
}
created = []
for name in names:
    song.create_midi_track(len(song.tracks))
    track = song.tracks[-1]
    track.name = name
    slot = track.clip_slots[0]
    slot.create_clip(4.0)
    clip = slot.clip
    clip.name = name + " Loop"
    specs = [Live.Clip.MidiNoteSpecification(pitch=pitch, start_time=start, duration=duration, velocity=velocity, mute=False) for pitch, start, duration, velocity in patterns[name]]
    clip.add_new_notes(tuple(specs))
    for bar in range(4):
        track.duplicate_clip_to_arrangement(clip, bar * 4.0)
    created.append({"track": track.name, "notes": len(specs), "arrangement_clips": len(track.arrangement_clips)})
result = {"tempo": song.tempo, "created": created, "track_count": len(song.tracks)}
'''

_CREATE_SAMPLE_TRACK_CODE = r'''
song.create_midi_track(len(song.tracks))
track = song.tracks[-1]
track.name = "Audit Library Sample"
song.view.selected_track = track
result = {"track_path": "live_set tracks %s" % (len(song.tracks) - 1), "track": track.name}
'''

_ADD_SAMPLE_NOTES_CODE = r'''
track = this._resolve_path("%s")
if not track.clip_slots[0].has_clip:
    track.clip_slots[0].create_clip(4.0)
clip = track.clip_slots[0].clip
clip.name = "Audit Cowbell Hook"
specs = [Live.Clip.MidiNoteSpecification(pitch=60, start_time=start, duration=0.1, velocity=100, mute=False) for start in (0.0, 0.75, 1.5, 2.25, 3.0)]
clip.add_new_notes(tuple(specs))
track.duplicate_clip_to_arrangement(clip, 16.0)
result = {"clip": clip.name, "notes": len(specs), "arrangement_clip_count": len(track.arrangement_clips)}
'''

_EXISTING_EDIT_SETUP_CODE = r'''
song.create_midi_track(len(song.tracks))
track = song.tracks[-1]
track.name = "Audit Existing MIDI"
slot = track.clip_slots[0]
slot.create_clip(4.0)
clip = slot.clip
clip.name = "MCP Prompt Audit Existing"
specs = [Live.Clip.MidiNoteSpecification(pitch=60 + index, start_time=index * 0.5, duration=0.25, velocity=50 + index * 4, mute=False) for index in range(8)]
clip.add_new_notes(tuple(specs))
arrangement_clip = track.duplicate_clip_to_arrangement(clip, 32.0)
arrangement_clip.name = "MCP Prompt Audit Existing"
result = {"track": track.name, "notes": len(specs), "arrangement_clip_count": len(track.arrangement_clips)}
'''

_AUDIO_WARP_SETUP_CODE = r'''
song.create_audio_track(len(song.tracks))
track = song.tracks[-1]
track.name = "Audit Existing Audio"
clip = track.create_audio_clip(r"%s", 48.0)
clip.name = "MCP Prompt Audit Warp"
result = {"track": track.name, "clip": clip.name, "warping": getattr(clip, "warping", None)}
'''


if __name__ == "__main__":
    raise SystemExit(main())
