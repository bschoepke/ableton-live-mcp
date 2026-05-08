from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from benchmark import run_benchmark
from bridge import AbletonBridgeClient, AbletonBridgeError, BridgeConfig
from install_remote_script import install_remote_script, remote_script_root
from prompt_audit import run_prompt_audit
from server import expected_agent_m4l_status_event, make_server, should_build_agent_m4l, wait_agent_m4l_status
from similar_sounds import encode_feature
from smoke import run_smoke


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
        "live_agent_m4l_device",
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


def test_initialize_includes_general_model_instructions():
    server = make_server(FakeBridge())
    response = server.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
    instructions = response["result"]["instructions"]
    assert "third-party audio plugins" in instructions
    assert "roots:['plugins']" in instructions
    assert "find_similar_sounds requires Live 12+" in instructions
    assert "start with path" in instructions
    assert "Idle sockets auto-retry" in instructions
    assert "jweb/jbrowser aliases" in instructions
    assert "agent-settable UI" in instructions
    assert "full Live object model remains available" in instructions
    assert len(instructions) < 1500


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
        "ref": {"path": "live_set tracks 0 clip_slots 0 clip"},
        "clear": True,
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
    assert forwarded["device_width"] == 340
    assert forwarded["webui"]["url"].startswith("file://")
    assert response["result"]["structuredContent"]["built"]["installed_path"] == ""
    assert response["result"]["structuredContent"]["built"]["device_width"] == 340
    assert response["result"]["structuredContent"]["webui"]["html_path"].endswith("index.html")


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


