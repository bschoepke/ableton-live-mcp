from __future__ import annotations

import json
from pathlib import Path

import agent_m4l
from agent_m4l import audio_bus_names, build_amxd, build_pool, command_file, infer_device_width, inject_webui_bootstrap, make_host_patch, replace_ptch_chunk, status_file, udp_port, write_webui


def test_agent_m4l_host_patch_contains_runtime_and_role_io():
    patch = make_host_patch("instrument", "Lead", "Lead")
    boxes = patch["patcher"]["boxes"]
    lines = patch["patcher"]["lines"]
    texts = {box["box"].get("text") for box in boxes}
    boxes_by_id = {box["box"].get("id"): box["box"] for box in boxes}
    assert "js agent_m4l_host.js instrument Lead %s %s" % (command_file("Lead"), status_file("Lead")) in texts
    assert "midiin" in texts
    assert "plugout~ 1 2" in texts
    assert "phasor~ 4" in texts
    assert ">~ 0.5" in texts
    assert "edge~" in texts
    assert "prepend __signal_wake" in texts
    buses = audio_bus_names("Lead")
    assert "receive~ %s" % buses["output_left"] in texts
    assert "receive~ %s" % buses["output_right"] in texts
    assert "udpreceive %d" % udp_port("Lead") in texts
    assert "metro 50 @active 1 @defer 1" in texts
    assert "live.thisdevice" in texts
    assert "live.path" in texts
    assert "path this_device" in texts
    assert "prepend __self_device" in texts
    assert "path this_device parameters 1" in texts
    assert "live.observer value" in texts
    assert "prepend __command_trigger" in texts
    assert "filewatch %s" % command_file("Lead") in texts
    assert "trigger b b" in texts
    assert command_file("Lead") in texts
    assert "prepend __filewatch" in texts
    assert "deferlow" in texts
    assert "delay 100" in texts
    assert boxes_by_id["command-trigger"]["maxclass"] == "live.numbox"
    assert boxes_by_id["command-trigger"]["parameter_shortname"] == "Agent Poll"
    assert boxes_by_id["command-trigger"]["parameter_enable"] == 1
    assert {"patchline": {"source": ["poll-live-device", 0], "destination": ["poll-start", 0]}} in lines
    assert {"patchline": {"source": ["poll-live-device", 0], "destination": ["poll-delay", 0]}} in lines
    assert {"patchline": {"source": ["poll-live-device", 0], "destination": ["self-path-message", 0]}} in lines
    assert {"patchline": {"source": ["self-path-message", 0], "destination": ["self-path", 0]}} in lines
    assert {"patchline": {"source": ["self-path", 0], "destination": ["self-prepend", 0]}} in lines
    assert {"patchline": {"source": ["self-prepend", 0], "destination": ["js", 0]}} in lines
    assert {"patchline": {"source": ["poll-live-device", 0], "destination": ["trigger-path-message", 0]}} in lines
    assert {"patchline": {"source": ["trigger-path-message", 0], "destination": ["trigger-path", 0]}} in lines
    assert {"patchline": {"source": ["trigger-path", 0], "destination": ["trigger-observer", 1]}} in lines
    assert {"patchline": {"source": ["trigger-observer", 0], "destination": ["trigger-prepend", 0]}} in lines
    assert {"patchline": {"source": ["trigger-prepend", 0], "destination": ["js", 0]}} in lines
    assert {"patchline": {"source": ["poll-loadbang", 0], "destination": ["command-filewatch-init", 0]}} in lines
    assert {"patchline": {"source": ["poll-live-device", 0], "destination": ["command-filewatch-init", 0]}} in lines
    assert {"patchline": {"source": ["command-filewatch-init", 1], "destination": ["command-filewatch-path", 0]}} in lines
    assert {"patchline": {"source": ["command-filewatch-init", 0], "destination": ["command-filewatch-start", 0]}} in lines
    assert {"patchline": {"source": ["command-filewatch-path", 0], "destination": ["command-filewatch", 0]}} in lines
    assert {"patchline": {"source": ["command-filewatch-start", 0], "destination": ["command-filewatch", 0]}} in lines
    assert {"patchline": {"source": ["command-filewatch", 0], "destination": ["command-filewatch-prepend", 0]}} in lines
    assert {"patchline": {"source": ["command-filewatch-prepend", 0], "destination": ["js", 0]}} in lines
    assert {"patchline": {"source": ["poll-delay", 0], "destination": ["js", 0]}} in lines
    assert {"patchline": {"source": ["command-trigger", 0], "destination": ["js", 0]}} in lines
    assert {"patchline": {"source": ["signal-wake-clock", 0], "destination": ["signal-wake-threshold", 0]}} in lines
    assert {"patchline": {"source": ["signal-wake-threshold", 0], "destination": ["signal-wake-edge", 0]}} in lines
    assert {"patchline": {"source": ["signal-wake-edge", 0], "destination": ["signal-wake-prepend", 0]}} in lines
    assert {"patchline": {"source": ["signal-wake-prepend", 0], "destination": ["js", 0]}} in lines
    assert {"patchline": {"source": ["midiin", 0], "destination": ["js", 0]}} in lines
    assert {"patchline": {"source": ["midiin", 0], "destination": ["midiout", 0]}} not in lines
    assert "jweb~ @rendermode 1" not in texts
    assert "prepend ui0" not in texts
    assert patch["patcher"]["devicewidth"] == 420.0
    assert patch["patcher"]["openrect"] == [0.0, 0.0, 420.0, 170.0]
    assert patch["patcher"]["amxdtype"] == 1768515945


