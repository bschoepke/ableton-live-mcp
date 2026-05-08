from __future__ import annotations

import argparse
import json
import os
import platform
import plistlib
import re
import subprocess
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from ableton_paths import state_dir


ABLETON_BUNDLE_ID = "com.ableton.live"


@dataclass(frozen=True)
class WindowInfo:
    platform: str
    id: int | str
    title: str
    owner: str
    pid: int | None = None
    process_path: str = ""
    bundle_id: str = ""
    sharing_state: int | None = None
    onscreen: bool | None = None
    bounds: dict[str, int] | None = None


def list_ableton_windows() -> list[WindowInfo]:
    return sorted(
        [window for window in list_platform_windows() if is_ableton_live_window(window)],
        key=window_area,
        reverse=True,
    )


def list_platform_windows() -> list[WindowInfo]:
    system = platform.system()
    if system == "Darwin":
        return list_macos_windows()
    if system == "Windows":
        return list_windows_windows()
    raise RuntimeError("Ableton visual capture currently supports macOS and Windows only")


def is_ableton_live_window(window: WindowInfo) -> bool:
    system = window.platform
    process_name = executable_stem(window.process_path) or executable_stem(window.owner)
    if system == "Darwin":
        if window.bundle_id == ABLETON_BUNDLE_ID:
            return True
        app_name = macos_app_name(window.process_path)
        return bool(app_name and app_name.lower().startswith("ableton live") and process_name.lower() == "live")
    if system == "Windows":
        return process_name.lower().startswith("ableton live")
    return False


def select_ableton_window(title_contains: str | None = None) -> WindowInfo:
    windows = list_ableton_windows()
    if title_contains:
        needle = title_contains.lower()
        windows = [window for window in windows if needle in window.title.lower()]
    if not windows:
        suffix = " matching %r" % title_contains if title_contains else ""
        raise RuntimeError("No Ableton Live window%s is available to capture" % suffix)
    return windows[0]


def window_area(window: WindowInfo) -> int:
    bounds = window.bounds or {}
    return max(0, int(bounds.get("width") or 0)) * max(0, int(bounds.get("height") or 0))


def capture_ableton_window(
    output_path: str | os.PathLike[str] | None = None,
    title_contains: str | None = None,
    list_only: bool = False,
    backend: str = "auto",
    region: str | None = None,
    crop: Any = None,
    bottom_fraction: float | None = None,
    max_width: int | None = None,
    max_height: int | None = None,
) -> dict[str, Any]:
    windows = list_ableton_windows()
    if list_only:
        return {"windows": [window_result(window) for window in windows], "count": len(windows)}
    window = select_ableton_window(title_contains)
    output = Path(output_path) if output_path else default_capture_path()
    output.parent.mkdir(parents=True, exist_ok=True)
    backend_used = capture_window(window, output, backend)
    postprocess = postprocess_capture(output, region, crop, bottom_fraction, max_width, max_height)
    result = {
        "ok": True,
        "path": str(output),
        "backend": backend_used,
        "window": window_result(window),
        "postprocess": postprocess,
    }
    if postprocess.get("content", {}).get("blank"):
        result.update(blank_capture_guidance())
    return result


def blank_capture_guidance() -> dict[str, str]:
    return {
        "warning": "blank_capture",
        "validation_blocker": "blank_capture_invalid",
        "next_action": "unlock_or_wake_display_before_visual_e2e",
    }


def capture_window(window: WindowInfo, output: Path, backend: str = "auto") -> str:
    if not is_ableton_live_window(window):
        raise RuntimeError("Refusing to capture a non-Ableton Live window")
    if window.platform == "Darwin":
        return capture_macos_window(window, output, backend)
    if window.platform == "Windows":
        capture_windows_window(window, output, backend)
        return "windows-capture"
    raise RuntimeError("Unsupported visual capture platform: %s" % window.platform)


def list_macos_windows() -> list[WindowInfo]:
    try:
        from Quartz import CGWindowListCopyWindowInfo, kCGNullWindowID, kCGWindowListOptionAll
    except ImportError as exc:
        raise RuntimeError("macOS Ableton visual capture requires pyobjc-framework-Quartz") from exc
    raw_windows = CGWindowListCopyWindowInfo(kCGWindowListOptionAll, kCGNullWindowID) or []
    windows = []
    for raw in raw_windows:
        title = str(raw.get("kCGWindowName") or "")
        owner = str(raw.get("kCGWindowOwnerName") or "")
        pid = int(raw.get("kCGWindowOwnerPID") or 0) or None
        process_path = macos_process_path(pid) if pid else ""
        bounds = normalize_bounds(raw.get("kCGWindowBounds") or {})
        windows.append(WindowInfo(
            platform="Darwin",
            id=int(raw.get("kCGWindowNumber") or 0),
            title=title,
            owner=owner,
            pid=pid,
            process_path=process_path,
            bundle_id=macos_bundle_id(process_path),
            sharing_state=int(raw.get("kCGWindowSharingState") or 0),
            onscreen=bool(raw.get("kCGWindowIsOnscreen")) if raw.get("kCGWindowIsOnscreen") is not None else None,
            bounds=bounds,
        ))
    return windows


