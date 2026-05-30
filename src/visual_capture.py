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
# The Max for Live console is a top-level window hosted inside Live's own
# process (Max for Live embeds the Max runtime in Live), so it is owned by
# Live and titled "Max for Live". Capturing it lets an agent read Max-level
# errors (object-not-found, native errors, bridge stderr) that never reach a
# plugin's own JS logging.
MAX_CONSOLE_WINDOW_TITLE = "Max for Live"


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


def list_max_console_windows() -> list[WindowInfo]:
    # Prefer on-screen, then larger: if an M4L patcher editor (same owner +
    # "Max for Live" title) is open alongside the console, this favors the
    # visible one.
    return sorted(
        [window for window in list_platform_windows() if is_max_console_window(window)],
        key=lambda window: (1 if window.onscreen else 0, window_area(window)),
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


def is_max_console_window(window: WindowInfo) -> bool:
    # The Max for Live console window is owned by Live and titled "Max for
    # Live" (M4L hosts the Max runtime inside Live's process). Caveat: an open
    # M4L *patcher editor* shares that owner+title; when both are open,
    # on-screen + largest wins in select_max_console_window.
    if str(window.title or "") != MAX_CONSOLE_WINDOW_TITLE:
        return False
    process_name = (executable_stem(window.process_path) or executable_stem(window.owner)).lower()
    return window.bundle_id == ABLETON_BUNDLE_ID or process_name == "live" or process_name.startswith("ableton live")


def select_max_console_window(title_contains: str | None = None) -> WindowInfo:
    windows = list_max_console_windows()
    if title_contains:
        needle = title_contains.lower()
        windows = [window for window in windows if needle in window.title.lower()]
    if not windows:
        suffix = " matching %r" % title_contains if title_contains else ""
        raise RuntimeError(
            "No Max for Live console window%s is available to capture. Open it in Live "
            "(a device's Max edit button → the Max Console) so the window exists first." % suffix
        )
    return windows[0]


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
    crop_relative_to_region: bool = False,
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
    postprocess = postprocess_capture(output, region, crop, crop_relative_to_region, bottom_fraction, max_width, max_height)
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


def capture_max_console_window(
    output_path: str | os.PathLike[str] | None = None,
    title_contains: str | None = None,
    list_only: bool = False,
    backend: str = "auto",
    region: str | None = None,
    crop: Any = None,
    crop_relative_to_region: bool = False,
    bottom_fraction: float | None = None,
    max_width: int | None = None,
    max_height: int | None = None,
    display: int | None = None,
) -> dict[str, Any]:
    # The Max Console's content lives on a GPU/IOSurface layer that the legacy
    # per-window APIs (screencapture -l / Quartz CGWindowListCreateImage) can't
    # read — they return all-black even when the window is fully visible. So on
    # macOS the default is the 'sck' (ScreenCaptureKit) backend, which reads the
    # window content directly and in isolation, regardless of z-order. Two
    # alternatives remain: backend='screencapture'/'quartz' (legacy, ~always
    # black here — kept for parity/diagnostics) and display=<n> (grab the whole
    # display the console is parked on; useful if SCK is unavailable, e.g.
    # macOS < 14).
    windows = list_max_console_windows()
    if list_only:
        result = {"windows": [window_result(window) for window in windows], "count": len(windows)}
        try:
            result["displays"] = list_macos_displays()
        except Exception as exc:
            result["displays_error"] = str(exc)
        return result
    output = Path(output_path) if output_path else default_max_console_path()
    output.parent.mkdir(parents=True, exist_ok=True)

    if display is not None:
        backend_used = capture_macos_display(int(display), output)
        postprocess = postprocess_capture(output, region, crop, crop_relative_to_region, bottom_fraction, max_width, max_height)
        result = {
            "ok": True,
            "path": str(output),
            "backend": backend_used,
            "display": int(display),
            "postprocess": postprocess,
        }
        if postprocess.get("content", {}).get("blank"):
            result.update(blank_capture_guidance())
            result["next_action"] = "display_blank_try_another_display_index_see_list_only_displays"
        return result

    window = select_max_console_window(title_contains)
    # On macOS, default to ScreenCaptureKit (the legacy backends are black here).
    console_backend = backend
    if backend == "auto" and platform.system() == "Darwin":
        console_backend = "sck"
    backend_used = capture_window(window, output, console_backend)
    postprocess = postprocess_capture(output, region, crop, crop_relative_to_region, bottom_fraction, max_width, max_height)
    result = {
        "ok": True,
        "path": str(output),
        "backend": backend_used,
        "window": window_result(window),
        "postprocess": postprocess,
    }
    if postprocess.get("content", {}).get("blank"):
        result.update(blank_capture_guidance())
        result["warning"] = "blank_capture"
        if backend_used in ("screencapture", "quartz"):
            # Legacy backends can't read the console's GPU-backed surface.
            result["next_action"] = "max_console_legacy_backend_returns_black_use_default_sck_backend_or_display_index"
            result["hint"] = "Drop backend (defaults to 'sck' on macOS 14+) or pass display=<n>; list_only=true enumerates displays."
        else:
            result["next_action"] = "max_console_blank_check_console_visible_or_try_display_index"
            result["hint"] = "Ensure the Max Console is open/visible, or pass display=<n> (list_only=true enumerates displays)."
    return result


def blank_capture_guidance() -> dict[str, str]:
    return {
        "warning": "blank_capture",
        "validation_blocker": "blank_capture_invalid",
        "next_action": "unlock_or_wake_display_before_visual_e2e",
        "permission_hint": "On macOS, if Screen Recording permission was just granted, restart the terminal or MCP client before retrying capture.",
    }


def capture_window(window: WindowInfo, output: Path, backend: str = "auto") -> str:
    if not (is_ableton_live_window(window) or is_max_console_window(window)):
        raise RuntimeError("Refusing to capture a non-Ableton Live / non-Max-Console window")
    if window.platform == "Darwin":
        return capture_macos_window(window, output, backend)
    if window.platform == "Windows":
        capture_windows_window(window, output, backend)
        return "windows-capture"
    raise RuntimeError("Unsupported visual capture platform: %s" % window.platform)


def list_macos_displays() -> list[dict[str, Any]]:
    try:
        from Quartz import (
            CGGetActiveDisplayList,
            CGDisplayBounds,
            CGDisplayPixelsHigh,
            CGDisplayPixelsWide,
            CGMainDisplayID,
        )
    except ImportError as exc:
        raise RuntimeError("macOS display enumeration requires pyobjc-framework-Quartz") from exc
    err, display_ids, count = CGGetActiveDisplayList(16, None, None)
    if err:
        raise RuntimeError("CGGetActiveDisplayList failed with error %s" % err)
    main_id = int(CGMainDisplayID())
    displays = []
    for offset in range(count):
        did = int(display_ids[offset])
        bounds = CGDisplayBounds(did)
        # screencapture -D uses a 1-based index; main display is 1. We surface
        # the bounds + main flag so the caller can match a window to a display.
        displays.append({
            "screencapture_index": offset + 1,
            "display_id": did,
            "is_main": did == main_id,
            "width": int(CGDisplayPixelsWide(did)),
            "height": int(CGDisplayPixelsHigh(did)),
            "origin": {"x": int(bounds.origin.x), "y": int(bounds.origin.y)},
        })
    return displays


def capture_macos_display(display_index: int, output: Path) -> str:
    if platform.system() != "Darwin":
        raise RuntimeError("Display-index capture is currently macOS-only")
    if int(display_index) < 1:
        raise RuntimeError("display index is 1-based (1 = main display)")
    try:
        subprocess.run(
            ["screencapture", "-x", "-D", str(int(display_index)), "-t", "png", str(output)],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or "").strip() or "exit status %s" % exc.returncode
        raise RuntimeError(detail) from exc
    if not output.exists() or output.stat().st_size <= 0:
        raise RuntimeError("no image was written for display index %s" % display_index)
    return "screencapture-display"


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
    if backend not in ("auto", "screencapture", "quartz", "sck"):
        raise RuntimeError("macOS visual capture supports backend='auto', 'screencapture', 'quartz', or 'sck'")
    # ScreenCaptureKit is the only backend that reads GPU/IOSurface-backed
    # window content (e.g. the Max Console — screencapture/quartz return black)
    # and it captures the window in isolation regardless of z-order. It is NOT
    # in the "auto" chain because for ordinary windows screencapture is faster
    # and the legacy path's black output wouldn't trip an exception here (the
    # blank is only detected later in postprocess), so it must be opt-in.
    if backend == "sck":
        capture_macos_window_sck(window, output)
        return "sck"
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
    write_cgimage_png(image, output)


def write_cgimage_png(image: Any, output: Path) -> None:
    from Quartz import (
        CFURLCreateFromFileSystemRepresentation,
        CGImageDestinationAddImage,
        CGImageDestinationCreateWithURL,
        CGImageDestinationFinalize,
        CGImageGetHeight,
        CGImageGetWidth,
    )
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


def _sck_await(call, what: str, timeout: float = 10.0):
    # ScreenCaptureKit's APIs are completion-handler (async) only. The handlers
    # fire on a GCD background queue, so blocking this thread on an Event does
    # not deadlock them — no CFRunLoop spin needed.
    import threading

    box: dict[str, Any] = {}
    done = threading.Event()

    def handler(result, error):
        box["result"] = result
        box["error"] = error
        done.set()

    call(handler)
    if not done.wait(timeout=timeout):
        raise RuntimeError("ScreenCaptureKit timed out trying to %s" % what)
    if box.get("error"):
        raise RuntimeError("ScreenCaptureKit failed to %s: %s" % (what, box["error"]))
    return box.get("result")


def capture_macos_window_sck(window: WindowInfo, output: Path, scale: int = 2) -> None:
    try:
        from ScreenCaptureKit import (
            SCContentFilter,
            SCScreenshotManager,
            SCShareableContent,
            SCStreamConfiguration,
        )
    except ImportError as exc:
        raise RuntimeError(
            "ScreenCaptureKit capture requires pyobjc-framework-ScreenCaptureKit (pip install pyobjc-framework-ScreenCaptureKit)"
        ) from exc
    if not hasattr(SCScreenshotManager, "captureImageWithFilter_configuration_completionHandler_"):
        raise RuntimeError("SCScreenshotManager.captureImage… requires macOS 14+")
    content = _sck_await(
        lambda h: SCShareableContent.getShareableContentWithCompletionHandler_(h),
        "enumerate shareable content",
    )
    target = None
    for candidate in content.windows():
        if int(candidate.windowID()) == int(window.id):
            target = candidate
            break
    if target is None:
        raise RuntimeError(
            "window id %s is not in ScreenCaptureKit's shareable set (off-screen, minimized, or not shareable)" % window.id
        )
    content_filter = SCContentFilter.alloc().initWithDesktopIndependentWindow_(target)
    config = SCStreamConfiguration.alloc().init()
    frame = target.frame()
    config.setWidth_(max(1, int(frame.size.width * scale)))
    config.setHeight_(max(1, int(frame.size.height * scale)))
    config.setShowsCursor_(False)
    image = _sck_await(
        lambda h: SCScreenshotManager.captureImageWithFilter_configuration_completionHandler_(content_filter, config, h),
        "capture window image",
    )
    if image is None:
        raise RuntimeError("ScreenCaptureKit returned no image")
    write_cgimage_png(image, output)


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
    ensure_windows_capture_title_unambiguous(window)
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


def ensure_windows_capture_title_unambiguous(window: WindowInfo) -> None:
    title = str(window.title or "")
    if not title.strip():
        raise RuntimeError("Windows Ableton visual capture requires a non-empty Ableton Live window title")
    matches = [
        candidate for candidate in list_platform_windows()
        if candidate.platform == "Windows" and str(candidate.title or "") == title
    ]
    if not matches:
        raise RuntimeError(
            "Refusing Windows title-based capture because the Ableton Live window title %r could not be re-verified"
            % title
        )
    ableton_matches = [candidate for candidate in matches if is_ableton_live_window(candidate)]
    non_ableton_matches = [candidate for candidate in matches if not is_ableton_live_window(candidate)]
    if non_ableton_matches:
        raise RuntimeError(
            "Refusing Windows title-based capture because the Ableton Live window title %r is shared by non-Ableton windows"
            % title
        )
    target_matches = [candidate for candidate in ableton_matches if str(candidate.id) == str(window.id)]
    if not target_matches:
        raise RuntimeError(
            "Refusing Windows title-based capture because the Ableton Live window id %r could not be re-verified"
            % window.id
        )
    if len(ableton_matches) > 1:
        raise RuntimeError(
            "Refusing Windows title-based capture because multiple Ableton Live windows share the title %r"
            % title
        )


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
    crop_relative_to_region: bool = False,
    bottom_fraction: float | None = None,
    max_width: int | None = None,
    max_height: int | None = None,
) -> dict[str, Any]:
    needs_write = bool(region or crop or max_width or max_height)
    try:
        from PIL import Image
    except ImportError as exc:
        if needs_write:
            raise RuntimeError("Ableton visual capture crop/downscale requires the Pillow package") from exc
        return {"content_error": "Pillow package unavailable; blank detection skipped"}
    with Image.open(output) as image:
        source_size = [int(image.width), int(image.height)]
        box = capture_region_box((image.width, image.height), region, crop, bottom_fraction, crop_relative_to_region)
        if box is not None:
            image = image.crop(box)
        max_size = normalized_max_size(max_width, max_height)
        if max_size is not None:
            resampling = getattr(Image, "Resampling", Image).LANCZOS
            image.thumbnail(max_size, resampling)
        if image.mode not in ("RGB", "RGBA"):
            image = image.convert("RGBA")
        content = image_content_stats(image)
        if needs_write or image.format != "PNG":
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
    crop_relative_to_region: bool = False,
) -> tuple[int, int, int, int] | None:
    width, height = int(image_size[0]), int(image_size[1])
    if width <= 0 or height <= 0:
        raise RuntimeError("Cannot crop an empty capture")
    normalized = normalize_region_name(region)
    region_box = capture_base_region_box(width, height, normalized, bottom_fraction, region)
    if crop is not None:
        x, y, crop_width, crop_height = parse_crop(crop)
        if crop_width <= 0 or crop_height <= 0:
            raise RuntimeError("Crop width and height must be positive")
        base = region_box if crop_relative_to_region and region_box is not None else (0, 0, width, height)
        base_left, base_top, base_right, base_bottom = base
        left = clamp_int(base_left + x, base_left, base_right)
        top = clamp_int(base_top + y, base_top, base_bottom)
        right = clamp_int(base_left + x + crop_width, base_left, base_right)
        bottom = clamp_int(base_top + y + crop_height, base_top, base_bottom)
        if right <= left or bottom <= top:
            boundary = "region" if crop_relative_to_region and region_box is not None else "captured Ableton Live window"
            raise RuntimeError("Crop falls outside the %s" % boundary)
        return (left, top, right, bottom)
    return region_box


