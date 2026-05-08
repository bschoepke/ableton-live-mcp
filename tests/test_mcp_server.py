from __future__ import annotations

import json
import os
import socket
import sqlite3
import time
from pathlib import Path

import pytest

import server as server_module
import validate
from benchmark import run_benchmark
from bridge import AbletonBridgeClient, AbletonBridgeError, BridgeConfig, effective_main_thread_timeout
from install_remote_script import install_remote_script, main as install_remote_script_main, remote_script_root, remote_script_status
from prompt_audit import run_generated_m4l_local_preflight, run_prompt_audit
from server import agent_m4l_status_timeout, agent_m4l_status_timeout_reason, expected_agent_m4l_status_event, make_server, should_build_agent_m4l, summarize_agent_m4l_status, wait_agent_m4l_status
from validate import main as validate_main
from similar_sounds import encode_feature
from smoke import main as smoke_main, run_core_regression, run_generated_m4l_regression, run_smoke


class FakeBridge:
    def __init__(self):
        self.calls = []

    def request(self, method, params):
        self.calls.append((method, params))
        return {"method": method, "params": params}


def test_lists_general_purpose_tools():
    server = make_server(FakeBridge())
    response = server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    names = {tool["name"] for tool in response["result"]["tools"]}
    assert {
        "live_ping",
        "live_bridge_status",
        "live_set_summary",
        "live_get",
        "live_set",
        "live_call",
        "live_children",
        "live_device_parameters",
        "live_parameter_set",
        "live_clip_notes",
        "live_clip_update_notes",
        "live_clip_add_notes",
        "live_clip_duplicate_to_arrangement",
        "live_clip_envelope",
        "live_clip_velocity_envelope",
        "live_clip_warp_markers",
        "live_track_create_audio_clip",
        "live_track_insert_device",
        "live_agent_audio_tap",
        "live_agent_audio_tap_setup",
        "live_visual_capture",
        "live_agent_m4l_device",
        "live_agent_m4l_cleanup",
        "live_transport",
        "live_eval",
        "live_exec",
        "live_batch",
        "live_browser_roots",
        "live_browser_capabilities",
        "live_browser_search",
        "live_browser_load",
        "live_browser_preview",
        "find_similar_sounds",
        "live_observe",
        "live_events",
    } <= names


def test_initialize_reports_current_server_version():
    server = make_server(FakeBridge())
    response = server.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
    assert response["result"]["serverInfo"] == {"name": "ableton-live-mcp", "version": "0.1.1"}


def test_initialize_includes_general_model_instructions():
    server = make_server(FakeBridge())
    response = server.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
    instructions = response["result"]["instructions"]
    assert "review AGENTS.md for tips as needed" in instructions
    assert "devices/plugins unless asked" in instructions
    assert "roots:['plugins']" in instructions
    assert "find_similar_sounds requires Live 12+" in instructions
    assert "start with path" in instructions
    assert "Idle sockets auto-retry" in instructions
    assert "Validate runtime_current" in instructions
    assert "live_mutations_safe" in instructions
    assert "no-arg tool schema=stale MCP" in instructions
    assert "No parallel Live API" in instructions
    assert "sent-call timeouts fail closed" in instructions
    assert "client/RS cooldown" in instructions
    assert "live_bridge_status" in instructions
    assert "Save/recover modals" in instructions
    assert "no retry mutations" in instructions
    assert "compact_result" in instructions
    assert "jweb/jbrowser aliases" in instructions
    assert "web assets/source_path" in instructions
    assert "agent-settable UI" in instructions
    assert "status_state_keys/_only diag" in instructions
    assert "web_reload UI-only" in instructions
    assert "direct status polls" in instructions
    assert "throttled fallback wakes" in instructions
    assert "load:false/set/status skip build" in instructions
    assert "host_not_woken=no ack" in instructions
    assert "host_runtime_version" in instructions
    assert "telemetry report:false" in instructions
    assert "ack guard/state throttles" in instructions
    assert "No default piano/knob UI/templates" in instructions
    assert "ui_bindings/no loops" in instructions
    assert "Agent must visually verify M4L device UI" in instructions
    assert "inspect pixels" in instructions
    assert "status/meter not enough" in instructions
    assert "Ableton-window-only" in instructions
    assert "no arbitrary apps/windows" in instructions
    assert "select target then device-detail crop" in instructions
    assert "device-detail crop" in instructions
    assert "blank_capture invalid" in instructions
    assert "locked/asleep display blocks capture/e2e" in instructions
    assert "audio-reactive web: clock/prove signal+visual delta" in instructions
    assert "full Live object model remains available" in instructions
    assert len(instructions) < 1600


def test_validate_reports_local_mcp_tool_schemas():
    status = validate.mcp_tool_schema_status()
    assert status["ok"] is True
    assert status["server_version"] == "0.1.1"
    checks = {item["tool"]: item for item in status["checks"]}
    assert checks["live_agent_audio_tap"]["ok"] is True
    assert checks["live_transport"]["ok"] is True
    assert checks["live_ping"]["ok"] is True


