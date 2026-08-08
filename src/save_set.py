"""Save the current Live Set from outside Live.

Live's Object Model has no save API at all: an agent can mutate everything in a
set and persist nothing (field-hit 2026-08-08 — an unsaved agent-built track was
lost when the set was reloaded). The only reliable trigger is the application's
own save accelerator, so this module sends a platform-level Ctrl+S / Cmd+S to
the Ableton Live main window and then verifies the save actually happened by
watching the .als file's mtime (the LOM also exposes no dirty flag, so the
file on disk is the only ground truth).

Refuses when the set has never been saved (empty ``song.file_path``): the
keystroke would open a Save As dialog the agent cannot see or drive.
"""

from __future__ import annotations

import os
import platform
import subprocess
import time
from typing import Any


class SaveSetError(RuntimeError):
    pass


def current_set_file(bridge) -> str:
    path = bridge.request("eval", {"expr": "song.file_path"})
    return str(path or "")


def save_set(bridge, timeout: float = 10.0, poll_interval: float = 0.25) -> dict[str, Any]:
    file_path = current_set_file(bridge)
    if not file_path:
        return {
            "saved": False,
            "file_path": "",
            "error": "unsaved_set",
            "hint": (
                "This set has never been saved (song.file_path is empty), so the save "
                "keystroke would open a Save As dialog the agent cannot drive. Ask the "
                "user to do the first File > Save As manually; after that live_save_set works."
            ),
        }
    if not os.path.exists(file_path):
        raise SaveSetError("song.file_path %r does not exist on disk; cannot verify a save against it" % file_path)

    mtime_before = os.path.getmtime(file_path)
    send_save_keystroke()
    deadline = time.monotonic() + max(1.0, float(timeout))
    while time.monotonic() < deadline:
        mtime_now = os.path.getmtime(file_path)
        if mtime_now != mtime_before:
            return {
                "saved": True,
                "file_path": file_path,
                "mtime_before": mtime_before,
                "mtime_after": mtime_now,
            }
        time.sleep(poll_interval)
    return {
        "saved": False,
        "file_path": file_path,
        "error": "save_not_observed",
        "hint": (
            "Sent the save keystroke but the set file's mtime did not change within "
            "%.1fs. Most often the set simply had no unsaved changes — Live skips the "
            "rewrite entirely when the set is not dirty (verified in-Live 2026-08-08), "
            "so this is usually harmless. Otherwise: a modal dialog may be open or the "
            "window may not have taken focus. Check the Live window." % float(timeout)
        ),
    }


def send_save_keystroke() -> None:
    system = platform.system()
    if system == "Windows":
        _send_save_keystroke_windows()
    elif system == "Darwin":
        _send_save_keystroke_macos()
    else:
        raise SaveSetError("live_save_set supports Windows and macOS only")


# --- Windows: focus the Live main window, send Ctrl+S via SendInput ---------

def _send_save_keystroke_windows() -> None:
    import ctypes
    from ctypes import wintypes

    hwnd = _find_live_main_window_windows()
    if hwnd is None:
        raise SaveSetError(
            "No Ableton Live main window found (looked for a visible top-level window "
            "whose process executable starts with 'Ableton Live')"
        )
    user32 = ctypes.windll.user32
    # Restore-if-minimized + foreground so the accelerator lands in Live.
    SW_RESTORE = 9
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, SW_RESTORE)
    user32.SetForegroundWindow(hwnd)
    time.sleep(0.15)

    INPUT_KEYBOARD = 1
    KEYEVENTF_KEYUP = 0x0002
    VK_CONTROL, VK_S = 0x11, 0x53

    class KEYBDINPUT(ctypes.Structure):
        _fields_ = [
            ("wVk", wintypes.WORD), ("wScan", wintypes.WORD),
            ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD),
            ("dwExtraInfo", ctypes.POINTER(wintypes.ULONG)),
        ]

    class INPUT(ctypes.Structure):
        class _U(ctypes.Union):
            _fields_ = [("ki", KEYBDINPUT), ("padding", ctypes.c_byte * 32)]
        _anonymous_ = ("u",)
        _fields_ = [("type", wintypes.DWORD), ("u", _U)]

    def key(vk, up=False):
        inp = INPUT()
        inp.type = INPUT_KEYBOARD
        inp.ki = KEYBDINPUT(vk, 0, KEYEVENTF_KEYUP if up else 0, 0, None)
        return inp

    seq = (key(VK_CONTROL), key(VK_S), key(VK_S, up=True), key(VK_CONTROL, up=True))
    arr = (INPUT * len(seq))(*seq)
    sent = user32.SendInput(len(seq), arr, ctypes.sizeof(INPUT))
    if sent != len(seq):
        raise SaveSetError("SendInput delivered %d of %d key events" % (sent, len(seq)))


def _find_live_main_window_windows():
    """Visible top-level window whose process image is Ableton Live itself.

    Matching by executable name ('Ableton Live*.exe') rather than window title:
    titles are set-dependent, and process-NAME globs like 'Ableton*' also match
    this project's own ableton-live-mcp server executable (field-hit 2026-08-08).
    """
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    found: list[int] = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def enum_proc(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd) or user32.GetWindow(hwnd, 4):  # GW_OWNER
            return True
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
        if not handle:
            return True
        try:
            buf = ctypes.create_unicode_buffer(4096)
            size = wintypes.DWORD(len(buf))
            if kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
                exe = os.path.basename(buf.value)
                if exe.lower().startswith("ableton live"):
                    found.append(hwnd)
        finally:
            kernel32.CloseHandle(handle)
        return True

    user32.EnumWindows(enum_proc, 0)
    return found[0] if found else None


# --- macOS: activate Live, send Cmd+S via System Events ----------------------

def _send_save_keystroke_macos() -> None:
    script = (
        'tell application "System Events"\n'
        '  set liveProcs to (every process whose name is "Live")\n'
        '  if (count of liveProcs) is 0 then error "No running Live process"\n'
        '  set frontmost of item 1 of liveProcs to true\n'
        '  delay 0.15\n'
        '  keystroke "s" using command down\n'
        'end tell\n'
    )
    result = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
    if result.returncode != 0:
        raise SaveSetError("osascript save keystroke failed: %s" % (result.stderr.strip() or result.stdout.strip()))
