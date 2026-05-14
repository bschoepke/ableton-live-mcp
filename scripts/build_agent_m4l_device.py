from __future__ import annotations

import argparse
import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load_agent_m4l():
    path = ROOT / "src" / "agent_m4l.py"
    spec = importlib.util.spec_from_file_location("agent_m4l", path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def main() -> None:
    parser = argparse.ArgumentParser(description="Build a dynamic Agent M4L host device.")
    parser.add_argument("instance_id")
    parser.add_argument("--role", default="audio_effect", choices=("audio_effect", "instrument", "midi_effect"))
    parser.add_argument("--name")
    parser.add_argument("--no-install", action="store_true")
    args = parser.parse_args()

    result = load_agent_m4l().build_device(args.role, args.instance_id, args.name, install=not args.no_install)
    print(result["amxd_path"])
    if result["installed_path"]:
        print(result["installed_path"])


if __name__ == "__main__":
    main()

