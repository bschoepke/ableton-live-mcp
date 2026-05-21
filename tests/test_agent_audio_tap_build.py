from __future__ import annotations

import json
import importlib.util
from pathlib import Path


def load_builder():
    path = Path("scripts/build_agent_audio_tap.py")
    spec = importlib.util.spec_from_file_location("build_agent_audio_tap", path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def test_agent_audio_tap_sources_are_valid_json():
    patch = json.loads(Path("m4l/AgentAudioTap.maxpat").read_text(encoding="utf-8"))
    boxes = patch["patcher"]["boxes"]
    texts = {box["box"].get("text") for box in boxes}
    assert "sfrecord~ 2" in texts
    assert "js agent_audio_tap.js" in texts
    assert "notein" in texts


def test_agent_audio_tap_builds_amxd_container(tmp_path):
    output = tmp_path / "AgentAudioTap.amxd"
    command_file = tmp_path / "agent_audio_tap_command.json"
    load_builder().build_amxd(Path("m4l/AgentAudioTap.maxpat"), output, command_file)
    data = output.read_bytes()
    assert data.startswith(b"ampf\x04\x00\x00\x00aaaameta")
    assert b"Agent Audio Tap" in data
    assert b"sfrecord~ 2" in data
    assert str(command_file).encode("utf-8") in data


def test_agent_audio_tap_js_has_cross_platform_command_file_default():
    source = Path("m4l/agent_audio_tap.js").read_text(encoding="utf-8")
    assert "jsarguments" in source
    assert "scheduleStartRecording" in source
    assert "startTask.schedule(500)" in source
    assert "/tmp/agent_audio_tap_command.json" not in source
