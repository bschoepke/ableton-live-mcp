import threading
import time
import wave

import pytest

import export_set as export_module
from export_set import ExportError, export_set


class BraceBridge:
    def __init__(self):
        self.calls = []

    def request(self, method, params):
        self.calls.append((method, params))
        return 1


def _write_wav(path, seconds=0.5, rate=44100):
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(2)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(b"\x00\x00" * 2 * int(seconds * rate))


def test_export_rejects_bad_paths(tmp_path):
    with pytest.raises(ExportError, match="must end in .wav"):
        export_set(BraceBridge(), str(tmp_path / "out.mp3"))
    existing = tmp_path / "there.wav"
    existing.write_bytes(b"x")
    with pytest.raises(ExportError, match="refusing to overwrite"):
        export_set(BraceBridge(), str(existing))
    with pytest.raises(ExportError, match="does not exist"):
        export_set(BraceBridge(), str(tmp_path / "no_dir" / "out.wav"))
    with pytest.raises(ExportError, match="together"):
        export_set(BraceBridge(), str(tmp_path / "out.wav"), start_beats=0.0)


def test_export_sets_brace_and_waits_for_finalized_wav(tmp_path, monkeypatch):
    out = tmp_path / "render.wav"

    def fake_drive(path, _delay):
        # simulate Live: file appears shortly after the dialog walk, then finalizes
        def render():
            time.sleep(0.2)
            _write_wav(path)
        threading.Thread(target=render, daemon=True).start()

    monkeypatch.setattr(export_module, "drive_export_dialog", fake_drive)
    bridge = BraceBridge()
    result = export_set(bridge, str(out), start_beats=0.0, length_beats=32.0, timeout=15.0)
    assert result["exported"] is True
    assert result["duration_s"] == pytest.approx(0.5, abs=0.05)
    assert result["bit_depth"] == 16
    code = bridge.calls[0][1]["code"]
    assert "song.loop_start = 0.0" in code and "song.loop_length = 32.0" in code


def test_export_reports_missing_render(tmp_path, monkeypatch):
    monkeypatch.setattr(export_module, "drive_export_dialog", lambda _p, _d: None)
    result = export_set(BraceBridge(), str(tmp_path / "never.wav"), timeout=1.0)
    assert result["exported"] is False
    assert result["error"] == "render_not_observed"
    assert "re-open dialogs" in result["hint"]


def test_export_ignores_unfinalized_header_until_stable(tmp_path, monkeypatch):
    out = tmp_path / "slow.wav"

    def fake_drive(path, _delay):
        def render():
            time.sleep(0.2)
            with open(path, "wb") as handle:
                handle.write(b"RIFFgarbage-not-finalized")   # stable but unparseable
            time.sleep(2.5)
            _write_wav(path, seconds=0.25)
        threading.Thread(target=render, daemon=True).start()

    monkeypatch.setattr(export_module, "drive_export_dialog", fake_drive)
    result = export_set(BraceBridge(), str(out), timeout=20.0)
    assert result["exported"] is True
    assert result["duration_s"] == pytest.approx(0.25, abs=0.05)
