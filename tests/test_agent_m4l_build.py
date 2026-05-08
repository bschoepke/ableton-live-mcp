from __future__ import annotations

import json
from pathlib import Path

import agent_m4l
from agent_m4l import audio_bus_names, build_amxd, build_pool, command_file, make_host_patch, replace_ptch_chunk, status_file, write_webui


def test_agent_m4l_host_patch_contains_runtime_and_role_io():
    patch = make_host_patch("instrument", "Lead", "Lead")
    boxes = patch["patcher"]["boxes"]
    lines = patch["patcher"]["lines"]
    texts = {box["box"].get("text") for box in boxes}
    assert "js agent_m4l_host.js instrument Lead %s %s" % (command_file("Lead"), status_file("Lead")) in texts
    assert "midiin" in texts
    assert "plugout~ 1 2" in texts
    buses = audio_bus_names("Lead")
    assert "receive~ %s" % buses["output_left"] in texts
    assert "receive~ %s" % buses["output_right"] in texts
    assert {"patchline": {"source": ["midiin", 0], "destination": ["midiout", 0]}} not in lines
    assert "jweb~ @rendermode 1" not in texts
    assert "prepend ui0" not in texts
    assert patch["patcher"]["amxdtype"] == 1768515945


def test_agent_m4l_host_does_not_impose_fixed_pass_through():
    audio_patch = make_host_patch("audio_effect", "Effect")
    audio_lines = audio_patch["patcher"]["lines"]
    audio_texts = {box["box"].get("text") for box in audio_patch["patcher"]["boxes"]}
    buses = audio_bus_names("Effect")
    midi_lines = make_host_patch("midi_effect", "Midi")["patcher"]["lines"]
    assert "plugout~ 1 2" in audio_texts
    assert "send~ %s" % buses["input_left"] in audio_texts
    assert "receive~ %s" % buses["output_left"] in audio_texts
    assert {"patchline": {"source": ["plugin", 0], "destination": ["plugout", 0]}} not in audio_lines
    assert {"patchline": {"source": ["plugin", 1], "destination": ["plugout", 1]}} not in audio_lines
    assert {"patchline": {"source": ["plugin", 0], "destination": ["audio-in-l", 0]}} in audio_lines
    assert {"patchline": {"source": ["audio-out-l", 0], "destination": ["plugout", 0]}} in audio_lines
    assert {"patchline": {"source": ["midiin", 0], "destination": ["midiout", 0]}} not in midi_lines


def test_agent_m4l_builds_amxd_container(tmp_path):
    patch_path = tmp_path / "Host.maxpat"
    patch_path.write_text(json.dumps(make_host_patch("audio_effect", "Wobble")), encoding="utf-8")
    output = tmp_path / "Host.amxd"
    build_amxd(patch_path, output)
    data = output.read_bytes()
    assert data.startswith(b"ampf\x04\x00\x00\x00aaaameta")
    assert b"agent_m4l_host.js audio_effect Wobble" in data
    assert b"plugin~" in data


def test_agent_m4l_builds_role_specific_amxd_wrappers(tmp_path):
    expected_headers = {
        "audio_effect": b"aaaa",
        "instrument": b"iiii",
        "midi_effect": b"mmmm",
    }
    for role, header in expected_headers.items():
        patch_path = tmp_path / ("%s.maxpat" % role)
        patch_path.write_text(json.dumps(make_host_patch(role, role)), encoding="utf-8")
        output = tmp_path / ("%s.amxd" % role)
        build_amxd(patch_path, output, role)
        data = output.read_bytes()
        assert data[:8] == b"ampf\x04\x00\x00\x00"
        assert data[8:12] == header
        assert b"agent_m4l_host.js %s %s" % (role.encode("utf-8"), role.encode("utf-8")) in data


def test_agent_m4l_uses_discovered_template_dir(monkeypatch, tmp_path):
    template_dir = tmp_path / "templates"
    template_dir.mkdir()
    template = (
        b"ampf\x04\x00\x00\x00iiiimeta\x04\x00\x00\x00\x00\x00\x00\x00"
        b"ptch\x03\x00\x00\x00oldTAIL"
    )
    (template_dir / "Max Instrument.amxd").write_bytes(template)
    monkeypatch.setenv("ABLETON_MAX_DEVICE_TEMPLATE_DIR", str(template_dir))
    patch_path = tmp_path / "instrument.maxpat"
    patch_path.write_text(json.dumps(make_host_patch("instrument", "Template Test")), encoding="utf-8")
    output = tmp_path / "instrument.amxd"
    build_amxd(patch_path, output, "instrument")
    data = output.read_bytes()
    assert data.startswith(b"ampf\x04\x00\x00\x00iiiimeta")
    assert data.endswith(b"TAIL")
    assert b"agent_m4l_host.js instrument Template_Test" in data