def test_validate_fails_on_local_mcp_tool_schema_gap(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(validate, "mcp_tool_schema_status", lambda: {
        "ok": False,
        "next_action": "Restart the MCP server/client so it advertises the current local tool schemas.",
    })

    assert validate_main([
        "--skip-live",
        "--target-dir", str(tmp_path),
        "--allow-stale-remote-script",
        "--allow-stale-m4l-host",
        "--allow-missing-visual-capture",
    ]) == 1
    output = capsys.readouterr()
    assert '"mcp_tools"' in output.out
    assert "local MCP tool schemas are stale or incomplete" in output.err


def test_tool_call_forwards_arguments_to_bridge():
    bridge = FakeBridge()
    server = make_server(bridge)
    response = server.handle({
        "jsonrpc": "2.0",
        "id": 2,
        "method": "tools/call",
        "params": {
            "name": "live_call",
            "arguments": {"ref": {"path": "live_set"}, "method": "create_midi_track", "args": [0]},
        },
    })
    assert bridge.calls == [("call", {"ref": {"path": "live_set"}, "method": "create_midi_track", "args": [0]})]
    content = response["result"]["content"][0]["text"]
    assert json.loads(content)["method"] == "call"
    assert response["result"]["structuredContent"]["method"] == "call"
    assert "\n" not in content


def test_ping_tool_accepts_timeout_for_stressed_live_sets():
    bridge = FakeBridge()
    server = make_server(bridge)
    args = {"timeout": 45}
    response = server.handle({
        "jsonrpc": "2.0",
        "id": 201,
        "method": "tools/call",
        "params": {"name": "live_ping", "arguments": args},
    })

    assert bridge.calls == [("ping", args)]
    assert response["result"]["structuredContent"]["method"] == "ping"


def test_bridge_status_tool_forwards_without_live_api_work():
    bridge = FakeBridge()
    server = make_server(bridge)
    args = {"timeout": 2}
    response = server.handle({
        "jsonrpc": "2.0",
        "id": 202,
        "method": "tools/call",
        "params": {"name": "live_bridge_status", "arguments": args},
    })

    assert bridge.calls == [("bridge_status", args)]
    assert response["result"]["structuredContent"]["method"] == "bridge_status"


def test_tool_call_validates_arguments_before_bridge():
    bridge = FakeBridge()
    server = make_server(bridge)
    response = server.handle({
        "jsonrpc": "2.0",
        "id": 20,
        "method": "tools/call",
        "params": {
            "name": "live_call",
            "arguments": {"ref": {"path": "live_set"}, "method": "create_midi_track", "unexpected": True},
        },
    })
    assert "unknown fields: unexpected" in response["error"]["message"]
    assert bridge.calls == []

    response = server.handle({
        "jsonrpc": "2.0",
        "id": 21,
        "method": "tools/call",
        "params": {
            "name": "live_browser_search",
            "arguments": {"query": "drum", "limit": 0},
        },
    })
    assert "arguments.limit must be >= 1" in response["error"]["message"]
    assert bridge.calls == []


def test_set_summary_tool_forwards_limits_to_bridge():
    bridge = FakeBridge()
    server = make_server(bridge)
    args = {"track_limit": 8, "clip_slot_limit": 4, "device_limit": 4, "arrangement_clip_limit": 2, "track_query": "bass"}
    response = server.handle({
        "jsonrpc": "2.0",
        "id": 21,
        "method": "tools/call",
        "params": {"name": "live_set_summary", "arguments": args},
    })
    assert bridge.calls == [("set_summary", args)]
    assert response["result"]["structuredContent"]["method"] == "set_summary"


def test_mutating_tools_accept_expected_set_signature_guard():
    bridge = FakeBridge()
    server = make_server(bridge)
    args = {
        "code": "song.tempo = 90",
        "expected_set_signature": "abc123",
        "timeout": 30,
    }
    response = server.handle({
        "jsonrpc": "2.0",
        "id": 22,
        "method": "tools/call",
        "params": {"name": "live_exec", "arguments": args},
    })

    assert bridge.calls == [("exec", args)]
    assert response["result"]["structuredContent"]["method"] == "exec"


def test_batch_tool_forwards_operations_to_bridge():
    bridge = FakeBridge()
    server = make_server(bridge)
    args = {
        "operations": [
            {"method": "get", "params": {"ref": {"path": "live_set"}, "properties": ["tempo"]}},
            {"method": "children", "params": {"ref": {"path": "live_set"}, "child": "tracks", "limit": 2}},
        ],
    }
    response = server.handle({
        "jsonrpc": "2.0",
        "id": 3,
        "method": "tools/call",
        "params": {"name": "live_batch", "arguments": args},
    })
    assert bridge.calls == [("batch", args)]
    assert response["result"]["structuredContent"]["method"] == "batch"


def test_device_parameters_tool_forwards_to_bridge():
    bridge = FakeBridge()
    server = make_server(bridge)
    args = {"ref": {"path": "live_set tracks 0 devices 0"}, "query": "threshold", "limit": 5}
    response = server.handle({
        "jsonrpc": "2.0",
        "id": 31,
        "method": "tools/call",
        "params": {"name": "live_device_parameters", "arguments": args},
    })
    assert bridge.calls == [("device_parameters", args)]
    assert response["result"]["structuredContent"]["method"] == "device_parameters"


def test_agent_audio_tap_tool_forwards_to_bridge():
    bridge = FakeBridge()
    server = make_server(bridge)
    args = {"command": "start", "path": "/tmp/agent-tap.wav"}
    response = server.handle({
        "jsonrpc": "2.0",
        "id": 32,
        "method": "tools/call",
        "params": {"name": "live_agent_audio_tap", "arguments": args},
    })
    assert bridge.calls == [("agent_audio_tap", args)]
    assert response["result"]["structuredContent"]["method"] == "agent_audio_tap"


def test_agent_audio_tap_tool_requires_command_before_bridge():
    bridge = FakeBridge()
    server = make_server(bridge)
    response = server.handle({
        "jsonrpc": "2.0",
        "id": 321,
        "method": "tools/call",
        "params": {"name": "live_agent_audio_tap", "arguments": {"path": "/tmp/agent-tap.wav"}},
    })

    assert response["error"]["message"] == "arguments.command is required"
    assert bridge.calls == []


def test_agent_audio_tap_tool_accepts_stop_command_without_path():
    bridge = FakeBridge()
    server = make_server(bridge)
    args = {"command": "stop"}
    response = server.handle({
        "jsonrpc": "2.0",
        "id": 322,
        "method": "tools/call",
        "params": {"name": "live_agent_audio_tap", "arguments": args},
    })

    assert bridge.calls == [("agent_audio_tap", args)]
    assert response["result"]["structuredContent"]["method"] == "agent_audio_tap"


def test_agent_audio_tap_tool_rejects_unknown_command_before_bridge():
    bridge = FakeBridge()
    server = make_server(bridge)
    response = server.handle({
        "jsonrpc": "2.0",
        "id": 323,
        "method": "tools/call",
        "params": {"name": "live_agent_audio_tap", "arguments": {"command": "pause"}},
    })

    assert response["error"]["message"] == "arguments.command must be one of: open, start, stop, status"
    assert bridge.calls == []


def test_agent_audio_tap_setup_and_transport_tools_forward_to_bridge():
    bridge = FakeBridge()
    server = make_server(bridge)
    setup_args = {
        "placement": "master",
        "solo_track": {"path": "live_set tracks 0"},
        "remove_existing": True,
        "reset_time": 0,
    }
    response = server.handle({
        "jsonrpc": "2.0",
        "id": 33,
        "method": "tools/call",
        "params": {"name": "live_agent_audio_tap_setup", "arguments": setup_args},
    })
    assert response["result"]["structuredContent"]["method"] == "agent_audio_tap_setup"

    transport_args = {"action": "play", "time": 0}
    response = server.handle({
        "jsonrpc": "2.0",
        "id": 34,
        "method": "tools/call",
        "params": {"name": "live_transport", "arguments": transport_args},
    })
    assert bridge.calls == [("agent_audio_tap_setup", setup_args), ("transport", transport_args)]
    assert response["result"]["structuredContent"]["method"] == "transport"


def test_transport_tool_rejects_unknown_action_before_bridge():
    bridge = FakeBridge()
    server = make_server(bridge)
    response = server.handle({
        "jsonrpc": "2.0",
        "id": 331,
        "method": "tools/call",
        "params": {"name": "live_transport", "arguments": {"action": "restart"}},
    })

    assert response["error"]["message"] == "arguments.action must be one of: play, continue, stop, status"
    assert bridge.calls == []


def test_parameter_set_tool_forwards_to_bridge():
    bridge = FakeBridge()
    server = make_server(bridge)
    args = {"ref": {"id": 99}, "value": 0.75, "coerce": True}
    response = server.handle({
        "jsonrpc": "2.0",
        "id": 36,
        "method": "tools/call",
        "params": {"name": "live_parameter_set", "arguments": args},
    })
    assert bridge.calls == [("parameter_set", args)]
    assert response["result"]["structuredContent"]["method"] == "parameter_set"


def test_clip_note_tools_forward_to_bridge():
    bridge = FakeBridge()
    server = make_server(bridge)
    read_args = {"ref": {"path": "live_set tracks 0 clip_slots 0 clip"}, "limit": 16}
    response = server.handle({
        "jsonrpc": "2.0",
        "id": 32,
        "method": "tools/call",
        "params": {"name": "live_clip_notes", "arguments": read_args},
    })
    assert response["result"]["structuredContent"]["method"] == "clip_notes"

    update_args = {"ref": {"path": "live_set tracks 0 clip_slots 0 clip"}, "updates": [{"note_id": 1, "velocity": 90}]}
    response = server.handle({
        "jsonrpc": "2.0",
        "id": 33,
        "method": "tools/call",
        "params": {"name": "live_clip_update_notes", "arguments": update_args},
    })
    assert bridge.calls == [("clip_notes", read_args), ("clip_update_notes", update_args)]
    assert response["result"]["structuredContent"]["method"] == "clip_update_notes"


def test_clip_add_notes_tool_forwards_to_bridge():
    bridge = FakeBridge()
    server = make_server(bridge)
    args = {
        "ref": {"path": "live_set tracks 0 clip_slots 0"},
        "create_clip_length": 4.0,
        "clip_name": "Generated Clip",
        "clear": True,
        "fire": True,
        "notes": [{"pitch": 60, "start_time": 0.0, "duration": 1.0, "velocity": 80}],
    }
    response = server.handle({
        "jsonrpc": "2.0",
        "id": 38,
        "method": "tools/call",
        "params": {"name": "live_clip_add_notes", "arguments": args},
    })
    assert bridge.calls == [("clip_add_notes", args)]
    assert response["result"]["structuredContent"]["method"] == "clip_add_notes"


def test_clip_duplicate_to_arrangement_tool_forwards_to_bridge():
    bridge = FakeBridge()
    server = make_server(bridge)
    args = {
        "track": {"path": "live_set tracks 0"},
        "clip": {"path": "live_set tracks 0 clip_slots 0 clip"},
        "destination_time": 16.0,
    }
    response = server.handle({
        "jsonrpc": "2.0",
        "id": 39,
        "method": "tools/call",
        "params": {"name": "live_clip_duplicate_to_arrangement", "arguments": args},
    })
    assert bridge.calls == [("clip_duplicate_to_arrangement", args)]
    assert response["result"]["structuredContent"]["method"] == "clip_duplicate_to_arrangement"


def test_clip_envelope_tool_forwards_to_bridge():
    bridge = FakeBridge()
    server = make_server(bridge)
    args = {
        "ref": {"path": "live_set tracks 0 clip_slots 0 clip"},
        "parameter": {"id": 42},
        "create": True,
        "insert_steps": [{"time": 0.0, "duration": 1.0, "value": 0.5}],
    }
    response = server.handle({
        "jsonrpc": "2.0",
        "id": 34,
        "method": "tools/call",
        "params": {"name": "live_clip_envelope", "arguments": args},
    })
    assert bridge.calls == [("clip_envelope", args)]
    assert response["result"]["structuredContent"]["method"] == "clip_envelope"


def test_clip_velocity_envelope_tool_forwards_to_bridge():
    bridge = FakeBridge()
    server = make_server(bridge)
    args = {
        "ref": {"path": "live_set tracks 0 clip_slots 0 clip"},
        "parameter": {"id": 42},
        "min_value": 0.2,
        "max_value": 0.8,
    }
    response = server.handle({
        "jsonrpc": "2.0",
        "id": 37,
        "method": "tools/call",
        "params": {"name": "live_clip_velocity_envelope", "arguments": args},
    })
    assert bridge.calls == [("clip_velocity_envelope", args)]
    assert response["result"]["structuredContent"]["method"] == "clip_velocity_envelope"


def test_clip_warp_markers_tool_forwards_to_bridge():
    bridge = FakeBridge()
    server = make_server(bridge)
    args = {
        "ref": {"id": 77},
        "move_markers": [{"beat_time": 4.0, "beat_time_delta": 0.25}],
    }
    response = server.handle({
        "jsonrpc": "2.0",
        "id": 35,
        "method": "tools/call",
        "params": {"name": "live_clip_warp_markers", "arguments": args},
    })
    assert bridge.calls == [("clip_warp_markers", args)]
    assert response["result"]["structuredContent"]["method"] == "clip_warp_markers"


def test_track_audio_and_device_tools_forward_to_bridge():
    bridge = FakeBridge()
    server = make_server(bridge)
    audio_args = {
        "ref": {"path": "live_set tracks 0"},
        "file_path": "/tmp/hook.wav",
        "destination_time": 32.0,
        "name": "Hook",
    }
    response = server.handle({
        "jsonrpc": "2.0",
        "id": 42,
        "method": "tools/call",
        "params": {"name": "live_track_create_audio_clip", "arguments": audio_args},
    })
    assert response["result"]["structuredContent"]["method"] == "track_create_audio_clip"

    device_args = {"ref": {"path": "live_set tracks 0"}, "device_name": "EQ Eight", "device_index": -1}
    response = server.handle({
        "jsonrpc": "2.0",
        "id": 43,
        "method": "tools/call",
        "params": {"name": "live_track_insert_device", "arguments": device_args},
    })
    assert bridge.calls == [("track_create_audio_clip", audio_args), ("track_insert_device", device_args)]
    assert response["result"]["structuredContent"]["method"] == "track_insert_device"


def test_agent_m4l_device_tool_builds_and_forwards(monkeypatch, tmp_path):
    import agent_m4l

    monkeypatch.setattr(agent_m4l, "GENERATED_DIR", tmp_path)
    monkeypatch.setattr(agent_m4l, "WEBUI_DIR", tmp_path / "webui")
    bridge = FakeBridge()
    server = make_server(bridge)
    args = {
        "role": "audio_effect",
        "instance_id": "Wobble",
        "name": "Wobble",
        "target_track": {"path": "live_set tracks 0"},
        "patch": {
            "objects": [
                {"id": "dial", "text": "live.dial", "presentation_rect": [12, 12, 48, 48]},
                {"id": "gain", "text": "*~ 0.5"},
            ],
            "connections": [{"from": "dial", "to": "gain", "inlet": 1}],
        },
        "webui": {
            "title": "Wobble",
            "html": "<html><script src=\"device.js\"></script></html>",
            "js": "window.largeSource = true;",
            "presentation_rect": [0, 0, 320, 160],
            "controls": [{"id": "dial", "label": "Amount", "value": 0.5}],
            "assets": {"lib/scene.js": "export const scene = true;"},
        },
        "install": False,
    }
    response = server.handle({
        "jsonrpc": "2.0",
        "id": 44,
        "method": "tools/call",
        "params": {"name": "live_agent_m4l_device", "arguments": args},
    })
    forwarded = bridge.calls[0][1]
    assert bridge.calls[0][0] == "agent_m4l_device"
    assert forwarded["device_name"] == "AgentM4L_audio_effect_Wobble"
    assert forwarded["command_file"] == agent_m4l.command_file("Wobble")
    assert forwarded["status_file"] == agent_m4l.status_file("Wobble")
    assert forwarded["patch"]["objects"][0]["presentation_rect"] == [12, 12, 48, 48]
    assert forwarded["patch"]["webui"]["html_path"].endswith("index.html")
    assert "html" not in forwarded["patch"]["webui"]
    assert "js" not in forwarded["patch"]["webui"]
    assert "controls" not in forwarded["patch"]["webui"]
    assert forwarded["patch"]["webui"]["assets"] == {
        "count": 1,
        "bytes": 26,
        "relative_paths": ["lib/scene.js"],
    }
    assert forwarded["patch"]["device_width"] == 340
    assert forwarded["patch"]["device_height"] == 180
    assert forwarded["device_width"] == 340
    assert forwarded["device_height"] == 180
    assert forwarded["webui"]["url"].startswith("file://")
    assert response["result"]["structuredContent"]["built"]["installed_path"] == ""
    assert response["result"]["structuredContent"]["built"]["device_width"] == 340
    assert response["result"]["structuredContent"]["built"]["device_height"] == 180
    assert response["result"]["structuredContent"]["webui"]["html_path"].endswith("index.html")


def test_agent_m4l_cleanup_tool_forwards_to_bridge():
    bridge = FakeBridge()
    server = make_server(bridge)
    args = {"delete": False, "role": "audio_effect", "track_query": "scratch"}

    response = server.handle({
        "jsonrpc": "2.0",
        "id": 804,
        "method": "tools/call",
        "params": {"name": "live_agent_m4l_cleanup", "arguments": args},
    })

    assert bridge.calls == [("agent_m4l_cleanup", args)]
    assert response["result"]["structuredContent"]["method"] == "agent_m4l_cleanup"


def test_agent_m4l_device_tool_preflight_only_reports_compact_errors(tmp_path):
    bridge = FakeBridge()
    server = make_server(bridge)
    response = server.handle({
        "jsonrpc": "2.0",
        "id": 54,
        "method": "tools/call",
        "params": {"name": "live_agent_m4l_device", "arguments": {
            "role": "audio_effect",
            "instance_id": "Bad Patch",
            "preflight_only": True,
            "patch": {
                "objects": [
                    {"id": "dial", "text": "flonum"},
                    {"id": "dial", "text": "flonum"},
                ],
                "connections": [
                    {"from": "missing", "to": "dial"},
                    {"from": "dial"},
                ],
                "ui_bindings": [
                    {"source": "ghost", "target": "amount"},
                ],
                "webui": {"id": "panel", "object": "jbrowser~"},
            },
            "command_file": str(tmp_path / "command.json"),
            "status_file": str(tmp_path / "status.json"),
        }},
    })

    payload = response["result"]["structuredContent"]
    codes = {item["code"] for item in payload["preflight"]["errors"]}
    assert bridge.calls == []
    assert payload["preflight_only"] is True
    assert payload["preflight"]["ok"] is False
    assert {
        "duplicate_object_id",
        "connection_source_missing",
        "connection_missing_from_or_to",
        "binding_source_missing",
    } <= codes
    assert payload["preflight"]["counts"] == {"objects": 2, "connections": 2, "webuis": 1, "bindings": 1, "values": 0}


def test_agent_m4l_device_tool_attaches_preflight_without_forwarding_flag(tmp_path):
    bridge = FakeBridge()
    server = make_server(bridge)
    response = server.handle({
        "jsonrpc": "2.0",
        "id": 55,
        "method": "tools/call",
        "params": {"name": "live_agent_m4l_device", "arguments": {
            "role": "audio_effect",
            "instance_id": "Good Patch",
            "preflight": True,
            "build": False,
            "load": False,
            "target_track": {"path": "live_set tracks 0"},
            "patch": {
                "objects": [{"id": "gain", "text": "*~ 0.5"}],
                "connections": [{"from": "plugin", "to": "gain"}, {"from": "gain", "to": "plugout"}],
            },
            "command_file": str(tmp_path / "command.json"),
            "status_file": str(tmp_path / "status.json"),
        }},
    })

    forwarded = bridge.calls[0][1]
    payload = response["result"]["structuredContent"]
    assert "preflight" not in forwarded
    assert "preflight_only" not in forwarded
    assert payload["preflight"]["ok"] is True
    assert payload["preflight"]["counts"]["connections"] == 2


def test_agent_m4l_device_tool_preflight_counts_top_level_webui(tmp_path):
    bridge = FakeBridge()
    server = make_server(bridge)
    html_path = tmp_path / "panel.html"
    html_path.write_text("<html></html>", encoding="utf-8")
    response = server.handle({
        "jsonrpc": "2.0",
        "id": 56,
        "method": "tools/call",
        "params": {"name": "live_agent_m4l_device", "arguments": {
            "role": "audio_effect",
            "instance_id": "Panel Only",
            "preflight_only": True,
            "webui": {"id": "panel", "object": "jbrowser~", "html_path": str(html_path)},
        }},
    })

    preflight = response["result"]["structuredContent"]["preflight"]
    assert bridge.calls == []
    assert preflight["ok"] is True
    assert preflight["counts"]["webuis"] == 1


def test_agent_m4l_preflight_reports_presentation_bounds_and_clip_warnings(tmp_path):
    html_path = tmp_path / "panel.html"
    html_path.write_text("<html></html>", encoding="utf-8")
    server = make_server(FakeBridge())

    response = server.handle({
        "jsonrpc": "2.0",
        "id": 807,
        "method": "tools/call",
        "params": {"name": "live_agent_m4l_device", "arguments": {
            "role": "instrument",
            "name": "Bounds Warning",
            "preflight_only": True,
            "patch": {
                "device_width": 320,
                "device_height": 130,
                "objects": [
                    {"id": "keys", "text": "kslider", "presentation_rect": [12, 12, 260, 58]},
                ],
                "webui": {"id": "panel", "html_path": str(html_path), "presentation_rect": [280, 20, 100, 130]},
            },
        }},
    })

    preflight = response["result"]["structuredContent"]["preflight"]
    warning_codes = {item["code"] for item in preflight["warnings"]}
    assert preflight["ok"] is True
    assert preflight["bounds"] == {"width": 320, "height": 130}
    assert preflight["presentation_bounds"] == {
        "min_x": 12,
        "min_y": 12,
        "right": 380,
        "bottom": 150,
        "width": 368,
        "height": 138,
    }
    assert "presentation_rect_exceeds_device_width" in warning_codes
    assert "presentation_rect_exceeds_device_height" in warning_codes


def test_agent_m4l_preflight_warns_on_tall_device_height(tmp_path):
    server = make_server(FakeBridge())

    response = server.handle({
        "jsonrpc": "2.0",
        "id": 808,
        "method": "tools/call",
        "params": {"name": "live_agent_m4l_device", "arguments": {
            "role": "audio_effect",
            "name": "Tall Freeform UI",
            "preflight_only": True,
            "patch": {
                "device_height": 320,
                "objects": [{"id": "scope", "text": "panel", "presentation_rect": [0, 0, 400, 300]}],
            },
        }},
    })

    preflight = response["result"]["structuredContent"]["preflight"]
    assert preflight["ok"] is True
    assert {"code": "tall_device_height_visual_capture_required", "device_height": 320, "advisory_height": 180} in preflight["warnings"]


def test_agent_m4l_preflight_warns_on_wide_device_width(tmp_path):
    server = make_server(FakeBridge())

    response = server.handle({
        "jsonrpc": "2.0",
        "id": 809,
        "method": "tools/call",
        "params": {"name": "live_agent_m4l_device", "arguments": {
            "role": "audio_effect",
            "name": "Wide Freeform UI",
            "preflight_only": True,
            "patch": {
                "device_width": 1120,
                "objects": [{"id": "scene", "text": "panel", "presentation_rect": [0, 0, 1100, 150]}],
            },
        }},
    })

    preflight = response["result"]["structuredContent"]["preflight"]
    assert preflight["ok"] is True
    assert {"code": "wide_device_width_visual_capture_required", "device_width": 1120, "advisory_width": 960} in preflight["warnings"]


def test_agent_m4l_preflight_warns_on_direct_live_api_observers(tmp_path):
    html_path = tmp_path / "panel.html"
    html_path.write_text("<html></html>")
    server = make_server(FakeBridge())

    response = server.handle({
        "jsonrpc": "2.0",
        "id": 809,
        "method": "tools/call",
        "params": {"name": "live_agent_m4l_device", "arguments": {
            "role": "instrument",
            "name": "Observer Warning",
            "preflight_only": True,
            "patch": {
                "live_api_observers": True,
                "objects": [{"id": "osc", "text": "cycle~ 220"}],
                "webui": {"id": "panel", "html_path": str(html_path)},
            },
        }},
    })

    preflight = response["result"]["structuredContent"]["preflight"]
    assert preflight["ok"] is True
    assert {"code": "direct_live_api_observers_opt_in"} in preflight["warnings"]


def test_agent_m4l_device_tool_preflight_set_uses_recovery_patch(tmp_path):
    bridge = FakeBridge()
    command_file = tmp_path / "command.json"
    command_file.write_text(json.dumps({
        "id": "patch1",
        "command": "update",
        "patch": {
            "objects": [{"id": "gain", "text": "flonum"}, {"id": "macro", "text": "live.dial"}],
            "ui_bindings": [{"source": "macro", "target": "virtual_amount"}],
        },
    }), encoding="utf-8")
    server = make_server(bridge)

    ok_response = server.handle({
        "jsonrpc": "2.0",
        "id": 57,
        "method": "tools/call",
        "params": {"name": "live_agent_m4l_device", "arguments": {
            "role": "audio_effect",
            "instance_id": "Recover Set",
            "preflight_only": True,
            "command": "set",
            "values": [{"id": "virtual_amount", "value": 0.25}],
            "command_file": str(command_file),
        }},
    })
    ok_preflight = ok_response["result"]["structuredContent"]["preflight"]
    assert ok_preflight["ok"] is True
    assert ok_preflight["recovered_patch"] is True
    assert ok_preflight["counts"]["values"] == 1

    bad_response = server.handle({
        "jsonrpc": "2.0",
        "id": 58,
        "method": "tools/call",
        "params": {"name": "live_agent_m4l_device", "arguments": {
            "role": "audio_effect",
            "instance_id": "Recover Set",
            "preflight_only": True,
            "command": "set",
            "values": [{"id": "missing_value", "value": 0.25}],
            "command_file": str(command_file),
        }},
    })
    bad_preflight = bad_response["result"]["structuredContent"]["preflight"]
    assert bad_preflight["ok"] is False
    assert {"code": "value_target_missing", "id": "missing_value"} in bad_preflight["errors"]


def test_agent_m4l_device_tool_retries_fresh_build_load(monkeypatch, tmp_path):
    import agent_m4l

    monkeypatch.setattr(agent_m4l, "GENERATED_DIR", tmp_path)
    monkeypatch.setattr(agent_m4l, "WEBUI_DIR", tmp_path / "webui")

    class RetryBridge(FakeBridge):
        def request(self, method, params):
            self.calls.append((method, params))
            if len(self.calls) == 1:
                return {
                    "method": method,
                    "loaded": False,
                    "load_error": "Device AgentM4L_instrument_Orbit_Glass_Synth not found.",
                    "command_id": "load1",
                    "status_file": params["status_file"],
                }
            assert params["write_command_file"] is False
            assert params["udp"] is False
            assert params["id"] == "load1"
            assert params["device_name"] == "AgentM4L_instrument_Orbit_Glass_Synth"
            assert "patch" not in params
            return {
                "method": method,
                "loaded": True,
                "command_id": "load1",
                "status_file": params["status_file"],
                "track": {"devices": [{"name": params["device_name"]}]},
            }

    bridge = RetryBridge()
    server = make_server(bridge)
    response = server.handle({
        "jsonrpc": "2.0",
        "id": 48,
        "method": "tools/call",
        "params": {"name": "live_agent_m4l_device", "arguments": {
            "role": "instrument",
            "instance_id": "orbit_glass_synth_001",
            "name": "Orbit Glass Synth",
            "target_track": {"path": "live_set tracks 4"},
            "patch": {"objects": []},
            "install": False,
            "load_retry_timeout": 1.0,
            "load_retry_interval": 0.0,
        }},
    })

    result = response["result"]["structuredContent"]
    assert len(bridge.calls) == 2
    assert result["loaded"] is True
    assert result["load_retry_attempts"] == 1
    assert "load_error" not in result


def test_agent_m4l_device_tool_handles_value_updates_directly(tmp_path):
    bridge = FakeBridge()
    server = make_server(bridge)
    command_file = tmp_path / "command.json"
    status_file = tmp_path / "status.json"
    args = {
        "role": "audio_effect",
        "instance_id": "Wobble",
        "command": "set",
        "values": [{"id": "dial", "value": 0.4}],
        "command_file": str(command_file),
        "status_file": str(status_file),
        "udp": False,
    }
    response = server.handle({
        "jsonrpc": "2.0",
        "id": 45,
        "method": "tools/call",
        "params": {"name": "live_agent_m4l_device", "arguments": args},
    })
    payload = json.loads(command_file.read_text(encoding="utf-8"))
    result = response["result"]["structuredContent"]
    assert bridge.calls == []
    assert payload["values"] == [{"id": "dial", "value": 0.4}]
    assert result["direct"] is True
    assert result["loaded"] is False
    assert "built" not in result


def test_agent_m4l_device_tool_handles_web_reload_directly(tmp_path):
    bridge = FakeBridge()
    server = make_server(bridge)
    command_file = tmp_path / "command.json"
    response = server.handle({
        "jsonrpc": "2.0",
        "id": 46,
        "method": "tools/call",
        "params": {"name": "live_agent_m4l_device", "arguments": {
            "role": "audio_effect",
            "instance_id": "Wobble",
            "command": "web_reload",
            "webuis": [{"id": "panel", "html_path": "/tmp/panel/index.html"}],
            "command_file": str(command_file),
            "udp": False,
        }},
    })

    payload = json.loads(command_file.read_text(encoding="utf-8"))
    result = response["result"]["structuredContent"]
    assert bridge.calls == []
    assert payload["command"] == "web_reload"
    assert payload["webuis"] == [{"id": "panel", "html_path": "/tmp/panel/index.html"}]
    assert result["direct"] is True
    assert result["loaded"] is False


def test_agent_m4l_device_tool_skips_oversized_direct_udp(tmp_path):
    bridge = FakeBridge()
    server = make_server(bridge)
    command_file = tmp_path / "command.json"
    args = {
        "role": "instrument",
        "instance_id": "Huge UI",
        "build": False,
        "patch": {"objects": [{"id": "big", "text": "comment " + ("x" * 70000)}]},
        "command_file": str(command_file),
    }
    response = server.handle({
        "jsonrpc": "2.0",
        "id": 145,
        "method": "tools/call",
        "params": {"name": "live_agent_m4l_device", "arguments": args},
    })

    result = response["result"]["structuredContent"]
    assert bridge.calls == []
    assert result["sent"] is False
    assert json.loads(command_file.read_text(encoding="utf-8"))["patch"]["objects"][0]["id"] == "big"


def test_agent_m4l_device_tool_direct_update_preserves_recovery_patch(tmp_path):
    bridge = FakeBridge()
    server = make_server(bridge)
    command_file = tmp_path / "command.json"
    status_file = tmp_path / "status.json"
    command_file.write_text(json.dumps({
        "id": "patch1",
        "command": "update",
        "objects": [{"id": "dial", "text": "flonum"}],
        "connections": [],
        "device_width": 640,
        "device_height": 220,
    }), encoding="utf-8")
    response = server.handle({
        "jsonrpc": "2.0",
        "id": 50,
        "method": "tools/call",
        "params": {"name": "live_agent_m4l_device", "arguments": {
            "role": "audio_effect",
            "instance_id": "Wobble",
            "command": "set",
            "values": [{"id": "dial", "value": 0.7}],
            "command_file": str(command_file),
            "status_file": str(status_file),
            "udp": False,
        }},
    })

    payload = json.loads(command_file.read_text(encoding="utf-8"))
    sidecar = json.loads(server_module.agent_m4l_sidecar_recovery_path(str(command_file)).read_text(encoding="utf-8"))
    assert bridge.calls == []
    assert response["result"]["structuredContent"]["direct"] is True
    assert "patch" not in payload
    assert sidecar["patch"]["objects"][0]["id"] == "dial"
    assert sidecar["patch"]["device_width"] == 640
    assert sidecar["patch"]["device_height"] == 220
    assert payload["values"] == [{"id": "dial", "value": 0.7}]


def test_agent_m4l_device_tool_recovers_patch_from_sidecar(tmp_path):
    command_file = tmp_path / "command.json"
    status_file = tmp_path / "status.json"
    patch = {"objects": [{"id": "dial", "text": "flonum"}], "connections": []}
    command_file.write_text(json.dumps({
        "id": "status1",
        "command": "status",
        "patch": None,
    }), encoding="utf-8")
    server_module.write_agent_m4l_recovery_patch(str(command_file), patch)

    response = make_server(FakeBridge()).handle({
        "jsonrpc": "2.0",
        "id": 51,
        "method": "tools/call",
        "params": {"name": "live_agent_m4l_device", "arguments": {
            "role": "audio_effect",
            "instance_id": "Wobble",
            "command": "status",
            "command_file": str(command_file),
            "status_file": str(status_file),
            "udp": False,
        }},
    })

    payload = json.loads(command_file.read_text(encoding="utf-8"))
    assert response["result"]["structuredContent"]["direct"] is True
    assert "patch" not in payload
    assert payload["command"] == "status"
    assert json.loads(server_module.agent_m4l_sidecar_recovery_path(str(command_file)).read_text(encoding="utf-8"))["patch"] == patch


def test_agent_m4l_device_tool_writes_recovery_sidecar_for_forwarded_patch(tmp_path):
    bridge = FakeBridge()
    command_file = tmp_path / "command.json"
    status_file = tmp_path / "status.json"
    patch = {"objects": [{"id": "dial", "text": "flonum"}], "connections": []}

    make_server(bridge).handle({
        "jsonrpc": "2.0",
        "id": 53,
        "method": "tools/call",
        "params": {"name": "live_agent_m4l_device", "arguments": {
            "role": "audio_effect",
            "instance_id": "Wobble",
            "build": False,
            "target_track": {"path": "live_set tracks 0"},
            "command_file": str(command_file),
            "status_file": str(status_file),
            "patch": patch,
            "udp": False,
        }},
    })

    assert bridge.calls[0][0] == "agent_m4l_device"
    sidecar = server_module.agent_m4l_sidecar_recovery_path(str(command_file))
    recovered = json.loads(sidecar.read_text(encoding="utf-8"))["patch"]
    assert recovered["objects"] == patch["objects"]
    assert recovered["connections"] == patch["connections"]
    assert recovered["device_width"] == 420
    assert recovered["device_height"] == 170


def test_agent_m4l_device_tool_load_false_patch_uses_direct_update(tmp_path):
    bridge = FakeBridge()
    server = make_server(bridge)
    command_file = tmp_path / "command.json"
    status_file = tmp_path / "status.json"
    patch = {"objects": [{"id": "dial", "text": "flonum"}], "connections": []}

    response = server.handle({
        "jsonrpc": "2.0",
        "id": 54,
        "method": "tools/call",
        "params": {"name": "live_agent_m4l_device", "arguments": {
            "role": "audio_effect",
            "instance_id": "Wobble",
            "command": "update",
            "load": False,
            "udp": False,
            "patch": patch,
            "command_file": str(command_file),
            "status_file": str(status_file),
        }},
    })

    result = response["result"]["structuredContent"]
    payload = json.loads(command_file.read_text(encoding="utf-8"))
    assert bridge.calls == []
    assert result["direct"] is True
    assert result["loaded"] is False
    assert "built" not in result
    assert payload["patch"]["objects"] == patch["objects"]
    assert payload["patch"]["connections"] == patch["connections"]
    assert payload["patch"]["device_width"] == 420
    assert payload["patch"]["device_height"] == 170


def test_agent_m4l_device_wait_status_uses_default_status_file_mtime(monkeypatch, tmp_path):
    import agent_m4l

    monkeypatch.setattr(agent_m4l, "state_dir", lambda: tmp_path)
    status_path = Path(agent_m4l.status_file("Default Wait"))
    status_path.write_text('{"event":"status","command_id":"old"}', encoding="utf-8")
    previous_mtime = status_path.stat().st_mtime
    captured = {}

    def fake_wait_status(path, before, command_id, timeout, poll_interval, expected_event=None):
        captured["path"] = path
        captured["previous_mtime"] = before
        captured["command_id"] = command_id
        captured["expected_event"] = expected_event
        return {"event": "status", "command_id": command_id}

    monkeypatch.setattr(server_module, "wait_agent_m4l_status", fake_wait_status)
    response = make_server(FakeBridge()).handle({
        "jsonrpc": "2.0",
        "id": 57,
        "method": "tools/call",
        "params": {"name": "live_agent_m4l_device", "arguments": {
            "role": "audio_effect",
            "instance_id": "Default Wait",
            "command": "status",
            "load": False,
            "udp": False,
            "wait_status": True,
        }},
    })

    assert captured["path"] == str(status_path)
    assert captured["previous_mtime"] == previous_mtime
    assert captured["expected_event"] == "status"
    assert response["result"]["structuredContent"]["status"]["command_id"] == captured["command_id"]


def test_agent_m4l_device_tool_materializes_webui_arrays(monkeypatch, tmp_path):
    import agent_m4l

    monkeypatch.setattr(agent_m4l, "GENERATED_DIR", tmp_path)
    monkeypatch.setattr(agent_m4l, "WEBUI_DIR", tmp_path / "webui")
    existing = tmp_path / "existing.html"
    existing.write_text("<html></html>", encoding="utf-8")
    bridge = FakeBridge()
    server = make_server(bridge)
    args = {
        "role": "audio_effect",
        "instance_id": "Panel Test",
        "patch": {"objects": []},
        "webuis": [
            {
                "id": "left",
                "object": "jbrowser~",
                "title": "Left",
                "reuse": False,
                "read_message": "readfile",
                "html_url": existing.resolve().as_uri(),
                "presentation_rect": [0, 0, 240, 120],
                "controls": [{"id": "mix", "label": "Mix", "value": 0.4}],
            },
            {
                "id": "right",
                "object": "jweb",
                "html_path": str(existing),
                "presentation_rect": [240, 0, 240, 120],
            },
        ],
        "install": False,
    }
    response = server.handle({
        "jsonrpc": "2.0",
        "id": 47,
        "method": "tools/call",
        "params": {"name": "live_agent_m4l_device", "arguments": args},
    })

    forwarded = bridge.calls[0][1]
    assert len(forwarded["webuis"]) == 2
    assert forwarded["webuis"][0]["object"] == "jbrowser~"
    assert forwarded["webuis"][0]["reuse"] is False
    assert forwarded["webuis"][0]["read_message"] == "readfile"
    assert forwarded["webuis"][0]["html_url"].startswith("file://")
    assert forwarded["webuis"][0]["html_path"].endswith("Panel_Test_left/index.html")
    assert forwarded["webuis"][1]["html_path"] == str(existing)
    assert forwarded["patch"]["webuis"] == forwarded["webuis"]
    assert forwarded["patch"]["device_width"] == 500
    assert forwarded["patch"]["device_height"] == 140
    assert response["result"]["structuredContent"]["built"]["device_width"] == 500
    assert response["result"]["structuredContent"]["built"]["device_height"] == 140
    assert response["result"]["structuredContent"]["webui"]["webuis"][0]["url"].startswith("file://")
    assert response["result"]["structuredContent"]["webui"]["webuis"][0]["reuse"] is False
    assert response["result"]["structuredContent"]["webui"]["webuis"][0]["read_message"] == "readfile"


def test_agent_m4l_device_tool_materializes_existing_webui_assets(monkeypatch, tmp_path):
    import agent_m4l

    monkeypatch.setattr(agent_m4l, "GENERATED_DIR", tmp_path)
    monkeypatch.setattr(agent_m4l, "WEBUI_DIR", tmp_path / "webui")
    existing = tmp_path / "existing.html"
    existing.write_text("<html><script src=\"lib/scene.js\"></script></html>", encoding="utf-8")
    bridge = FakeBridge()
    server = make_server(bridge)
    args = {
        "role": "audio_effect",
        "instance_id": "Asset Existing",
        "patch": {"objects": []},
        "webui": {
            "id": "asset_panel",
            "object": "jbrowser~",
            "html_path": str(existing),
            "presentation_rect": [0, 0, 360, 140],
            "assets": {"lib/scene.js": "window.sceneReady = true;"},
        },
        "install": False,
    }
    response = server.handle({
        "jsonrpc": "2.0",
        "id": 52,
        "method": "tools/call",
        "params": {"name": "live_agent_m4l_device", "arguments": args},
    })

    forwarded = bridge.calls[0][1]
    assert forwarded["patch"]["webui"]["html_path"] == str(existing)
    assert forwarded["patch"]["webui"]["assets"] == {
        "count": 1,
        "bytes": 25,
        "relative_paths": ["lib/scene.js"],
    }
    assert (tmp_path / "lib" / "scene.js").read_text(encoding="utf-8") == "window.sceneReady = true;"
    assert not (agent_m4l.WEBUI_DIR / "Asset_Existing" / "lib" / "scene.js").exists()
    assert response["result"]["structuredContent"]["webui"]["assets"]["bytes"] == 25


def test_agent_m4l_device_tool_materializes_patch_webui(monkeypatch, tmp_path):
    import agent_m4l

    monkeypatch.setattr(agent_m4l, "GENERATED_DIR", tmp_path)
    monkeypatch.setattr(agent_m4l, "WEBUI_DIR", tmp_path / "webui")
    bridge = FakeBridge()
    server = make_server(bridge)
    args = {
        "role": "instrument",
        "instance_id": "Nested Panel",
        "patch": {
            "objects": [],
            "webui": {
                "object": "jbrowser~",
                "title": "Nested",
                "presentation_rect": [0, 0, 220, 120],
                "controls": [{"id": "tone", "label": "Tone", "value": 0.5}],
            },
        },
        "install": False,
    }
    response = server.handle({
        "jsonrpc": "2.0",
        "id": 49,
        "method": "tools/call",
        "params": {"name": "live_agent_m4l_device", "arguments": args},
    })

    forwarded = bridge.calls[0][1]
    assert forwarded["patch"]["webui"]["object"] == "jbrowser~"
    assert forwarded["patch"]["webui"]["html_path"].endswith("Nested_Panel/index.html")
    assert forwarded["patch"]["device_width"] == 260
    assert forwarded["patch"]["device_height"] == 140
    assert response["result"]["structuredContent"]["built"]["device_width"] == 260
    assert response["result"]["structuredContent"]["built"]["device_height"] == 140
    assert response["result"]["structuredContent"]["webui"]["html_path"].endswith("Nested_Panel/index.html")


def test_agent_m4l_device_tool_waits_for_status_without_forwarding_wait_args(tmp_path):
    class StatusBridge(FakeBridge):
        def request(self, method, params):
            self.calls.append((method, params))
            status_file = Path(params["status_file"])
            status_file.write_text('{"event":"set","command_id":"cmd1","dynamic_objects":4,"webuis":1}', encoding="utf-8")
            return {
                "method": method,
                "command_id": "cmd1",
                "status_file": str(status_file),
                "params": params,
            }

    bridge = StatusBridge()
    server = make_server(bridge)
    status_file = tmp_path / "status.json"
    args = {
        "role": "audio_effect",
        "instance_id": "Wobble",
        "build": False,
        "command": "set",
        "values": [{"id": "dial", "value": 0.4}],
        "command_file": str(tmp_path / "command.json"),
        "status_file": str(status_file),
        "ref": {"path": "live_set tracks 0"},
        "wait_status": True,
        "status_timeout": 0.2,
    }
    response = server.handle({
        "jsonrpc": "2.0",
        "id": 46,
        "method": "tools/call",
        "params": {"name": "live_agent_m4l_device", "arguments": args},
    })

    forwarded = bridge.calls[0][1]
    assert "wait_status" not in forwarded
    assert "status_timeout" not in forwarded
    assert "status_detail" not in forwarded
    assert "compact_status" not in forwarded
    assert response["result"]["structuredContent"]["status"]["event"] == "set"
    assert response["result"]["structuredContent"]["status"]["dynamic_objects"] == 4


def test_agent_m4l_device_tool_can_return_compact_status(tmp_path):
    class StatusBridge(FakeBridge):
        def request(self, method, params):
            self.calls.append((method, params))
            status_file = Path(params["status_file"])
            status_file.write_text(json.dumps({
                "event": "set",
                "command_id": "cmd1",
                "dynamic_objects": 4,
                "changed": 1,
                "source": "dial",
                "target": "gain",
                "state": {
                    "gain": 0.5,
                    "web_read_pending": 0,
                    "web_panel_ready": 1,
                },
            }), encoding="utf-8")
            return {"method": method, "command_id": "cmd1", "status_file": str(status_file), "params": params}

    bridge = StatusBridge()
    mcp = make_server(bridge)
    args = {
        "role": "audio_effect",
        "instance_id": "Compact",
        "build": False,
        "command": "set",
        "values": [{"id": "dial", "value": 0.5}],
        "command_file": str(tmp_path / "command.json"),
        "status_file": str(tmp_path / "status.json"),
        "ref": {"path": "live_set tracks 0"},
        "wait_status": True,
        "status_detail": "summary",
    }

    response = mcp.handle({
        "jsonrpc": "2.0",
        "id": 48,
        "method": "tools/call",
        "params": {"name": "live_agent_m4l_device", "arguments": args},
    })

    forwarded = bridge.calls[0][1]
    status = response["result"]["structuredContent"]["status"]
    assert "status_detail" not in forwarded
    assert status["event"] == "set"
    assert status["changed"] == 1
    assert status["source"] == "dial"
    assert status["target"] == "gain"
    assert status["state"]["web_panel_ready"] == 1
    assert "gain" not in status.get("state", {})


def test_agent_m4l_device_compact_status_can_include_requested_state_keys(tmp_path):
    class StatusBridge(FakeBridge):
        def request(self, method, params):
            self.calls.append((method, params))
            status_file = Path(params["status_file"])
            status_file.write_text(json.dumps({
                "event": "status",
                "command_id": "cmd1",
                "dynamic_objects": 4,
                "state": {
                    "drive_amount": 0.85,
                    "level_meter": 0.123,
                    "web_panel_ready": 1,
                },
            }), encoding="utf-8")
            return {"method": method, "command_id": "cmd1", "status_file": str(status_file), "params": params}

    bridge = StatusBridge()
    mcp = make_server(bridge)
    args = {
        "role": "audio_effect",
        "instance_id": "CompactTelemetry",
        "build": False,
        "command": "status",
        "command_file": str(tmp_path / "command.json"),
        "status_file": str(tmp_path / "status.json"),
        "ref": {"path": "live_set tracks 0"},
        "wait_status": True,
        "compact_status": True,
        "status_state_keys": ["level_meter"],
    }

    response = mcp.handle({
        "jsonrpc": "2.0",
        "id": 481,
        "method": "tools/call",
        "params": {"name": "live_agent_m4l_device", "arguments": args},
    })

    forwarded = bridge.calls[0][1]
    status = response["result"]["structuredContent"]["status"]
    assert "status_state_keys" not in forwarded
    assert status["event"] == "status"
    assert status["state"]["level_meter"] == 0.123
    assert status["state"]["web_panel_ready"] == 1
    assert "drive_amount" not in status.get("state", {})


def test_agent_m4l_device_compact_status_can_return_requested_state_keys_only(tmp_path):
    class StatusBridge(FakeBridge):
        def request(self, method, params):
            self.calls.append((method, params))
            status_file = Path(params["status_file"])
            status_file.write_text(json.dumps({
                "event": "status",
                "command_id": "cmd1",
                "dynamic_objects": 4,
                "state": {
                    "level_meter": 0.25,
                    "web_panel_ready": 1,
                    "command_wake_count": 12,
                },
            }), encoding="utf-8")
            return {"method": method, "command_id": "cmd1", "status_file": str(status_file), "params": params}

    bridge = StatusBridge()
    mcp = make_server(bridge)
    args = {
        "role": "audio_effect",
        "instance_id": "CompactTelemetryOnly",
        "build": False,
        "command": "status",
        "command_file": str(tmp_path / "command.json"),
        "status_file": str(tmp_path / "status.json"),
        "ref": {"path": "live_set tracks 0"},
        "wait_status": True,
        "compact_status": True,
        "status_state_keys": "level_meter",
        "status_state_keys_only": True,
    }

    response = mcp.handle({
        "jsonrpc": "2.0",
        "id": 482,
        "method": "tools/call",
        "params": {"name": "live_agent_m4l_device", "arguments": args},
    })

    forwarded = bridge.calls[0][1]
    status = response["result"]["structuredContent"]["status"]
    assert "status_state_keys" not in forwarded
    assert "status_state_keys_only" not in forwarded
    assert status["state"] == {"level_meter": 0.25}
    assert status["state_keys"] == ["command_wake_count", "level_meter", "web_panel_ready"]


def test_agent_m4l_device_tool_can_return_compact_status_alias(tmp_path):
    class StatusBridge(FakeBridge):
        def request(self, method, params):
            self.calls.append((method, params))
            status_file = Path(params["status_file"])
            status_file.write_text(json.dumps({
                "event": "set",
                "command_id": "cmd1",
                "dynamic_objects": 4,
                "changed": 1,
                "state": {
                    "gain": 0.5,
                    "web_read_pending": 0,
                    "web_panel_ready": 1,
                },
            }), encoding="utf-8")
            return {"method": method, "command_id": "cmd1", "status_file": str(status_file), "params": params}

    bridge = StatusBridge()
    mcp = make_server(bridge)
    args = {
        "role": "audio_effect",
        "instance_id": "CompactAlias",
        "build": False,
        "command": "set",
        "values": [{"id": "dial", "value": 0.5}],
        "command_file": str(tmp_path / "command.json"),
        "status_file": str(tmp_path / "status.json"),
        "ref": {"path": "live_set tracks 0"},
        "wait_status": True,
        "compact_status": True,
    }

    response = mcp.handle({
        "jsonrpc": "2.0",
        "id": 49,
        "method": "tools/call",
        "params": {"name": "live_agent_m4l_device", "arguments": args},
    })

    forwarded = bridge.calls[0][1]
    status = response["result"]["structuredContent"]["status"]
    assert "compact_status" not in forwarded
    assert status["event"] == "set"
    assert status["changed"] == 1
    assert status["state"]["web_panel_ready"] == 1
    assert "gain" not in status.get("state", {})


def test_agent_m4l_device_tool_can_return_compact_result(tmp_path):
    class ResultBridge(FakeBridge):
        def request(self, method, params):
            self.calls.append((method, params))
            devices = [
                {
                    "id": index,
                    "name": f"Device {index}",
                    "class_name": "MxDeviceInstrument",
                    "parameters": [{"name": "P", "value": 0.5}],
                    "chains": [{"name": "Nested"}],
                }
                for index in range(10)
            ]
            return {
                "method": method,
                "params": params,
                "sent": True,
                "command": "update",
                "role": "instrument",
                "instance_id": "Compact_Result",
                "device_name": "AgentM4L_instrument_Compact_Result",
                "command_id": "cmd1",
                "command_file": str(tmp_path / "command.json"),
                "status_file": str(tmp_path / "status.json"),
                "loaded": True,
                "track": {
                    "id": 123,
                    "name": "Track 2",
                    "devices": devices + [{"truncated": True}],
                    "clips": [{"name": "Clip"}],
                    "arrangement_clips": [{"name": "Arrangement Clip"}],
                },
            }

    bridge = ResultBridge()
    mcp = make_server(bridge)
    response = mcp.handle({
        "jsonrpc": "2.0",
        "id": 64,
        "method": "tools/call",
        "params": {"name": "live_agent_m4l_device", "arguments": {
            "role": "instrument",
            "instance_id": "Compact Result",
            "build": False,
            "target_track": {"path": "live_set tracks 1"},
            "patch": {"objects": []},
            "compact_result": True,
        }},
    })

    forwarded = bridge.calls[0][1]
    result = response["result"]["structuredContent"]
    assert "compact_result" not in forwarded
    assert "params" not in result
    assert result["loaded"] is True
    assert result["track"]["name"] == "Track 2"
    assert result["track"]["device_count"] == 10
    assert len(result["track"]["devices"]) == 8
    assert result["track"]["devices"][0]["parameter_count"] == 1
    assert result["track"]["devices"][0]["chain_count"] == 1
    assert "parameters" not in result["track"]["devices"][0]
    assert result["track"]["devices_truncated"] is True
    assert result["track"]["clip_count"] == 1
    assert result["track"]["arrangement_clip_preview_count"] == 1


def test_agent_m4l_device_tool_result_detail_summary_alias_is_server_side(tmp_path):
    class ResultBridge(FakeBridge):
        def request(self, method, params):
            self.calls.append((method, params))
            return {
                "method": method,
                "params": params,
                "command": "status",
                "track": {"name": "Track 1", "devices": [{"name": "Device", "parameters": [1, 2]}]},
            }

    bridge = ResultBridge()
    mcp = make_server(bridge)
    response = mcp.handle({
        "jsonrpc": "2.0",
        "id": 65,
        "method": "tools/call",
        "params": {"name": "live_agent_m4l_device", "arguments": {
            "role": "audio_effect",
            "instance_id": "Alias",
            "build": False,
            "target_track": {"path": "live_set tracks 0"},
            "command": "status",
            "result_detail": "summary",
        }},
    })

    forwarded = bridge.calls[0][1]
    result = response["result"]["structuredContent"]
    assert "result_detail" not in forwarded
    assert "params" not in result
    assert result["track"]["devices"] == [{"name": "Device", "parameter_count": 2}]


def test_agent_m4l_device_tool_compact_result_preserves_direct_fast_path(tmp_path):
    bridge = FakeBridge()
    command_file = tmp_path / "command.json"
    response = make_server(bridge).handle({
        "jsonrpc": "2.0",
        "id": 66,
        "method": "tools/call",
        "params": {"name": "live_agent_m4l_device", "arguments": {
            "role": "audio_effect",
            "instance_id": "Direct Compact",
            "command": "set",
            "values": [{"id": "gain", "value": 0.25}],
            "command_file": str(command_file),
            "udp": False,
            "compact_result": True,
        }},
    })

    payload = json.loads(command_file.read_text(encoding="utf-8"))
    result = response["result"]["structuredContent"]
    assert bridge.calls == []
    assert result["direct"] is True
    assert result["command_file_written"] is True
    assert "compact_result" not in payload
    assert payload["values"] == [{"id": "gain", "value": 0.25}]


def test_agent_m4l_status_timeout_defaults_longer_for_webui():
    assert agent_m4l_status_timeout(None, False) == 2.0
    assert agent_m4l_status_timeout(None, True) == 9.0
    assert agent_m4l_status_timeout(0.25, True) == 0.25


def test_agent_m4l_device_tool_uses_webui_wait_status_default(monkeypatch, tmp_path):
    captured = {}

    class StatusBridge(FakeBridge):
        def request(self, method, params):
            self.calls.append((method, params))
            return {
                "method": method,
                "command": "update",
                "command_id": "cmd-web",
                "status_file": str(tmp_path / "status.json"),
                "params": params,
            }

    def fake_wait_status(path, previous_mtime, command_id, timeout, poll_interval, expected_event=None):
        captured["timeout"] = timeout
        captured["expected_event"] = expected_event
        return {"event": "reload", "command_id": command_id}

    monkeypatch.setattr(server_module, "wait_agent_m4l_status", fake_wait_status)
    bridge = StatusBridge()
    mcp = make_server(bridge)
    args = {
        "role": "audio_effect",
        "instance_id": "Web Wait",
        "build": False,
        "command": "update",
        "command_file": str(tmp_path / "command.json"),
        "status_file": str(tmp_path / "status.json"),
        "ref": {"path": "live_set tracks 0"},
        "patch": {
            "objects": [],
            "webui": {"id": "panel", "html_path": str(tmp_path / "panel.html")},
        },
        "wait_status": True,
    }

    response = mcp.handle({
        "jsonrpc": "2.0",
        "id": 47,
        "method": "tools/call",
        "params": {"name": "live_agent_m4l_device", "arguments": args},
    })

    assert response["result"]["structuredContent"]["status"]["event"] == "reload"
    assert captured["timeout"] == 9.0
    assert captured["expected_event"] == "reload"


def test_wait_agent_m4l_status_requires_matching_command_id(tmp_path):
    status_file = tmp_path / "status.json"
    status_file.write_text('{"event":"set","command_id":"old"}', encoding="utf-8")
    before = status_file.stat().st_mtime - 1.0
    status_file.write_text('{"event":"set","command_id":"new","webuis":1}', encoding="utf-8")

    result = wait_agent_m4l_status(str(status_file), before, "new", 0.2, 0.01)

    assert result["command_id"] == "new"
    assert result["webuis"] == 1
    assert result["host_runtime_status"] == "missing"


def test_wait_agent_m4l_status_tolerates_stale_trailing_json(tmp_path):
    status_file = tmp_path / "status.json"
    before = 1.0
    status_file.write_text('{"event":"set","command_id":"new","changed":1}{"event":"set","command_id":"old"}', encoding="utf-8")

    result = wait_agent_m4l_status(str(status_file), before, "new", 0.2, 0.01, "set")

    assert result["event"] == "set"
    assert result["command_id"] == "new"
    assert result["changed"] == 1


def test_wait_agent_m4l_status_preserves_host_runtime_version(tmp_path):
    status_file = tmp_path / "status.json"
    before = 1.0
    status_file.write_text(json.dumps({
        "event": "reload",
        "command_id": "new",
        "host_runtime_version": "web-clear-guard-1",
    }), encoding="utf-8")

    result = wait_agent_m4l_status(str(status_file), before, "new", 0.2, 0.01, "reload")
    compact = summarize_agent_m4l_status(result)

    assert result["host_runtime_version"] == "web-clear-guard-1"
    assert result["host_runtime_status"] == "reported"
    assert compact["host_runtime_version"] == "web-clear-guard-1"
    assert compact["host_runtime_status"] == "reported"


def test_wait_agent_m4l_status_accepts_reload_seen_before_web_ack(tmp_path):
    status_file = tmp_path / "status.json"
    before = 1.0
    status_file.write_text(json.dumps({
        "event": "set",
        "command_id": "reload1",
        "last_reload_command_id": "reload1",
        "dynamic_objects": 8,
    }), encoding="utf-8")

    result = wait_agent_m4l_status(str(status_file), before, "reload1", 0.2, 0.01, "reload")

    assert result["event"] == "set"
    assert result["last_reload_command_id"] == "reload1"
    assert result["reload_seen"] is True


def test_wait_agent_m4l_status_marks_terminal_webui_exhaustion(tmp_path):
    status_file = tmp_path / "status.json"
    before = 1.0
    status_file.write_text(json.dumps({
        "event": "error",
        "command_id": "reload1",
        "last_reload_command_id": "reload1",
        "reason": "webui_read_exhausted",
        "id": "panel",
        "attempts": 6,
        "state": {"web_read_pending": 0, "web_panel_read_exhausted": 1},
    }), encoding="utf-8")

    result = wait_agent_m4l_status(str(status_file), before, "reload1", 0.2, 0.01, "reload")

    assert result["event"] == "error"
    assert result["reload_seen"] is True
    assert result["webui_status"] == "read_exhausted"
    assert result["reason"] == "webui_read_exhausted"
    assert result["attempts"] == 6


def test_wait_agent_m4l_status_does_not_accept_pending_web_read_as_reload(tmp_path):
    status_file = tmp_path / "status.json"
    before = 1.0
    status_file.write_text(json.dumps({
        "event": "webui_read",
        "command_id": "reload1",
        "last_reload_command_id": "reload1",
        "attempt": 2,
        "message": "read",
        "state": {"web_read_pending": 1, "web_panel_read_attempts": 2},
    }), encoding="utf-8")

    result = wait_agent_m4l_status(str(status_file), before, "reload1", 0.01, 0.01, "reload")

    assert result["timed_out"] is True
    assert result["mismatch"] == "webui_read_pending"
    assert result["last_status"]["event"] == "webui_read"
    assert result["last_status"]["attempt"] == 2
    assert result["last_status"]["message"] == "read"
    assert result["last_status"]["state"]["web_read_pending"] == 1


def test_wait_agent_m4l_status_does_not_accept_binding_set_while_web_pending(tmp_path):
    status_file = tmp_path / "status.json"
    before = 1.0
    status_file.write_text(json.dumps({
        "event": "set",
        "command_id": "reload1",
        "last_reload_command_id": "reload1",
        "source": "macro",
        "target": "amount",
        "webuis": 1,
        "state": {"web_read_pending": 2, "web_panel_read_attempts": 2},
    }), encoding="utf-8")

    result = wait_agent_m4l_status(str(status_file), before, "reload1", 0.01, 0.01, "reload")

    assert result["timed_out"] is True
    assert result["mismatch"] == "webui_read_pending"
    assert result["last_status"]["event"] == "set"
    assert result["last_status"]["source"] == "macro"


def test_wait_agent_m4l_status_does_not_accept_unloaded_webui_after_reload(tmp_path):
    status_file = tmp_path / "status.json"
    before = 1.0
    status_file.write_text(json.dumps({
        "event": "reload",
        "command_id": "reload1",
        "last_reload_command_id": "reload1",
        "webuis": 1,
        "state": {"web_read_pending": 0, "web_panel_read_attempts": 6},
    }), encoding="utf-8")

    result = wait_agent_m4l_status(str(status_file), before, "reload1", 0.01, 0.01, "reload")

    assert result["timed_out"] is True
    assert result["mismatch"] == "webui_not_loaded"
    assert result["last_status"]["event"] == "reload"


def test_wait_agent_m4l_status_timeout_includes_compact_last_status(tmp_path):
    status_file = tmp_path / "status.json"
    status_file.write_text(json.dumps({
        "event": "set",
        "command_id": "old",
        "last_reload_command_id": "old_reload",
        "dynamic_objects": 8,
        "webuis": 1,
        "device_width": 900,
        "device_height": 320,
        "id": "panel",
        "reason": "webui_read_exhausted",
        "attempts": 6,
        "message": "readfile",
        "connection_errors_truncated": 3,
        "connection_errors": [{"from": "a", "to": "b", "reason": "missing"} for _index in range(20)],
        "state": {
            "command_wake_source": "float",
            "command_wake_count": 1,
            "web_ready": None,
            "web_error": "x" * 260,
            "web_history": list(range(20)),
            "web_payload": {("k%02d" % index): index for index in range(14)},
            "level_value": 0.5,
        },
    }), encoding="utf-8")
    stale_time = time.time() - 5
    os.utime(status_file, (stale_time, stale_time))

    result = wait_agent_m4l_status(str(status_file), status_file.stat().st_mtime - 1.0, "new", 0.01, 0.01, "set")

    assert result["timed_out"] is True
    assert result["expected_command_id"] == "new"
    assert result["expected_event"] == "set"
    assert result["mismatch"] == "command_id_mismatch"
    assert result["last_status_age_seconds"] >= 4
    assert result["status_file_updated_after_command"] is True
    assert result["timeout_reason"] == "stale_or_other_command"
    assert result["last_status"]["command_id"] == "old"
    assert result["last_status"]["dynamic_objects"] == 8
    assert result["last_status"]["device_width"] == 900
    assert result["last_status"]["device_height"] == 320
    assert result["last_status"]["id"] == "panel"
    assert result["last_status"]["reason"] == "webui_read_exhausted"
    assert result["last_status"]["attempts"] == 6
    assert result["last_status"]["message"] == "readfile"
    assert result["last_status"]["connection_errors_truncated"] == 3
    assert result["last_status"]["connection_errors"]["items"] == 20
    assert len(result["last_status"]["connection_errors"]["preview"]) == 12
    assert result["last_status"]["state"]["command_wake_source"] == "float"
    assert result["last_status"]["state"]["web_ready"] is None
    assert result["last_status"]["state"]["web_error"].endswith("...")
    assert len(result["last_status"]["state"]["web_error"]) == 240
    assert result["last_status"]["state"]["web_history"]["items"] == 20
    assert result["last_status"]["state"]["web_history"]["preview"] == list(range(12))
    assert result["last_status"]["state"]["web_payload"]["key_count"] == 14
    assert result["last_status"]["state"]["web_payload"]["keys"] == ["k%02d" % index for index in range(12)]
    assert "level_value" not in result["last_status"].get("state", {})


def test_compact_agent_m4l_status_preserves_timeout_diagnostics():
    status = summarize_agent_m4l_status({
        "timed_out": True,
        "path": "/tmp/status.json",
        "expected_command_id": "new",
        "expected_event": "status",
        "mismatch": "command_id_mismatch",
        "timeout_reason": "host_not_woken",
        "last_status_age_seconds": 8.25,
        "status_file_updated_after_command": False,
        "last_status": {
            "event": "set",
            "command_id": "old",
            "dynamic_objects": 76,
            "state_keys": ["web_read_pending"],
            "state": {"web_read_pending": 5},
        },
    })

    assert status["timed_out"] is True
    assert status["expected_command_id"] == "new"
    assert status["expected_event"] == "status"
    assert status["mismatch"] == "command_id_mismatch"
    assert status["timeout_reason"] == "host_not_woken"
    assert status["last_status_age_seconds"] == 8.25
    assert status["status_file_updated_after_command"] is False
    assert status["last_status"]["command_id"] == "old"
    assert status["last_status"]["dynamic_objects"] == 76
    assert status["last_status"]["state"]["web_read_pending"] == 5


def test_agent_m4l_status_timeout_reason_classifies_common_cases():
    assert agent_m4l_status_timeout_reason({"mismatch": "missing_status_file"}) == "missing_status_file"
    assert agent_m4l_status_timeout_reason({"mismatch": "command_id_mismatch", "status_file_updated_after_command": False}) == "host_not_woken"
    assert agent_m4l_status_timeout_reason({"mismatch": "command_id_mismatch", "status_file_updated_after_command": True}) == "stale_or_other_command"
    assert agent_m4l_status_timeout_reason({"mismatch": "webui_read_pending"}) == "webui_read_pending"
    assert agent_m4l_status_timeout_reason({"error": "bad json"}) == "status_unreadable"


def test_compact_agent_m4l_status_recursively_compacts_raw_last_status():
    status = summarize_agent_m4l_status({
        "timed_out": True,
        "expected_command_id": "new",
        "last_status": {
            "event": "set",
            "command_id": "old",
            "dynamic_objects": 76,
            "state": {
                "web_history": list(range(20)),
                "web_payload": {("k%02d" % index): index for index in range(14)},
                "level_value": 0.5,
            },
        },
    })

    last_status = status["last_status"]
    assert last_status["command_id"] == "old"
    assert last_status["dynamic_objects"] == 76
    assert last_status["state_keys"] == ["level_value", "web_history", "web_payload"]
    assert last_status["state"]["web_history"]["items"] == 20
    assert last_status["state"]["web_payload"]["key_count"] == 14
    assert "level_value" not in last_status.get("state", {})


def test_agent_m4l_expected_status_event_tracks_command_intent():
    assert expected_agent_m4l_status_event("update") == "reload"
    assert expected_agent_m4l_status_event("web_reload") == "webui_reload"
    assert expected_agent_m4l_status_event("reload_webui") == "webui_reload"
    assert expected_agent_m4l_status_event("set") == "set"
    assert expected_agent_m4l_status_event("status") == "status"
    assert expected_agent_m4l_status_event("other") is None


def test_agent_m4l_build_default_tracks_command_intent():
    assert should_build_agent_m4l({"patch": {"objects": []}}) is True
    assert should_build_agent_m4l({"webui": {"html_path": "/tmp/x.html"}}) is True
    assert should_build_agent_m4l({"webuis": [{"html_path": "/tmp/x.html"}]}) is True
    assert should_build_agent_m4l({"patch": {"objects": []}, "load": False}) is False
    assert should_build_agent_m4l({"patch": {"objects": []}, "load": False, "target_track": {"path": "live_set tracks 0"}}) is True
    assert should_build_agent_m4l({"values": [{"id": "dial", "value": 0.5}]}) is False
    assert should_build_agent_m4l({"command": "set"}) is False
    assert should_build_agent_m4l({"command": "status"}) is False
    assert should_build_agent_m4l({"command": "clear"}) is False
    assert should_build_agent_m4l({"target_track": {"path": "live_set tracks 0"}}) is True
    assert should_build_agent_m4l({"build": True, "command": "set"}) is True
    assert should_build_agent_m4l({"build": False, "patch": {"objects": []}}) is False


def test_browser_search_tool_forwards_query_to_bridge():
    bridge = FakeBridge()
    server = make_server(bridge)
    args = {"query": "cowbell", "roots": ["drums"], "limit": 5}
    response = server.handle({
        "jsonrpc": "2.0",
        "id": 4,
        "method": "tools/call",
        "params": {"name": "live_browser_search", "arguments": args},
    })
    assert bridge.calls == [("browser_search", args)]
    assert response["result"]["structuredContent"]["method"] == "browser_search"


def test_browser_capabilities_tool_forwards_to_bridge():
    bridge = FakeBridge()
    server = make_server(bridge)
    response = server.handle({
        "jsonrpc": "2.0",
        "id": 40,
        "method": "tools/call",
        "params": {"name": "live_browser_capabilities", "arguments": {}},
    })
    assert bridge.calls == [("browser_capabilities", {})]
    assert response["result"]["structuredContent"]["method"] == "browser_capabilities"


def test_find_similar_sounds_reads_live_database_without_bridge(tmp_path):
    db_path = make_similarity_db(tmp_path)
    bridge = FakeBridge()
    server = make_server(bridge)
    response = server.handle({
        "jsonrpc": "2.0",
        "id": 46,
        "method": "tools/call",
        "params": {
            "name": "find_similar_sounds",
            "arguments": {"base": "Base Kick", "limit": 2, "db_path": str(db_path)},
        },
    })

    result = response["result"]["structuredContent"]
    assert bridge.calls == []
    assert result["base"]["name"] == "Base Kick.wav"
    assert [item["name"] for item in result["results"]] == ["Near Kick.wav", "Far Kick.wav"]
    assert result["results"][0]["distance"] < result["results"][1]["distance"]
    assert result["results"][0]["path"] == "Pack / Samples / Near Kick.wav"


def make_similarity_db(tmp_path):
    db_path = tmp_path / "Live-files-test.db"
    conn = sqlite3.connect(db_path)
    conn.executescript("""
        CREATE TABLE files (
            file_id INTEGER PRIMARY KEY,
            parent_id INTEGER,
            file_type INTEGER,
            subtype INTEGER DEFAULT 0,
            file_kind INTEGER DEFAULT 0,
            mod_date INTEGER,
            file_size INTEGER,
            aggr_id INTEGER,
            name TEXT,
            colors INTEGER DEFAULT 1,
            md_version INTEGER DEFAULT 0,
            scanner_version INTEGER DEFAULT 0,
            use_count INTEGER DEFAULT 0,
            place_id INTEGER NOT NULL DEFAULT 0,
            flags INTEGER DEFAULT 3,
            device_type INTEGER DEFAULT 0,
            device_arch INTEGER DEFAULT 0,
            device_id TEXT,
            edit_source TEXT,
            edit_date INTEGER,
            fe_version INTEGER DEFAULT 0
        );
        CREATE TABLE places (file_id INTEGER, folder_kind INTEGER, level INTEGER NOT NULL DEFAULT 0, name TEXT);
        CREATE TABLE fe_values (file_id INTEGER, data BLOB, hash INTEGER);
    """)
    rows = [
        (1, 0, 0, 0, 0, 0, 0, 0, "Pack", 1, 0, 0, 0, 1, 3, 0, 0, "", "", 0, 0),
        (2, 1, 0, 0, 0, 0, 0, 0, "Samples", 1, 0, 0, 0, 1, 3, 0, 0, "", "", 0, 0),
        (10, 2, 2002875949, 0, 4, 0, 0, 0, "Base Kick.wav", 1, 0, 0, 0, 1, 3, 0, 0, "", "", 0, 18),
        (11, 2, 2002875949, 0, 4, 0, 0, 0, "Near Kick.wav", 1, 0, 0, 0, 1, 3, 0, 0, "", "", 0, 18),
        (12, 2, 2002875949, 0, 4, 0, 0, 0, "Far Kick.wav", 1, 0, 0, 0, 1, 3, 0, 0, "", "", 0, 18),
    ]
    conn.executemany("INSERT INTO files VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    conn.execute("INSERT INTO places VALUES (1, 0, 0, 'Pack')")
    conn.executemany(
        "INSERT INTO fe_values VALUES (?, ?, ?)",
        [
            (10, encode_feature([0.0, 0.0, 0.0]), 10),
            (11, encode_feature([0.2, 0.0, 0.0]), 11),
            (12, encode_feature([1.0, 0.0, 0.0]), 12),
        ],
    )
    conn.commit()
    conn.close()
    return db_path


def test_exec_tool_forwards_code_to_bridge():
    bridge = FakeBridge()
    server = make_server(bridge)
    args = {"code": "result = {'tracks': len(song.tracks)}", "max_items": 10, "allow_legacy_note_api": True}
    response = server.handle({
        "jsonrpc": "2.0",
        "id": 41,
        "method": "tools/call",
        "params": {"name": "live_exec", "arguments": args},
    })
    assert bridge.calls == [("exec", args)]
    assert response["result"]["structuredContent"]["method"] == "exec"
    tools = server.handle({"jsonrpc": "2.0", "id": 42, "method": "tools/list"})["result"]["tools"]
    exec_tool = next(tool for tool in tools if tool["name"] == "live_exec")
    eval_tool = next(tool for tool in tools if tool["name"] == "live_eval")
    assert "allow_legacy_note_api" in exec_tool["inputSchema"]["properties"]
    assert "allow_legacy_note_api" in eval_tool["inputSchema"]["properties"]


def test_browser_search_schema_mentions_plugins_root():
    server = make_server(FakeBridge())
    response = server.handle({"jsonrpc": "2.0", "id": 5, "method": "tools/list"})
    search = next(tool for tool in response["result"]["tools"] if tool["name"] == "live_browser_search")
    roots_description = search["inputSchema"]["properties"]["roots"]["description"]
    assert "plugins" in roots_description


def test_remote_bridge_default_browser_roots_include_plugins():
    root = Path(__file__).resolve().parents[1]
    bridge_source = (root / "Ableton_Live_MCP" / "bridge.py").read_text()
    assert '"plugins"' in bridge_source
    assert "def _rpc_browser_search" in bridge_source
    assert "def _rpc_browser_load" in bridge_source


def test_browser_load_tool_forwards_item_ref_to_bridge():
    bridge = FakeBridge()
    server = make_server(bridge)
    args = {"item": {"id": 123, "uri": "fake:item", "path": "sounds > Bass > Fake.adg"}, "target_track": {"path": "live_set tracks 0"}}
    response = server.handle({
        "jsonrpc": "2.0",
        "id": 6,
        "method": "tools/call",
        "params": {"name": "live_browser_load", "arguments": args},
    })
    assert bridge.calls == [("browser_load", args)]
    assert response["result"]["structuredContent"]["method"] == "browser_load"


def test_browser_preview_tool_forwards_item_ref_to_bridge():
    bridge = FakeBridge()
    server = make_server(bridge)
    args = {"item": {"id": 123}}
    response = server.handle({
        "jsonrpc": "2.0",
        "id": 61,
        "method": "tools/call",
        "params": {"name": "live_browser_preview", "arguments": args},
    })
    assert bridge.calls == [("browser_preview", args)]
    assert response["result"]["structuredContent"]["method"] == "browser_preview"

    stop_args = {"stop": True}
    server.handle({
        "jsonrpc": "2.0",
        "id": 62,
        "method": "tools/call",
        "params": {"name": "live_browser_preview", "arguments": stop_args},
    })
    assert bridge.calls[-1] == ("browser_preview", stop_args)


def test_tool_list_stays_compact():
    server = make_server(FakeBridge())
    response = server.handle({"jsonrpc": "2.0", "id": 7, "method": "tools/list"})
    payload = json.dumps(response, separators=(",", ":"))
    assert len(payload) < 16350
    live_eval = next(tool for tool in response["result"]["tools"] if tool["name"] == "live_eval")
    assert "live_exec" in live_eval["description"]
    assert "duplicate session clips" not in live_eval["description"].lower()
    similar = next(tool for tool in response["result"]["tools"] if tool["name"] == "find_similar_sounds")
    assert "Live 12+" in similar["description"]
    m4l = next(tool for tool in response["result"]["tools"] if tool["name"] == "live_agent_m4l_device")
    assert "arbitrary native UI" in m4l["description"]
    assert "jweb/jbrowser web UI" in m4l["description"]
    assert "wait_status" in m4l["description"]
    assert "compact_status" in m4l["description"]
    assert "compact_result" in m4l["description"]
    assert "web diag" in m4l["description"]
    assert "status_state_keys" in m4l["description"]
    cleanup = next(tool for tool in response["result"]["tools"] if tool["name"] == "live_agent_m4l_cleanup")
    assert "Dry-run" in cleanup["description"]
    assert "ask before delete" in cleanup["description"]
    clip_add = next(tool for tool in response["result"]["tools"] if tool["name"] == "live_clip_add_notes")
    assert "create a MIDI clip" in clip_add["description"]
    transport = next(tool for tool in response["result"]["tools"] if tool["name"] == "live_transport")
    assert "continue" in transport["description"]
    assert {"action", "time", "timeout", "strict_timeout"} <= set(transport["inputSchema"]["properties"])
    assert transport["inputSchema"]["properties"]["action"]["enum"] == ["play", "continue", "stop", "status"]
    tap = next(tool for tool in response["result"]["tools"] if tool["name"] == "live_agent_audio_tap")
    assert {"command", "path", "id", "udp"} <= set(tap["inputSchema"]["properties"])
    assert tap["inputSchema"]["required"] == ["command"]
    assert "stop" in tap["inputSchema"]["properties"]["command"]["enum"]
    assert "start with path" in tap["description"]
    assert "UDP optional" in tap["description"]
    tap_setup = next(tool for tool in response["result"]["tools"] if tool["name"] == "live_agent_audio_tap_setup")
    assert "solo target track" in tap_setup["description"]
    visual = next(tool for tool in response["result"]["tools"] if tool["name"] == "live_visual_capture")
    assert "Ableton Live window-only" in visual["description"]
    assert "device-detail crop/downscale" in visual["description"]
    assert "arbitrary apps/windows" in visual["description"]


def test_live_visual_capture_forwards_region_crop_and_size_args(monkeypatch):
    captured = {}

    def fake_capture(**kwargs):
        captured.update(kwargs)
        return {"ok": True, "path": "/tmp/live.png"}

    monkeypatch.setattr(server_module, "capture_ableton_window", fake_capture)
    server = make_server(FakeBridge())
    response = server.handle({
        "jsonrpc": "2.0",
        "id": 806,
        "method": "tools/call",
        "params": {"name": "live_visual_capture", "arguments": {
            "output_path": "/tmp/live.png",
            "title_contains": "vibe",
            "backend": "auto",
            "region": "device-detail",
            "crop": [0, 500, 1200, 300],
            "crop_relative_to_region": True,
            "bottom_fraction": 0.3,
            "max_width": 900,
            "max_height": 260,
        }},
    })

    assert response["result"]["structuredContent"]["ok"] is True
    assert captured == {
        "output_path": "/tmp/live.png",
        "title_contains": "vibe",
        "list_only": False,
        "backend": "auto",
        "region": "device-detail",
        "crop": [0, 500, 1200, 300],
        "crop_relative_to_region": True,
        "bottom_fraction": 0.3,
        "max_width": 900,
        "max_height": 260,
    }


def test_bridge_error_omits_traceback_by_default(monkeypatch):
    monkeypatch.delenv("ABLETON_MCP_TRACEBACK", raising=False)
    client = AbletonBridgeClient()
    message = {
        "jsonrpc": "2.0",
        "id": 1,
        "error": {"code": -32000, "message": "bad call", "data": "Traceback: noisy"},
    }
    monkeypatch.setattr(client, "_read_line", lambda _sock, _max_bytes, _deadline=None: json.dumps(message).encode("utf-8"))

    class FakeSocket:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def settimeout(self, _timeout):
            pass

        def sendall(self, _line):
            pass

    monkeypatch.setattr("socket.create_connection", lambda *_args, **_kwargs: FakeSocket())
    with pytest.raises(AbletonBridgeError) as exc:
        client.request("ping")
    assert "bad call" in str(exc.value)
    assert "Traceback" not in str(exc.value)


def test_bridge_client_defaults_can_be_configured_from_env(monkeypatch):
    monkeypatch.setenv("ABLETON_MCP_HOST", "127.0.0.2")
    monkeypatch.setenv("ABLETON_MCP_PORT", "9876")
    monkeypatch.setenv("ABLETON_MCP_TIMEOUT", "45")
    monkeypatch.setenv("ABLETON_MCP_CONNECT_TIMEOUT", "3")
    monkeypatch.setenv("ABLETON_MCP_MAX_RESPONSE_BYTES", "123456")

    config = AbletonBridgeClient().config

    assert config == BridgeConfig(
        host="127.0.0.2",
        port=9876,
        timeout=45.0,
        connect_timeout=3.0,
        idle_timeout=8.0,
        max_response_bytes=123456,
    )


def test_bridge_client_uses_short_connect_timeout(monkeypatch):
    connect_calls = []

    class FakeSocket:
        def __init__(self):
            self.responses = [b'{"jsonrpc":"2.0","id":1,"result":{"ok":true}}\n']

        def settimeout(self, _timeout):
            pass

        def sendall(self, _line):
            pass

        def recv(self, _size):
            return self.responses.pop(0)

        def close(self):
            pass

    def connect(address, timeout):
        connect_calls.append((address, timeout))
        return FakeSocket()

    monkeypatch.setattr("socket.create_connection", connect)
    client = AbletonBridgeClient(BridgeConfig(timeout=45.0, connect_timeout=1.5))

    assert client.request("ping") == {"ok": True}
    assert connect_calls == [(("127.0.0.1", 8765), 1.5)]


def test_bridge_client_response_timeout_is_not_retried(monkeypatch):
    client_sock, server_sock = socket.socketpair()
    connect_calls = []

    def connect(address, timeout):
        connect_calls.append((address, timeout))
        return client_sock

    monkeypatch.setattr("socket.create_connection", connect)
    client = AbletonBridgeClient(BridgeConfig(timeout=0.05, connect_timeout=0.01, idle_timeout=0.0))

    try:
        with pytest.raises(AbletonBridgeError) as exc:
            client.request("agent_m4l_device")
    finally:
        server_sock.close()
        client.close()

    assert "timed out after" in str(exc.value)
    assert "not retried automatically" in str(exc.value)
    assert len(connect_calls) == 1


def test_bridge_client_reuses_socket(monkeypatch):
    created = []

    class FakeSocket:
        def __init__(self):
            self.sent = []
            self.responses = [
                b'{"jsonrpc":"2.0","id":1,"result":{"ok":true}}\n',
                b'{"jsonrpc":"2.0","id":2,"result":{"ok":true}}\n',
            ]

        def settimeout(self, _timeout):
            pass

        def sendall(self, line):
            self.sent.append(line)

        def recv(self, _size):
            return self.responses.pop(0)

        def close(self):
            pass

    def connect(*_args, **_kwargs):
        sock = FakeSocket()
        if len(created) == 1:
            sock.responses = [b'{"jsonrpc":"2.0","id":3,"result":{"ok":true}}\n']
        created.append(sock)
        return sock

    monkeypatch.setattr("socket.create_connection", connect)
    client = AbletonBridgeClient()
    assert client.request("ping") == {"ok": True}
    assert client.request("ping") == {"ok": True}
    assert len(created) == 1
    assert len(created[0].sent) == 2


def test_bridge_client_expands_socket_timeout_for_long_live_request(monkeypatch):
    created = []

    class FakeSocket:
        def __init__(self):
            self.timeouts = []
            self.responses = [b'{"jsonrpc":"2.0","id":1,"result":{"ok":true}}\n']

        def settimeout(self, timeout):
            self.timeouts.append(timeout)

        def sendall(self, _line):
            pass

        def recv(self, _size):
            return self.responses.pop(0)

        def close(self):
            pass

    def connect(*_args, **_kwargs):
        sock = FakeSocket()
        if len(created) == 1:
            sock.responses = [b'{"jsonrpc":"2.0","id":3,"result":{"ok":true}}\n']
        created.append(sock)
        return sock

    monkeypatch.setattr("socket.create_connection", connect)
    client = AbletonBridgeClient(BridgeConfig(timeout=10.0))

    assert client.request("exec", {"code": "result = True", "timeout": 45.0}) == {"ok": True}
    assert created[0].timeouts[-1] == 46.0


def test_bridge_client_clamps_short_non_strict_timeout_for_socket(monkeypatch):
    created = []

    class FakeSocket:
        def __init__(self):
            self.timeouts = []
            self.responses = [b'{"jsonrpc":"2.0","id":1,"result":{"ok":true}}\n']

        def settimeout(self, timeout):
            self.timeouts.append(timeout)

        def sendall(self, _line):
            pass

        def recv(self, _size):
            return self.responses.pop(0)

        def close(self):
            pass

    def connect(*_args, **_kwargs):
        sock = FakeSocket()
        created.append(sock)
        return sock

    monkeypatch.setattr("socket.create_connection", connect)
    client = AbletonBridgeClient(BridgeConfig(timeout=5.0))

    assert effective_main_thread_timeout({"timeout": 10.0}) == 30.0
    assert effective_main_thread_timeout({"timeout": 10.0, "strict_timeout": True}) == 10.0
    assert client.request("transport", {"action": "play", "timeout": 10.0}) == {"ok": True}
    assert created[0].timeouts[-1] == 31.0


def test_bridge_client_honors_short_strict_timeout_for_socket(monkeypatch):
    created = []

    class FakeSocket:
        def __init__(self):
            self.timeouts = []
            self.responses = [b'{"jsonrpc":"2.0","id":1,"result":{"ok":true}}\n']

        def settimeout(self, timeout):
            self.timeouts.append(timeout)

        def sendall(self, _line):
            pass

        def recv(self, _size):
            return self.responses.pop(0)

        def close(self):
            pass

    def connect(*_args, **_kwargs):
        sock = FakeSocket()
        created.append(sock)
        return sock

    monkeypatch.setattr("socket.create_connection", connect)
    client = AbletonBridgeClient(BridgeConfig(timeout=30.0))

    assert client.request("ping", {"timeout": 3.0, "strict_timeout": True}) == {"ok": True}
    assert created[0].timeouts[-1] == 4.0


def test_bridge_client_keeps_bridge_status_timeout_short(monkeypatch):
    created = []

    class FakeSocket:
        def __init__(self):
            self.timeouts = []
            self.responses = [b'{"jsonrpc":"2.0","id":1,"result":{"ok":true}}\n']

        def settimeout(self, timeout):
            self.timeouts.append(timeout)

        def sendall(self, _line):
            pass

        def recv(self, _size):
            return self.responses.pop(0)

        def close(self):
            pass

    def connect(*_args, **_kwargs):
        sock = FakeSocket()
        created.append(sock)
        return sock

    monkeypatch.setattr("socket.create_connection", connect)
    client = AbletonBridgeClient(BridgeConfig(timeout=30.0))

    assert client.request("bridge_status", {"timeout": 2.0}) == {"ok": True}
    assert created[0].timeouts[-1] == 2.0


def test_bridge_client_stall_cooldown_after_sent_timeout(monkeypatch):
    created = []
    now = [100.0]

    class FakeSocket:
        def __init__(self):
            self.sent = []
            self.responses = [socket.timeout("timed out")]

        def settimeout(self, _timeout):
            pass

        def sendall(self, line):
            self.sent.append(line)

        def recv(self, _size):
            response = self.responses.pop(0)
            if isinstance(response, BaseException):
                raise response
            return response

        def close(self):
            pass

    def connect(*_args, **_kwargs):
        sock = FakeSocket()
        if len(created) == 1:
            sock.responses = [b'{"jsonrpc":"2.0","id":3,"result":{"ok":true}}\n']
        created.append(sock)
        return sock

    monkeypatch.setattr("socket.create_connection", connect)
    monkeypatch.setattr("bridge.time.monotonic", lambda: now[0])
    client = AbletonBridgeClient(BridgeConfig(timeout=0.01))

    with pytest.raises(AbletonBridgeError, match="client-side stall cooldown"):
        client.request("transport", {"action": "play", "timeout": 0.01, "strict_timeout": True})
    assert len(created) == 1

    with pytest.raises(AbletonBridgeError, match="refusing to send 'ping'"):
        client.request("ping")
    assert len(created) == 1

    assert client.request("bridge_status", {"timeout": 0.01}) == {"ok": True}
    assert len(created) == 2


def test_bridge_status_timeout_message_does_not_claim_cooldown(monkeypatch):
    created = []

    class FakeSocket:
        def settimeout(self, _timeout):
            pass

        def sendall(self, _line):
            pass

        def recv(self, _size):
            raise socket.timeout("timed out")

        def close(self):
            pass

    def connect(*_args, **_kwargs):
        sock = FakeSocket()
        created.append(sock)
        return sock

    monkeypatch.setattr("socket.create_connection", connect)
    client = AbletonBridgeClient(BridgeConfig(timeout=0.01))

    with pytest.raises(AbletonBridgeError) as err:
        client.request("bridge_status", {"timeout": 0.01})
    message = str(err.value)
    assert "bridge_status" in message
    assert "client-side stall cooldown" not in message


def test_bridge_client_force_probe_bypasses_stall_cooldown(monkeypatch):
    created = []
    now = [100.0]

    class FakeSocket:
        def __init__(self):
            self.responses = [socket.timeout("timed out")]

        def settimeout(self, _timeout):
            pass

        def sendall(self, _line):
            pass

        def recv(self, _size):
            response = self.responses.pop(0)
            if isinstance(response, BaseException):
                raise response
            return response

        def close(self):
            pass

    def connect(*_args, **_kwargs):
        sock = FakeSocket()
        if len(created) == 1:
            sock.responses = [b'{"jsonrpc":"2.0","id":2,"result":{"ok":true}}\n']
        created.append(sock)
        return sock

    monkeypatch.setattr("socket.create_connection", connect)
    monkeypatch.setattr("bridge.time.monotonic", lambda: now[0])
    client = AbletonBridgeClient(BridgeConfig(timeout=0.01))

    with pytest.raises(AbletonBridgeError, match="client-side stall cooldown"):
        client.request("transport", {"timeout": 0.01, "strict_timeout": True})
    assert client.request("ping", {"force_main_thread_probe": True}) == {"ok": True}
    assert len(created) == 2


def test_bridge_client_marks_stall_when_remote_reports_main_thread_timeout(monkeypatch):
    created = []
    now = [100.0]

    class FakeSocket:
        def __init__(self):
            self.responses = [b'{"jsonrpc":"2.0","id":1,"error":{"code":-32000,"message":"Timed out waiting for Live main thread during batch after 3s"}}\n']

        def settimeout(self, _timeout):
            pass

        def sendall(self, _line):
            pass

        def recv(self, _size):
            return self.responses.pop(0)

        def close(self):
            pass

    def connect(*_args, **_kwargs):
        sock = FakeSocket()
        created.append(sock)
        return sock

    monkeypatch.setattr("socket.create_connection", connect)
    monkeypatch.setattr("bridge.time.monotonic", lambda: now[0])
    client = AbletonBridgeClient(BridgeConfig(timeout=5.0))

    with pytest.raises(AbletonBridgeError, match="Timed out waiting for Live main thread"):
        client.request("batch")
    with pytest.raises(AbletonBridgeError, match="refusing to send 'ping'"):
        client.request("ping")
    assert len(created) == 1


def test_bridge_client_reconnects_before_remote_idle_timeout(monkeypatch):
    created = []
    now = [100.0]

    class FakeSocket:
        def __init__(self):
            self.responses = [b'{"jsonrpc":"2.0","id":1,"result":{"ok":true}}\n']
            self.closed = False

        def settimeout(self, _timeout):
            pass

        def sendall(self, _line):
            pass

        def recv(self, _size):
            return self.responses.pop(0)

        def close(self):
            self.closed = True

    def connect(*_args, **_kwargs):
        sock = FakeSocket()
        sock.responses = [b'{"jsonrpc":"2.0","id":%d,"result":{"ok":true}}\n' % (len(created) + 1)]
        created.append(sock)
        return sock

    monkeypatch.setattr("socket.create_connection", connect)
    monkeypatch.setattr("bridge.time.monotonic", lambda: now[0])
    client = AbletonBridgeClient(BridgeConfig(timeout=10.0, idle_timeout=8.0))

    assert client.request("ping") == {"ok": True}
    now[0] += 9.0
    assert client.request("ping") == {"ok": True}
    assert len(created) == 2
    assert created[0].closed is True


def test_bridge_client_discards_stale_idle_timeout_response(monkeypatch):
    created = []

    class FakeSocket:
        def __init__(self, response):
            self.response = response
            self.closed = False

        def settimeout(self, _timeout):
            pass

        def sendall(self, _line):
            pass

        def recv(self, _size):
            value = self.response
            self.response = b""
            return value

        def close(self):
            self.closed = True

    responses = [
        b'{"jsonrpc":"2.0","id":null,"error":{"code":-32000,"message":"timed out"}}\n',
        b'{"jsonrpc":"2.0","id":1,"result":{"ok":true}}\n',
    ]

    def connect(*_args, **_kwargs):
        sock = FakeSocket(responses.pop(0))
        created.append(sock)
        return sock

    monkeypatch.setattr("socket.create_connection", connect)
    client = AbletonBridgeClient(BridgeConfig(timeout=10.0, idle_timeout=0.0))

    assert client.request("ping") == {"ok": True}
    assert len(created) == 2
    assert created[0].closed is True


def test_bridge_client_rejects_oversized_response():
    class FakeSocket:
        def __init__(self):
            self.chunks = [b"x" * 5, b"x" * 5]

        def recv(self, _size):
            return self.chunks.pop(0) if self.chunks else b""

    with pytest.raises(OSError) as exc:
        AbletonBridgeClient._read_line(FakeSocket(), max_bytes=8)
    assert "response exceeds" in str(exc.value)


def test_remote_script_default_bridge_exists():
    root = Path(__file__).resolve().parents[1]
    canonical = root / "Ableton_Live_MCP" / "bridge.py"
    assert canonical.exists()
    assert "class AbletonLiveMCP" in canonical.read_text()


def test_remote_script_installer_copies_default_script(tmp_path):
    target = install_remote_script("Ableton_Live_MCP", tmp_path)
    assert target == tmp_path / "Ableton_Live_MCP"
    assert (target / "__init__.py").exists()
    assert (target / "bridge.py").exists()


def test_remote_script_installer_rejects_unknown_script(tmp_path):
    with pytest.raises(ValueError):
        install_remote_script("Unknown", tmp_path)


def test_remote_script_status_detects_stale_install(tmp_path):
    target = install_remote_script("Ableton_Live_MCP", tmp_path)
    current = remote_script_status(target_dir=tmp_path)
    assert current["installed"] is True
    assert current["current"] is True
    assert len(current["source_bridge_sha256"]) == 64
    assert current["target_bridge_sha256"] == current["source_bridge_sha256"]
    assert current["source_runtime_version"] == "transport-stop-settle-1"
    assert current["target_runtime_version"] == current["source_runtime_version"]
    assert len(current["source_runtime_code_sha256"]) == 64
    assert current["target_runtime_code_sha256"] == current["source_runtime_code_sha256"]
    assert current["missing"] == []
    assert current["mismatched"] == []

    (target / "bridge.py").write_text("# stale\n", encoding="utf-8")
    stale = remote_script_status(target_dir=tmp_path)
    assert stale["installed"] is True
    assert stale["current"] is False
    assert stale["mismatched"] == ["bridge.py"]
    assert stale["target_bridge_sha256"] != stale["source_bridge_sha256"]


def test_remote_script_installer_update_replaces_stale_install(tmp_path, capsys):
    target = install_remote_script("Ableton_Live_MCP", tmp_path)
    (target / "bridge.py").write_text("# stale\n", encoding="utf-8")

    assert install_remote_script_main(["--target-dir", str(tmp_path), "--update"]) == 0
    updated_output = capsys.readouterr()
    assert "Installed" in updated_output.out
    assert remote_script_status(target_dir=tmp_path)["current"] is True

    assert install_remote_script_main(["--target-dir", str(tmp_path), "--update"]) == 0
    current_output = capsys.readouterr()
    assert "already current" in current_output.out


def test_validate_can_check_remote_script_without_live(tmp_path, capsys):
    assert validate_main(["--skip-live", "--target-dir", str(tmp_path)]) == 1
    missing_output = capsys.readouterr()
    assert "missing or stale" in missing_output.err
    assert '"current": false' in missing_output.out

    install_remote_script("Ableton_Live_MCP", tmp_path)
    assert validate_main(["--skip-live", "--target-dir", str(tmp_path)]) == 0
    current_output = capsys.readouterr()
    assert '"current": true' in current_output.out


def test_validate_checks_agent_m4l_host_companion_js(tmp_path, monkeypatch, capsys):
    source = tmp_path / "source" / "agent_m4l_host.js"
    source.parent.mkdir()
    source.write_text("current host\n", encoding="utf-8")
    generated = tmp_path / "generated"
    generated.mkdir()
    (generated / "AgentM4L_audio_effect_Test.amxd").write_text("device", encoding="utf-8")
    (generated / "agent_m4l_host.js").write_text("stale host\n", encoding="utf-8")
    install = tmp_path / "install" / "audio"
    install.mkdir(parents=True)
    (install / "AgentM4L_audio_effect_Test.amxd").write_text("device", encoding="utf-8")

    monkeypatch.setattr(validate.agent_m4l, "HOST_JS", source)
    monkeypatch.setattr(validate.agent_m4l, "GENERATED_DIR", generated)
    monkeypatch.setattr(validate.agent_m4l, "ROLE_PRESETS", {"audio_effect": {}})
    monkeypatch.setattr(validate.agent_m4l, "install_folder", lambda _role: install)

    status = validate.agent_m4l.agent_m4l_host_status()
    assert status["current"] is False
    assert status["stale"] == [str(generated / "agent_m4l_host.js")]
    assert status["missing"] == [str(install / "agent_m4l_host.js")]

    install_remote_script("Ableton_Live_MCP", tmp_path / "remote")
    assert validate_main(["--skip-live", "--target-dir", str(tmp_path / "remote")]) == 1
    failed = capsys.readouterr()
    assert "host files are missing or stale" in failed.err

    (generated / "agent_m4l_host.js").write_text("current host\n", encoding="utf-8")
    (install / "agent_m4l_host.js").write_text("current host\n", encoding="utf-8")
    assert validate_main(["--skip-live", "--target-dir", str(tmp_path / "remote")]) == 0
    passed = capsys.readouterr()
    assert '"m4l_host"' in passed.out
    assert '"current": true' in passed.out


def test_agent_m4l_host_defers_web_message_driven_reload_teardown():
    source = validate.agent_m4l.HOST_JS.read_text(encoding="utf-8")

    assert "var webMessageDepth = 0;" in source
    assert "function deferCommandPoll()" in source
    assert "function deferRawCommand(raw)" in source
    assert "function handleDeferredCommandTask()" in source
    assert "if (webMessageDepth > 0) {\n        deferCommandPoll();" in source
    assert "if (webMessageDepth > 0) {\n        deferRawCommand(raw);" in source
    assert "hasRemovableWebObjects(preserveWebIds)" in source
    assert "web_clear_deferred" in source
    assert "payload.host_runtime_version" in source
    assert "function readPendingWebUis() {\n    if (webMessageDepth > 0) {" in source
    assert "beginWebMessage();" in source
    assert "this.patcher.remove(dynamicObjects[i]);" in source


def test_visual_capture_dependency_status_classifies_platform_requirements():
    found = {"PIL.Image", "Quartz"}
    status = validate.visual_capture_dependency_status("Darwin", lambda module: object() if module in found else None)

    assert status["ok"] is True
    assert status["supported_platform"] is True
    assert {item["module"] for item in status["dependencies"]} == {"PIL.Image", "Quartz"}

    missing = validate.visual_capture_dependency_status("Windows", lambda module: object() if module == "PIL.Image" else None)
    assert missing["ok"] is False
    assert missing["missing"] == ["windows-capture"]
    assert ".[visual]" in missing["next_action"]

    unsupported = validate.visual_capture_dependency_status("Linux", lambda _module: object())
    assert unsupported["ok"] is False
    assert unsupported["supported_platform"] is False


def test_validate_requires_visual_capture_dependencies_for_default_setup(tmp_path, monkeypatch, capsys):
    install_remote_script("Ableton_Live_MCP", tmp_path)
    monkeypatch.setattr(validate, "visual_capture_dependency_status", lambda: {
        "ok": False,
        "missing": ["Pillow"],
        "next_action": 'Install visual capture dependencies with python -m pip install -e ".[visual]" or ".[dev]".',
    })

    assert validate_main(["--skip-live", "--target-dir", str(tmp_path)]) == 1
    failed = capsys.readouterr()
    assert "visual capture dependencies are unavailable" in failed.err
    assert '"visual_capture"' in failed.out

    assert validate_main(["--skip-live", "--target-dir", str(tmp_path), "--allow-missing-visual-capture"]) == 0
    capsys.readouterr()


def test_validate_rejects_running_stale_remote_script(tmp_path, monkeypatch, capsys):
    missing = remote_script_status(target_dir=tmp_path)
    assert missing["installed"] is False
    install_remote_script("Ableton_Live_MCP", tmp_path)
    status = remote_script_status(target_dir=tmp_path)

    class FakeClient:
        def request(self, method, _params):
            assert method == "batch"
            return [
                {"ok": True, "result": {"ok": True, "remote_script": {
                    "bridge_sha256": "0" * 64,
                    "runtime_version": status["source_runtime_version"],
                    "runtime_code_sha256": status["source_runtime_code_sha256"],
                }}},
                {"ok": True, "result": {"ok": True}},
                {"ok": True, "result": 12},
            ]

    monkeypatch.setattr(validate, "AbletonBridgeClient", FakeClient)

    assert validate_main(["--target-dir", str(tmp_path)]) == 1
    output = capsys.readouterr()
    assert "running Remote Script is stale" in output.err
    assert "reload the Ableton_Live_MCP Control Surface" in output.err
    assert '"installed_files_current": true' in output.out
    assert '"runtime_current": false' in output.out
    assert '"live_mutations_safe": false' in output.out
    assert '"loaded_runtime_state": "loaded_code_mismatch"' in output.out
    assert '"runtime_mismatch": "bridge_hash_mismatch"' in output.out
    assert '"runtime_reload_required": true' in output.out
    assert "Reload the Ableton_Live_MCP Control Surface" in output.out


def test_validate_rejects_running_remote_script_without_runtime_version(tmp_path, monkeypatch, capsys):
    install_remote_script("Ableton_Live_MCP", tmp_path)
    status = remote_script_status(target_dir=tmp_path)

    class FakeClient:
        def request(self, method, _params):
            assert method == "batch"
            return [
                {"ok": True, "result": {"ok": True, "remote_script": {"bridge_sha256": status["source_bridge_sha256"]}}},
                {"ok": True, "result": {"ok": True}},
                {"ok": True, "result": 12},
            ]

    monkeypatch.setattr(validate, "AbletonBridgeClient", FakeClient)

    assert validate_main(["--target-dir", str(tmp_path)]) == 1
    output = capsys.readouterr()
    assert '"runtime_current": false' in output.out
    assert '"live_mutations_safe": false' in output.out
    assert '"loaded_runtime_state": "loaded_code_stale_or_unverified"' in output.out
    assert '"runtime_mismatch": "missing_runtime_version"' in output.out
    assert '"runtime_reload_required": true' in output.out


def test_validate_rejects_running_remote_script_without_runtime_code_hash(tmp_path, monkeypatch, capsys):
    install_remote_script("Ableton_Live_MCP", tmp_path)
    status = remote_script_status(target_dir=tmp_path)

    class FakeClient:
        def request(self, method, _params):
            assert method == "batch"
            return [
                {"ok": True, "result": {"ok": True, "remote_script": {
                    "bridge_sha256": status["source_bridge_sha256"],
                    "runtime_version": status["source_runtime_version"],
                }}},
                {"ok": True, "result": {"ok": True}},
                {"ok": True, "result": 12},
            ]

    monkeypatch.setattr(validate, "AbletonBridgeClient", FakeClient)

    assert validate_main(["--target-dir", str(tmp_path)]) == 1
    output = capsys.readouterr()
    assert '"runtime_current": false' in output.out
    assert '"live_mutations_safe": false' in output.out
    assert '"loaded_runtime_state": "loaded_code_stale_or_unverified"' in output.out
    assert '"runtime_mismatch": "missing_runtime_code_hash"' in output.out
    assert '"runtime_reload_required": true' in output.out


def test_validate_rejects_running_remote_script_code_hash_mismatch(tmp_path, monkeypatch, capsys):
    install_remote_script("Ableton_Live_MCP", tmp_path)
    status = remote_script_status(target_dir=tmp_path)

    class FakeClient:
        def request(self, method, _params):
            assert method == "batch"
            return [
                {"ok": True, "result": {"ok": True, "remote_script": {
                    "bridge_sha256": status["source_bridge_sha256"],
                    "runtime_version": status["source_runtime_version"],
                    "runtime_code_sha256": "0" * 64,
                }}},
                {"ok": True, "result": {"ok": True}},
                {"ok": True, "result": 12},
            ]

    monkeypatch.setattr(validate, "AbletonBridgeClient", FakeClient)

    assert validate_main(["--target-dir", str(tmp_path)]) == 1
    output = capsys.readouterr()
    assert '"runtime_current": false' in output.out
    assert '"runtime_mismatch": "runtime_code_hash_mismatch"' in output.out
    assert '"runtime_reload_required": true' in output.out


def test_validate_live_checks_are_compact_and_timed(tmp_path, monkeypatch, capsys):
    install_remote_script("Ableton_Live_MCP", tmp_path)
    status = remote_script_status(target_dir=tmp_path)
    calls = []

    class FakeClient:
        def request(self, method, params):
            calls.append((method, params))
            assert method == "batch"
            return [
                {"ok": True, "result": {"ok": True, "remote_script": {
                    "bridge_sha256": status["source_bridge_sha256"],
                    "runtime_version": status["source_runtime_version"],
                    "runtime_code_sha256": status["source_runtime_code_sha256"],
                }}},
                {"ok": True, "result": {"ok": True}},
                {"ok": True, "result": 12},
            ]

    monkeypatch.setattr(validate, "AbletonBridgeClient", FakeClient)

    assert validate_main(["--target-dir", str(tmp_path)]) == 0
    output = capsys.readouterr()
    assert '"runtime_current": true' in output.out
    assert '"live_mutations_safe": true' in output.out
    assert '"loaded_runtime_state": "current"' in output.out
    assert len(calls) == 1
    assert calls[0][0] == "batch"
    assert calls[0][1]["timeout"] == 45.0
    operations = calls[0][1]["operations"]
    assert operations[0] == {"method": "ping", "params": {"timeout": 45.0}}
    assert operations[1] == {"method": "get", "params": {
        "ref": {"path": "live_set"},
        "properties": ["tempo", "signature_numerator", "signature_denominator"],
        "timeout": 45.0,
    }}
    assert operations[2]["method"] == "eval"
    assert operations[2]["params"]["timeout"] == 45.0
    assert operations[3]["method"] == "exec"
    assert "_runtime_code_fingerprint" in operations[3]["params"]["code"]
    assert operations[3]["params"]["timeout"] == 45.0


def test_validate_compares_runtime_code_with_live_compiled_hash(tmp_path, monkeypatch, capsys):
    install_remote_script("Ableton_Live_MCP", tmp_path)
    status = remote_script_status(target_dir=tmp_path)
    live_hash = "1" * 64

    class FakeClient:
        def request(self, method, params):
            assert method == "batch"
            assert params["operations"][3]["method"] == "exec"
            return [
                {"ok": True, "result": {"ok": True, "remote_script": {
                    "bridge_sha256": status["source_bridge_sha256"],
                    "runtime_version": status["source_runtime_version"],
                    "runtime_code_sha256": live_hash,
                }}},
                {"ok": True, "result": {"ok": True}},
                {"ok": True, "result": 12},
                {"ok": True, "result": {"runtime_code_sha256": live_hash}},
            ]

    monkeypatch.setattr(validate, "AbletonBridgeClient", FakeClient)

    assert validate_main(["--target-dir", str(tmp_path)]) == 0
    output = capsys.readouterr()
    assert '"runtime_current": true' in output.out
    assert '"live_mutations_safe": true' in output.out
    assert '"live_compiled_runtime_code_sha256": "1111111111111111111111111111111111111111111111111111111111111111"' in output.out


def test_validate_live_checks_can_use_strict_short_timeout(tmp_path, monkeypatch, capsys):
    install_remote_script("Ableton_Live_MCP", tmp_path)
    status = remote_script_status(target_dir=tmp_path)
    calls = []

    class FakeClient:
        def request(self, method, params):
            calls.append((method, params))
            return [
                {"ok": True, "result": {"ok": True, "remote_script": {
                    "bridge_sha256": status["source_bridge_sha256"],
                    "runtime_version": status["source_runtime_version"],
                    "runtime_code_sha256": status["source_runtime_code_sha256"],
                }}},
                {"ok": True, "result": {"ok": True}},
                {"ok": True, "result": 12},
            ]

    monkeypatch.setattr(validate, "AbletonBridgeClient", FakeClient)

    assert validate_main(["--target-dir", str(tmp_path), "--timeout", "3", "--strict-timeout"]) == 0
    capsys.readouterr()
    assert calls[0][1]["timeout"] == 3.0
    assert calls[0][1]["strict_timeout"] is True
    for operation in calls[0][1]["operations"]:
        assert operation["params"]["timeout"] == 3.0
        assert operation["params"]["strict_timeout"] is True


def test_validate_live_failure_prints_structured_diagnostics(tmp_path, monkeypatch, capsys):
    install_remote_script("Ableton_Live_MCP", tmp_path)

    class FakeClient:
        def request(self, method, _params):
            if method == "bridge_status":
                raise AbletonBridgeError("bridge_status unavailable")
            raise AbletonBridgeError("bridge timed out")

    monkeypatch.setattr(validate, "AbletonBridgeClient", FakeClient)

    assert validate_main(["--target-dir", str(tmp_path), "--timeout", "3", "--strict-timeout"]) == 1
    output = capsys.readouterr()
    assert "bridge timed out" in output.err
    assert '"current": true' in output.out
    assert '"live_error": "bridge timed out"' in output.out
    assert '"live_failure_type": "bridge_response_timeout"' in output.out
    assert '"runtime_current": false' in output.out
    assert '"runtime_mismatch": "bridge_response_timeout"' in output.out
    assert "modal" in output.out


def test_validate_classifies_bridge_status_timeout_as_unresponsive(tmp_path, monkeypatch, capsys):
    install_remote_script("Ableton_Live_MCP", tmp_path)

    class FakeClient:
        def request(self, method, _params):
            if method == "bridge_status":
                raise AbletonBridgeError("Ableton bridge request 'bridge_status' timed out after 2s")
            raise AbletonBridgeError("Ableton bridge request 'batch' timed out after 4s")

    monkeypatch.setattr(validate, "AbletonBridgeClient", FakeClient)

    assert validate_main(["--target-dir", str(tmp_path), "--timeout", "3", "--strict-timeout"]) == 1
    output = capsys.readouterr()
    assert '"live_failure_type": "bridge_unresponsive"' in output.out
    assert '"runtime_mismatch": "bridge_unresponsive"' in output.out
    assert '"bridge_status": {' in output.out
    assert '"ok": false' in output.out
    assert "stop retrying Live API calls" in output.out


def test_validate_classifies_live_main_thread_timeout(tmp_path, monkeypatch, capsys):
    install_remote_script("Ableton_Live_MCP", tmp_path)

    class FakeClient:
        def request(self, method, _params):
            if method == "bridge_status":
                raise AbletonBridgeError("bridge_status unavailable")
            raise AbletonBridgeError("-32000 Timed out waiting for Live main thread")

    monkeypatch.setattr(validate, "AbletonBridgeClient", FakeClient)

    assert validate_main(["--target-dir", str(tmp_path), "--timeout", "3", "--strict-timeout"]) == 1
    output = capsys.readouterr()
    assert '"live_failure_type": "live_main_thread_timeout"' in output.out
    assert '"runtime_mismatch": "live_main_thread_timeout"' in output.out
    assert "modal dialogs" in output.out
    assert "before sending more mutations" in output.out


def test_validate_classifies_main_thread_timeout_with_status_timeout_as_process_unresponsive(tmp_path, monkeypatch, capsys):
    install_remote_script("Ableton_Live_MCP", tmp_path)

    class FakeClient:
        def request(self, method, _params):
            if method == "bridge_status":
                raise AbletonBridgeError("Ableton bridge request 'bridge_status' timed out after 2s")
            raise AbletonBridgeError("-32000 Timed out waiting for Live main thread during batch after 3s")

    monkeypatch.setattr(validate, "AbletonBridgeClient", FakeClient)

    assert validate_main(["--target-dir", str(tmp_path), "--timeout", "3", "--strict-timeout"]) == 1
    output = capsys.readouterr()
    assert '"live_failure_type": "live_process_unresponsive"' in output.out
    assert '"runtime_mismatch": "live_process_unresponsive"' in output.out
    assert "socket-thread bridge_status probe also timed out" in output.out
    assert "OS process sample" in output.out


def test_validate_classifies_live_main_thread_hung_when_socket_thread_responds(tmp_path, monkeypatch, capsys):
    install_remote_script("Ableton_Live_MCP", tmp_path)

    class FakeClient:
        def request(self, method, _params):
            if method == "bridge_status":
                return {"ok": True, "server_thread_responsive": True, "main_thread": {"timeouts": 1}}
            raise AbletonBridgeError("-32000 Timed out waiting for Live main thread during batch after 3s")

    monkeypatch.setattr(validate, "AbletonBridgeClient", FakeClient)

    assert validate_main(["--target-dir", str(tmp_path), "--timeout", "3", "--strict-timeout"]) == 1
    output = capsys.readouterr()
    assert '"bridge_status": {' in output.out
    assert '"live_failure_type": "live_main_thread_hung"' in output.out
    assert '"runtime_mismatch": "live_main_thread_hung"' in output.out
    assert "socket thread is responsive" in output.out
    assert "restart Ableton Live" in output.out


def test_validate_classifies_client_stall_cooldown_as_live_hung_when_status_responds(tmp_path, monkeypatch, capsys):
    install_remote_script("Ableton_Live_MCP", tmp_path)

    class FakeClient:
        def request(self, method, _params):
            if method == "bridge_status":
                return {"ok": True, "server_thread_responsive": True, "main_thread": {"timeouts": 1}}
            raise AbletonBridgeError("Ableton bridge client is in stall cooldown after 'batch' timed out; refusing to send 'ping' for 9.8s")

    monkeypatch.setattr(validate, "AbletonBridgeClient", FakeClient)

    assert validate_main(["--target-dir", str(tmp_path), "--timeout", "3", "--strict-timeout"]) == 1
    output = capsys.readouterr()
    assert '"live_failure_type": "live_main_thread_hung"' in output.out
    assert "protective stall cooldown" in output.out
    assert "Stop sending Live API mutations" in output.out


def test_validate_classifies_bridge_not_listening(tmp_path, monkeypatch, capsys):
    install_remote_script("Ableton_Live_MCP", tmp_path)

    class FakeClient:
        def request(self, _method, _params):
            raise AbletonBridgeError("Could not connect to Ableton bridge at 127.0.0.1:8765: [Errno 61] Connection refused")

    monkeypatch.setattr(validate, "AbletonBridgeClient", FakeClient)

    assert validate_main(["--target-dir", str(tmp_path), "--timeout", "3", "--strict-timeout"]) == 1
    output = capsys.readouterr()
    assert '"live_failure_type": "bridge_not_listening"' in output.out
    assert '"runtime_mismatch": "bridge_not_listening"' in output.out
    assert "bridge is not listening" in output.out


def test_remote_script_resources_available_from_source_checkout():
    root = remote_script_root()
    assert (root / "Ableton_Live_MCP" / "bridge.py").exists()


def test_debug_commands_are_not_published_console_scripts():
    pyproject = (Path(__file__).resolve().parents[1] / "pyproject.toml").read_text()
    assert 'version = "0.1.1"' in pyproject
    assert 'ableton-live-mcp = "server:main"' in pyproject
    assert 'ableton-live-mcp-validate = "validate:main"' in pyproject
    assert 'ableton-live-mcp-install-remote-script = "install_remote_script:main"' in pyproject
    assert 'ableton-live-mcp-sync-m4l-host = "agent_m4l:sync_host_main"' in pyproject
    assert 'ableton-live-mcp-capture-window = "visual_capture:main"' in pyproject
    assert "ableton-live-mcp-smoke =" not in pyproject
    assert "ableton-live-mcp-benchmark =" not in pyproject
    assert "ableton-live-mcp-prompt-audit =" not in pyproject


def test_dev_extra_includes_visual_capture_dependencies():
    pyproject = (Path(__file__).resolve().parents[1] / "pyproject.toml").read_text()
    dev_block = pyproject.split("dev = [", 1)[1].split("]\n", 1)[0]

    assert '"pytest>=8.0"' in dev_block
    assert '"Pillow>=10"' in dev_block
    assert '"pyobjc-framework-Quartz; platform_system == \'Darwin\'"' in dev_block
    assert '"windows-capture; platform_system == \'Windows\'"' in dev_block


def test_smoke_suite_runs_expected_bridge_methods():
    class SmokeBridge:
        def __init__(self):
            self.calls = []

        def request(self, method, params):
            self.calls.append((method, params))
            return {"method": method, "params": params}

    bridge = SmokeBridge()
    code, output = run_smoke(bridge)
    methods = [method for method, _params in bridge.calls]
    assert code == 0
    assert output["ok"] is True
    assert {"ping", "get", "children", "eval", "exec", "batch", "agent_m4l_device", "browser_roots", "browser_search", "observe", "events"} <= set(methods)
    agent_m4l_calls = [params for method, params in bridge.calls if method == "agent_m4l_device"]
    assert agent_m4l_calls[0]["load"] is False
    assert agent_m4l_calls[0]["udp"] is False
    assert agent_m4l_calls[0]["patch"]["device_height"] == 130


def test_smoke_suite_treats_plugin_search_as_optional():
    class SmokeBridge:
        def request(self, method, params):
            if method == "browser_search" and params.get("roots") == ["plugins"]:
                raise RuntimeError("plugins unavailable")
            return {"method": method, "params": params}

    code, output = run_smoke(SmokeBridge())
    assert code == 0
    assert output["ok"] is True
    failed = [check["name"] for check in output["checks"] if not check["ok"]]
    assert failed == ["browser_plugin_search"]


def test_core_regression_exercises_existing_non_m4l_surfaces(tmp_path):
    class RegressionBridge:
        def __init__(self):
            self.calls = []

        def request(self, method, params):
            self.calls.append((method, params))
            if method == "ping":
                return {"ok": True}
            if method == "exec":
                return {"midi_path": "live_set tracks 0", "audio_path": "live_set tracks 1"}
            if method == "clip_notes":
                return {"notes": [{"pitch": 60}, {"pitch": 64}, {"pitch": 67}, {"pitch": 72}]}
            if method == "browser_search":
                return {"results": [{"id": 123, "name": "Limiter", "is_loadable": True}]}
            if method == "set_summary":
                return {"tracks": [
                    {"name": "MCP Regression MIDI Clip", "clips": [{"name": "MCP Regression MIDI Clip"}]},
                    {
                        "name": "MCP Regression Audio Device Clip",
                        "devices": [{"name": "Limiter"}],
                        "arrangement_clips": [{"name": "MCP Regression Audio File"}],
                    },
                ]}
            return {"ok": True}

    def similar(_params):
        return {
            "database": "fake.db",
            "base": {"name": "Kick"},
            "results": [{"name": "Similar Kick"}],
        }

    audio_file = tmp_path / "tone.wav"
    audio_file.write_bytes(b"fake")
    bridge = RegressionBridge()
    code, output = run_core_regression(bridge, similar_finder=similar, audio_file=audio_file)
    methods = [method for method, _params in bridge.calls]

    assert code == 0
    assert output["ok"] is True
    assert output["destructive"] is True
    assert all("result" not in check for check in output["checks"] if check["ok"])
    assert {"browser_search", "browser_load", "clip_add_notes", "clip_notes", "track_create_audio_clip", "set_summary"} <= set(methods)
    assert [check["name"] for check in output["checks"] if check["name"] == "find_similar_sounds"]
    assert next(check for check in output["checks"] if check["name"] == "read_back_midi_clip_notes")["note_count"] == 4
    audio_call = next(params for method, params in bridge.calls if method == "track_create_audio_clip")
    assert audio_call["file_path"] == str(audio_file)


def test_generated_m4l_regression_exercises_three_roles():
    class RegressionBridge:
        def __init__(self):
            self.calls = []

        def request(self, method, params):
            self.calls.append((method, params))
            if method == "ping":
                return {"ok": True, "remote_script": {"runtime_version": "test"}}
            if method == "exec":
                return {"instrument_path": "live_set tracks 0", "midi_path": "live_set tracks 1"}
            if method == "clip_add_notes":
                return {"added": len(params.get("notes") or []), "note_api": "extended", "launched": bool(params.get("fire"))}
            if method == "transport":
                return {"playing": True}
            if method == "get":
                return {"properties": {"output_meter_level": 0.5, "playing_slot_index": 0}}
            raise AssertionError(method)

    class RegressionMcp:
        def __init__(self):
            self.calls = []

        def handle(self, request):
            args = request["params"]["arguments"]
            self.calls.append(args)
            command = args.get("command") or ("update" if args.get("patch") else "status")
            role = args.get("role", "instrument")
            instance = args.get("instance_id", "Device").replace(" ", "_")
            event = "webui_reload" if command == "web_reload" else ("reload" if command == "update" else command)
            state = {
                "level_meter": 0.25,
                "pitch_value": 60,
                "input_pitch": 60,
                "web_title": "Smoke",
                "web_read_pending": 0,
            }
            content = {
                "role": role,
                "instance_id": instance,
                "command": command,
                "command_file": "/tmp/%s.json" % instance,
                "status_file": "/tmp/%s_status.json" % instance,
                "loaded": command == "update",
                "status": {
                    "event": event,
                    "host_runtime_version": "web-reload-1",
                    "device_width": 760,
                    "device_height": 170,
                    "state": state,
                },
            }
            if role == "instrument":
                content["webui"] = {"webuis": [{"id": "smoke_scope", "html_path": "/tmp/smoke/index.html", "url": "file:///tmp/smoke/index.html"}]}
            return {"result": {"structuredContent": content}}

    bridge = RegressionBridge()
    mcp = RegressionMcp()
    code, output = run_generated_m4l_regression(bridge, mcp=mcp, settle_seconds=0)
    methods = [method for method, _params in bridge.calls]
    roles = {call.get("role") for call in mcp.calls if call.get("command") != "status"}

    assert code == 0
    assert output["ok"] is True
    assert output["destructive"] is True
    assert {"instrument", "audio_effect", "midi_effect"} <= roles
    assert any(call.get("command") == "web_reload" for call in mcp.calls)
    assert {"ping", "exec", "clip_add_notes", "transport", "get"} <= set(methods)


def test_smoke_core_regression_cli_requires_yes(monkeypatch, capsys):
    monkeypatch.setenv("ABLETON_LIVE_MCP_DEBUG", "1")
    monkeypatch.setattr("sys.argv", ["ableton-live-mcp-smoke", "--core-regression"])

    assert smoke_main() == 2
    output = capsys.readouterr()
    assert "Refusing to run destructive smoke regression without --yes" in output.err


def test_benchmark_records_latency_and_payload_size():
    class BenchBridge:
        def __init__(self):
            self.calls = []

        def request(self, method, params):
            self.calls.append((method, params))
            return {"method": method, "params": params}

    bridge = BenchBridge()
    code, output = run_benchmark(bridge, iterations=1, include_browser=False)
    methods = [method for method, _params in bridge.calls]
    assert code == 0
    assert output["ok"] is True
    assert output["summary"]["max_median_ms"] is not None
    assert output["summary"]["max_median_bytes"] > 0
    assert "agent_m4l_device" in methods
    agent_m4l_calls = [params for method, params in bridge.calls if method == "agent_m4l_device"]
    assert agent_m4l_calls[0]["load"] is False
    assert agent_m4l_calls[0]["udp"] is False
    assert agent_m4l_calls[0]["patch"]["device_height"] == 130


def test_benchmark_skips_optional_failures():
    class BenchBridge:
        def request(self, method, params):
            if method == "device_parameters":
                raise RuntimeError("no devices")
            return {"method": method, "params": params}

    code, output = run_benchmark(BenchBridge(), iterations=1, include_browser=False)
    assert code == 0
    assert output["ok"] is True
    assert output["summary"]["skipped"] == 1
    skipped = [check for check in output["checks"] if check.get("skipped")]
    assert skipped[0]["name"] == "device_parameter_filter"


def test_prompt_audit_runs_expected_bridge_methods(monkeypatch, tmp_path):
    import agent_m4l

    monkeypatch.setattr(agent_m4l, "WEBUI_DIR", tmp_path / "webui")

    class PromptBridge:
        def __init__(self):
            self.calls = []

        def request(self, method, params):
            self.calls.append((method, params))
            if method == "browser_search" and params.get("query") == "cowbell":
                return {"results": [{"id": 101, "name": "Cowbell.wav"}]}
            if method == "browser_search":
                return {"results": [{"id": 202, "name": "Plugin"}]}
            if method == "batch":
                return [{"ok": True, "result": {"results": [{"id": 501}]}}, {"ok": True, "result": {"results": []}}, {"ok": True, "result": {"results": []}}]
            if method == "exec" and "track_paths" in params.get("code", ""):
                return {"track_paths": {"Audit Drums": "live_set tracks 2"}}
            if method == "exec" and "track_path" in params.get("code", ""):
                return {"track_path": "live_set tracks 1", "track": "Audit Library Sample"}
            if method == "exec":
                return {"ok": True}
            if method == "agent_m4l_device":
                return {"command": params.get("command"), "command_file_written": True, "loaded": False}
            if method == "agent_m4l_cleanup":
                return {"delete": params.get("delete"), "matched_count": 0, "deleted_count": 0}
            if method == "set_summary":
                return {"tracks": [
                    {"index": 0, "name": "Audit Automation", "clips": [{"id": 201, "name": "MCP Prompt Audit Automation"}], "arrangement_clips": []},
                    {"index": 1, "name": "Audit Existing MIDI", "arrangement_clips": [{"id": 301, "name": "MCP Prompt Audit Existing"}]},
                    {"arrangement_clips": [{"id": 401, "name": "MCP Prompt Audit Warp"}]},
                ]}
            if method == "get":
                return {"id": 601, "properties": {"name": "Track Volume", "value": 0.85}}
            if method == "clip_notes":
                return {"notes": [{"note_id": 1, "velocity": 64.0}]}
            if method in {"clip_add_notes", "clip_duplicate_to_arrangement", "clip_update_notes", "clip_warp_markers", "clip_envelope", "browser_load", "track_create_audio_clip"}:
                return {"ok": True}
            raise AssertionError(method)

    bridge = PromptBridge()
    code, output = run_prompt_audit(bridge)
    methods = [method for method, _params in bridge.calls]
    audit = [check for check in output["checks"] if check["name"] == "generated_m4l_creative_devices"][0]
    assert code == 0
    assert output["ok"] is True
    assert output["destructive"] is True
    assert sum(1 for call in audit["calls"] if call["method"] == "local_m4l_preflight") == 3
    assert {"batch", "exec", "set_summary", "get", "clip_notes", "clip_add_notes", "clip_duplicate_to_arrangement", "clip_update_notes", "clip_envelope", "clip_warp_markers", "track_create_audio_clip", "browser_search", "browser_load", "agent_m4l_device", "agent_m4l_cleanup"} <= set(methods)
    cleanup_calls = [params for method, params in bridge.calls if method == "agent_m4l_cleanup"]
    assert cleanup_calls == [{"delete": False, "name_prefix": "AgentM4L_", "limit": 32}]
    agent_m4l_calls = [params for method, params in bridge.calls if method == "agent_m4l_device"]
    assert len(agent_m4l_calls) == 4
    assert {call["role"] for call in agent_m4l_calls[:3]} == {"audio_effect", "midi_effect", "instrument"}
    assert all(call["load"] is False and call["udp"] is False for call in agent_m4l_calls)
    assert agent_m4l_calls[0]["patch"]["webuis"][0]["object"] == "jbrowser~"
    assert agent_m4l_calls[0]["patch"]["webuis"][0]["html_path"].endswith("index.html")
    assert "html" not in agent_m4l_calls[0]["patch"]["webuis"][0]
    assert agent_m4l_calls[0]["patch"]["webuis"][0]["assets"]["relative_paths"] == ["assets/three.module.js", "scene/field.json"]
    assert agent_m4l_calls[0]["patch"]["objects"][3]["text"].startswith("pictslider")
    assert agent_m4l_calls[1]["patch"]["objects"][1]["text"] == "kslider"
    assert agent_m4l_calls[2]["patch"]["webui"]["id"] == "glass_scene"
    assert agent_m4l_calls[2]["patch"]["webui"]["js_path"].endswith("device.js")
    assert agent_m4l_calls[3]["command"] == "set"
    assert agent_m4l_calls[3]["values"][0]["value"] == [1, 0, 1, 1, 0, 1, 0, 1]
    assert agent_m4l_calls[3]["values"][1]["value"]["pressure"] == 0.44
    js = Path(agent_m4l_calls[0]["patch"]["webuis"][0]["js_path"]).read_text(encoding="utf-8")
    html = Path(agent_m4l_calls[0]["patch"]["webuis"][0]["html_path"]).read_text(encoding="utf-8")
    assert "agentm4lstate" in js
    assert "set_many_silent" in js
    assert 'import * as THREE from "./assets/three.module.js"' in js
    assert "three_ready" in js
    assert 'type="module"' in html


def test_prompt_audit_can_preflight_generated_m4l_creativity_without_live():
    code, output = run_generated_m4l_local_preflight()
    audit = output["checks"][0]
    assert code == 0
    assert output["ok"] is True
    assert output["destructive"] is False
    assert audit["name"] == "generated_m4l_creative_devices_local_preflight"
    assert sum(1 for call in audit["calls"] if call["method"] == "local_m4l_preflight") == 3
    assert audit["max_call_bytes"] < 5000
