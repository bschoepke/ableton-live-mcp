from __future__ import annotations

import json
from pathlib import Path

import pytest

from ableton_object_mcp.bridge import AbletonBridgeClient, AbletonBridgeError
from ableton_object_mcp.install_remote_script import install_remote_script, remote_script_root
from ableton_object_mcp.server import make_server
from ableton_object_mcp.smoke import run_smoke


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
        "live_get",
        "live_set",
        "live_call",
        "live_children",
        "live_eval",
        "live_batch",
        "live_browser_roots",
        "live_browser_search",
        "live_browser_load",
        "live_observe",
        "live_events",
    } <= names


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


def test_browser_search_schema_mentions_plugins_root():
    server = make_server(FakeBridge())
    response = server.handle({"jsonrpc": "2.0", "id": 5, "method": "tools/list"})
    search = next(tool for tool in response["result"]["tools"] if tool["name"] == "live_browser_search")
    roots_description = search["inputSchema"]["properties"]["roots"]["description"]
    assert "plugins" in roots_description


def test_remote_bridge_default_browser_roots_include_plugins():
    root = Path(__file__).resolve().parents[1]
    bridge_source = (root / "remote_scripts" / "Ableton_Object_MCP" / "bridge.py").read_text()
    assert '"plugins"' in bridge_source
    assert "def _rpc_browser_search" in bridge_source
    assert "def _rpc_browser_load" in bridge_source


def test_browser_load_tool_forwards_item_ref_to_bridge():
    bridge = FakeBridge()
    server = make_server(bridge)
    args = {"item": {"id": 123}, "target_track": {"path": "live_set tracks 0"}}
    response = server.handle({
        "jsonrpc": "2.0",
        "id": 6,
        "method": "tools/call",
        "params": {"name": "live_browser_load", "arguments": args},
    })
    assert bridge.calls == [("browser_load", args)]
    assert response["result"]["structuredContent"]["method"] == "browser_load"


def test_tool_list_stays_compact():
    server = make_server(FakeBridge())
    response = server.handle({"jsonrpc": "2.0", "id": 7, "method": "tools/list"})
    payload = json.dumps(response, separators=(",", ":"))
    assert len(payload) < 14000
    live_eval = next(tool for tool in response["result"]["tools"] if tool["name"] == "live_eval")
    assert "exec" in live_eval["description"]
    assert "duplicate session clips" not in live_eval["description"].lower()


def test_bridge_error_omits_traceback_by_default(monkeypatch):
    monkeypatch.delenv("ABLETON_MCP_TRACEBACK", raising=False)
    client = AbletonBridgeClient()
    message = {
        "jsonrpc": "2.0",
        "id": 1,
        "error": {"code": -32000, "message": "bad call", "data": "Traceback: noisy"},
    }
    monkeypatch.setattr(client, "_read_line", lambda _sock: json.dumps(message).encode("utf-8"))

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


def test_remote_script_bridge_alias_stays_identical():
    root = Path(__file__).resolve().parents[1]
    canonical = root / "remote_scripts" / "Ableton_Object_MCP" / "bridge.py"
    alias = root / "remote_scripts" / "AbletonMCP" / "bridge.py"
    assert alias.read_text() == canonical.read_text()


def test_remote_script_installer_copies_selected_alias(tmp_path):
    target = install_remote_script("Ableton_Object_MCP", tmp_path)
    assert target == tmp_path / "Ableton_Object_MCP"
    assert (target / "__init__.py").exists()
    assert (target / "bridge.py").exists()
    assert not (tmp_path / "AbletonMCP").exists()


def test_remote_script_installer_rejects_unknown_alias(tmp_path):
    with pytest.raises(ValueError):
        install_remote_script("Unknown", tmp_path)


def test_remote_script_resources_available_from_source_checkout():
    root = remote_script_root()
    assert (root / "Ableton_Object_MCP" / "bridge.py").exists()
    assert (root / "AbletonMCP" / "bridge.py").exists()


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
    assert {"ping", "get", "children", "eval", "batch", "browser_roots", "browser_search", "observe", "events"} <= set(methods)


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
