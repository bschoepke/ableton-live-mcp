import json
import os
import time

import pytest

import save_set as save_set_module
from save_set import SaveSetError, save_set
from server import make_server


class FileBridge:
    """Fake bridge that reports a set file path for the eval probe."""

    def __init__(self, file_path):
        self.file_path = file_path
        self.calls = []

    def request(self, method, params):
        self.calls.append((method, params))
        assert method == "eval" and params == {"expr": "song.file_path"}
        return str(self.file_path)


def test_save_set_refuses_never_saved_set():
    bridge = FileBridge("")
    result = save_set(bridge)
    assert result["saved"] is False
    assert result["error"] == "unsaved_set"
    assert "Save As" in result["hint"]


def test_save_set_verifies_by_mtime_change(tmp_path, monkeypatch):
    als = tmp_path / "MySet.als"
    als.write_bytes(b"live-set")
    old = time.time() - 100
    os.utime(als, (old, old))

    def fake_keystroke():
        als.write_bytes(b"live-set-v2")  # Live rewriting the file on save

    monkeypatch.setattr(save_set_module, "send_save_keystroke", fake_keystroke)
    result = save_set(FileBridge(als), timeout=2.0, poll_interval=0.05)
    assert result["saved"] is True
    assert result["file_path"] == str(als)
    assert result["mtime_after"] != result["mtime_before"]


def test_save_set_reports_unobserved_save(tmp_path, monkeypatch):
    als = tmp_path / "MySet.als"
    als.write_bytes(b"live-set")
    monkeypatch.setattr(save_set_module, "send_save_keystroke", lambda: None)
    result = save_set(FileBridge(als), timeout=1.0, poll_interval=0.05)
    assert result["saved"] is False
    assert result["error"] == "save_not_observed"
    assert "no unsaved changes" in result["hint"]


def test_save_set_raises_on_missing_file(tmp_path):
    with pytest.raises(SaveSetError, match="does not exist"):
        save_set(FileBridge(tmp_path / "gone.als"))


def test_live_save_set_tool_registered_and_wired(tmp_path, monkeypatch):
    als = tmp_path / "Tool.als"
    als.write_bytes(b"x")
    old = time.time() - 100
    os.utime(als, (old, old))
    monkeypatch.setattr(save_set_module, "send_save_keystroke", lambda: als.write_bytes(b"y"))

    class ToolBridge(FileBridge):
        pass

    server = make_server(ToolBridge(als))
    listed = server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    names = {tool["name"] for tool in listed["result"]["tools"]}
    assert "live_save_set" in names
    response = server.handle({
        "jsonrpc": "2.0", "id": 2, "method": "tools/call",
        "params": {"name": "live_save_set", "arguments": {"timeout": 2.0}},
    })
    content = response["result"]["structuredContent"]
    assert content["saved"] is True
    assert content["file_path"] == str(als)
