"""Render the current arrangement to a WAV by driving Live's export dialog.

Live has no export API, so this walks the UI the way a human would: set the
arrangement loop brace over the render range (the export dialog picks it up as
Render Start/Length), open the dialog with the export accelerator, confirm it
(Live re-uses the last-used format settings), type the output path into the
native save dialog, and then poll until the rendered WAV exists and parses.

Assumptions an agent should state to the user: the last-used export settings
(sample rate, bit depth, Normalize OFF) are what this render inherits — this
tool does not walk the dialog's individual fields.

Windows-tested; the macOS path uses the same System Events approach as
save_set but is untested.
"""

from __future__ import annotations

import os
import platform
import subprocess
import time
import wave
from typing import Any

from save_set import SaveSetError, _find_live_main_window_windows


class ExportError(RuntimeError):
    pass


def export_set(
    bridge,
    output_path: str,
    start_beats: float | None = None,
    length_beats: float | None = None,
    timeout: float = 240.0,
    dialog_delay: float = 1.5,
) -> dict[str, Any]:
    if not output_path:
        raise ExportError("output_path is required (target .wav)")
    output_path = str(output_path)
    if not output_path.lower().endswith(".wav"):
        raise ExportError("output_path must end in .wav (Live's last-used file type must be WAV)")
    if os.path.exists(output_path):
        raise ExportError("output_path %r already exists; refusing to overwrite (an overwrite prompt would stall the dialog walk)" % output_path)
    parent = os.path.dirname(output_path) or "."
    if not os.path.isdir(parent):
        raise ExportError("output directory %r does not exist" % parent)

    if (start_beats is None) != (length_beats is None):
        raise ExportError("pass start_beats and length_beats together, or neither (uses the current loop brace)")
    if start_beats is not None:
        bridge.request("exec", {"code": (
            "song.loop_start = %f\n"
            "song.loop_length = %f\n"
            "song.loop = True\n"
            "song.stop_playing()\n"
            "result = 1" % (float(start_beats), float(length_beats))
        )})

    drive_export_dialog(output_path, dialog_delay)

    deadline = time.monotonic() + max(10.0, float(timeout))
    last_size = -1
    stable_since = None
    while time.monotonic() < deadline:
        if os.path.exists(output_path):
            size = os.path.getsize(output_path)
            if size > 0 and size == last_size:
                if stable_since is None:
                    stable_since = time.monotonic()
                elif time.monotonic() - stable_since >= 1.5:
                    try:
                        with wave.open(output_path, "rb") as handle:
                            frames = handle.getnframes()
                            rate = handle.getframerate()
                            if frames > 0:
                                return {
                                    "exported": True,
                                    "path": output_path,
                                    "duration_s": round(frames / float(rate), 2),
                                    "sample_rate": rate,
                                    "channels": handle.getnchannels(),
                                    "bit_depth": handle.getsampwidth() * 8,
                                }
                    except (wave.Error, EOFError):
                        stable_since = None  # header not finalized yet
            else:
                stable_since = None
            last_size = size
        time.sleep(0.5)
    return {
        "exported": False,
        "path": output_path,
        "error": "render_not_observed",
        "hint": (
            "Drove the export dialog but no finished WAV appeared within %.0fs. "
            "A dialog may be stuck open (check the Live window), the last-used "
            "file type may not be WAV, or the render is simply still running — "
            "check the file again in a moment before retrying (a retry would "
            "re-open dialogs)." % float(timeout)
        ),
    }


def drive_export_dialog(output_path: str, dialog_delay: float) -> None:
    system = platform.system()
    if system == "Windows":
        _drive_export_dialog_windows(output_path, dialog_delay)
    elif system == "Darwin":
        _drive_export_dialog_macos(output_path, dialog_delay)
    else:
        raise ExportError("live_export supports Windows and macOS only")


# --- Windows -----------------------------------------------------------------

