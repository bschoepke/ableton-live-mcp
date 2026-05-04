from __future__ import annotations

import json
from pathlib import Path

import pytest

from benchmark import run_benchmark
from bridge import AbletonBridgeClient, AbletonBridgeError, BridgeConfig
from install_remote_script import install_remote_script, remote_script_root
from prompt_audit import run_prompt_audit
from server import make_server
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
        "live_transport",
        "live_eval",
        "live_exec",
        "live_batch",
        "live_browser_roots",
        "live_browser_capabilities",
        "live_browser_search",
        "live_browser_load",
        "live_browser_preview",
        "live_observe",
        "live_events",
    } <= names


def test_initialize_includes_general_model_instructions():
    server = make_server(FakeBridge())
    response = server.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
    instructions = response["result"]["instructions"]
    assert "third-party audio plugins" in instructions
    assert "roots:['plugins']" in instructions
    assert "full Live object model remains available" in instructions
    assert len(instructions) < 1400


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
    assert len(payload) < 17000
    live_eval = next(tool for tool in response["result"]["tools"] if tool["name"] == "live_eval")
    assert "live_exec" in live_eval["description"]
    assert "duplicate session clips" not in live_eval["description"].lower()


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
