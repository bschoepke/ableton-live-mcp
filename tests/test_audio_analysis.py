import math
import wave

import pytest

np = pytest.importorskip("numpy")

from audio_analysis import analyze_wav, integrated_lufs, read_wav
from server import make_server


def write_sine(path, freq=500.0, amplitude=0.5, seconds=3.0, rate=44100, channels=2):
    frames = int(seconds * rate)
    t = np.arange(frames) / rate
    mono = (amplitude * np.sin(2 * math.pi * freq * t) * 32767).astype(np.int16)
    data = np.repeat(mono[:, None], channels, axis=1).reshape(-1)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(channels)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(data.tobytes())
    return path


def test_integrated_lufs_of_known_stereo_sine(tmp_path):
    # BS.1770 calibration: a full-scale 997 Hz sine in ONE channel reads
    # -3.01 LUFS; the same sine in BOTH channels doubles the energy sum
    # (+3.01), so a half-amplitude (-6.02 dBFS) dual-mono sine reads ~-6.0 LUFS.
    path = write_sine(tmp_path / "sine.wav", freq=997.0, amplitude=0.5)
    samples, rate = read_wav(path)
    assert abs(integrated_lufs(samples, rate) - (-6.03)) < 0.3


def test_analyze_wav_reports_peak_bands_and_pitch(tmp_path):
    path = write_sine(tmp_path / "a440.wav", freq=440.0, amplitude=0.25, seconds=2.0)
    result = analyze_wav(path)
    assert abs(result["sample_peak_dbfs"] - (-12.0)) < 0.3
    assert result["duration_s"] == pytest.approx(2.0, abs=0.05)
    assert result["channels"] == 2
    assert abs(result["dominant_hz"] - 440.0) < 2.0
    bands = result["bands_db"]
    # All the energy lives in lowmid (300-1000); neighbors are far below.
    assert bands["lowmid"] > bands["bass"] + 20
    assert bands["lowmid"] > bands["mid"] + 20
    assert "lufs_integrated" in result


def test_analyze_wav_too_short_for_lufs_reports_error(tmp_path):
    path = write_sine(tmp_path / "blip.wav", seconds=0.2)
    result = analyze_wav(path)
    assert "lufs_error" in result
    assert "lufs_integrated" not in result


class AnalyzeBridge:
    """Fake bridge whose tap 'capture' writes a sine wav immediately."""

    def __init__(self):
        self.calls = []

    def request(self, method, params):
        self.calls.append((method, params))
        assert method == "agent_audio_tap" and params["command"] == "start"
        write_sine(params["path"], freq=500.0, amplitude=0.5, seconds=1.0)
        return {"command_id": "x", "duration_ms": params.get("duration_ms")}


def test_live_analyze_tool_capture_flow(tmp_path, monkeypatch):
    import server as server_module

    monkeypatch.setattr(server_module.time, "sleep", lambda _s: None)
    bridge = AnalyzeBridge()
    server = make_server(bridge)
    response = server.handle({
        "jsonrpc": "2.0", "id": 5, "method": "tools/call",
        "params": {"name": "live_analyze", "arguments": {
            "duration_ms": 1000, "capture_path": str(tmp_path / "cap.wav"),
        }},
    })
    content = response["result"]["structuredContent"]
    assert content["sample_peak_dbfs"] == pytest.approx(-6.0, abs=0.3)
    assert bridge.calls[0][1]["duration_ms"] == 1000
    assert bridge.calls[0][1]["udp"] is True


def test_live_analyze_tool_rejects_ambiguous_args(tmp_path):
    server = make_server(AnalyzeBridge())
    response = server.handle({
        "jsonrpc": "2.0", "id": 6, "method": "tools/call",
        "params": {"name": "live_analyze", "arguments": {
            "path": str(tmp_path / "x.wav"), "duration_ms": 1000,
        }},
    })
    assert "not both" in response["error"]["message"]
