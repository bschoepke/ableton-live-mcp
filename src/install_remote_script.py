from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

from ableton_paths import remote_scripts_dir


DEFAULT_REMOTE_SCRIPT = "Ableton_Live_MCP"


def _resource_root() -> Path | None:
    script = Path(__file__).resolve().parent / DEFAULT_REMOTE_SCRIPT
    return script.parent if script.is_dir() else None


def _source_root() -> Path | None:
    repo_root = Path(__file__).resolve().parents[1]
    script = repo_root / DEFAULT_REMOTE_SCRIPT
    return script.parent if script.is_dir() else None


def remote_script_root() -> Path:
    for root in (_resource_root(), _source_root()):
        if root is not None:
            return root
    raise FileNotFoundError("Could not find packaged Ableton Remote Scripts")


def default_install_dir() -> Path:
    return remote_scripts_dir()


def install_remote_script(name: str = DEFAULT_REMOTE_SCRIPT, target_dir: Path | None = None, force: bool = False) -> Path:
    if name != DEFAULT_REMOTE_SCRIPT:
        raise ValueError("Unknown Remote Script %r. Expected: %s" % (name, DEFAULT_REMOTE_SCRIPT))
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
    parser = argparse.ArgumentParser(description="Install the Ableton Live MCP Remote Script into the Ableton User Library.")
    parser.add_argument("--name", choices=(DEFAULT_REMOTE_SCRIPT,), default=DEFAULT_REMOTE_SCRIPT, help="Remote Script package to install. Default: %(default)s")
    parser.add_argument("--target-dir", type=Path, default=default_install_dir(), help="Ableton Remote Scripts directory. Default: %(default)s")
    parser.add_argument("--force", action="store_true", help="Replace an existing installed Remote Script directory.")
    parser.add_argument("--list", action="store_true", help="List packaged Remote Scripts and exit.")
    args = parser.parse_args(argv)

    if args.list:
        print(DEFAULT_REMOTE_SCRIPT)
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