def test_agent_m4l_replaces_template_patch_chunk():
    payload = b'{"patcher":{"boxes":[]}}\x00'
    template = b"ampf\x04\x00\x00\x00iiiimeta\x04\x00\x00\x00\x00\x00\x00\x00ptch\x03\x00\x00\x00oldtail"
    result = replace_ptch_chunk(template, payload)
    assert result.startswith(b"ampf\x04\x00\x00\x00iiiimeta")
    assert result.endswith(b"tail")
    assert payload in result
    assert len(payload).to_bytes(4, "little") in result


def test_agent_m4l_build_device_installs_companion_js_when_not_installing(tmp_path, monkeypatch):
    monkeypatch.setattr(agent_m4l, "GENERATED_DIR", tmp_path)
    result = agent_m4l.build_device("midi_effect", "Gate Test", install=False)
    patch = json.loads(Path(result["patch_path"]).read_text(encoding="utf-8"))
    texts = {box["box"].get("text") for box in patch["patcher"]["boxes"]}
    assert result["name"] == "AgentM4L_midi_effect_Gate_Test"
    assert result["installed_path"] == ""
    assert "midiout" in texts
    assert Path(result["amxd_path"]).with_name("agent_m4l_host.js").exists()


def test_agent_m4l_host_runtime_supports_ui_and_value_updates():
    source = Path("m4l/agent_m4l_host.js").read_text(encoding="utf-8")
    assert "jweb~" in source
    assert "createWebUi" in source
    assert "createWebUis" in source
    assert "presentation_rect" in source
    assert "scriptSendBox" in source
    assert "script\", \"newdefault\"" in source
    assert "setattr" in source
    assert "sendtoback" in source
    assert "webMessageOutlet" in source
    assert "function webMessageOutlet(name) {\n    return 0;\n}" in source
    assert "normalizeWebObject" in source
    assert 'if (value === "jbrowser~") {\n        return "jweb";\n    }' in source
    assert "arrayfromargs(arguments)" in source
    assert "uiBindings" in source
    assert "ui_bindings" in source
    assert "configureUiBindings" in source
    assert "applyUiBinding" in source
    assert "updateUiBindings" in source
    assert "setUiSourceValue" in source
    assert "webObjects[i].message(\"state\"" in source
    assert "command.command === \"set\"" in source
    assert "applyValues" in source
    assert "sendNumericValue" in source
    assert "shouldOutputStoredValue" in source
    assert "obj.message(\"set\", value)" in source
    assert "obj.message(\"bang\")" in source
    assert "connectPatchlines" in source
    assert "connection_errors" in source
    assert "ensureRecovered" in source
    assert "readCommandFileJson" in source
    assert "recovery.patch || recovery.spec" in source
    assert "objectById" in source
    assert "statusFile" in source
    assert "writeStatus" in source
    assert "statusPadSize" in source
    assert "command_id" in source


def test_agent_m4l_write_webui_generates_jweb_page(tmp_path, monkeypatch):
    monkeypatch.setattr(agent_m4l, "WEBUI_DIR", tmp_path)
    result = write_webui("Filter", {
        "title": "Filter",
        "controls": [{"id": "cutoff", "label": "Cutoff", "value": 0.25}],
    })
    html = Path(result["html_path"]).read_text(encoding="utf-8")
    js = Path(result["js_path"]).read_text(encoding="utf-8")
    assert 'data-param="cutoff"' in html
    assert "window.max.outlet" in js
    assert "bindInlet(\"state\"" in js
    assert result["url"].startswith("file://")


def test_agent_m4l_build_pool_creates_stable_slots(tmp_path, monkeypatch):
    monkeypatch.setattr(agent_m4l, "GENERATED_DIR", tmp_path)
    result = build_pool("audio_effect", 3, "pool", install=False)
    assert [item["instance_id"] for item in result] == ["pool_000", "pool_001", "pool_002"]
    assert [item["name"] for item in result] == [
        "AgentM4L_audio_effect_pool_000",
        "AgentM4L_audio_effect_pool_001",
        "AgentM4L_audio_effect_pool_002",
    ]


def test_agent_m4l_role_amxdtype_values_match_live_categories():
    assert make_host_patch("audio_effect", "Fx")["patcher"]["amxdtype"] == 1633771873
    assert make_host_patch("instrument", "Inst")["patcher"]["amxdtype"] == 1768515945
    assert make_host_patch("midi_effect", "Midi")["patcher"]["amxdtype"] == 1835887981
