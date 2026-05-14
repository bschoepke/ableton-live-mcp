from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ableton_paths import default_user_library, state_dir
from agent_m4l import build_amxd as build_role_amxd

SOURCE_PATCH = ROOT / "m4l" / "AgentAudioTap.maxpat"
DEFAULT_OUTPUT = ROOT / "m4l" / "AgentAudioTap.amxd"


def user_library_device() -> Path:
    return default_user_library() / "Presets" / "Audio Effects" / "Max Audio Effect" / "AgentAudioTap.amxd"


def max_arg(value: Path | str) -> str:
    text = str(value).replace("\\", "/")
    return '"%s"' % text.replace('"', '\\"') if any(char.isspace() for char in text) else text


def patch_with_command_file(source: Path, command_file: Path | str) -> str:
    patch = json.loads(source.read_text(encoding="utf-8"))
    js_text = "js agent_audio_tap.js %s" % max_arg(command_file)
    for item in patch["patcher"]["boxes"]:
        box = item.get("box", {})
        if box.get("text") == "js agent_audio_tap.js":
            box["text"] = js_text
    return json.dumps(patch, indent=2)


def build_amxd(source: Path, output: Path, command_file: Path | str | None = None) -> None:
    patch_json = patch_with_command_file(source, command_file or state_dir() / "agent_audio_tap_command.json")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmpdir:
        patched_source = Path(tmpdir) / source.name
        patched_source.write_text(patch_json, encoding="utf-8")
        build_role_amxd(patched_source, output, "audio_effect")


def install_companion_files(device_path: Path) -> None:
    js_source = ROOT / "m4l" / "agent_audio_tap.js"
    js_output = device_path.with_name(js_source.name)
    js_output.write_text(js_source.read_text(encoding="utf-8"), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the AgentAudioTap Max for Live audio effect.")
    parser.add_argument("--source", type=Path, default=SOURCE_PATCH)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--install", action="store_true", help="Also install into the Ableton User Library.")
    args = parser.parse_args()

    build_amxd(args.source, args.output)
    install_companion_files(args.output)
    print(args.output)

    if args.install:
        installed = user_library_device()
        build_amxd(args.source, installed)
        install_companion_files(installed)
        print(installed)


if __name__ == "__main__":
    main()
