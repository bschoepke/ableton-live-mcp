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


def test_device_detail_region_crops_bottom_of_ableton_window():
    assert visual_capture.capture_region_box((1000, 900), "device-detail") == (0, 594, 1000, 900)
    assert visual_capture.capture_region_box((1000, 900), "detail", bottom_fraction=0.25) == (0, 675, 1000, 900)


def test_explicit_crop_clamps_to_ableton_window_bounds():
    assert visual_capture.capture_region_box((1000, 900), crop=[-10, 50, 120, 60]) == (0, 50, 110, 110)
    with pytest.raises(RuntimeError, match="outside"):
        visual_capture.capture_region_box((1000, 900), crop=[1200, 50, 100, 60])


def test_region_relative_crop_uses_region_as_origin():
    assert visual_capture.capture_region_box(
        (1000, 900),
        region="device-detail",
        crop=[10, 20, 200, 50],
        bottom_fraction=0.25,
        crop_relative_to_region=True,
    ) == (10, 695, 210, 745)
    assert visual_capture.capture_region_box(
        (1000, 900),
        region="device-detail",
        crop=[10, 20, 200, 50],
        bottom_fraction=0.25,
    ) == (10, 20, 210, 70)
    with pytest.raises(RuntimeError, match="outside the region"):
        visual_capture.capture_region_box(
            (1000, 900),
            region="device-detail",
            crop=[10, 300, 200, 50],
            bottom_fraction=0.25,
            crop_relative_to_region=True,
        )


def test_unknown_capture_region_is_rejected():
    with pytest.raises(RuntimeError, match="Unknown Ableton visual capture region"):
        visual_capture.capture_region_box((1000, 900), "browser")


def test_postprocess_capture_crops_and_downscales(tmp_path):
    Image = pytest.importorskip("PIL.Image")
    output = tmp_path / "live.png"
    Image.new("RGB", (200, 100), "black").save(output)

    result = visual_capture.postprocess_capture(output, region="device-detail", bottom_fraction=0.5, max_width=50)

    assert result["source_size"] == [200, 100]
    assert result["crop_box"] == [0, 50, 200, 100]
    assert result["size"][0] <= 50
    assert result["content"]["blank"] is True
    assert output.stat().st_size > 0


def test_postprocess_capture_supports_region_relative_crop(tmp_path):
    Image = pytest.importorskip("PIL.Image")
    output = tmp_path / "live.png"
    Image.new("RGB", (200, 100), "black").save(output)

    result = visual_capture.postprocess_capture(
        output,
        region="device-detail",
        crop=[10, 5, 40, 20],
        crop_relative_to_region=True,
        bottom_fraction=0.5,
    )

    assert result["crop_box"] == [10, 55, 50, 75]
    assert result["size"] == [40, 20]


def test_postprocess_capture_collects_full_window_content_stats_without_crop(tmp_path):
    Image = pytest.importorskip("PIL.Image")
    output = tmp_path / "live.png"
    image = Image.new("RGB", (40, 20), "black")
    for x in range(10, 30):
        for y in range(5, 15):
            image.putpixel((x, y), (240, 240, 240))
    image.save(output)

    result = visual_capture.postprocess_capture(output)

    assert result["source_size"] == [40, 20]
    assert result["size"] == [40, 20]
    assert result["crop_box"] is None
    assert result["content"]["blank"] is False


def test_visual_capture_cli_passes_crop_relative_to_region(monkeypatch, capsys):
    captured = {}

    def fake_capture(**kwargs):
        captured.update(kwargs)
        return {"ok": True, "path": "/tmp/live.png"}

    monkeypatch.setattr(visual_capture, "capture_ableton_window", fake_capture)

    assert visual_capture.main([
        "--output", "/tmp/live.png",
        "--region", "device-detail",
        "--crop", "1,2,3,4",
        "--crop-relative-to-region",
        "--bottom-fraction", "0.5",
        "--max-width", "800",
        "--max-height", "300",
    ]) == 0
    capsys.readouterr()
    assert captured["crop_relative_to_region"] is True
    assert captured["bottom_fraction"] == 0.5
    assert captured["max_width"] == 800
    assert captured["max_height"] == 300


def test_capture_blank_full_window_includes_validation_blocker(monkeypatch, tmp_path):
    Image = pytest.importorskip("PIL.Image")
    monkeypatch.setattr(visual_capture, "list_platform_windows", lambda: [
        WindowInfo(
            platform="Darwin",
            id=100,
            title="vibe-m4l",
            owner="Live",
            process_path="/Applications/Ableton Live Suite.app/Contents/MacOS/Live",
            bundle_id="com.ableton.live",
            bounds={"x": 0, "y": 33, "width": 1200, "height": 800},
        )
    ])
    monkeypatch.setattr(visual_capture, "capture_window", lambda _window, output, _backend: Image.new("RGB", (200, 100), "black").save(output) or "fake")

    result = visual_capture.capture_ableton_window(output_path=tmp_path / "live.png")

    assert result["warning"] == "blank_capture"
    assert result["validation_blocker"] == "blank_capture_invalid"
    assert "restart the terminal" in result["permission_hint"]


