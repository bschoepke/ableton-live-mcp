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
    load_builder().build_amxd(Path("m4l/AgentAudioTap.maxpat"), output)
    data = output.read_bytes()
    assert data.startswith(b"ampf\x04\x00\x00\x00aaaameta")
    assert b"Agent Audio Tap" in data
    assert b"sfrecord~ 2" in data
