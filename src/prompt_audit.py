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

import agent_m4l
from bridge import AbletonBridgeClient, AbletonBridgeError
from debug import require_debug_cli
from server import preflight_agent_m4l


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


def _local_m4l_preflight(params: dict[str, Any], name: str) -> dict[str, Any]:
    start = time.perf_counter()
    result = preflight_agent_m4l(dict(params))
    elapsed_ms = (time.perf_counter() - start) * 1000
    if not result.get("ok"):
        raise RuntimeError("%s failed: %s" % (name, json.dumps(result.get("errors") or [], separators=(",", ":"))))
    return {
        "name": name,
        "method": "local_m4l_preflight",
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
    track_paths = calls[1]["result"].get("track_paths", {}) if isinstance(calls[1]["result"], dict) else {}
    discovered = calls[0]["result"] if isinstance(calls[0]["result"], list) else []
    load_targets = [
        (0, "Audit Drums", "load_drum_instrument"),
        (1, "Audit Bass", "load_bass_instrument_or_preset"),
        (2, "Audit Lead", "load_lead_instrument_or_preset"),
    ]
    for result_index, track_name, call_name in load_targets:
        try:
            results = discovered[result_index]["result"].get("results", [])
            item_id = results[0]["id"] if results else None
            track_path = track_paths.get(track_name)
            if item_id and track_path:
                calls.append(_call(client, "browser_load", {"item": {"id": item_id}, "target_track": {"path": track_path}}, call_name))
        except Exception:
            pass
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
    calls.append(_call(client, "clip_add_notes", {
        "ref": {"path": "%s clip_slots 0" % track_path},
        "create_clip_length": 4.0,
        "clip_name": "Audit Cowbell Hook",
        "notes": [{"pitch": 60, "start_time": start, "duration": 0.1, "velocity": 100} for start in (0.0, 0.75, 1.5, 2.25, 3.0)],
    }, "add_sample_midi_hook"))
    calls.append(_call(client, "clip_duplicate_to_arrangement", {
        "track": {"path": track_path},
        "clip": {"path": "%s clip_slots 0 clip" % track_path},
        "destination_time": 16.0,
    }, "duplicate_sample_hook_to_arrangement"))
    return _scenario_result("library_sample_track", "Use an installed cowbell sample", calls)


def _scenario_existing_project_edit(client: AbletonBridgeClient) -> dict[str, Any]:
    calls = [
        _call(client, "exec", {"code": _EXISTING_EDIT_SETUP_CODE, "timeout": 5}, "create_existing_midi_track"),
    ]
    track_path = calls[0]["result"]["track_path"]
    calls.append(_call(client, "clip_add_notes", {
        "ref": {"path": "%s clip_slots 0" % track_path},
        "create_clip_length": 4.0,
        "clip_name": "MCP Prompt Audit Existing",
        "notes": [{"pitch": 60 + index, "start_time": index * 0.5, "duration": 0.25, "velocity": 50 + index * 4} for index in range(8)],
    }, "seed_existing_midi_session_clip"))
    calls.append(_call(client, "clip_duplicate_to_arrangement", {
        "track": {"path": track_path},
        "clip": {"path": "%s clip_slots 0 clip" % track_path},
        "destination_time": 32.0,
    }, "seed_existing_midi_arrangement_clip"))
    calls.append(_call(client, "set_summary", {"track_query": "Audit Existing MIDI", "track_limit": 1, "clip_slot_limit": 2, "device_limit": 2, "arrangement_clip_limit": 8}, "summarize_existing_project"))
    clip_id = None
    for track in calls[-1]["result"].get("tracks", []):
        for clip in track.get("arrangement_clips") or []:
            if clip.get("name") == "MCP Prompt Audit Existing":
                clip_id = clip["id"]
    if clip_id is None:
        raise RuntimeError("Prompt audit setup clip was not found in set_summary")
    calls.append(_call(client, "clip_notes", {"ref": {"id": clip_id}, "limit": 16}, "inspect_existing_midi_notes"))
    updates = [{"note_id": note["note_id"], "velocity": min(127, note["velocity"] + 12)} for note in calls[-1]["result"].get("notes", [])[:8]]
    calls.append(_call(client, "clip_update_notes", {"ref": {"id": clip_id}, "updates": updates}, "humanize_existing_midi_notes"))
    return _scenario_result("existing_project_midi_edit", "Edit notes in an existing Arrangement clip", calls)


def _scenario_clip_automation_edit(client: AbletonBridgeClient) -> dict[str, Any]:
    calls = [
        _call(client, "exec", {"code": _AUTOMATION_SETUP_CODE, "timeout": 5}, "create_automation_track"),
    ]
    track_path = calls[0]["result"]["track_path"]
    calls.append(_call(client, "clip_add_notes", {
        "ref": {"path": "%s clip_slots 0" % track_path},
        "create_clip_length": 4.0,
        "clip_name": "MCP Prompt Audit Automation",
        "notes": [{"pitch": 48, "start_time": start, "duration": 0.25, "velocity": 76} for start in (0.0, 1.0, 2.0, 3.0)],
    }, "seed_automation_session_clip"))
    calls.append(_call(client, "clip_duplicate_to_arrangement", {
        "track": {"path": track_path},
        "clip": {"path": "%s clip_slots 0 clip" % track_path},
        "destination_time": 40.0,
    }, "seed_existing_automation_clip"))
    calls.append(_call(client, "set_summary", {"track_query": "Audit Automation", "track_limit": 1, "clip_slot_limit": 2, "device_limit": 1, "arrangement_clip_limit": 4}, "summarize_automation_target"))
    clip_id = None
    track_index = None
    for track in calls[-1]["result"].get("tracks", []):
        if track.get("name") == "Audit Automation":
            track_index = track.get("index")
        for clip in track.get("clips") or []:
            if clip.get("name") == "MCP Prompt Audit Automation":
                clip_id = clip["id"]
    if track_index is None:
        raise RuntimeError("Prompt audit automation track was not found")
    calls.append(_call(client, "get", {"ref": {"path": "live_set tracks %s mixer_device volume" % track_index}, "properties": ["name", "value"]}, "inspect_mixer_volume_parameter"))
    parameter_id = calls[-1]["result"].get("id") if isinstance(calls[-1]["result"], dict) else None
    if clip_id is None or parameter_id is None:
        raise RuntimeError("Prompt audit automation target clip or parameter was not found")
    calls.append(_call(client, "clip_envelope", {
        "ref": {"id": clip_id},
        "parameter": {"id": parameter_id},
        "create": True,
        "delete_range": {"start_time": 0.0, "end_time": 4.0},
        "insert_steps": [
            {"time": 0.0, "duration": 1.0, "value": 0.85},
            {"time": 1.0, "duration": 1.0, "value": 0.55},
        ],
        "start_time": 0.0,
        "end_time": 4.0,
    }, "write_clip_volume_automation"))
    return _scenario_result("clip_automation_edit", "Edit clip automation in an existing project", calls)


def _scenario_audio_warp_edit(client: AbletonBridgeClient) -> dict[str, Any]:
    wav_path = _write_probe_wav()
    setup_code = _AUDIO_WARP_SETUP_CODE % str(wav_path)
    calls = [
        _call(client, "exec", {"code": setup_code, "timeout": 5}, "seed_existing_audio_clip"),
        _call(client, "set_summary", {"track_query": "Audit Existing Audio", "track_limit": 1, "clip_slot_limit": 0, "device_limit": 0, "arrangement_clip_limit": 8}, "summarize_audio_arrangement_clip"),
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


def _scenario_audio_vocal_import(client: AbletonBridgeClient) -> dict[str, Any]:
    wav_path = _write_probe_wav()
    calls = [
        _call(client, "exec", {"code": _AUDIO_VOCAL_TRACK_CODE, "timeout": 5}, "create_audio_vocal_track"),
    ]
    track_path = calls[0]["result"].get("track_path") if isinstance(calls[0]["result"], dict) else None
    if not track_path:
        raise RuntimeError("Prompt audit audio vocal track was not created")
    calls.append(_call(client, "track_create_audio_clip", {
        "ref": {"path": track_path},
        "file_path": str(wav_path),
        "destination_time": 32.0,
        "name": "MCP Prompt Audit Audio Vocal",
    }, "import_audio_vocal_phrase"))
    return _scenario_result("audio_vocal_import", "I want audio vocals", calls)


def _scenario_generated_m4l_device(client: AbletonBridgeClient) -> dict[str, Any]:
    audio_effect, midi_effect, instrument, values = _generated_m4l_prompt_params()
    calls = [
        _call(client, "agent_m4l_cleanup", {"delete": False, "name_prefix": "AgentM4L_", "limit": 32}, "dry_run_stale_generated_m4l_cleanup"),
        _local_m4l_preflight(audio_effect, "preflight_audio_effect_native_web_reactive_patch"),
        _call(client, "agent_m4l_device", audio_effect, "write_audio_effect_native_web_reactive_patch"),
        _local_m4l_preflight(midi_effect, "preflight_midi_effect_keyboard_matrix_patch"),
        _call(client, "agent_m4l_device", midi_effect, "write_midi_effect_keyboard_matrix_patch"),
        _local_m4l_preflight(instrument, "preflight_instrument_piano_web_patch"),
        _call(client, "agent_m4l_device", instrument, "write_instrument_piano_web_patch"),
        _call(client, "agent_m4l_device", values, "write_list_and_object_ui_values"),
    ]
    return _scenario_result("generated_m4l_creative_devices", "Make custom Max for Live devices with creative UI", calls)


def _generated_m4l_prompt_params() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
    return (
        {
            "role": "audio_effect",
            "instance_id": "Prompt Audit Reactive Field",
            "command": "update",
            "load": False,
            "udp": False,
            "id": "prompt-audit-reactive-field-update",
            "patch": _creative_audio_effect_patch(),
        },
        {
            "role": "midi_effect",
            "instance_id": "Prompt Audit Matrix Sequencer",
            "command": "update",
            "load": False,
            "udp": False,
            "id": "prompt-audit-matrix-sequencer-update",
            "patch": _creative_midi_effect_patch(),
        },
        {
            "role": "instrument",
            "instance_id": "Prompt Audit Piano Synth",
            "command": "update",
            "load": False,
            "udp": False,
            "id": "prompt-audit-piano-synth-update",
            "patch": _creative_instrument_patch(),
        },
        {
            "role": "audio_effect",
            "instance_id": "Prompt Audit Reactive Field",
            "command": "set",
            "load": False,
            "udp": False,
            "id": "prompt-audit-reactive-field-values",
            "values": [
                {"id": "step_pattern", "value": [1, 0, 1, 1, 0, 1, 0, 1], "message": "list"},
                {"id": "gesture_payload", "value": {"x": 0.31, "y": 0.72, "pressure": 0.44}, "message": "symbol"},
            ],
        },
    )


def _creative_audio_effect_patch() -> dict[str, Any]:
    return {
        "device_width": 980,
        "device_height": 260,
        "objects": [
            {"id": "title", "text": "comment Reactive Field FX", "presentation_rect": [12, 10, 160, 18]},
            {"id": "drive_ui", "text": "live.dial @parameter_enable 1 @parameter_shortname Drive", "presentation_rect": [14, 40, 52, 64]},
            {"id": "mix_ui", "text": "live.dial @parameter_enable 1 @parameter_shortname Mix", "presentation_rect": [76, 40, 52, 64]},
            {"id": "xy_field", "text": "pictslider", "presentation_rect": [144, 36, 150, 120]},
            {"id": "step_pattern", "text": "multislider @size 8 @setstyle 1", "presentation_rect": [14, 174, 280, 52], "list_message": "list"},
            {"id": "gesture_payload", "text": "message", "presentation_rect": [308, 174, 120, 22], "object_message": "symbol"},
            {"id": "drive_amount", "text": "flonum", "value": 0.55, "patching_rect": [20, 300, 60, 22]},
            {"id": "mix_amount", "text": "flonum", "value": 0.8, "patching_rect": [100, 300, 60, 22]},
            {"id": "gain_l", "text": "*~ 0.8", "patching_rect": [20, 340, 70, 22]},
            {"id": "gain_r", "text": "*~ 0.8", "patching_rect": [120, 340, 70, 22]},
            {"id": "level_probe", "text": "peakamp~ 40", "patching_rect": [220, 340, 90, 22]},
            {"id": "level_value", "text": "flonum", "presentation_rect": [308, 46, 72, 22]},
        ],
        "webuis": [
            _prompt_audit_webui("Prompt Audit Reactive Field", {
                "id": "reactive_scene",
                "object": "jbrowser~",
                "title": "Reactive Field",
                "presentation_rect": [420, 10, 540, 220],
                "reuse": True,
                "html": _WEBUI_THREE_CANVAS_HTML,
                "css": _WEBUI_CANVAS_CSS,
                "js": _WEBUI_THREE_REACTIVE_FIELD_JS,
                "assets": {
                    "assets/three.module.js": {"content": _THREE_AUDIT_MODULE_JS},
                    "scene/field.json": "{\"kind\":\"three-reactive-field\",\"version\":1}",
                },
            })
        ],
        "ui_bindings": [
            {"source": "drive_ui", "target": "drive_amount", "source_min": 0, "source_max": 127, "target_min": 0.1, "target_max": 1.5},
            {"source": "mix_ui", "target": "mix_amount", "source_min": 0, "source_max": 127, "target_min": 0, "target_max": 1},
            {"source": "level_value", "target": "level_meter", "report": False, "source_settable": False},
        ],
        "connections": [
            {"from": "plugin", "outlet": 0, "to": "gain_l", "inlet": 0},
            {"from": "plugin", "outlet": 1, "to": "gain_r", "inlet": 0},
            {"from": "drive_amount", "outlet": 0, "to": "gain_l", "inlet": 1},
            {"from": "drive_amount", "outlet": 0, "to": "gain_r", "inlet": 1},
            {"from": "gain_l", "outlet": 0, "to": "plugout", "inlet": 0},
            {"from": "gain_r", "outlet": 0, "to": "plugout", "inlet": 1},
            {"from": "gain_l", "outlet": 0, "to": "level_probe", "inlet": 0},
            {"from": "level_probe", "outlet": 0, "to": "level_value", "inlet": 0},
        ],
    }


def _creative_midi_effect_patch() -> dict[str, Any]:
    return {
        "device_width": 760,
        "device_height": 230,
        "objects": [
            {"id": "title", "text": "comment Matrix MIDI Sequencer", "presentation_rect": [12, 10, 180, 18]},
            {"id": "keyboard", "text": "kslider", "presentation_rect": [12, 38, 330, 54]},
            {"id": "gate_grid", "text": "matrixctrl @rows 4 @columns 16", "presentation_rect": [12, 108, 420, 84]},
            {"id": "transpose_ui", "text": "live.numbox @parameter_enable 1 @parameter_shortname Transpose", "presentation_rect": [356, 38, 76, 24]},
            {"id": "midi_parser", "text": "midiparse", "patching_rect": [20, 290, 90, 22]},
            {"id": "note_unpack", "text": "unpack 0 0", "patching_rect": [120, 290, 90, 22]},
            {"id": "input_pitch", "text": "number", "patching_rect": [220, 290, 60, 22]},
            {"id": "input_velocity", "text": "number", "patching_rect": [290, 290, 60, 22]},
        ],
        "webui": {
            **_prompt_audit_webui("Prompt Audit Matrix Sequencer", {
                "id": "piano_roll",
                "object": "jbrowser~",
                "title": "Piano Roll",
                "presentation_rect": [452, 10, 292, 182],
                "reuse": True,
                "html": _WEBUI_CANVAS_HTML,
                "css": _WEBUI_CANVAS_CSS,
                "js": _WEBUI_PIANO_ROLL_JS,
            }),
        },
        "ui_bindings": [
            {"source": "transpose_ui", "target": "transpose_value", "source_min": -24, "source_max": 24, "target_min": -24, "target_max": 24},
            {"source": "input_pitch", "target": "input_pitch", "report": False, "source_settable": False},
        ],
        "connections": [
            {"from": "midiin", "outlet": 0, "to": "midi_parser", "inlet": 0},
            {"from": "midi_parser", "outlet": 0, "to": "note_unpack", "inlet": 0},
            {"from": "note_unpack", "outlet": 0, "to": "input_pitch", "inlet": 0},
            {"from": "note_unpack", "outlet": 1, "to": "input_velocity", "inlet": 0},
            {"from": "midiin", "outlet": 0, "to": "midiout", "inlet": 0},
        ],
    }


def _creative_instrument_patch() -> dict[str, Any]:
    buses = {
        "left": "agent_m4l_Prompt_Audit_Piano_Synth_audio_out_l",
        "right": "agent_m4l_Prompt_Audit_Piano_Synth_audio_out_r",
    }
    return {
        "device_width": 900,
        "device_height": 270,
        "objects": [
            {"id": "title", "text": "comment Piano Glass Synth", "presentation_rect": [12, 10, 160, 18]},
            {"id": "keyboard", "text": "kslider", "presentation_rect": [12, 38, 420, 58]},
            {"id": "partials", "text": "multislider @size 12 @setstyle 1", "presentation_rect": [12, 118, 420, 64]},
            {"id": "tone_ui", "text": "live.dial @parameter_enable 1 @parameter_shortname Tone", "presentation_rect": [448, 38, 54, 64]},
            {"id": "motion_ui", "text": "pictslider", "presentation_rect": [518, 38, 112, 112]},
            {"id": "midi_parser", "text": "midiparse", "patching_rect": [20, 330, 90, 22]},
            {"id": "note_unpack", "text": "unpack 0 0", "patching_rect": [120, 330, 90, 22]},
            {"id": "pitch_mtof", "text": "mtof", "patching_rect": [220, 330, 60, 22]},
            {"id": "osc", "text": "cycle~ 220", "patching_rect": [300, 330, 80, 22]},
            {"id": "velocity_scale", "text": "/ 127.", "patching_rect": [220, 365, 60, 22]},
            {"id": "velocity_signal", "text": "sig~ 0.", "patching_rect": [300, 365, 70, 22]},
            {"id": "amp", "text": "*~", "patching_rect": [400, 330, 60, 22]},
            {"id": "out_l", "text": "send~ %s" % buses["left"], "patching_rect": [480, 320, 150, 22]},
            {"id": "out_r", "text": "send~ %s" % buses["right"], "patching_rect": [480, 350, 150, 22]},
        ],
        "webui": {
            **_prompt_audit_webui("Prompt Audit Piano Synth", {
                "id": "glass_scene",
                "object": "jbrowser~",
                "title": "Glass Scene",
                "presentation_rect": [648, 10, 232, 210],
                "reuse": True,
                "html": _WEBUI_CANVAS_HTML,
                "css": _WEBUI_CANVAS_CSS,
                "js": _WEBUI_GLASS_SCENE_JS,
            }),
        },
        "ui_bindings": [
            {"source": "tone_ui", "target": "tone_value", "source_min": 0, "source_max": 127, "target_min": 0, "target_max": 1},
        ],
        "connections": [
            {"from": "midiin", "outlet": 0, "to": "midi_parser", "inlet": 0},
            {"from": "midi_parser", "outlet": 0, "to": "note_unpack", "inlet": 0},
            {"from": "note_unpack", "outlet": 0, "to": "pitch_mtof", "inlet": 0},
            {"from": "pitch_mtof", "outlet": 0, "to": "osc", "inlet": 0},
            {"from": "note_unpack", "outlet": 1, "to": "velocity_scale", "inlet": 0},
            {"from": "velocity_scale", "outlet": 0, "to": "velocity_signal", "inlet": 0},
            {"from": "osc", "outlet": 0, "to": "amp", "inlet": 0},
            {"from": "velocity_signal", "outlet": 0, "to": "amp", "inlet": 1},
            {"from": "amp", "outlet": 0, "to": "out_l", "inlet": 0},
            {"from": "amp", "outlet": 0, "to": "out_r", "inlet": 0},
        ],
    }


def _prompt_audit_webui(instance_id: str, source: dict[str, Any]) -> dict[str, Any]:
    rendered = agent_m4l.write_webui("%s_%s" % (instance_id, source["id"]), source)
    result = {
        key: source[key]
        for key in ("id", "object", "presentation_rect", "patching_rect", "reuse", "read_message", "readMessage")
        if key in source
    }
    result.update({key: rendered[key] for key in ("html_path", "css_path", "js_path", "url") if key in rendered})
    assets = rendered.get("assets") or []
    if assets:
        result["assets"] = {
            "count": len(assets),
            "bytes": sum(int(item.get("bytes") or 0) for item in assets if isinstance(item, dict)),
            "relative_paths": [str(item["relative_path"]) for item in assets if isinstance(item, dict) and item.get("relative_path")][:8],
        }
    return result


_WEBUI_CANVAS_HTML = """<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Agent M4L Panel</title>
  <link rel="stylesheet" href="style.css">
</head>
<body>
  <canvas id="scene"></canvas>
  <script src="device.js"></script>
</body>
</html>
"""


_WEBUI_THREE_CANVAS_HTML = """<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Agent M4L Three Panel</title>
  <link rel="stylesheet" href="style.css">
</head>
<body>
  <canvas id="scene"></canvas>
  <script type="module" src="device.js"></script>
</body>
</html>
"""


_WEBUI_CANVAS_CSS = """
html, body { margin: 0; width: 100%; height: 100%; overflow: hidden; background: #08090d; color: #edf2ff; }
canvas { display: block; width: 100vw; height: 100vh; touch-action: none; }
"""


_WEBUI_PANEL_PREAMBLE_JS = """
const outlet = (...args) => {
  if (window.agentM4L && window.agentM4L.outlet) window.agentM4L.outlet(...args);
  else if (window.max && window.max.outlet) window.max.outlet(...args);
};
const canvas = document.getElementById("scene");
const ctx = canvas.getContext("2d");
let state = {};
function resize() {
  const ratio = Math.max(1, window.devicePixelRatio || 1);
  const rect = canvas.getBoundingClientRect();
  canvas.width = Math.max(1, Math.floor(rect.width * ratio));
  canvas.height = Math.max(1, Math.floor(rect.height * ratio));
  ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
}
function receiveState(next) {
  state = next || {};
}
window.addEventListener("resize", resize);
window.addEventListener("agentm4lstate", (event) => receiveState(event.detail || {}));
window.agentM4L = window.agentM4L || {};
window.agentM4L.onstate = receiveState;
resize();
"""


_THREE_AUDIT_MODULE_JS = """
export class Color {
  constructor(value) { this.value = value || "#ffffff"; }
}
export class Scene {
  constructor() { this.children = []; this.background = new Color("#05070c"); }
  add(...items) { this.children.push(...items); }
}
export class PerspectiveCamera {
  constructor() { this.position = { x: 0, y: 0, z: 4 }; this.aspect = 1; }
  updateProjectionMatrix() {}
}
export class IcosahedronGeometry {
  constructor(radius = 1) { this.radius = radius; }
}
export class MeshStandardMaterial {
  constructor(options = {}) { this.color = new Color(options.color || "#79ffe1"); this.roughness = options.roughness || 0; this.metalness = options.metalness || 0; }
}
export class Mesh {
  constructor(geometry, material) {
    this.geometry = geometry;
    this.material = material;
    this.rotation = { x: 0, y: 0, z: 0 };
    this.scale = { value: 1, setScalar: (value) => { this.scale.value = value; } };
  }
}
export class DirectionalLight {
  constructor(color = "#ffffff", intensity = 1) { this.color = new Color(color); this.intensity = intensity; this.position = { set: () => {} }; }
}
export class WebGLRenderer {
  constructor(options = {}) {
    this.canvas = options.canvas;
    this.ctx = this.canvas.getContext("2d");
    this.pixelRatio = 1;
  }
  setPixelRatio(value) { this.pixelRatio = Math.max(1, value || 1); }
  setSize(width, height) {
    this.canvas.width = Math.max(1, Math.floor(width * this.pixelRatio));
    this.canvas.height = Math.max(1, Math.floor(height * this.pixelRatio));
    this.ctx.setTransform(this.pixelRatio, 0, 0, this.pixelRatio, 0, 0);
  }
  render(scene) {
    const width = this.canvas.clientWidth;
    const height = this.canvas.clientHeight;
    const ctx = this.ctx;
    ctx.fillStyle = scene.background && scene.background.value || "#05070c";
    ctx.fillRect(0, 0, width, height);
    scene.children.filter((item) => item.geometry).forEach((mesh, index) => {
      const radius = Math.min(width, height) * 0.18 * (mesh.scale.value || 1);
      const sides = 12;
      const cx = width * (0.5 + Math.sin(mesh.rotation.y + index) * 0.06);
      const cy = height * (0.5 + Math.cos(mesh.rotation.x + index) * 0.08);
      ctx.fillStyle = mesh.material && mesh.material.color && mesh.material.color.value || "#79ffe1";
      ctx.beginPath();
      for (let i = 0; i <= sides; i++) {
        const angle = (i / sides) * Math.PI * 2 + mesh.rotation.z;
        const r = radius * (0.76 + 0.24 * Math.sin(angle * 3 + mesh.rotation.x));
        const x = cx + Math.cos(angle) * r;
        const y = cy + Math.sin(angle) * r;
        if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
      }
      ctx.closePath();
      ctx.fill();
    });
  }
}
export const MathUtils = {
  clamp(value, min, max) { return Math.max(min, Math.min(max, value)); }
};
"""


_WEBUI_THREE_REACTIVE_FIELD_JS = """
import * as THREE from "./assets/three.module.js";

const outlet = (...args) => {
  if (window.agentM4L && window.agentM4L.outlet) window.agentM4L.outlet(...args);
  else if (window.max && window.max.outlet) window.max.outlet(...args);
};
const canvas = document.getElementById("scene");
let state = {};
let pointer = { x: 0.5, y: 0.5, pressure: 0 };
function receiveState(next) {
  state = next || {};
}
window.addEventListener("agentm4lstate", (event) => receiveState(event.detail || {}));
window.agentM4L = window.agentM4L || {};
window.agentM4L.onstate = receiveState;

const renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: true });
const scene = new THREE.Scene();
scene.background = new THREE.Color("#07090f");
const camera = new THREE.PerspectiveCamera(42, 1, 0.1, 100);
camera.position.z = 4;
const core = new THREE.Mesh(
  new THREE.IcosahedronGeometry(1, 3),
  new THREE.MeshStandardMaterial({ color: "#79ffe1", roughness: 0.28, metalness: 0.36 })
);
const halo = new THREE.Mesh(
  new THREE.IcosahedronGeometry(1.35, 1),
  new THREE.MeshStandardMaterial({ color: "#8d7cff", roughness: 0.5, metalness: 0.2 })
);
const key = new THREE.DirectionalLight("#ffffff", 1.5);
key.position.set(2, 3, 4);
scene.add(core, halo, key);

function resize() {
  const rect = canvas.getBoundingClientRect();
  camera.aspect = rect.width / Math.max(1, rect.height);
  camera.updateProjectionMatrix();
  renderer.setPixelRatio(Math.max(1, window.devicePixelRatio || 1));
  renderer.setSize(rect.width, rect.height, false);
}
function sendGesture(event) {
  const rect = canvas.getBoundingClientRect();
  pointer = {
    x: THREE.MathUtils.clamp((event.clientX - rect.left) / Math.max(1, rect.width), 0, 1),
    y: THREE.MathUtils.clamp((event.clientY - rect.top) / Math.max(1, rect.height), 0, 1),
    pressure: event.pressure || 0.5
  };
  const pattern = Array.from({ length: 8 }, (_, i) => ((i / 7) < pointer.x || ((i + Math.round(pointer.y * 8)) % 4) === 0) ? 1 : 0);
  outlet("set_many_silent", JSON.stringify({ values: [
    { id: "gesture_payload", value: pointer },
    { id: "step_pattern", value: pattern }
  ] }));
}
function draw(time) {
  const level = Number(state.level_meter || state.level_value || 0);
  const drive = Number(state.drive_amount || 0.4);
  const hue = Math.round(172 + drive * 88 + level * 45);
  core.rotation.x = time * 0.00045 + pointer.y * Math.PI;
  core.rotation.y = time * 0.0006 + pointer.x * Math.PI;
  core.rotation.z = time * 0.00028;
  halo.rotation.x = -time * 0.0003;
  halo.rotation.y = time * 0.00035;
  core.scale.setScalar(1 + level * 1.9 + pointer.pressure * 0.2);
  halo.scale.setScalar(1.1 + level * 1.2);
  core.material.color = new THREE.Color(`hsl(${hue}, 88%, ${58 + level * 22}%)`);
  halo.material.color = new THREE.Color(`hsl(${hue + 74}, 78%, ${48 + level * 20}%)`);
  renderer.render(scene, camera);
  requestAnimationFrame(draw);
}
window.addEventListener("resize", resize);
canvas.addEventListener("pointermove", sendGesture);
canvas.addEventListener("pointerdown", sendGesture);
resize();
outlet("web_ready", "reactive_scene");
outlet("three_ready", "reactive_scene");
requestAnimationFrame(draw);
"""


_WEBUI_REACTIVE_FIELD_JS = _WEBUI_PANEL_PREAMBLE_JS + """
let pointer = { x: 0.5, y: 0.5, pressure: 0 };
function sendGesture(event) {
  const rect = canvas.getBoundingClientRect();
  pointer = {
    x: Math.max(0, Math.min(1, (event.clientX - rect.left) / Math.max(1, rect.width))),
    y: Math.max(0, Math.min(1, (event.clientY - rect.top) / Math.max(1, rect.height))),
    pressure: event.pressure || 0.5
  };
  const pattern = Array.from({ length: 8 }, (_, i) => ((i / 7) < pointer.x || (i % 3) === 0) ? 1 : 0);
  outlet("set_many_silent", JSON.stringify({ values: [
    { id: "gesture_payload", value: pointer },
    { id: "step_pattern", value: pattern }
  ] }));
}
canvas.addEventListener("pointermove", sendGesture);
function draw(time) {
  const w = canvas.clientWidth, h = canvas.clientHeight;
  const level = Number(state.level_meter || state.level_value || 0);
  const drive = Number(state.drive_amount || 0.4);
  ctx.clearRect(0, 0, w, h);
  ctx.fillStyle = "#08090d";
  ctx.fillRect(0, 0, w, h);
  for (let i = 0; i < 36; i++) {
    const a = i / 36 * Math.PI * 2 + time * 0.0004;
    const r = (0.15 + level * 0.7 + i / 80) * Math.min(w, h);
    const x = w * pointer.x + Math.cos(a) * r;
    const y = h * pointer.y + Math.sin(a * 1.7) * r * 0.55;
    ctx.fillStyle = `hsla(${180 + i * 5 + drive * 90}, 82%, ${45 + level * 35}%, 0.72)`;
    ctx.beginPath();
    ctx.arc(x, y, 3 + level * 18, 0, Math.PI * 2);
    ctx.fill();
  }
  requestAnimationFrame(draw);
}
outlet("web_ready", "reactive_scene");
requestAnimationFrame(draw);
"""


_WEBUI_PIANO_ROLL_JS = _WEBUI_PANEL_PREAMBLE_JS + """
function draw() {
  const w = canvas.clientWidth, h = canvas.clientHeight;
  const pitch = Number(state.input_pitch || 60);
  const transpose = Number(state.transpose_value || 0);
  ctx.clearRect(0, 0, w, h);
  ctx.fillStyle = "#10131a";
  ctx.fillRect(0, 0, w, h);
  for (let i = 0; i < 16; i++) {
    const active = (i + Math.round(transpose)) % 5 === 0;
    ctx.fillStyle = active ? "#9cffd8" : "#202735";
    ctx.fillRect(i * w / 16 + 1, 0, w / 16 - 2, h);
  }
  ctx.fillStyle = "#f8f4b8";
  const y = h - ((pitch - 36) / 48) * h;
  ctx.fillRect(0, Math.max(0, Math.min(h - 4, y)), w, 4);
  requestAnimationFrame(draw);
}
canvas.addEventListener("pointerdown", (event) => {
  const rect = canvas.getBoundingClientRect();
  const value = Math.round(((event.clientX - rect.left) / Math.max(1, rect.width)) * 48 - 24);
  outlet("set_silent", "transpose_value", value);
});
outlet("web_ready", "piano_roll");
requestAnimationFrame(draw);
"""


_WEBUI_GLASS_SCENE_JS = _WEBUI_PANEL_PREAMBLE_JS + """
function draw(time) {
  const w = canvas.clientWidth, h = canvas.clientHeight;
  const tone = Number(state.tone_value || 0.5);
  ctx.clearRect(0, 0, w, h);
  const gradient = ctx.createLinearGradient(0, 0, w, h);
  gradient.addColorStop(0, "#071018");
  gradient.addColorStop(1, "#25182f");
  ctx.fillStyle = gradient;
  ctx.fillRect(0, 0, w, h);
  for (let i = 0; i < 10; i++) {
    const x = (i + 0.5) * w / 10;
    const y = h * (0.5 + 0.25 * Math.sin(time * 0.001 + i + tone * 4));
    ctx.strokeStyle = `hsla(${250 + i * 11}, 90%, 75%, 0.72)`;
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.moveTo(x, h);
    ctx.lineTo(x + Math.sin(i) * 12, y);
    ctx.stroke();
  }
  requestAnimationFrame(draw);
}
outlet("web_ready", "glass_scene");
requestAnimationFrame(draw);
"""


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
        (_scenario_clip_automation_edit, False),
        (_scenario_audio_warp_edit, False),
        (_scenario_audio_vocal_import, False),
        (_scenario_generated_m4l_device, False),
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


def run_generated_m4l_local_preflight() -> tuple[int, dict[str, Any]]:
    audio_effect, midi_effect, instrument, _values = _generated_m4l_prompt_params()
    scenarios = [
        (audio_effect, "preflight_audio_effect_native_web_reactive_patch"),
        (midi_effect, "preflight_midi_effect_keyboard_matrix_patch"),
        (instrument, "preflight_instrument_piano_web_patch"),
    ]
    calls = []
    errors = []
    for params, name in scenarios:
        try:
            calls.append(_local_m4l_preflight(params, name))
        except Exception as exc:
            errors.append({"name": name, "error": str(exc)})
    check = _scenario_result(
        "generated_m4l_creative_devices_local_preflight",
        "Preflight custom Max for Live devices with creative UI without touching Live",
        calls,
    )
    if errors:
        check["ok"] = False
        check["errors"] = errors
    output = {
        "ok": not errors,
        "destructive": False,
        "checks": [check],
        "summary": {
            "total": 1,
            "passed": 0 if errors else 1,
            "failed": 1 if errors else 0,
            "hard_failed": 1 if errors else 0,
        },
    }
    return (0 if output["ok"] else 1), output


def main() -> int:
    if not require_debug_cli("ableton-live-mcp prompt-audit"):
        return 2
    parser = argparse.ArgumentParser(description="Run destructive real-prompt Ableton Live MCP workflow audits.")
    parser.add_argument("--yes", action="store_true", help="Required: acknowledge that this modifies the open Live set.")
    parser.add_argument("--no-optional", action="store_true", help="Skip library/plugin-dependent optional scenarios.")
    parser.add_argument("--local-generated-m4l", action="store_true", help="Run non-destructive local generated-M4L creative preflight checks.")
    args = parser.parse_args()
    if args.local_generated_m4l:
        code, output = run_generated_m4l_local_preflight()
        print(json.dumps(output, indent=2, sort_keys=True))
        return code
    if not args.yes:
        print("Refusing to run destructive prompt audit without --yes.", file=sys.stderr)
        return 2
    try:
        code, output = run_prompt_audit(include_optional=not args.no_optional)
    except AbletonBridgeError as exc:
        print(f"Ableton Live MCP prompt audit failed: {exc}", file=sys.stderr)
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
    created.append({"track": track.name, "path": "live_set tracks %s" % (len(song.tracks) - 1), "notes": len(specs), "arrangement_clips": len(track.arrangement_clips)})
result = {"tempo": song.tempo, "created": created, "track_paths": dict((item["track"], item["path"]) for item in created), "track_count": len(song.tracks)}
'''

_CREATE_SAMPLE_TRACK_CODE = r'''
song.create_midi_track(len(song.tracks))
track = song.tracks[-1]
track.name = "Audit Library Sample"
song.view.selected_track = track
result = {"track_path": "live_set tracks %s" % (len(song.tracks) - 1), "track": track.name}
'''

_EXISTING_EDIT_SETUP_CODE = r'''
song.create_midi_track(len(song.tracks))
track = song.tracks[-1]
track.name = "Audit Existing MIDI"
song.view.selected_track = track
result = {"track_path": "live_set tracks %s" % (len(song.tracks) - 1), "track": track.name}
'''

_AUTOMATION_SETUP_CODE = r'''
song.create_midi_track(len(song.tracks))
track = song.tracks[-1]
track.name = "Audit Automation"
song.view.selected_track = track
result = {"track_path": "live_set tracks %s" % (len(song.tracks) - 1), "track": track.name}
'''

_AUDIO_WARP_SETUP_CODE = r'''
song.create_audio_track(len(song.tracks))
track = song.tracks[-1]
track.name = "Audit Existing Audio"
clip = track.create_audio_clip(r"%s", 48.0)
clip.name = "MCP Prompt Audit Warp"
result = {"track": track.name, "clip": clip.name, "warping": getattr(clip, "warping", None)}
'''

_AUDIO_VOCAL_TRACK_CODE = r'''
song.create_audio_track(len(song.tracks))
track = song.tracks[-1]
track.name = "Audit Audio Vocal"
song.view.selected_track = track
result = {"track_path": "live_set tracks %s" % (len(song.tracks) - 1), "track": track.name}
'''


if __name__ == "__main__":
    raise SystemExit(main())
