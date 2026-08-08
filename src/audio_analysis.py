"""Offline analysis of tap-captured WAVs: loudness, peak, spectral balance.

Gives an agent ears with numbers attached: capture the master (or any tap
point) with AgentAudioTap, then measure instead of guessing. Integrated
loudness follows ITU-R BS.1770-4 (K-weighting + 400 ms gated blocks), the
same standard game platforms specify (e.g. Meta Quest's -16 LUFS ceiling),
so "master to -16" becomes a closed loop: measure, adjust, re-measure.

numpy is required (the optional [analyze] extra); everything else is stdlib.
Ported from a field-proven implementation (2026-08-08: measured a real mix at
-20.0 LUFS, mastered to the -16.0 target, verified by re-measurement).
"""

from __future__ import annotations

import wave
from typing import Any

DEFAULT_BANDS = (
    (20, 100, "sub"),
    (100, 300, "bass"),
    (300, 1000, "lowmid"),
    (1000, 3000, "mid"),
    (3000, 8000, "presence"),
    (8000, 16000, "air"),
)


def _numpy():
    try:
        import numpy
    except ImportError as exc:
        raise RuntimeError(
            "live_analyze requires numpy: install the [analyze] extra "
            "(uv sync --extra analyze) or pip install numpy"
        ) from exc
    return numpy


def read_wav(path) -> tuple[Any, int]:
    """Return (float64 samples shaped [frames, channels] in -1..1, sample_rate)."""
    np = _numpy()
    with wave.open(str(path), "rb") as handle:
        params = handle.getparams()
        raw = handle.readframes(params.nframes)
    if params.sampwidth == 2:
        data = np.frombuffer(raw, dtype=np.int16).astype(np.float64) / 32768.0
    elif params.sampwidth == 3:
        as_bytes = np.frombuffer(raw, dtype=np.uint8).reshape(-1, 3)
        ints = (
            as_bytes[:, 0].astype(np.int32)
            | (as_bytes[:, 1].astype(np.int32) << 8)
            | (as_bytes[:, 2].astype(np.int32) << 16)
        )
        ints = np.where(ints & 0x800000, ints - 0x1000000, ints)
        data = ints.astype(np.float64) / float(0x800000)
    elif params.sampwidth == 4:
        data = np.frombuffer(raw, dtype=np.int32).astype(np.float64) / 2147483648.0
    else:
        raise RuntimeError("Unsupported WAV sample width: %d bytes" % params.sampwidth)
    return data.reshape(-1, params.nchannels), params.framerate


def _biquad(x, b, a):
    np = _numpy()
    y = np.empty_like(x)
    x1 = x2 = y1 = y2 = 0.0
    b0, b1, b2 = b
    a1, a2 = a[1], a[2]
    for i in range(len(x)):
        y0 = b0 * x[i] + b1 * x1 + b2 * x2 - a1 * y1 - a2 * y2
        x2, x1 = x1, x[i]
        y2, y1 = y1, y0
        y[i] = y0
    return y


def _k_weight(x, sample_rate):
    """BS.1770-4 K-weighting: high-shelf pre-filter + RLB high-pass."""
    np = _numpy()
    f0, gain_db, q = 1681.9744509555319, 3.99984385397, 0.7071752369554193
    k = np.tan(np.pi * f0 / sample_rate)
    vh = 10.0 ** (gain_db / 20.0)
    vb = vh ** 0.4996667741545416
    a0 = 1 + k / q + k * k
    b = ((vh + vb * k / q + k * k) / a0, 2 * (k * k - vh) / a0, (vh - vb * k / q + k * k) / a0)
    a = (1.0, 2 * (k * k - 1) / a0, (1 - k / q + k * k) / a0)
    x = _biquad(x, b, a)
    f0, q = 38.13547087602444, 0.5003270373238773
    k = np.tan(np.pi * f0 / sample_rate)
    a0 = 1 + k / q + k * k
    b = (1 / a0, -2 / a0, 1 / a0)
    a = (1.0, 2 * (k * k - 1) / a0, (1 - k / q + k * k) / a0)
    return _biquad(x, b, a)


def integrated_lufs(samples, sample_rate) -> float:
    """Gated integrated loudness per BS.1770-4 (400 ms blocks, 75% overlap)."""
    np = _numpy()
    weighted = [_k_weight(samples[:, ch], sample_rate) for ch in range(samples.shape[1])]
    block = int(0.4 * sample_rate)
    hop = int(0.1 * sample_rate)
    count = (len(weighted[0]) - block) // hop
    if count < 1:
        raise RuntimeError("Capture too short for integrated LUFS (needs >= 0.4 s)")
    z = np.array([
        sum(float(np.mean(ch[i * hop:i * hop + block] ** 2)) for ch in weighted)
        for i in range(count)
    ])
    lk = -0.691 + 10 * np.log10(z + 1e-12)
    gated = z[lk > -70.0]                                   # absolute gate
    if not len(gated):
        return float("-inf")
    relative_gate = -0.691 + 10 * np.log10(gated.mean()) - 10.0
    final = gated[(-0.691 + 10 * np.log10(gated + 1e-12)) > relative_gate]
    if not len(final):
        return float("-inf")
    return float(-0.691 + 10 * np.log10(final.mean()))


def band_energies(samples, sample_rate, bands=DEFAULT_BANDS) -> dict[str, float]:
    """Mean per-band energy in dB (relative), from a Hann-windowed rFFT of the mono mix."""
    np = _numpy()
    mono = samples.mean(axis=1)
    spectrum = np.abs(np.fft.rfft(mono * np.hanning(len(mono)))) ** 2
    freqs = np.fft.rfftfreq(len(mono), 1.0 / sample_rate)
    result = {}
    for lo, hi, name in bands:
        sel = (freqs >= lo) & (freqs < hi)
        energy = float(spectrum[sel].sum()) if sel.any() else 0.0
        result[name] = round(10 * float(np.log10(energy + 1e-12)), 1)
    return result


def dominant_frequency(samples, sample_rate, lo=40.0, hi=8000.0) -> float:
    np = _numpy()
    mono = samples.mean(axis=1)
    spectrum = np.abs(np.fft.rfft(mono * np.hanning(len(mono))))
    freqs = np.fft.rfftfreq(len(mono), 1.0 / sample_rate)
    sel = (freqs >= lo) & (freqs <= hi)
    if not sel.any():
        return 0.0
    window = spectrum[sel]
    peak = int(window.argmax())
    a = max(0, peak - 2)
    b = min(len(window), peak + 3)
    return float((freqs[sel][a:b] * window[a:b]).sum() / (window[a:b].sum() + 1e-12))


def analyze_wav(path) -> dict[str, Any]:
    np = _numpy()
    samples, sample_rate = read_wav(path)
    peak = float(np.abs(samples).max()) if len(samples) else 0.0
    result = {
        "path": str(path),
        "duration_s": round(len(samples) / float(sample_rate), 2),
        "sample_rate": sample_rate,
        "channels": int(samples.shape[1]),
        "sample_peak_dbfs": round(20 * float(np.log10(peak + 1e-12)), 1),
        "bands_db": band_energies(samples, sample_rate),
        "dominant_hz": round(dominant_frequency(samples, sample_rate), 1),
    }
    try:
        result["lufs_integrated"] = round(integrated_lufs(samples, sample_rate), 1)
    except RuntimeError as exc:
        result["lufs_error"] = str(exc)
    return result