def test_agent_m4l_device_tool_direct_update_preserves_recovery_patch(tmp_path):
    bridge = FakeBridge()
    server = make_server(bridge)
    command_file = tmp_path / "command.json"
    status_file = tmp_path / "status.json"
    command_file.write_text(json.dumps({
        "id": "patch1",
        "command": "update",
        "patch": {"objects": [{"id": "dial", "text": "flonum"}], "connections": []},
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
    assert bridge.calls == []
    assert response["result"]["structuredContent"]["direct"] is True
    assert payload["patch"]["objects"][0]["id"] == "dial"
    assert payload["values"] == [{"id": "dial", "value": 0.7}]


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
    assert forwarded["webuis"][0]["html_path"].endswith("Panel_Test_left/index.html")
    assert forwarded["webuis"][1]["html_path"] == str(existing)
    assert forwarded["patch"]["webuis"] == forwarded["webuis"]
    assert forwarded["patch"]["device_width"] == 500
    assert response["result"]["structuredContent"]["built"]["device_width"] == 500
    assert response["result"]["structuredContent"]["webui"]["webuis"][0]["url"].startswith("file://")


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
    assert (agent_m4l.WEBUI_DIR / "Asset_Existing" / "lib" / "scene.js").read_text(encoding="utf-8") == "window.sceneReady = true;"
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
    assert response["result"]["structuredContent"]["built"]["device_width"] == 260
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
    assert response["result"]["structuredContent"]["status"]["event"] == "set"
    assert response["result"]["structuredContent"]["status"]["dynamic_objects"] == 4


def test_wait_agent_m4l_status_requires_matching_command_id(tmp_path):
    status_file = tmp_path / "status.json"
    status_file.write_text('{"event":"set","command_id":"old"}', encoding="utf-8")
    before = status_file.stat().st_mtime - 1.0
    status_file.write_text('{"event":"set","command_id":"new","webuis":1}', encoding="utf-8")

    result = wait_agent_m4l_status(str(status_file), before, "new", 0.2, 0.01)

    assert result["command_id"] == "new"
    assert result["webuis"] == 1


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


def test_agent_m4l_expected_status_event_tracks_command_intent():
    assert expected_agent_m4l_status_event("update") == "reload"
    assert expected_agent_m4l_status_event("set") == "set"
    assert expected_agent_m4l_status_event("status") == "status"
    assert expected_agent_m4l_status_event("other") is None


def test_agent_m4l_build_default_tracks_command_intent():
    assert should_build_agent_m4l({"patch": {"objects": []}}) is True
    assert should_build_agent_m4l({"webui": {"html_path": "/tmp/x.html"}}) is True
    assert should_build_agent_m4l({"webuis": [{"html_path": "/tmp/x.html"}]}) is True
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
    args = {"code": "result = {'tracks': len(song.tracks)}", "max_items": 10}
    response = server.handle({
        "jsonrpc": "2.0",
        "id": 41,
        "method": "tools/call",
        "params": {"name": "live_exec", "arguments": args},
    })
    assert bridge.calls == [("exec", args)]
    assert response["result"]["structuredContent"]["method"] == "exec"


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
    assert len(payload) < 17500
    live_eval = next(tool for tool in response["result"]["tools"] if tool["name"] == "live_eval")
    assert "live_exec" in live_eval["description"]
    assert "duplicate session clips" not in live_eval["description"].lower()
    similar = next(tool for tool in response["result"]["tools"] if tool["name"] == "find_similar_sounds")
    assert "Live 12+" in similar["description"]


def test_bridge_error_omits_traceback_by_default(monkeypatch):
    monkeypatch.delenv("ABLETON_MCP_TRACEBACK", raising=False)
    client = AbletonBridgeClient()
    message = {
        "jsonrpc": "2.0",
        "id": 1,
        "error": {"code": -32000, "message": "bad call", "data": "Traceback: noisy"},
    }
    monkeypatch.setattr(client, "_read_line", lambda _sock, _max_bytes: json.dumps(message).encode("utf-8"))

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
    monkeypatch.setenv("ABLETON_MCP_MAX_RESPONSE_BYTES", "123456")

    config = AbletonBridgeClient().config

    assert config == BridgeConfig(
        host="127.0.0.2",
        port=9876,
        timeout=45.0,
        idle_timeout=8.0,
        max_response_bytes=123456,
    )


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
        created.append(sock)
        return sock

    monkeypatch.setattr("socket.create_connection", connect)
    client = AbletonBridgeClient(BridgeConfig(timeout=10.0))

    assert client.request("exec", {"code": "result = True", "timeout": 45.0}) == {"ok": True}
    assert created[0].timeouts[-1] == 46.0


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


def test_remote_script_resources_available_from_source_checkout():
    root = remote_script_root()
    assert (root / "Ableton_Live_MCP" / "bridge.py").exists()


def test_debug_commands_are_not_published_console_scripts():
    pyproject = (Path(__file__).resolve().parents[1] / "pyproject.toml").read_text()
    assert 'ableton-live-mcp = "server:main"' in pyproject
    assert 'ableton-live-mcp-validate = "validate:main"' in pyproject
    assert 'ableton-live-mcp-install-remote-script = "install_remote_script:main"' in pyproject
    assert "ableton-live-mcp-smoke =" not in pyproject
    assert "ableton-live-mcp-benchmark =" not in pyproject
    assert "ableton-live-mcp-prompt-audit =" not in pyproject


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
    assert {"ping", "get", "children", "eval", "exec", "batch", "browser_roots", "browser_search", "observe", "events"} <= set(methods)


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


def test_benchmark_records_latency_and_payload_size():
    class BenchBridge:
        def request(self, method, params):
            return {"method": method, "params": params}

    code, output = run_benchmark(BenchBridge(), iterations=1, include_browser=False)
    assert code == 0
    assert output["ok"] is True
    assert output["summary"]["max_median_ms"] is not None
    assert output["summary"]["max_median_bytes"] > 0


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


def test_prompt_audit_runs_expected_bridge_methods():
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
            if method in {"clip_update_notes", "clip_warp_markers", "clip_envelope", "browser_load", "track_create_audio_clip"}:
                return {"ok": True}
            raise AssertionError(method)

    bridge = PromptBridge()
    code, output = run_prompt_audit(bridge)
    methods = [method for method, _params in bridge.calls]
    assert code == 0
    assert output["ok"] is True
    assert output["destructive"] is True
    assert {"batch", "exec", "set_summary", "get", "clip_notes", "clip_update_notes", "clip_envelope", "clip_warp_markers", "track_create_audio_clip", "browser_search", "browser_load"} <= set(methods)