def capture_macos_window(window: WindowInfo, output: Path, backend: str = "auto") -> str:
    if backend not in ("auto", "screencapture", "quartz"):
        raise RuntimeError("macOS Ableton visual capture supports backend='auto', 'screencapture', or 'quartz'")
    errors = []
    if backend in ("auto", "screencapture"):
        try:
            capture_macos_window_screencapture(window, output)
            return "screencapture"
        except Exception as exc:
            errors.append("screencapture: %s" % exc)
            if backend == "screencapture":
                raise RuntimeError(errors[0]) from exc
    if backend in ("auto", "quartz"):
        try:
            capture_macos_window_quartz(window, output)
            return "quartz"
        except Exception as exc:
            errors.append("quartz: %s" % exc)
            if backend == "quartz":
                raise RuntimeError(errors[-1]) from exc
    raise RuntimeError(
        "macOS Ableton visual capture failed; screen recording permission may be missing or Live may mark the window non-shareable. "
        + " | ".join(errors)
    )


def capture_macos_window_screencapture(window: WindowInfo, output: Path) -> None:
    try:
        subprocess.run(
            ["screencapture", "-x", "-l", str(window.id), str(output)],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or "").strip() or "exit status %s" % exc.returncode
        raise RuntimeError(detail) from exc
    if not output.exists() or output.stat().st_size <= 0:
        raise RuntimeError("no image was written")


def capture_macos_window_quartz(window: WindowInfo, output: Path) -> None:
    try:
        from Quartz import (
            CFURLCreateFromFileSystemRepresentation,
            CGImageDestinationAddImage,
            CGImageDestinationCreateWithURL,
            CGImageDestinationFinalize,
            CGImageGetHeight,
            CGImageGetWidth,
            CGWindowListCreateImage,
            CGRectNull,
            kCGWindowImageBoundsIgnoreFraming,
            kCGWindowListOptionIncludingWindow,
        )
    except ImportError as exc:
        raise RuntimeError("pyobjc-framework-Quartz is not installed") from exc
    image = CGWindowListCreateImage(
        CGRectNull,
        kCGWindowListOptionIncludingWindow,
        int(window.id),
        kCGWindowImageBoundsIgnoreFraming,
    )
    if image is None:
        raise RuntimeError("no image returned; sharing_state=%s onscreen=%s" % (window.sharing_state, window.onscreen))
    width = int(CGImageGetWidth(image) or 0)
    height = int(CGImageGetHeight(image) or 0)
    if width <= 0 or height <= 0:
        raise RuntimeError("empty image returned")
    raw_path = os.fsencode(str(output))
    url = CFURLCreateFromFileSystemRepresentation(None, raw_path, len(raw_path), False)
    destination = CGImageDestinationCreateWithURL(url, "public.png", 1, None)
    if destination is None:
        raise RuntimeError("could not create PNG destination")
    CGImageDestinationAddImage(destination, image, None)
    if not CGImageDestinationFinalize(destination):
        raise RuntimeError("could not finalize PNG")
    if not output.exists() or output.stat().st_size <= 0:
        raise RuntimeError("no image was written")


def list_windows_windows() -> list[WindowInfo]:
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    windows: list[WindowInfo] = []

    enum_proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    @enum_proc
    def callback(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return True
        buffer = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buffer, length + 1)
        title = buffer.value
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        rect = wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(rect))
        process_path = windows_process_path(int(pid.value))
        windows.append(WindowInfo(
            platform="Windows",
            id=int(hwnd),
            title=title,
            owner=executable_stem(process_path),
            pid=int(pid.value) or None,
            process_path=process_path,
            bounds={
                "x": int(rect.left),
                "y": int(rect.top),
                "width": int(rect.right - rect.left),
                "height": int(rect.bottom - rect.top),
            },
        ))
        return True

    user32.EnumWindows(callback, 0)
    return windows