def test_agent_m4l_infers_device_width_from_presentation_bounds():
    assert infer_device_width() == 420
    assert infer_device_width({"device_width": 760}) == 760
    assert infer_device_width({
        "objects": [{"id": "wide_knob", "presentation_rect": [20, 8, 520, 60]}],
        "webui": {"id": "panel", "presentation_rect": [560, 0, 260, 120]},
    }) == 840


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
    assert {"patchline": {"source": ["signal-wake-clock", 0], "destination": ["signal-wake-threshold", 0]}} in audio_lines
    assert {"patchline": {"source": ["signal-wake-prepend", 0], "destination": ["js", 0]}} in audio_lines
    assert {"patchline": {"source": ["midiin", 0], "destination": ["js", 0]}} in midi_lines
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
    assert result["udp_port"] == udp_port("Gate Test")
    assert result["device_width"] == 420
    assert patch["patcher"]["devicewidth"] == 420.0
    assert "midiout" in texts
    assert Path(result["amxd_path"]).with_name("agent_m4l_host.js").exists()


def test_agent_m4l_udp_ports_are_stable_and_instance_scoped():
    assert udp_port("Lead") == udp_port("Lead")
    assert udp_port("Lead") != udp_port("Other Lead")
    assert 17655 <= udp_port("Lead") < 47655


