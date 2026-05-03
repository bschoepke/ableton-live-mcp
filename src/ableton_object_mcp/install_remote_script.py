from __future__ import annotations

import argparse
import shutil
import sys
from importlib import resources
from pathlib import Path


DEFAULT_REMOTE_SCRIPT = "Ableton_Object_MCP"
ALIASES = ("Ableton_Object_MCP", "AbletonMCP")


def _resource_root() -> Path | None:
    try:
        root = resources.files("ableton_object_mcp") / "remote_scripts"
        if root.is_dir():
            return Path(str(root))
    except Exception:
        return None
    return None


def _source_root() -> Path | None:
    repo_root = Path(__file__).resolve().parents[2]
    root = repo_root / "remote_scripts"
    return root if root.is_dir() else None


def remote_script_root() -> Path:
    for root in (_resource_root(), _source_root()):
        if root is not None:
            return root
    raise FileNotFoundError("Could not find packaged Ableton Remote Scripts")


def default_install_dir() -> Path:
    return Path.home() / "Music" / "Ableton" / "User Library" / "Remote Scripts"


def install_remote_script(name: str = DEFAULT_REMOTE_SCRIPT, target_dir: Path | None = None, force: bool = False) -> Path:
    if name not in ALIASES:
        raise ValueError("Unknown Remote Script %r. Expected one of: %s" % (name, ", ".join(ALIASES)))
    source = remote_script_root() / name
    if not source.is_dir():
        raise FileNotFoundError("Remote Script %s is not available at %s" % (name, source))
    target_base = target_dir or default_install_dir()
    target = target_base / name
    if target.exists():
        if not force:
            raise FileExistsError("%s already exists; pass --force to replace it" % target)
        if target.is_symlink() or target.is_file():
            target.unlink()
        else:
            shutil.rmtree(str(target))
    target_base.mkdir(parents=True, exist_ok=True)
    shutil.copytree(str(source), str(target), ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo"))
    return target


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Install the Ableton Object MCP Remote Script into the Ableton User Library.")
    parser.add_argument("--name", choices=ALIASES, default=DEFAULT_REMOTE_SCRIPT, help="Remote Script package to install. Default: %(default)s")
    parser.add_argument("--target-dir", type=Path, default=default_install_dir(), help="Ableton Remote Scripts directory. Default: %(default)s")
    parser.add_argument("--force", action="store_true", help="Replace an existing installed Remote Script directory.")
    parser.add_argument("--list", action="store_true", help="List packaged Remote Script aliases and exit.")
    args = parser.parse_args(argv)

    if args.list:
        print("\n".join(ALIASES))
        return 0

    try:
        target = install_remote_script(args.name, args.target_dir, args.force)
    except Exception as exc:
        print("Remote Script install failed: %s" % exc, file=sys.stderr)
        return 1
    print("Installed %s" % target)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
