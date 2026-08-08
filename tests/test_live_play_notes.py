import json
from pathlib import Path

import agent_m4l
from server import make_server


class NullBridge:
    def request(self, method, params):
        raise AssertionError("live_play_notes must not touch the bridge")


def _call(server, arguments):
    response = server.handle({
        "jsonrpc": "2.0", "id": 11, "method": "tools/call",
        "params": {"name": "live_play_notes", "arguments": arguments},
    })
    return response


def test_play_notes_writes_command_file(tmp_path, monkeypatch):
    monkeypatch.setenv("ABLETON_MCP_STATE_DIR", str(tmp_path))
    response = _call(make_server(NullBridge()), {
        "pitches": [60, 64, 67], "duration_ms": 900, "velocity": 90, "spread_ms": 50,
    })
    content = response["result"]["structuredContent"]
    assert content["notes"] == 3
    payload = json.loads(Path(content["written"]).read_text(encoding="utf-8"))
    assert payload["command"] == "play_notes"
    assert payload["instance_id"] == "audition"
    assert payload["notes"][0] == {"pitch": 60, "velocity": 90, "duration_ms": 900.0, "at_ms": 0.0}
    assert payload["notes"][2]["at_ms"] == 100.0  # 2 * spread


def test_play_notes_explicit_notes_and_instance(tmp_path, monkeypatch):
    monkeypatch.setenv("ABLETON_MCP_STATE_DIR", str(tmp_path))
    notes = [{"pitch": 69, "velocity": 70, "duration_ms": 250, "at_ms": 0}]
    response = _call(make_server(NullBridge()), {"instance_id": "My Synth!", "notes": notes})
    content = response["result"]["structuredContent"]
    assert content["instance_id"] == "My_Synth"
    payload = json.loads(Path(content["written"]).read_text(encoding="utf-8"))
    assert payload["notes"] == notes


def test_play_notes_requires_notes(tmp_path, monkeypatch):
    monkeypatch.setenv("ABLETON_MCP_STATE_DIR", str(tmp_path))
    response = _call(make_server(NullBridge()), {})
    assert "notes[] or pitches[]" in response["error"]["message"]


def test_host_js_has_play_notes_scheduler():
    source = Path("m4l/agent_m4l_host.js").read_text(encoding="utf-8")
    assert "outlets = 4" in source
    assert 'command.command === "play_notes"' in source
    assert "outlet(3, note.pitch, note.velocity, note.duration)" in source
    # scheduler state must live in module vars, never Task.arguments
    assert "noteQueue" in source and "noteEpoch" in source


def test_midi_effect_patch_wires_audition_chain():
    patch = agent_m4l.make_host_patch("midi_effect", "aud")
    boxes = {box["box"]["id"]: box["box"] for box in patch["patcher"]["boxes"]}
    assert boxes["audition-makenote"]["text"].startswith("makenote")
    assert boxes["audition-pack"]["text"] == "pack 0 0"
    assert boxes["audition-midiformat"]["text"] == "midiformat"
    lines = [entry["patchline"] for entry in patch["patcher"]["lines"]]
    assert {"source": ["js", 3], "destination": ["audition-makenote", 0]} in lines
    # midiformat's note inlet needs a [pitch velocity] list via pack — its inlet 1
    # is poly pressure, and wiring velocity there yields velocity-0 note-ons.
    assert {"source": ["audition-makenote", 0], "destination": ["audition-pack", 0]} in lines
    assert {"source": ["audition-makenote", 1], "destination": ["audition-pack", 1]} in lines
    assert {"source": ["audition-pack", 0], "destination": ["audition-midiformat", 0]} in lines
    assert {"source": ["audition-midiformat", 0], "destination": ["midiout", 0]} in lines
    assert {"source": ["audition-makenote", 1], "destination": ["audition-midiformat", 1]} not in lines