def _drive_export_dialog_windows(output_path: str, dialog_delay: float) -> None:
    import ctypes

    hwnd = _find_live_main_window_windows()
    if hwnd is None:
        raise SaveSetError("No Ableton Live main window found")
    user32 = ctypes.windll.user32
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, 9)  # SW_RESTORE
    user32.SetForegroundWindow(hwnd)
    time.sleep(0.2)

    VK_CONTROL, VK_SHIFT, VK_R, VK_RETURN = 0x11, 0x10, 0x52, 0x0D
    _send_vk_sequence([(VK_CONTROL, False), (VK_SHIFT, False), (VK_R, False),
                       (VK_R, True), (VK_SHIFT, True), (VK_CONTROL, True)])
    time.sleep(dialog_delay)                     # export settings dialog opens
    _send_vk_sequence([(VK_RETURN, False), (VK_RETURN, True)])   # confirm (last-used settings)
    time.sleep(dialog_delay)                     # native save dialog opens, name field focused
    _type_unicode(output_path)                   # full path replaces the selected name
    time.sleep(0.3)
    _send_vk_sequence([(VK_RETURN, False), (VK_RETURN, True)])   # start the render


def _send_vk_sequence(events) -> None:
    import ctypes
    from ctypes import wintypes

    INPUT_KEYBOARD, KEYEVENTF_KEYUP = 1, 0x0002

    class KEYBDINPUT(ctypes.Structure):
        _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD),
                    ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD),
                    ("dwExtraInfo", ctypes.POINTER(wintypes.ULONG))]

    class INPUT(ctypes.Structure):
        class _U(ctypes.Union):
            _fields_ = [("ki", KEYBDINPUT), ("padding", ctypes.c_byte * 32)]
        _anonymous_ = ("u",)
        _fields_ = [("type", wintypes.DWORD), ("u", _U)]

    seq = []
    for vk, up in events:
        inp = INPUT()
        inp.type = INPUT_KEYBOARD
        inp.ki = KEYBDINPUT(vk, 0, KEYEVENTF_KEYUP if up else 0, 0, None)
        seq.append(inp)
    arr = (INPUT * len(seq))(*seq)
    sent = ctypes.windll.user32.SendInput(len(seq), arr, ctypes.sizeof(INPUT))
    if sent != len(seq):
        raise ExportError("SendInput delivered %d of %d key events" % (sent, len(seq)))


def _type_unicode(text: str) -> None:
    import ctypes
    from ctypes import wintypes

    INPUT_KEYBOARD, KEYEVENTF_KEYUP, KEYEVENTF_UNICODE = 1, 0x0002, 0x0004

    class KEYBDINPUT(ctypes.Structure):
        _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD),
                    ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD),
                    ("dwExtraInfo", ctypes.POINTER(wintypes.ULONG))]

    class INPUT(ctypes.Structure):
        class _U(ctypes.Union):
            _fields_ = [("ki", KEYBDINPUT), ("padding", ctypes.c_byte * 32)]
        _anonymous_ = ("u",)
        _fields_ = [("type", wintypes.DWORD), ("u", _U)]

    seq = []
    for char in text:
        for up in (False, True):
            inp = INPUT()
            inp.type = INPUT_KEYBOARD
            inp.ki = KEYBDINPUT(0, ord(char), KEYEVENTF_UNICODE | (KEYEVENTF_KEYUP if up else 0), 0, None)
            seq.append(inp)
    arr = (INPUT * len(seq))(*seq)
    sent = ctypes.windll.user32.SendInput(len(seq), arr, ctypes.sizeof(INPUT))
    if sent != len(seq):
        raise ExportError("SendInput delivered %d of %d unicode events" % (sent, len(seq)))


# --- macOS (untested; mirrors save_set's System Events approach) -------------

def _drive_export_dialog_macos(output_path: str, dialog_delay: float) -> None:
    escaped = output_path.replace("\\", "\\\\").replace('"', '\\"')
    script = (
        'tell application "System Events"\n'
        '  set liveProcs to (every process whose name is "Live")\n'
        '  if (count of liveProcs) is 0 then error "No running Live process"\n'
        '  set frontmost of item 1 of liveProcs to true\n'
        '  delay 0.2\n'
        '  keystroke "r" using {command down, shift down}\n'
        '  delay %f\n'
        '  keystroke return\n'
        '  delay %f\n'
        '  keystroke "g" using {command down, shift down}\n'
        '  delay 0.4\n'
        '  keystroke "%s"\n'
        '  keystroke return\n'
        '  delay 0.4\n'
        '  keystroke return\n'
        'end tell\n'
    ) % (dialog_delay, dialog_delay, escaped)
    result = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
    if result.returncode != 0:
        raise ExportError("osascript export drive failed: %s" % (result.stderr.strip() or result.stdout.strip()))