def capture_windows_window(window: WindowInfo, output: Path, backend: str = "auto") -> None:
    if backend not in ("auto", "windows-capture"):
        raise RuntimeError("Windows Ableton visual capture supports backend='auto' or 'windows-capture'")
    try:
        from windows_capture import InternalCaptureControl, WindowsCapture
    except ImportError as exc:
        raise RuntimeError("Windows Ableton visual capture requires the windows-capture package") from exc

    saved = {"ok": False}
    capture = WindowsCapture(cursor_capture=False, draw_border=False, monitor_index=None, window_name=window.title)

    @capture.event
    def on_frame_arrived(frame, capture_control: InternalCaptureControl):
        frame.save_as_image(str(output))
        saved["ok"] = True
        capture_control.stop()

    @capture.event
    def on_closed():
        return None

    capture.start()
    if not saved["ok"] or not output.exists() or output.stat().st_size <= 0:
        raise RuntimeError("Windows Ableton visual capture produced no image")


def windows_process_path(pid: int) -> str:
    import ctypes
    from ctypes import wintypes

    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return ""
    try:
        size = wintypes.DWORD(32768)
        buffer = ctypes.create_unicode_buffer(size.value)
        if kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
            return buffer.value
        return ""
    finally:
        kernel32.CloseHandle(handle)


def macos_process_path(pid: int | None) -> str:
    if not pid:
        return ""
    try:
        result = subprocess.run(
            ["ps", "-p", str(pid), "-o", "comm="],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=1,
        )
    except Exception:
        return ""
    return result.stdout.strip()


def macos_bundle_id(process_path: str) -> str:
    app = macos_app_bundle(process_path)
    if not app:
        return ""
    info = app / "Contents" / "Info.plist"
    try:
        with info.open("rb") as handle:
            plist = plistlib.load(handle)
    except Exception:
        return ""
    return str(plist.get("CFBundleIdentifier") or "")


def macos_app_name(process_path: str) -> str:
    app = macos_app_bundle(process_path)
    return app.stem if app else ""


def macos_app_bundle(process_path: str) -> Path | None:
    if not process_path:
        return None
    path = Path(process_path)
    for item in (path, *path.parents):
        if item.suffix == ".app":
            return item
    return None


def executable_stem(path_or_name: str) -> str:
    if not path_or_name:
        return ""
    name = re.split(r"[\\/]", str(path_or_name))[-1]
    if "." in name:
        name = name.rsplit(".", 1)[0]
    return name


def normalize_bounds(bounds: dict[str, Any]) -> dict[str, int]:
    return {
        "x": int(bounds.get("X") or bounds.get("x") or 0),
        "y": int(bounds.get("Y") or bounds.get("y") or 0),
        "width": int(bounds.get("Width") or bounds.get("width") or 0),
        "height": int(bounds.get("Height") or bounds.get("height") or 0),
    }


def postprocess_capture(
    output: Path,
    region: str | None = None,
    crop: Any = None,
    bottom_fraction: float | None = None,
    max_width: int | None = None,
    max_height: int | None = None,
) -> dict[str, Any]:
    needs_image = bool(region or crop or max_width or max_height)
    if not needs_image:
        return {}
    try:
        from PIL import Image
    except ImportError as exc:
        raise RuntimeError("Ableton visual capture crop/downscale requires the Pillow package") from exc
    with Image.open(output) as image:
        source_size = [int(image.width), int(image.height)]
        box = capture_region_box((image.width, image.height), region, crop, bottom_fraction)
        if box is not None:
            image = image.crop(box)
        max_size = normalized_max_size(max_width, max_height)
        if max_size is not None:
            resampling = getattr(Image, "Resampling", Image).LANCZOS
            image.thumbnail(max_size, resampling)
        if image.mode not in ("RGB", "RGBA"):
            image = image.convert("RGBA")
        content = image_content_stats(image)
        image.save(output, format="PNG")
        return {
            "source_size": source_size,
            "size": [int(image.width), int(image.height)],
            "crop_box": list(box) if box is not None else None,
            "region": normalize_region_name(region) if region else None,
            "max_size": list(max_size) if max_size is not None else None,
            "content": content,
        }


