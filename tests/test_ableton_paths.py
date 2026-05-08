from __future__ import annotations

from pathlib import Path

import ableton_paths


def test_default_user_library_uses_env(monkeypatch, tmp_path):
    custom = tmp_path / "Custom User Library"
    monkeypatch.setenv("ABLETON_USER_LIBRARY", str(custom))
    assert ableton_paths.default_user_library() == custom


def test_windows_user_library_prefers_documents(monkeypatch, tmp_path):
    monkeypatch.delenv("ABLETON_USER_LIBRARY", raising=False)
    monkeypatch.delenv("ABLETON_LIVE_USER_LIBRARY", raising=False)
    monkeypatch.setattr(ableton_paths.platform, "system", lambda: "Windows")
    monkeypatch.setattr(ableton_paths.Path, "home", staticmethod(lambda: tmp_path))
    assert ableton_paths.default_user_library() == tmp_path / "Documents" / "Ableton" / "User Library"


def test_find_max_device_template_uses_template_dir_env(monkeypatch, tmp_path):
    template_dir = tmp_path / "Max Devices"
    template_dir.mkdir()
    template = template_dir / "Max Instrument.amxd"
    template.write_bytes(b"template")
    monkeypatch.setenv("ABLETON_MAX_DEVICE_TEMPLATE_DIR", str(template_dir))
    assert ableton_paths.find_max_device_template("Max Instrument.amxd") == template


def test_find_max_device_template_from_live_path_env(monkeypatch, tmp_path):
    install = tmp_path / "Live Suite"
    exe = install / "Program" / "Live.exe"
    template_dir = install / "Resources" / "Misc" / "Max Devices"
    template = template_dir / "Max MIDI Effect.amxd"
    exe.parent.mkdir(parents=True)
    exe.write_text("")
    template_dir.mkdir(parents=True)
    template.write_bytes(b"template")
    monkeypatch.delenv("ABLETON_MAX_DEVICE_TEMPLATE_DIR", raising=False)
    monkeypatch.setenv("ABLETON_LIVE_PATH", str(exe))
    assert ableton_paths.find_max_device_template("Max MIDI Effect.amxd") == template


def test_state_dir_uses_env(monkeypatch, tmp_path):
    custom = tmp_path / "state"
    monkeypatch.setenv("ABLETON_MCP_STATE_DIR", str(custom))
    assert ableton_paths.state_dir() == custom
    assert custom.is_dir()
