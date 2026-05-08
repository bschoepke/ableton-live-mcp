from __future__ import annotations

from pathlib import Path

import pytest

import visual_capture
from visual_capture import WindowInfo


def test_visual_capture_filters_to_ableton_windows():
    windows = [
        WindowInfo(
            platform="Windows",
            id=100,
            title="vibe-m4l",
            owner="Ableton Live Suite",
            process_path=r"C:\Program Files\Ableton\Ableton Live Suite\Program\Ableton Live Suite.exe",
            bounds={"x": 0, "y": 0, "width": 1200, "height": 800},
        ),
        WindowInfo(
            platform="Windows",
            id=200,
            title="Private Browser",
            owner="Chrome",
            process_path=r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        ),
        WindowInfo(
            platform="Darwin",
            id=300,
            title="Other Live",
            owner="Live",
            process_path="/Applications/Not Ableton.app/Contents/MacOS/Live",
            bundle_id="example.live",
        ),
    ]
    assert [window.id for window in windows if visual_capture.is_ableton_live_window(window)] == [100]


def test_visual_capture_accepts_verified_macos_ableton_bundle():
    window = WindowInfo(
        platform="Darwin",
        id=100,
        title="vibe-m4l",
        owner="Live",
        process_path="/Applications/Ableton Live Suite.app/Contents/MacOS/Live",
        bundle_id="com.ableton.live",
    )
    assert visual_capture.is_ableton_live_window(window) is True


def test_capture_refuses_non_ableton_window(tmp_path):
    window = WindowInfo(
        platform="Windows",
        id=200,
        title="Browser",
        owner="Chrome",
        process_path=r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    )
    with pytest.raises(RuntimeError, match="non-Ableton"):
        visual_capture.capture_window(window, tmp_path / "browser.png")


def test_title_filter_applies_after_ableton_filter(monkeypatch):
    monkeypatch.setattr(visual_capture, "list_platform_windows", lambda: [
        WindowInfo(
            platform="Windows",
            id=100,
            title="Ableton Set",
            owner="Ableton Live",
            process_path=r"C:\Ableton Live.exe",
            bounds={"x": 0, "y": 0, "width": 1200, "height": 800},
        ),
        WindowInfo(
            platform="Windows",
            id=200,
            title="Ableton Notes in Browser",
            owner="Chrome",
            process_path=r"C:\chrome.exe",
            bounds={"x": 0, "y": 0, "width": 1400, "height": 900},
        ),
    ])
    assert visual_capture.select_ableton_window("Set").id == 100
    with pytest.raises(RuntimeError, match="No Ableton Live window"):
        visual_capture.select_ableton_window("Browser")


def test_list_only_returns_ableton_windows(monkeypatch):
    monkeypatch.setattr(visual_capture, "list_platform_windows", lambda: [
        WindowInfo(
            platform="Windows",
            id=100,
            title="Ableton Set",
            owner="Ableton Live",
            process_path=r"C:\Ableton Live.exe",
            bounds={"x": 0, "y": 0, "width": 1200, "height": 800},
        ),
        WindowInfo(
            platform="Windows",
            id=200,
            title="Browser",
            owner="Chrome",
            process_path=r"C:\chrome.exe",
            bounds={"x": 0, "y": 0, "width": 1400, "height": 900},
        ),
    ])
    result = visual_capture.capture_ableton_window(list_only=True)
    assert result["count"] == 1
    assert result["windows"][0]["id"] == 100


def test_default_selection_prefers_largest_verified_ableton_window(monkeypatch):
    monkeypatch.setattr(visual_capture, "list_platform_windows", lambda: [
        WindowInfo(
            platform="Darwin",
            id=10,
            title="",
            owner="Live",
            process_path="/Applications/Ableton Live Suite.app/Contents/MacOS/Live",
            bundle_id="com.ableton.live",
            bounds={"x": 0, "y": 0, "width": 1470, "height": 33},
        ),
        WindowInfo(
            platform="Darwin",
            id=20,
            title="vibe-m4l",
            owner="Live",
            process_path="/Applications/Ableton Live Suite.app/Contents/MacOS/Live",
            bundle_id="com.ableton.live",
            bounds={"x": 0, "y": 33, "width": 1313, "height": 923},
        ),
    ])
    assert visual_capture.select_ableton_window().id == 20


def test_macos_capture_uses_window_id_screencapture(monkeypatch, tmp_path):
    calls = []

    class Result:
        stdout = ""
        stderr = ""

    def fake_run(args, **_kwargs):
        calls.append(args)
        Path(args[-1]).write_bytes(b"png")
        return Result()

    monkeypatch.setattr(visual_capture.subprocess, "run", fake_run)
    window = WindowInfo(
        platform="Darwin",
        id=9876,
        title="vibe-m4l",
        owner="Live",
        process_path="/Applications/Ableton Live Suite.app/Contents/MacOS/Live",
        bundle_id="com.ableton.live",
    )
    output = tmp_path / "live.png"
    visual_capture.capture_window(window, output)
    assert calls == [["screencapture", "-x", "-l", "9876", str(output)]]


def test_macos_capture_reports_screencapture_stderr(monkeypatch, tmp_path):
    def fake_run(*_args, **_kwargs):
        raise visual_capture.subprocess.CalledProcessError(
            1,
            ["screencapture"],
            stderr="could not create image from window\n",
        )

    monkeypatch.setattr(visual_capture.subprocess, "run", fake_run)
    window = WindowInfo(
        platform="Darwin",
        id=9876,
        title="vibe-m4l",
        owner="Live",
        process_path="/Applications/Ableton Live Suite.app/Contents/MacOS/Live",
        bundle_id="com.ableton.live",
    )
    with pytest.raises(RuntimeError, match="could not create image from window"):
        visual_capture.capture_window(window, tmp_path / "live.png", backend="screencapture")


def test_visual_capture_cli_returns_json_error(monkeypatch, capsys):
    monkeypatch.setattr(visual_capture, "capture_ableton_window", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("blocked")))
    assert visual_capture.main([]) == 1
    output = capsys.readouterr().out
    assert '"ok": false' in output
    assert '"blocked"' in output
