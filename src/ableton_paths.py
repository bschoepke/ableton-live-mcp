from __future__ import annotations

import os
import platform
from pathlib import Path
from typing import Iterable


USER_LIBRARY_ENV_VARS = ("ABLETON_USER_LIBRARY", "ABLETON_LIVE_USER_LIBRARY")
TEMPLATE_DIR_ENV_VARS = ("ABLETON_MAX_DEVICE_TEMPLATE_DIR", "ABLETON_M4L_TEMPLATE_DIR")
LIVE_PATH_ENV_VARS = ("ABLETON_LIVE_APP", "ABLETON_LIVE_PATH", "ABLETON_LIVE_INSTALL_DIR")
LIVE_ROOTS_ENV_VAR = "ABLETON_LIVE_INSTALL_ROOTS"
STATE_DIR_ENV_VAR = "ABLETON_MCP_STATE_DIR"


def _split_env_paths(value: str | None) -> list[Path]:
    if not value:
        return []
    return [Path(item).expanduser() for item in value.split(os.pathsep) if item]


def _dedupe(paths: Iterable[Path]) -> list[Path]:
    result: list[Path] = []
    seen: set[str] = set()
    for path in paths:
        key = str(path.expanduser())
        if key in seen:
            continue
        seen.add(key)
        result.append(path.expanduser())
    return result


def user_library_candidates() -> list[Path]:
    env_paths = _user_library_env_paths()
    home = Path.home()
    if platform.system() == "Windows":
        defaults = [
            home / "Documents" / "Ableton" / "User Library",
            home / "Music" / "Ableton" / "User Library",
        ]
    else:
        defaults = [
            home / "Music" / "Ableton" / "User Library",
            home / "Documents" / "Ableton" / "User Library",
        ]
    return _dedupe(env_paths + defaults)


def _user_library_env_paths() -> list[Path]:
    env_paths: list[Path] = []
    for name in USER_LIBRARY_ENV_VARS:
        env_paths.extend(_split_env_paths(os.environ.get(name)))
    return _dedupe(env_paths)


def default_user_library() -> Path:
    env_paths = _user_library_env_paths()
    if env_paths:
        return env_paths[0]
    candidates = user_library_candidates()
    for path in candidates:
        if path.exists():
            return path
    return candidates[0]


def remote_scripts_dir() -> Path:
    return default_user_library() / "Remote Scripts"


def state_dir() -> Path:
    override = os.environ.get(STATE_DIR_ENV_VAR)
    path = Path(override).expanduser() if override else Path.home() / ".ableton-live-mcp"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _template_dirs_from_live_path(path: Path) -> list[Path]:
    path = path.expanduser()
    bases = [path]
    if path.is_file():
        bases.extend([path.parent, path.parent.parent])
    else:
        bases.extend([path.parent])

    candidates: list[Path] = []
    for base in bases:
        candidates.extend([
            base,
            base / "Contents" / "App-Resources" / "Misc" / "Max Devices",
            base / "App-Resources" / "Misc" / "Max Devices",
            base / "Resources" / "Misc" / "Max Devices",
            base / "Misc" / "Max Devices",
        ])
    return _dedupe(candidates)


def _live_install_roots() -> list[Path]:
    roots = _split_env_paths(os.environ.get(LIVE_ROOTS_ENV_VAR))
    system = platform.system()
    home = Path.home()
    if system == "Darwin":
        roots.extend([Path("/Applications"), home / "Applications"])
    elif system == "Windows":
        for name in ("ProgramData", "ProgramW6432", "ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA"):
            value = os.environ.get(name)
            if value:
                roots.append(Path(value) / "Ableton")
    else:
        roots.extend([Path("/opt"), Path("/usr/local"), home / ".local" / "share"])
    return _dedupe(roots)


def _live_install_candidates() -> list[Path]:
    candidates: list[Path] = []
    for root in _live_install_roots():
        if not root.exists():
            continue
        for pattern in ("Ableton Live*.app", "Ableton Live*", "Live*"):
            candidates.extend(sorted(root.glob(pattern), reverse=True))
    return _dedupe(candidates)


def max_device_template_dirs() -> list[Path]:
    paths: list[Path] = []
    for name in TEMPLATE_DIR_ENV_VARS:
        paths.extend(_split_env_paths(os.environ.get(name)))
    for name in LIVE_PATH_ENV_VARS:
        for path in _split_env_paths(os.environ.get(name)):
            paths.extend(_template_dirs_from_live_path(path))
    for path in _live_install_candidates():
        paths.extend(_template_dirs_from_live_path(path))
    return _dedupe(paths)


def find_max_device_template(filename: str) -> Path | None:
    for directory in max_device_template_dirs():
        path = directory / filename
        if path.is_file():
            return path
    return None