def test_capture_blank_result_includes_validation_blocker(monkeypatch, tmp_path):
    Image = pytest.importorskip("PIL.Image")
    monkeypatch.setattr(visual_capture, "list_platform_windows", lambda: [
        WindowInfo(
            platform="Darwin",
            id=100,
            title="vibe-m4l",
            owner="Live",
            process_path="/Applications/Ableton Live Suite.app/Contents/MacOS/Live",
            bundle_id="com.ableton.live",
            bounds={"x": 0, "y": 33, "width": 1200, "height": 800},
        )
    ])

    def fake_capture(_window, output, _backend):
        Image.new("RGB", (200, 100), "black").save(output)
        return "fake"

    monkeypatch.setattr(visual_capture, "capture_window", fake_capture)

    result = visual_capture.capture_ableton_window(output_path=tmp_path / "live.png", max_width=100)

    assert result["warning"] == "blank_capture"
    assert result["validation_blocker"] == "blank_capture_invalid"
    assert result["next_action"] == "unlock_or_wake_display_before_visual_e2e"
    assert "Screen Recording permission" in result["permission_hint"]


def test_image_content_stats_detects_nonblank_capture():
    Image = pytest.importorskip("PIL.Image")
    image = Image.new("RGB", (40, 20), "black")
    for x in range(10, 30):
        for y in range(5, 15):
            image.putpixel((x, y), (220, 220, 220))

    stats = visual_capture.image_content_stats(image)

    assert stats["blank"] is False
    assert stats["bbox"] == [10, 5, 30, 15]
    assert stats["nonblack_fraction"] > 0.1


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


def test_windows_capture_rejects_empty_title(tmp_path):
    window = WindowInfo(
        platform="Windows",
        id=100,
        title="",
        owner="Ableton Live",
        process_path=r"C:\Program Files\Ableton\Ableton Live Suite\Program\Ableton Live Suite.exe",
    )
    with pytest.raises(RuntimeError, match="non-empty Ableton Live window title"):
        visual_capture.capture_window(window, tmp_path / "live.png", backend="windows-capture")


def test_windows_capture_rejects_stale_window_id(monkeypatch, tmp_path):
    target = WindowInfo(
        platform="Windows",
        id=100,
        title="Untitled",
        owner="Ableton Live",
        process_path=r"C:\Program Files\Ableton\Ableton Live Suite\Program\Ableton Live Suite.exe",
    )
    monkeypatch.setattr(visual_capture, "list_platform_windows", lambda: [
        WindowInfo(
            platform="Windows",
            id=101,
            title="Untitled",
            owner="Ableton Live",
            process_path=r"C:\Program Files\Ableton\Ableton Live Suite\Program\Ableton Live Suite.exe",
        ),
    ])

    with pytest.raises(RuntimeError, match="window id .* could not be re-verified"):
        visual_capture.capture_window(target, tmp_path / "live.png", backend="windows-capture")


def test_windows_capture_rejects_duplicate_ableton_titles(monkeypatch, tmp_path):
    target = WindowInfo(
        platform="Windows",
        id=100,
        title="Untitled",
        owner="Ableton Live",
        process_path=r"C:\Program Files\Ableton\Ableton Live Suite\Program\Ableton Live Suite.exe",
    )
    monkeypatch.setattr(visual_capture, "list_platform_windows", lambda: [
        target,
        WindowInfo(
            platform="Windows",
            id=101,
            title="Untitled",
            owner="Ableton Live",
            process_path=r"C:\Program Files\Ableton\Ableton Live Suite\Program\Ableton Live Suite.exe",
        ),
    ])

    with pytest.raises(RuntimeError, match="multiple Ableton Live windows share the title"):
        visual_capture.capture_window(target, tmp_path / "live.png", backend="windows-capture")


def test_windows_capture_rejects_title_shared_with_non_ableton(monkeypatch, tmp_path):
    target = WindowInfo(
        platform="Windows",
        id=100,
        title="Untitled",
        owner="Ableton Live",
        process_path=r"C:\Program Files\Ableton\Ableton Live Suite\Program\Ableton Live Suite.exe",
    )
    monkeypatch.setattr(visual_capture, "list_platform_windows", lambda: [
        target,
        WindowInfo(
            platform="Windows",
            id=200,
            title="Untitled",
            owner="Notes",
            process_path=r"C:\Windows\notepad.exe",
        ),
    ])

    with pytest.raises(RuntimeError, match="shared by non-Ableton windows"):
        visual_capture.capture_window(target, tmp_path / "live.png", backend="windows-capture")


def test_visual_capture_cli_returns_json_error(monkeypatch, capsys):
    monkeypatch.setattr(visual_capture, "capture_ableton_window", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("blocked")))
    assert visual_capture.main([]) == 1
    output = capsys.readouterr().out
    assert '"ok": false' in output
    assert '"blocked"' in output