def test_agent_m4l_host_runtime_supports_ui_and_value_updates():
    source = Path("m4l/agent_m4l_host.js").read_text(encoding="utf-8")
    assert "jweb~" in source
    assert "createWebUi" in source
    assert "createWebUis" in source
    assert "webui.html_path || webui.path || webui.url || webui.html_url" in source
    assert '? "readfile" : "read"' in source
    assert "scheduleWebUiRead" in source
    assert "readPendingWebUis" in source
    assert "if (attempt === 0)" in source
    assert "web_read_scheduled" in source
    assert "web_read_attempts" in source
    assert "WEBUI_READ_DELAYS" in source
    assert "web_read_pending" in source
    assert "webui_read_exhausted" in source
    assert "webUiReadRequest" in source
    assert '"readfile" && read.fallback_path' in source
    assert "web_\" + key + \"_read_message" in source
    assert "handleTaggedWebUiMessage" in source
    assert "webUiIdByTag" in source
    assert "markWebUiLoaded" in source
    assert "safeStateKey" in source
    assert '"prepend", tag' in source
    assert 'state["web_" + safeStateKey(id) + "_loaded"] = 1' in source
    assert "presentation_rect" in source
    assert "scriptSendBox" in source
    assert "setBoxOnlyAttr" in source
    assert "script\", \"newdefault\"" in source
    assert "setattr" in source
    assert "sendtoback" in source
    assert "webMessageOutlet" in source
    assert 'if (value.indexOf("jweb~") === 0) {' in source
    assert "return 2;" in source
    assert "normalizeWebObject" in source
    assert 'if (value === "jbrowser~") {\n        return "jweb";\n    }' in source
    assert "arrayfromargs(arguments)" in source
    assert "uiBindings" in source
    assert "LiveAPI" in source
    assert "startLiveParameterObservers" in source
    assert "handleLiveParameterChange" in source
    assert "isCommandTriggerName" in source
    assert "live_parameter_raw" in source
    assert "handleSelfDevicePath" in source
    assert "firstLiveApiId" in source
    assert "live_parameter_device_id" in source
    assert "handleCommandTrigger" in source
    assert "handleFilewatchWake" in source
    assert "filewatch_bangs" in source
    assert "markCommandWake" in source
    assert "command_wake_source" in source
    assert "function list()" in source
    assert "handleSignalWake" in source
    assert "__signal_wake" in source
    assert "handleLiveParameterObserverMessage" in source
    assert "createLiveParameterObserverForSource" in source
    assert "trackGeneratedLiveParameter" in source
    assert '"live.observer", "value"' in source
    assert '"path", "this_device", "parameters", parameterIndex' in source
    assert "live_parameter_box_observers" in source
    assert "var ids = liveApiIds(rawParameters)" in source
    assert "function liveApiList(values)" in source
    assert "values.split(/\\s+/)" in source
    assert "ui_bindings" in source
    assert "configureUiBindings" in source
    assert "applyUiBinding" in source
    assert "createDynamicPoller" in source
    assert '"__agent_m4l_poll"' in source
    assert '["qmetro", 50]' in source
    assert "updateUiBindings" in source
    assert "setUiSourceValue" in source
    assert "var binding = uiBindings[id]" in source
    assert "setBoundTarget(binding, valueFromUiBinding(binding, value), id)" in source
    assert "source_settable" in source
    assert "canSetUiSource" in source
    assert "restoreState" in source
    assert "reapplyStateValues" in source
    assert "restored_state" in source
    assert "webObjects[i].message(\"state\"" in source
    assert "reusableWebIdsForSpec" in source
    assert "reusableWebObject" in source
    assert "rememberWebObject" in source
    assert "webObjectById" in source
    assert "webRouterById" in source
    assert "webObjectNameById" in source
    assert "webui.reuse === false" in source
    assert "command.command === \"set\"" in source
    assert "applyValues" in source
    assert "set_silent" in source
    assert "param_silent" in source
    assert "set_many_silent" in source
    assert "handleWebUiLoadMessage" in source
    assert "handleWebUiReadyMessage" in source
    assert "handleWebUiErrorMessage" in source
    assert "web_ready" in source
    assert "web_error" in source
    assert "shortStatusText" in source
    assert "valuesFromAtoms" in source
    assert "valuesFromJson" in source
    assert "applyValues([{ id: String(atoms[0]), value: atoms[1] }], false)" in source
    assert "sendNumericValue" in source
    assert "function bang()" in source
    assert "startStaticPolling" in source
    assert 'getNamed("poll-metro")' in source
    assert 'metro.message("active", 1)' in source
    assert 'metro.message("int", 1)' in source
    assert 'poller.message("int", 1)' in source
    assert 'metro.message("start")' in source
    assert 'poller.message("start")' in source
    assert 'pollCommandFile();' in source
    assert "function msg_int(value)" in source
    assert "function msg_float(value)" in source
    assert "if (pendingWebUiReads.length)" in source
    assert "shouldSendToggleValue" in source
    assert 'obj.message("int", Math.round(value) ? 1 : 0)' in source
    assert "shouldOutputStoredValue" in source
    assert "obj.message(\"set\", value)" in source
    assert "obj.message(\"bang\")" in source
    assert "connectPatchlines" in source
    assert '"audio-in-l", "audio-in-r", "audio-out-l", "audio-out-r"' in source
    assert "connection_errors" in source
    assert "lastConnectionErrors.push" in source
    assert "lastConnectionErrors.concat(errors)" in source
    assert "configureDeviceBounds" in source
    assert "inferDeviceWidth" in source
    assert "devicewidth" in source
    assert "openrect" in source
    assert "device_width" in source
    assert "lastReloadCommandId" in source
    assert "last_reload_command_id" in source
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
        "assets": {
            "lib/scene.js": "export const scene = true;",
            "../unsafe name.txt": {"content": "safe"},
        },
    })
    html = Path(result["html_path"]).read_text(encoding="utf-8")
    js = Path(result["js_path"]).read_text(encoding="utf-8")
    assert "agent-m4l-bootstrap" in html
    assert "web_ready" in html
    assert "web_error" in html
    assert 'data-param="cutoff"' in html
    assert "window.max.outlet" in js
    assert "bindInlet(\"state\"" in js
    assert result["url"].startswith("file://")
    assert Path(result["assets"][0]["path"]).read_text(encoding="utf-8") == "export const scene = true;"
    assert result["assets"][0]["relative_path"] == "lib/scene.js"
    assert result["assets"][1]["relative_path"] == "unsafe_name.txt"


def test_agent_m4l_webui_bootstrap_preserves_custom_html_order():
    html = inject_webui_bootstrap('<html><body><script src="device.js"></script></body></html>')
    assert html.index("agent-m4l-bootstrap") < html.index('src="device.js"')
    assert inject_webui_bootstrap(html) == html


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
