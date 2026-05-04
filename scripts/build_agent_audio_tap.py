from __future__ import annotations

import argparse
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE_PATCH = ROOT / "m4l" / "AgentAudioTap.maxpat"
DEFAULT_OUTPUT = ROOT / "m4l" / "AgentAudioTap.amxd"
USER_LIBRARY_DEVICE = (
    Path.home()
    / "Music"
    / "Ableton"
    / "User Library"
    / "Presets"
    / "Audio Effects"
    / "Max Audio Effect"
    / "AgentAudioTap.amxd"
)


def build_amxd(source: Path, output: Path) -> None:
    patch_json = source.read_text(encoding="utf-8")
    json.loads(patch_json)
    payload = patch_json.encode("utf-8") + b"\x00"
    data = (
        b"ampf"
        + (4).to_bytes(4, "little")
        + b"aaaa"
        + b"meta"
        + (4).to_bytes(4, "little")
        + b"\x00\x00\x00\x00"
        + b"ptch"
        + len(payload).to_bytes(4, "little")
        + payload
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(data)


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
        build_amxd(args.source, USER_LIBRARY_DEVICE)
        install_companion_files(USER_LIBRARY_DEVICE)
        print(USER_LIBRARY_DEVICE)


if __name__ == "__main__":
    main()