def capture_region_box(
    image_size: tuple[int, int],
    region: str | None = None,
    crop: Any = None,
    bottom_fraction: float | None = None,
) -> tuple[int, int, int, int] | None:
    width, height = int(image_size[0]), int(image_size[1])
    if width <= 0 or height <= 0:
        raise RuntimeError("Cannot crop an empty capture")
    if crop is not None:
        x, y, crop_width, crop_height = parse_crop(crop)
        if crop_width <= 0 or crop_height <= 0:
            raise RuntimeError("Crop width and height must be positive")
        left = clamp_int(x, 0, width)
        top = clamp_int(y, 0, height)
        right = clamp_int(x + crop_width, 0, width)
        bottom = clamp_int(y + crop_height, 0, height)
        if right <= left or bottom <= top:
            raise RuntimeError("Crop falls outside the captured Ableton Live window")
        return (left, top, right, bottom)
    normalized = normalize_region_name(region)
    if normalized is None:
        return None
    if normalized == "device_detail":
        fraction = float(bottom_fraction) if bottom_fraction is not None else 0.34
        if fraction <= 0 or fraction > 1:
            raise RuntimeError("bottom_fraction must be greater than 0 and no more than 1")
        top = max(0, min(height - 1, int(round(height * (1 - fraction)))))
        return (0, top, width, height)
    raise RuntimeError("Unknown Ableton visual capture region: %s" % region)


def parse_crop(crop: Any) -> tuple[int, int, int, int]:
    if isinstance(crop, str):
        parts = [part.strip() for part in crop.split(",")]
    elif isinstance(crop, (list, tuple)):
        parts = list(crop)
    else:
        raise RuntimeError("Crop must be a comma-separated string or [x, y, width, height]")
    if len(parts) != 4:
        raise RuntimeError("Crop must contain x, y, width, height")
    try:
        return tuple(int(round(float(part))) for part in parts)  # type: ignore[return-value]
    except (TypeError, ValueError) as exc:
        raise RuntimeError("Crop values must be numeric") from exc


def normalize_region_name(region: str | None) -> str | None:
    if region is None or str(region).strip() == "":
        return None
    normalized = re.sub(r"[-\s]+", "_", str(region).strip().lower())
    if normalized in ("bottom", "detail", "device_detail", "device_view", "device_chain"):
        return "device_detail"
    return normalized


def normalized_max_size(max_width: int | None, max_height: int | None) -> tuple[int, int] | None:
    width = int(max_width or 0)
    height = int(max_height or 0)
    if width < 0 or height < 0:
        raise RuntimeError("max_width and max_height must be non-negative")
    if width == 0 and height == 0:
        return None
    return (width or 1000000, height or 1000000)


def clamp_int(value: int, lower: int, upper: int) -> int:
    return max(lower, min(upper, int(value)))


def image_content_stats(image: Any, threshold: int = 8) -> dict[str, Any]:
    grayscale = image.convert("L")
    width, height = grayscale.size
    total = max(1, width * height)
    histogram = grayscale.histogram()
    nonblack = sum(histogram[threshold + 1:])
    mean_luma = sum(index * count for index, count in enumerate(histogram)) / total
    mask = grayscale.point(lambda pixel: 255 if pixel > threshold else 0)
    bbox = mask.getbbox()
    nonblack_fraction = nonblack / total
    return {
        "mean_luma": round(mean_luma, 3),
        "nonblack_fraction": round(nonblack_fraction, 6),
        "bbox": list(bbox) if bbox else None,
        "blank": bool(mean_luma < 2.0 or nonblack_fraction < 0.001),
    }


def default_capture_path() -> Path:
    return state_dir() / ("ableton_window_%d.png" % int(time.time() * 1000))


def window_result(window: WindowInfo) -> dict[str, Any]:
    result = asdict(window)
    if result.get("process_path"):
        result["process_name"] = executable_stem(str(result["process_path"]))
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Capture only Ableton Live windows for visual validation.")
    parser.add_argument("--output", help="PNG output path. Defaults to the Ableton MCP state directory.")
    parser.add_argument("--title-contains", help="Optional substring filter applied only after Ableton Live windows are found.")
    parser.add_argument("--backend", default="auto", choices=("auto", "screencapture", "quartz", "windows-capture"))
    parser.add_argument("--region", help="Optional post-capture region. Use 'device-detail' for Live's bottom device view.")
    parser.add_argument("--crop", help="Optional post-capture crop as x,y,width,height inside the Ableton Live window.")
    parser.add_argument("--bottom-fraction", type=float, help="Bottom fraction used by --region device-detail. Defaults to 0.34.")
    parser.add_argument("--max-width", type=int, help="Downscale output to this maximum width.")
    parser.add_argument("--max-height", type=int, help="Downscale output to this maximum height.")
    parser.add_argument("--list", action="store_true", help="List capturable Ableton Live windows without capturing.")
    args = parser.parse_args(argv)
    try:
        result = capture_ableton_window(
            args.output,
            args.title_contains,
            args.list,
            args.backend,
            args.region,
            args.crop,
            args.bottom_fraction,
            args.max_width,
            args.max_height,
        )
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, indent=2, sort_keys=True))
        return 1
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
