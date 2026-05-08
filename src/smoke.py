from __future__ import annotations

import argparse
import json
import math
import struct
import sys
import tempfile
import time
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


def run_generated_m4l_regression(
    client: AbletonBridgeClient | None = None,
    *,
    mcp: Any | None = None,
    compact: bool = True,
    settle_seconds: float = 1.0,
) -> tuple[int, dict[str, Any]]:
    client = client or AbletonBridgeClient()
    if mcp is None:
        from server import make_server
        mcp = make_server(client)
    checks: list[dict[str, Any]] = []
    prefix = "MCP Generated M4L "

    def call(name: str, method: str, params: dict[str, Any]) -> Any:
        check = _call(client, name, method, params)
        checks.append(check)
        if not check["ok"]:
            raise RuntimeError("%s failed: %s" % (name, check.get("error")))
        return check["result"]

    def m4l(name: str, args: dict[str, Any]) -> Any:
        try:
            result = _mcp_tool_call(mcp, "live_agent_m4l_device", args)
            checks.append(_ok(name, result))
            return result
        except Exception as exc:
            checks.append(_fail(name, exc))
            raise RuntimeError("%s failed: %s" % (name, exc)) from exc

    def wait_m4l_state(name: str, load_result: dict[str, Any], state_keys: list[str], required_key: str, timeout: float) -> Any:
        deadline = time.time() + timeout
        last_result: dict[str, Any] | None = None
        while time.time() <= deadline:
            last_result = _mcp_tool_call(mcp, "live_agent_m4l_device", _generated_m4l_status_args(load_result, state_keys))
            state = (last_result.get("status") or {}).get("state") or {}
            if required_key in state:
                checks.append(_ok(name, last_result))
                return last_result
            time.sleep(0.5)
        result = last_result or {}
        checks.append(_ok(name, result))
        return result

    try:
        call("ping_current_runtime", "ping", {"timeout": 10})
        setup = call("create_generated_m4l_tracks", "exec", {"code": _generated_m4l_setup_code(prefix), "timeout": 15})
        instrument = m4l("load_generated_instrument_hybrid_ui", _generated_m4l_device_args(
            "instrument",
            "Smoke Hybrid Synth",
            setup["instrument_path"],
            _smoke_instrument_patch("Smoke Hybrid Synth"),
            ["level_meter", "pitch_value", "web_title", "web_smoke_scope_loaded", "web_read_pending"],
        ))
        notes = [
            {"pitch": pitch, "start_time": index * 0.25, "duration": 0.18, "velocity": 104}
            for index, pitch in enumerate([48, 55, 60, 63, 67, 70, 72, 75, 72, 70, 67, 63, 60, 55, 58, 63])
        ]
        clip = call("add_and_launch_generated_instrument_clip", "clip_add_notes", {
            "ref": {"path": setup["instrument_path"] + " clip_slots 0"},
            "create_clip_length": 4.0,
            "replace_existing_clip": True,
            "clip_name": prefix + "Hybrid Pattern",
            "loop_start": 0.0,
            "loop_end": 4.0,
            "notes": notes,
            "fire": True,
            "timeout": 15,
        })
        if clip.get("note_api") != "extended":
            raise RuntimeError("generated instrument clip did not use extended note API")
        call("start_transport_for_generated_m4l", "transport", {"action": "play", "timeout": 10})
        if settle_seconds > 0:
            time.sleep(settle_seconds)
        meter = call("verify_generated_instrument_meter", "get", {
            "ref": {"path": setup["instrument_path"]},
            "properties": ["output_meter_level", "playing_slot_index"],
            "timeout": 10,
        })
        if float((meter.get("properties") or {}).get("output_meter_level") or 0.0) <= 0.01:
            raise RuntimeError("generated instrument track meter stayed silent")
        inst_status = m4l("poll_generated_instrument_compact_status", _generated_m4l_status_args(instrument, ["level_meter", "pitch_value", "web_title"]))
        if float(((inst_status.get("status") or {}).get("state") or {}).get("level_meter") or 0.0) <= 0:
            raise RuntimeError("generated instrument level telemetry stayed silent")
        webui = ((instrument.get("webui") or {}).get("webuis") or [{}])[0]
        if webui.get("id") and webui.get("html_path"):
            reload_result = m4l("web_reload_generated_instrument_ui", _generated_m4l_web_reload_args(instrument, webui))
            if (reload_result.get("status") or {}).get("event") != "webui_reload":
                raise RuntimeError("generated instrument web_reload did not ack")
        audio_effect = m4l("load_generated_audio_effect_native_web_ui", _generated_m4l_device_args(
            "audio_effect",
            "Smoke Field FX",
            setup["instrument_path"],
            _smoke_audio_effect_patch("Smoke Field FX"),
            ["level_meter", "web_title", "web_fx_panel_loaded", "web_read_pending"],
        ))
        audio_status = m4l("poll_generated_audio_effect_status", _generated_m4l_status_args(audio_effect, ["level_meter", "web_title"]))
        if float(((audio_status.get("status") or {}).get("state") or {}).get("level_meter") or 0.0) <= 0:
            raise RuntimeError("generated audio effect level telemetry stayed silent")
        midi_effect = m4l("load_generated_midi_effect_native_ui", _generated_m4l_device_args(
            "midi_effect",
            "Smoke MIDI Matrix",
            setup["midi_path"],
            _smoke_midi_effect_patch(),
            ["input_pitch"],
        ))
        call("add_and_launch_generated_midi_effect_clip", "clip_add_notes", {
            "ref": {"path": setup["midi_path"] + " clip_slots 0"},
            "create_clip_length": 4.0,
            "replace_existing_clip": True,
            "clip_name": prefix + "MIDI Matrix Pattern",
            "loop_start": 0.0,
            "loop_end": 4.0,
            "notes": notes[:8],
            "fire": True,
            "timeout": 15,
        })
        if settle_seconds > 0:
            time.sleep(min(0.75, settle_seconds))
        midi_status = wait_m4l_state("poll_generated_midi_effect_status", midi_effect, ["input_pitch"], "input_pitch", 6.0)
        if "input_pitch" not in (((midi_status.get("status") or {}).get("state") or {})):
            raise RuntimeError("generated MIDI effect did not report input_pitch telemetry")
    except Exception as exc:
        checks.append(_fail("generated_m4l_assertion", exc))

    hard_failures = [check for check in checks if not check["ok"]]
    output = {
        "ok": not hard_failures,
        "destructive": True,
        "checks": [_compact_generated_m4l_check(check) for check in checks] if compact else checks,
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


def _compact_generated_m4l_check(check: dict[str, Any]) -> dict[str, Any]:
    result = check.get("result")
    compact = {key: value for key, value in check.items() if key != "result"}
    if not isinstance(result, dict):
        return compact
    name = str(check.get("name") or "")
    if name == "ping_current_runtime":
        remote = result.get("remote_script") or {}
        compact["version"] = result.get("version")
        compact["runtime_version"] = remote.get("runtime_version")
    elif name.startswith("load_generated_") or name.startswith("web_reload_") or name.startswith("poll_generated_"):
        status = result.get("status") or {}
        state = status.get("state") or {}
        compact.update({
            "command": result.get("command"),
            "loaded": result.get("loaded"),
            "direct": result.get("direct"),
            "event": status.get("event"),
            "host_runtime_version": status.get("host_runtime_version"),
            "device_width": status.get("device_width"),
            "device_height": status.get("device_height"),
            "state": {key: state.get(key) for key in sorted(state) if key in {"level_meter", "pitch_value", "input_pitch", "web_title", "web_read_pending"}},
        })
    elif name.startswith("add_and_launch_"):
        compact["added"] = result.get("added")
        compact["note_api"] = result.get("note_api")
        compact["launched"] = result.get("launched")
    elif name.startswith("verify_generated_"):
        compact["properties"] = result.get("properties")
    return compact


def _mcp_tool_call(mcp: Any, name: str, args: dict[str, Any]) -> dict[str, Any]:
    response = mcp.handle({
        "jsonrpc": "2.0",
        "id": int(time.time() * 1000000) % 1000000000,
        "method": "tools/call",
        "params": {"name": name, "arguments": args},
    })
    if "error" in response:
        raise RuntimeError(json.dumps(response["error"], separators=(",", ":")))
    return response["result"]["structuredContent"]


def _generated_m4l_device_args(role: str, instance: str, track_path: str, patch: dict[str, Any], state_keys: list[str]) -> dict[str, Any]:
    return {
        "role": role,
        "instance_id": instance,
        "title": instance,
        "target_track": {"path": track_path},
        "patch": patch,
        "preflight": True,
        "wait_status": True,
        "compact_status": True,
        "status_timeout": 14,
        "status_state_keys": state_keys,
        "compact_result": True,
        "load_retry_timeout": 18,
        "load_retry_interval": 1,
    }


def _generated_m4l_status_args(load_result: dict[str, Any], state_keys: list[str]) -> dict[str, Any]:
    return {
        "role": load_result.get("role") or "instrument",
        "instance_id": load_result.get("instance_id") or "device",
        "build": False,
        "load": False,
        "command": "status",
        "command_file": load_result.get("command_file"),
        "status_file": load_result.get("status_file"),
        "wait_status": True,
        "status_timeout": 4,
        "compact_status": True,
        "status_state_keys": state_keys,
        "status_state_keys_only": True,
        "result_detail": "summary",
    }


def _generated_m4l_web_reload_args(load_result: dict[str, Any], webui: dict[str, Any]) -> dict[str, Any]:
    return {
        "role": load_result.get("role") or "instrument",
        "instance_id": load_result.get("instance_id") or "device",
        "build": False,
        "load": False,
        "command": "web_reload",
        "webuis": [{
            "id": webui.get("id"),
            "html_path": webui.get("html_path"),
            "url": webui.get("url"),
        }],
        "command_file": load_result.get("command_file"),
        "status_file": load_result.get("status_file"),
        "wait_status": True,
        "status_timeout": 8,
        "compact_status": True,
        "status_state_keys": ["level_meter", "web_title", "web_read_pending"],
        "result_detail": "summary",
    }


def _generated_m4l_setup_code(prefix: str) -> str:
    return f'''
prefix = {json.dumps(prefix)}
for index in reversed(range(len(song.tracks))):
    try:
        if str(song.tracks[index].name).startswith(prefix):
            song.delete_track(index)
    except Exception:
        pass
instrument_index = len(song.tracks)
song.create_midi_track(instrument_index)
instrument_track = song.tracks[instrument_index]
instrument_track.name = prefix + "Hybrid Synth"
try:
    instrument_track.arm = True
except Exception:
    pass
midi_index = len(song.tracks)
song.create_midi_track(midi_index)
midi_track = song.tracks[midi_index]
midi_track.name = prefix + "MIDI Matrix"
result = {{
    "instrument_path": "live_set tracks %s" % instrument_index,
    "midi_path": "live_set tracks %s" % midi_index,
}}
'''.strip()


def _smoke_webui(panel_id: str, title: str) -> dict[str, Any]:
    return {
        "id": panel_id,
        "object": "jbrowser~",
        "title": title,
        "presentation_rect": [500, 10, 240, 140],
        "reuse": True,
        "html": "<!doctype html><html><head><meta charset='utf-8'><link rel='stylesheet' href='style.css'><title>%s</title></head><body><canvas id='scene'></canvas><script src='device.js'></script></body></html>" % title,
        "css": "html,body{margin:0;width:100%;height:100%;overflow:hidden;background:#08090d}canvas{display:block;width:100vw;height:100vh}",
        "js": _smoke_webui_js(title),
    }


def _smoke_webui_js(title: str) -> str:
    return """
const canvas=document.getElementById('scene'),ctx=canvas.getContext('2d');let state={},phase=0;
function outlet(...a){if(window.agentM4L&&window.agentM4L.outlet)window.agentM4L.outlet(...a);else if(window.max&&window.max.outlet)window.max.outlet(...a)}
function resize(){const r=canvas.getBoundingClientRect(),d=Math.max(1,window.devicePixelRatio||1);canvas.width=Math.max(1,Math.floor(r.width*d));canvas.height=Math.max(1,Math.floor(r.height*d));ctx.setTransform(d,0,0,d,0,0)}
function draw(){const w=canvas.clientWidth||1,h=canvas.clientHeight||1,l=Math.min(1,Math.max(0,Number(state.level_meter||0)*8));phase+=0.04+l*0.08;ctx.fillStyle=`hsl(${(phase*80)%360},70%,${8+l*20}%)`;ctx.fillRect(0,0,w,h);for(let i=0;i<12;i++){ctx.strokeStyle=`hsla(${(phase*90+i*22)%360},95%,65%,${.25+l*.6})`;ctx.beginPath();for(let x=0;x<30;x++){const px=x*w/29,py=h*(i+1)/14+Math.sin(x*.7+phase+i)*(.08+l*.18)*h;if(x)ctx.lineTo(px,py);else ctx.moveTo(px,py)}ctx.stroke()}ctx.fillStyle='rgba(240,248,255,.92)';ctx.font='12px system-ui,sans-serif';ctx.fillText('__TITLE__ '+(state.pitch_value||state.input_pitch||'')+' '+l.toFixed(2),12,h-14)}
function receive(s){state=s||{};draw()}window.addEventListener('resize',resize);window.addEventListener('agentm4lstate',e=>receive(e.detail||{}));window.agentM4L=window.agentM4L||{};window.agentM4L.onstate=receive;resize();draw();setInterval(()=>{outlet('agent_web_tick',Date.now());if((state.level_meter||0)>0.001)draw()},250);outlet('web_ready','smoke');outlet('web_title',document.title);
""".replace("__TITLE__", title)


def _smoke_instrument_patch(instance: str) -> dict[str, Any]:
    from agent_m4l import audio_bus_names
    bus = audio_bus_names(instance)
    return {
        "device_width": 760,
        "device_height": 170,
        "objects": [
            {"id": "title", "text": "comment Smoke Hybrid Synth", "presentation_rect": [12, 10, 160, 18]},
            {"id": "keyboard", "text": "kslider", "presentation_rect": [12, 38, 300, 50]},
            {"id": "level_ui", "text": "live.dial @parameter_enable 1 @parameter_shortname Level", "presentation_rect": [328, 32, 52, 64]},
            {"id": "level_value", "text": "flonum", "presentation_rect": [398, 54, 70, 22]},
            {"id": "pitch_display", "text": "number", "presentation_rect": [398, 92, 56, 22]},
            {"id": "midi_parser", "text": "midiparse", "patching_rect": [20, 260, 90, 22]},
            {"id": "note_unpack", "text": "unpack 0 0", "patching_rect": [120, 260, 90, 22]},
            {"id": "pitch_value", "text": "number", "patching_rect": [220, 260, 60, 22]},
            {"id": "pitch_mtof", "text": "mtof", "patching_rect": [220, 294, 60, 22]},
            {"id": "osc", "text": "cycle~ 220", "patching_rect": [300, 294, 82, 22]},
            {"id": "velocity_scale", "text": "/ 127.", "patching_rect": [220, 328, 60, 22]},
            {"id": "velocity_signal", "text": "sig~ 0.", "patching_rect": [300, 328, 70, 22]},
            {"id": "amp", "text": "*~", "patching_rect": [398, 294, 55, 22]},
            {"id": "level_amount", "text": "flonum", "value": 0.32, "patching_rect": [398, 260, 70, 22]},
            {"id": "level_mul", "text": "*~ 0.32", "patching_rect": [470, 294, 75, 22]},
            {"id": "meter_probe", "text": "peakamp~ 40", "patching_rect": [555, 294, 85, 22]},
            {"id": "out_l", "text": "send~ %s" % bus["output_left"], "patching_rect": [650, 282, 170, 22]},
            {"id": "out_r", "text": "send~ %s" % bus["output_right"], "patching_rect": [650, 316, 170, 22]},
        ],
        "webuis": [_smoke_webui("smoke_scope", "Smoke Hybrid")],
        "ui_bindings": [
            {"source": "level_ui", "target": "level_amount", "source_min": 0, "source_max": 127, "target_min": 0, "target_max": 0.8},
            {"source": "level_value", "target": "level_meter", "report": False, "source_settable": False},
            {"source": "pitch_display", "target": "pitch_value", "report": False, "source_settable": False},
        ],
        "connections": [
            {"from": "midiin", "outlet": 0, "to": "midi_parser", "inlet": 0},
            {"from": "midi_parser", "outlet": 0, "to": "note_unpack", "inlet": 0},
            {"from": "note_unpack", "outlet": 0, "to": "pitch_value", "inlet": 0},
            {"from": "pitch_value", "outlet": 0, "to": "pitch_display", "inlet": 0},
            {"from": "pitch_value", "outlet": 0, "to": "pitch_mtof", "inlet": 0},
            {"from": "pitch_mtof", "outlet": 0, "to": "osc", "inlet": 0},
            {"from": "note_unpack", "outlet": 1, "to": "velocity_scale", "inlet": 0},
            {"from": "velocity_scale", "outlet": 0, "to": "velocity_signal", "inlet": 0},
            {"from": "osc", "outlet": 0, "to": "amp", "inlet": 0},
            {"from": "velocity_signal", "outlet": 0, "to": "amp", "inlet": 1},
            {"from": "amp", "outlet": 0, "to": "level_mul", "inlet": 0},
            {"from": "level_amount", "outlet": 0, "to": "level_mul", "inlet": 1},
            {"from": "level_mul", "outlet": 0, "to": "out_l", "inlet": 0},
            {"from": "level_mul", "outlet": 0, "to": "out_r", "inlet": 0},
            {"from": "level_mul", "outlet": 0, "to": "meter_probe", "inlet": 0},
            {"from": "meter_probe", "outlet": 0, "to": "level_value", "inlet": 0},
        ],
    }


def _smoke_audio_effect_patch(instance: str) -> dict[str, Any]:
    return {
        "device_width": 760,
        "device_height": 170,
        "objects": [
            {"id": "title", "text": "comment Smoke Field FX", "presentation_rect": [12, 10, 140, 18]},
            {"id": "drive_ui", "text": "live.dial @parameter_enable 1 @parameter_shortname Drive", "presentation_rect": [18, 38, 52, 64]},
            {"id": "level_value", "text": "flonum", "presentation_rect": [92, 58, 70, 22]},
            {"id": "shape", "text": "multislider @size 12 @setstyle 1", "presentation_rect": [178, 36, 280, 62]},
            {"id": "drive_amount", "text": "flonum", "value": 0.85, "patching_rect": [20, 230, 70, 22]},
            {"id": "gain_l", "text": "*~ 0.85", "patching_rect": [20, 265, 70, 22]},
            {"id": "gain_r", "text": "*~ 0.85", "patching_rect": [120, 265, 70, 22]},
            {"id": "meter_probe", "text": "peakamp~ 40", "patching_rect": [220, 265, 85, 22]},
        ],
        "webuis": [_smoke_webui("fx_panel", "Smoke FX")],
        "ui_bindings": [
            {"source": "drive_ui", "target": "drive_amount", "source_min": 0, "source_max": 127, "target_min": 0.1, "target_max": 1.4},
            {"source": "level_value", "target": "level_meter", "report": False, "source_settable": False},
        ],
        "connections": [
            {"from": "plugin", "outlet": 0, "to": "gain_l", "inlet": 0},
            {"from": "plugin", "outlet": 1, "to": "gain_r", "inlet": 0},
            {"from": "drive_amount", "outlet": 0, "to": "gain_l", "inlet": 1},
            {"from": "drive_amount", "outlet": 0, "to": "gain_r", "inlet": 1},
            {"from": "gain_l", "outlet": 0, "to": "plugout", "inlet": 0},
            {"from": "gain_r", "outlet": 0, "to": "plugout", "inlet": 1},
            {"from": "gain_l", "outlet": 0, "to": "meter_probe", "inlet": 0},
            {"from": "meter_probe", "outlet": 0, "to": "level_value", "inlet": 0},
        ],
    }


def _smoke_midi_effect_patch() -> dict[str, Any]:
    return {
        "device_width": 560,
        "device_height": 160,
        "objects": [
            {"id": "title", "text": "comment Smoke MIDI Matrix", "presentation_rect": [12, 10, 150, 18]},
            {"id": "keyboard", "text": "kslider", "presentation_rect": [12, 38, 300, 50]},
            {"id": "gate_grid", "text": "matrixctrl @rows 3 @columns 8", "presentation_rect": [328, 36, 190, 58]},
            {"id": "midi_parser", "text": "midiparse", "patching_rect": [20, 220, 90, 22]},
            {"id": "note_unpack", "text": "unpack 0 0", "patching_rect": [120, 220, 90, 22]},
            {"id": "input_pitch", "text": "number", "patching_rect": [220, 220, 60, 22]},
        ],
        "ui_bindings": [
            {"source": "input_pitch", "target": "input_pitch", "report": False, "source_settable": False},
        ],
        "connections": [
            {"from": "midiin", "outlet": 0, "to": "midi_parser", "inlet": 0},
            {"from": "midi_parser", "outlet": 0, "to": "note_unpack", "inlet": 0},
            {"from": "note_unpack", "outlet": 0, "to": "input_pitch", "inlet": 0},
            {"from": "midiin", "outlet": 0, "to": "midiout", "inlet": 0},
        ],
    }


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
    parser.add_argument("--generated-m4l", action="store_true", help="Run destructive generated M4L instrument/audio-effect/MIDI-effect checks.")
    parser.add_argument("--detail", action="store_true", help="Include full regression result payloads instead of compact evidence.")
    parser.add_argument("--yes", action="store_true", help="Required with destructive regression checks because they modify the open Live set.")
    args = parser.parse_args()
    destructive = args.core_regression or args.generated_m4l
    if args.core_regression and args.generated_m4l:
        print("Choose only one destructive smoke mode at a time.", file=sys.stderr)
        return 2
    if destructive and not args.yes:
        print("Refusing to run destructive smoke regression without --yes.", file=sys.stderr)
        return 2
    try:
        if args.core_regression:
            code, output = run_core_regression(compact=not args.detail)
        elif args.generated_m4l:
            code, output = run_generated_m4l_regression(compact=not args.detail)
        else:
            code, output = run_smoke()
    except AbletonBridgeError as exc:
        print(f"Ableton Live MCP smoke failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(output, indent=2, sort_keys=True))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