def capture_base_region_box(width: int, height: int, normalized: str | None, bottom_fraction: float | None, raw_region: str | None = None) -> tuple[int, int, int, int] | None:
    if normalized is None:
        return None
    if normalized == "device_detail":
        fraction = float(bottom_fraction) if bottom_fraction is not None else 0.34
        if fraction <= 0 or fraction > 1:
            raise RuntimeError("bottom_fraction must be greater than 0 and no more than 1")
        top = max(0, min(height - 1, int(round(height * (1 - fraction)))))
        return (0, top, width, height)
    raise RuntimeError("Unknown Ableton visual capture region: %s" % raw_region)


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


def default_max_console_path() -> Path:
    return state_dir() / ("max_console_%d.png" % int(time.time() * 1000))


def window_result(window: WindowInfo) -> dict[str, Any]:
    result = asdict(window)
    if result.get("process_path"):
        result["process_name"] = executable_stem(str(result["process_path"]))
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Capture only Ableton Live windows for visual validation.")
    parser.add_argument("--output", help="PNG output path. Defaults to the Ableton MCP state directory.")
    parser.add_argument("--title-contains", help="Optional substring filter applied only after Ableton Live windows are found.")
    parser.add_argument("--backend", default="auto", choices=("auto", "screencapture", "quartz", "sck", "windows-capture"))
    parser.add_argument("--region", help="Optional post-capture region. Use 'device-detail' for Live's bottom device view.")
    parser.add_argument("--crop", help="Optional post-capture crop as x,y,width,height inside the Ableton Live window.")
    parser.add_argument("--crop-relative-to-region", action="store_true", help="Interpret --crop coordinates relative to --region instead of the full Ableton Live window.")
    parser.add_argument("--bottom-fraction", type=float, help="Bottom fraction used by --region device-detail. Defaults to 0.34.")
    parser.add_argument("--max-width", type=int, help="Downscale output to this maximum width.")
    parser.add_argument("--max-height", type=int, help="Downscale output to this maximum height.")
    parser.add_argument("--list", action="store_true", help="List capturable Ableton Live windows without capturing.")
    parser.add_argument("--max-console", action="store_true", help="Capture the Max Console window (Max for Live runtime log) instead of an Ableton Live window.")
    parser.add_argument("--display", type=int, help="With --max-console: capture this whole display (1=main) instead of the window. Needed because the Max Console's surface is unreadable via the per-window API.")
    args = parser.parse_args(argv)
    try:
        if args.max_console:
            result = capture_max_console_window(
                output_path=args.output,
                title_contains=args.title_contains,
                list_only=args.list,
                backend=args.backend,
                region=args.region,
                crop=args.crop,
                crop_relative_to_region=args.crop_relative_to_region,
                bottom_fraction=args.bottom_fraction,
                max_width=args.max_width,
                max_height=args.max_height,
                display=args.display,
            )
            print(json.dumps(result, indent=2, sort_keys=True))
            return 0 if result.get("ok") else 1
        result = capture_ableton_window(
            output_path=args.output,
            title_contains=args.title_contains,
            list_only=args.list,
            backend=args.backend,
            region=args.region,
            crop=args.crop,
            crop_relative_to_region=args.crop_relative_to_region,
            bottom_fraction=args.bottom_fraction,
            max_width=args.max_width,
            max_height=args.max_height,
        )
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, indent=2, sort_keys=True))
        return 1
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
