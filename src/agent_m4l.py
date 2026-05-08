from __future__ import annotations

import json
import argparse
import base64
import hashlib
import re
import shutil
from pathlib import Path
from typing import Any

from ableton_paths import default_user_library, find_max_device_template, state_dir

ROOT = Path(__file__).resolve().parents[1]
HOST_JS = ROOT / "m4l" / "agent_m4l_host.js"
GENERATED_DIR = ROOT / "m4l" / "generated"
WEBUI_DIR = GENERATED_DIR / "webui"
UDP_PORT_BASE = 17655
UDP_PORT_SPAN = 30000
DEFAULT_DEVICE_WIDTH = 420
MIN_DEVICE_WIDTH = 260
DEVICE_WIDTH_PADDING = 20

ROLE_PRESETS = {
    "audio_effect": {
        "folder_parts": ("Presets", "Audio Effects", "Max Audio Effect"),
        "template_name": "Max Audio Effect.amxd",
        "header": b"aaaa",
        "amxdtype": 1633771873,
        "io": "audio_effect",
    },
    "instrument": {
        "folder_parts": ("Presets", "Instruments", "Max Instrument"),
        "template_name": "Max Instrument.amxd",
        "header": b"iiii",
        "amxdtype": 1768515945,
        "io": "instrument",
    },
    "midi_effect": {
        "folder_parts": ("Presets", "MIDI Effects", "Max MIDI Effect"),
        "template_name": "Max MIDI Effect.amxd",
        "header": b"mmmm",
        "amxdtype": 1835887981,
        "io": "midi_effect",
    },
}


def normalize_role(role: str | None) -> str:
    value = (role or "audio_effect").strip().lower().replace("-", "_").replace(" ", "_")
    aliases = {
        "audio": "audio_effect",
        "effect": "audio_effect",
        "fx": "audio_effect",
        "synth": "instrument",
        "midi": "midi_effect",
    }
    value = aliases.get(value, value)
    if value not in ROLE_PRESETS:
        raise ValueError("role must be audio_effect, instrument, or midi_effect")
    return value


def slugify(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "_", value.strip())
    slug = re.sub(r"_+", "_", slug).strip("._-")
    return slug[:80] or "Device"


def device_name(role: str, instance_id: str, title: str | None = None) -> str:
    stem = slugify(title or instance_id)
    return "AgentM4L_%s_%s" % (role, stem)


def command_file(instance_id: str) -> str:
    return str(state_dir() / ("agent_m4l_%s.json" % slugify(instance_id)))


def status_file(instance_id: str) -> str:
    return str(state_dir() / ("agent_m4l_%s_status.json" % slugify(instance_id)))


def audio_bus_names(instance_id: str) -> dict[str, str]:
    stem = "agent_m4l_%s" % slugify(instance_id)
    return {
        "input_left": "%s_audio_in_l" % stem,
        "input_right": "%s_audio_in_r" % stem,
        "output_left": "%s_audio_out_l" % stem,
        "output_right": "%s_audio_out_r" % stem,
    }


def udp_port(instance_id: str) -> int:
    digest = hashlib.sha1(slugify(instance_id).encode("utf-8")).hexdigest()
    return UDP_PORT_BASE + (int(digest[:8], 16) % UDP_PORT_SPAN)


def infer_device_width(spec: dict[str, Any] | None = None, fallback: int = DEFAULT_DEVICE_WIDTH) -> int:
    explicit = _positive_int(spec.get("device_width") or spec.get("devicewidth") or spec.get("width")) if isinstance(spec, dict) else 0
    if explicit > 0:
        return max(MIN_DEVICE_WIDTH, explicit)
    width = 0
    if isinstance(spec, dict):
        for item in spec.get("objects") or []:
            width = max(width, _rect_right(item.get("presentation_rect")))
        for item in _webui_items(spec.get("webuis") or spec.get("webui")):
            width = max(width, _rect_right(item.get("presentation_rect")))
    if width <= 0:
        width = fallback
        return max(MIN_DEVICE_WIDTH, int(round(width)))
    return max(MIN_DEVICE_WIDTH, int(round(width + DEVICE_WIDTH_PADDING)))


def _positive_int(value: Any) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def _webui_items(webui: Any) -> list[dict[str, Any]]:
    if isinstance(webui, list):
        return [item for item in webui if isinstance(item, dict)]
    if isinstance(webui, dict):
        return [webui]
    return []


def _rect_right(rect: Any) -> int:
    if not isinstance(rect, list) or len(rect) < 4:
        return 0
    try:
        return int(float(rect[0]) + float(rect[2]))
    except (TypeError, ValueError):
        return 0


def max_arg(value: str) -> str:
    text = str(value).replace("\\", "/")
    if re.search(r"\s", text):
        return '"%s"' % text.replace('"', '\\"')
    return text


def install_folder(role: str) -> Path:
    preset = ROLE_PRESETS[normalize_role(role)]
    return default_user_library().joinpath(*preset["folder_parts"])


def role_template(role: str) -> Path | None:
    preset = ROLE_PRESETS[normalize_role(role)]
    return find_max_device_template(str(preset["template_name"]))


def _role_from_patch(patch: dict[str, Any], fallback: str = "audio_effect") -> str:
    amxdtype = patch.get("patcher", {}).get("amxdtype")
    for role, preset in ROLE_PRESETS.items():
        if preset["amxdtype"] == amxdtype:
            return role
    return normalize_role(fallback)


def replace_ptch_chunk(container: bytes, payload: bytes) -> bytes:
    index = container.find(b"ptch")
    if index < 0:
        raise ValueError("AMXD template is missing a ptch chunk")
    size_start = index + 4
    size_end = size_start + 4
    if size_end > len(container):
        raise ValueError("AMXD template has a truncated ptch chunk header")
    old_size = int.from_bytes(container[size_start:size_end], "little")
    payload_start = size_end
    payload_end = payload_start + old_size
    if payload_end > len(container):
        raise ValueError("AMXD template has a truncated ptch chunk payload")
    return (
        container[:size_start]
        + len(payload).to_bytes(4, "little")
        + payload
        + container[payload_end:]
    )


def _minimal_amxd_container(role: str, payload: bytes) -> bytes:
    header = ROLE_PRESETS[role]["header"]
    return (
        b"ampf"
        + (4).to_bytes(4, "little")
        + header
        + b"meta"
        + (4).to_bytes(4, "little")
        + b"\x00\x00\x00\x00"
        + b"ptch"
        + len(payload).to_bytes(4, "little")
        + payload
    )


def build_amxd(source: Path, output: Path, role: str | None = None) -> None:
    patch_json = source.read_text(encoding="utf-8")
    patch = json.loads(patch_json)
    role = normalize_role(role or _role_from_patch(patch))
    payload = patch_json.encode("utf-8") + b"\x00"
    template = role_template(role)
    if template and template.exists():
        data = replace_ptch_chunk(template.read_bytes(), payload)
    else:
        data = _minimal_amxd_container(role, payload)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(data)


def _box(box_id: str, maxclass: str, text: str | None, x: float, y: float, **extra: Any) -> dict[str, Any]:
    box: dict[str, Any] = {
        "id": box_id,
        "maxclass": maxclass,
        "varname": box_id,
        "patching_rect": [x, y, 140.0, 22.0],
    }
    if text is not None:
        box["text"] = text
    box.update(extra)
    return {"box": box}


def _line(src: str, src_out: int, dst: str, dst_in: int) -> dict[str, Any]:
    return {"patchline": {"source": [src, src_out], "destination": [dst, dst_in]}}


def make_host_patch(role: str, instance_id: str, title: str | None = None, device_width: int | None = None) -> dict[str, Any]:
    role = normalize_role(role)
    name = device_name(role, instance_id, title)
    preset = ROLE_PRESETS[role]
    device_width = infer_device_width({"device_width": device_width} if device_width else None)
    js_text = "js agent_m4l_host.js %s %s %s %s" % (
        role,
        slugify(instance_id),
        max_arg(command_file(instance_id)),
        max_arg(status_file(instance_id)),
    )
    boxes = [
        _box("comment-title", "comment", "Agent M4L Dynamic Host: %s" % name, 20.0, 20.0),
        _box("js", "newobj", js_text, 20.0, 58.0),
        _box("status", "newobj", "print %s" % name, 20.0, 96.0),
        _box("udp", "newobj", "udpreceive %d" % udp_port(instance_id), 220.0, 20.0),
        _box("poll-loadbang", "newobj", "loadbang", 220.0, 58.0),
        _box("poll-start", "message", "1", 220.0, 96.0),
        _box("poll-metro", "newobj", "metro 50 @active 1 @defer 1", 220.0, 134.0),
        _box("poll-live-device", "newobj", "live.thisdevice", 340.0, 58.0),
        _box("poll-defer", "newobj", "deferlow", 340.0, 96.0),
        _box("poll-delay", "newobj", "delay 100", 340.0, 134.0),
        _box("self-path-message", "message", "path this_device", 520.0, 58.0),
        _box("self-path", "newobj", "live.path this_device", 520.0, 96.0),
        _box("self-prepend", "newobj", "prepend __self_device", 520.0, 134.0),
        _box("trigger-path-message", "message", "path this_device parameters 1", 680.0, 58.0),
        _box("trigger-path", "newobj", "live.path this_device parameters 1", 680.0, 96.0),
        _box("trigger-observer", "newobj", "live.observer value", 680.0, 134.0),
        _box("trigger-prepend", "newobj", "prepend __command_trigger", 680.0, 172.0),
        _box("command-filewatch", "newobj", "filewatch %s" % max_arg(command_file(instance_id)), 860.0, 58.0),
        _box("command-filewatch-init", "newobj", "trigger b b", 860.0, 96.0),
        _box("command-filewatch-path", "message", max_arg(command_file(instance_id)), 860.0, 134.0),
        _box("command-filewatch-start", "message", "1", 1010.0, 134.0),
        _box("command-filewatch-prepend", "newobj", "prepend __filewatch", 860.0, 172.0),
        _box(
            "command-trigger",
            "live.numbox",
            None,
            340.0,
            172.0,
            parameter_enable=1,
            parameter_shortname="Agent Poll",
            parameter_longname="Agent M4L Poll",
        ),
        _box("script", "newobj", "thispatcher", 420.0, 20.0),
    ]
    lines = [
        _line("js", 2, "status", 0),
        _line("udp", 0, "js", 0),
        _line("poll-loadbang", 0, "poll-start", 0),
        _line("poll-loadbang", 0, "poll-defer", 0),
        _line("poll-defer", 0, "poll-start", 0),
        _line("poll-loadbang", 0, "poll-delay", 0),
        _line("poll-live-device", 0, "poll-start", 0),
        _line("poll-live-device", 0, "poll-delay", 0),
        _line("poll-live-device", 0, "self-path-message", 0),
        _line("poll-loadbang", 0, "self-path", 0),
        _line("poll-live-device", 0, "self-path", 0),
        _line("self-path-message", 0, "self-path", 0),
        _line("self-path", 0, "self-prepend", 0),
        _line("self-prepend", 0, "js", 0),
        _line("poll-live-device", 0, "trigger-path-message", 0),
        _line("poll-loadbang", 0, "trigger-path", 0),
        _line("poll-live-device", 0, "trigger-path", 0),
        _line("trigger-path-message", 0, "trigger-path", 0),
        _line("trigger-path", 0, "trigger-observer", 1),
        _line("trigger-observer", 0, "trigger-prepend", 0),
        _line("trigger-prepend", 0, "js", 0),
        _line("poll-loadbang", 0, "command-filewatch-init", 0),
        _line("poll-live-device", 0, "command-filewatch-init", 0),
        _line("command-filewatch-init", 1, "command-filewatch-path", 0),
        _line("command-filewatch-init", 0, "command-filewatch-start", 0),
        _line("command-filewatch-path", 0, "command-filewatch", 0),
        _line("command-filewatch-start", 0, "command-filewatch", 0),
        _line("command-filewatch", 0, "command-filewatch-prepend", 0),
        _line("command-filewatch-prepend", 0, "js", 0),
        _line("poll-delay", 0, "js", 0),
        _line("command-trigger", 0, "js", 0),
        _line("poll-start", 0, "poll-metro", 0),
        _line("poll-metro", 0, "js", 0),
    ]
    audio_buses = audio_bus_names(instance_id)
    if preset["io"] == "audio_effect":
        boxes += [
            _box("plugin", "newobj", "plugin~", 20.0, 150.0),
            _box("audio-in-l", "newobj", "send~ %s" % audio_buses["input_left"], 20.0, 190.0),
            _box("audio-in-r", "newobj", "send~ %s" % audio_buses["input_right"], 120.0, 190.0),
            _box("audio-out-l", "newobj", "receive~ %s" % audio_buses["output_left"], 20.0, 220.0),
            _box("audio-out-r", "newobj", "receive~ %s" % audio_buses["output_right"], 120.0, 220.0),
            _box("plugout", "newobj", "plugout~ 1 2", 20.0, 250.0),
            _box("signal-wake-clock", "newobj", "phasor~ 4", 220.0, 280.0),
            _box("signal-wake-threshold", "newobj", ">~ 0.5", 320.0, 280.0),
            _box("signal-wake-edge", "newobj", "edge~", 420.0, 280.0),
            _box("signal-wake-prepend", "newobj", "prepend __signal_wake", 520.0, 280.0),
            _box("signal-wake-sink", "newobj", "*~ 0.", 660.0, 280.0),
        ]
        lines += [
            _line("plugin", 0, "audio-in-l", 0),
            _line("plugin", 1, "audio-in-r", 0),
            _line("audio-out-l", 0, "plugout", 0),
            _line("audio-out-r", 0, "plugout", 1),
            _line("signal-wake-clock", 0, "signal-wake-threshold", 0),
            _line("signal-wake-threshold", 0, "signal-wake-edge", 0),
            _line("signal-wake-edge", 0, "signal-wake-prepend", 0),
            _line("signal-wake-prepend", 0, "js", 0),
            _line("signal-wake-threshold", 0, "signal-wake-sink", 0),
            _line("signal-wake-sink", 0, "plugout", 0),
        ]
    elif preset["io"] == "instrument":
        boxes += [
            _box("midiin", "newobj", "midiin", 20.0, 150.0),
            _box("midiout", "newobj", "midiout", 20.0, 250.0),
            _box("audio-out-l", "newobj", "receive~ %s" % audio_buses["output_left"], 220.0, 220.0),
            _box("audio-out-r", "newobj", "receive~ %s" % audio_buses["output_right"], 320.0, 220.0),
            _box("plugout", "newobj", "plugout~ 1 2", 220.0, 250.0),
            _box("signal-wake-clock", "newobj", "phasor~ 4", 420.0, 220.0),
            _box("signal-wake-threshold", "newobj", ">~ 0.5", 520.0, 220.0),
            _box("signal-wake-edge", "newobj", "edge~", 620.0, 220.0),
            _box("signal-wake-prepend", "newobj", "prepend __signal_wake", 720.0, 220.0),
            _box("signal-wake-sink", "newobj", "*~ 0.", 860.0, 220.0),
        ]
        lines += [
            _line("audio-out-l", 0, "plugout", 0),
            _line("audio-out-r", 0, "plugout", 1),
            _line("signal-wake-clock", 0, "signal-wake-threshold", 0),
            _line("signal-wake-threshold", 0, "signal-wake-edge", 0),
            _line("signal-wake-edge", 0, "signal-wake-prepend", 0),
            _line("signal-wake-prepend", 0, "js", 0),
            _line("signal-wake-threshold", 0, "signal-wake-sink", 0),
            _line("signal-wake-sink", 0, "plugout", 0),
            _line("midiin", 0, "js", 0),
        ]
    else:
        boxes += [
            _box("midiin", "newobj", "midiin", 20.0, 150.0),
            _box("midiout", "newobj", "midiout", 20.0, 250.0),
        ]
        lines += [
            _line("midiin", 0, "js", 0),
        ]
    return {
        "patcher": {
            "fileversion": 1,
            "appversion": {"major": 8, "minor": 6, "revision": 0, "architecture": "x64"},
            "classnamespace": "box",
            "rect": [80.0, 80.0, float(max(620, device_width)), 360.0],
            "openrect": [0.0, 0.0, float(device_width), 170.0],
            "bglocked": 0,
            "openinpresentation": 1,
            "devicewidth": float(device_width),
            "default_fontsize": 12.0,
            "default_fontface": 0,
            "default_fontname": "Arial",
            "gridonopen": 1,
            "gridsize": [15.0, 15.0],
            "boxes": boxes,
            "lines": lines,
            "appversion_at_last_save": "8.6.0",
            "amxdtype": preset["amxdtype"],
        }
    }


def build_device(role: str, instance_id: str, title: str | None = None, install: bool = True, device_width: int | None = None) -> dict[str, Any]:
    role = normalize_role(role)
    name = device_name(role, instance_id, title)
    patch_path = GENERATED_DIR / ("%s.maxpat" % name)
    amxd_path = GENERATED_DIR / ("%s.amxd" % name)
    patch_path.parent.mkdir(parents=True, exist_ok=True)
    resolved_width = infer_device_width({"device_width": device_width} if device_width else None)
    patch_path.write_text(json.dumps(make_host_patch(role, instance_id, title, resolved_width), indent=2), encoding="utf-8")
    build_amxd(patch_path, amxd_path, role)
    shutil.copyfile(HOST_JS, amxd_path.with_name(HOST_JS.name))
    installed_path = ""
    if install:
        installed = install_folder(role) / amxd_path.name
        installed.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(amxd_path, installed)
        shutil.copyfile(HOST_JS, installed.with_name(HOST_JS.name))
        installed_path = str(installed)
    return {
        "name": name,
        "role": role,
        "instance_id": slugify(instance_id),
        "patch_path": str(patch_path),
        "amxd_path": str(amxd_path),
        "installed_path": installed_path,
        "command_file": command_file(instance_id),
        "status_file": status_file(instance_id),
        "audio_buses": audio_bus_names(instance_id),
        "udp_port": udp_port(instance_id),
        "device_width": resolved_width,
    }


def build_pool(role: str, count: int, prefix: str = "slot", install: bool = True) -> list[dict[str, Any]]:
    if count < 1:
        raise ValueError("count must be >= 1")
    return [
        build_device(role, "%s_%03d" % (slugify(prefix), index), "%s_%03d" % (slugify(prefix), index), install=install)
        for index in range(count)
    ]


def write_webui(instance_id: str, webui: dict[str, Any]) -> dict[str, Any]:
    slug = slugify(instance_id)
    directory = WEBUI_DIR / slug
    directory.mkdir(parents=True, exist_ok=True)
    html_path = directory / "index.html"
    css_path = directory / "style.css"
    js_path = directory / "device.js"
    css = str(webui.get("css") or DEFAULT_WEBUI_CSS)
    js = str(webui.get("js") or DEFAULT_WEBUI_JS)
    html = inject_webui_bootstrap(str(webui.get("html") or default_webui_html(str(webui.get("title") or slug), webui.get("controls") or [])))
    css_path.write_text(css, encoding="utf-8")
    js_path.write_text(js, encoding="utf-8")
    html_path.write_text(html, encoding="utf-8")
    assets = write_webui_asset_files(instance_id, webui.get("assets"))
    return {
        "html_path": str(html_path),
        "css_path": str(css_path),
        "js_path": str(js_path),
        "url": html_path.resolve().as_uri(),
        "assets": assets,
    }


def write_webui_asset_files(instance_id: str, assets: Any) -> list[dict[str, Any]]:
    directory = WEBUI_DIR / slugify(instance_id)
    directory.mkdir(parents=True, exist_ok=True)
    return write_webui_assets(directory, assets)


def write_webui_assets(directory: Path, assets: Any) -> list[dict[str, Any]]:
    rendered = []
    for name, asset in webui_asset_items(assets):
        relative = safe_asset_path(name)
        path = directory / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(asset, dict):
            data = asset_data(asset)
        else:
            data = str(asset).encode("utf-8")
        path.write_bytes(data)
        rendered.append({
            "path": str(path),
            "relative_path": relative,
            "url": path.resolve().as_uri(),
            "bytes": len(data),
        })
    return rendered


def webui_asset_items(assets: Any) -> list[tuple[str, Any]]:
    if isinstance(assets, dict):
        return [(str(name), asset) for name, asset in assets.items()]
    if isinstance(assets, list):
        result = []
        for index, asset in enumerate(assets):
            if isinstance(asset, dict):
                name = asset.get("path") or asset.get("name") or asset.get("filename") or str(index)
                result.append((str(name), asset))
        return result
    return []


def asset_data(asset: dict[str, Any]) -> bytes:
    if asset.get("base64") is not None:
        return base64.b64decode(str(asset["base64"]))
    if asset.get("content") is not None:
        return str(asset["content"]).encode("utf-8")
    if asset.get("text") is not None:
        return str(asset["text"]).encode("utf-8")
    return b""


def safe_asset_path(name: str) -> str:
    parts = []
    for part in str(name).replace("\\", "/").split("/"):
        safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", part.strip())
        safe = re.sub(r"_+", "_", safe).strip("._-")
        if safe:
            parts.append(safe[:80])
    if not parts:
        raise ValueError("webui asset path must include a filename")
    return "/".join(parts)


def default_webui_html(title: str, controls: list[dict[str, Any]]) -> str:
    if not controls:
        controls = [{"id": "amount", "label": "Amount", "min": 0, "max": 1, "value": 0.5, "step": 0.001}]
    rows = []
    for control in controls:
        cid = slugify(str(control.get("id") or "amount"))
        label = str(control.get("label") or cid)
        minimum = control.get("min", 0)
        maximum = control.get("max", 1)
        value = control.get("value", 0)
        step = control.get("step", 0.001)
        rows.append(
            '<label class="control"><span>%s</span><input data-param="%s" type="range" min="%s" max="%s" step="%s" value="%s"><output>%s</output></label>'
            % (label, cid, minimum, maximum, step, value, value)
        )
    return """<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>%s</title>
  <link rel="stylesheet" href="style.css">
</head>
<body>
  <main>
    <header>%s</header>
    %s
  </main>
  <script src="device.js"></script>
</body>
</html>
""" % (title, title, "\n    ".join(rows))


def inject_webui_bootstrap(html: str) -> str:
    """Add load/error telemetry without constraining the page's custom UI."""
    if "agent-m4l-bootstrap" in html:
        return html
    script = '<script id="agent-m4l-bootstrap">%s</script>' % AGENT_M4L_WEBUI_BOOTSTRAP_JS
    lower = html.lower()
    head_index = lower.find("</head>")
    if head_index >= 0:
        return html[:head_index] + script + html[head_index:]
    script_index = lower.find("<script")
    if script_index >= 0:
        return html[:script_index] + script + html[script_index:]
    return html + script


AGENT_M4L_WEBUI_BOOTSTRAP_JS = (
    "(function(){"
    "if(window.__agentM4LBootstrap)return;"
    "window.__agentM4LBootstrap=1;"
    "function o(){if(window.max&&window.max.outlet){try{window.max.outlet.apply(window.max,arguments)}catch(_e){}}}"
    "window.agentM4L=window.agentM4L||{};"
    "window.agentM4L.outlet=o;"
    "o('web_ready',Date.now()%1000000000);"
    "window.addEventListener('error',function(e){o('web_error',String(e&&e.message||e&&e.error||'error').slice(0,240))});"
    "window.addEventListener('unhandledrejection',function(e){o('web_error',String(e&&e.reason||'unhandledrejection').slice(0,240))});"
    "})();"
)


DEFAULT_WEBUI_CSS = """
:root { color-scheme: dark; font-family: system-ui, -apple-system, BlinkMacSystemFont, sans-serif; }
html, body { box-sizing: border-box; width: 100%; height: 100%; overflow: hidden; }
body { margin: 0; background: #14161a; color: #f3f6fb; }
main { box-sizing: border-box; width: 100%; height: 100%; padding: 12px; display: grid; gap: 10px; align-content: start; overflow: auto; }
header { font-size: 13px; font-weight: 700; color: #9fd2ff; }
.control { display: grid; grid-template-columns: 76px 1fr 44px; align-items: center; gap: 8px; font-size: 11px; }
input[type=range] { width: 100%; accent-color: #62d2a2; }
output { text-align: right; font-variant-numeric: tabular-nums; color: #c8ced8; }
"""


DEFAULT_WEBUI_JS = """
const maxApi = window.max;
const send = (id, value) => {
  if (window.max && window.max.outlet) window.max.outlet("set", id, Number(value));
};
document.querySelectorAll("[data-param]").forEach((el) => {
  const output = el.parentElement.querySelector("output");
  const update = () => {
    output.value = el.value;
    send(el.dataset.param, el.value);
  };
  el.addEventListener("input", update);
});
if (maxApi && maxApi.bindInlet) {
  maxApi.bindInlet("state", (raw) => {
    let state = {};
    try { state = typeof raw === "string" ? JSON.parse(raw) : raw; } catch (_err) {}
    Object.keys(state).forEach((id) => {
      const el = document.querySelector(`[data-param="${id}"]`);
      if (!el) return;
      el.value = state[id];
      const output = el.parentElement.querySelector("output");
      if (output) output.value = state[id];
    });
  });
}
"""


def main() -> None:
    parser = argparse.ArgumentParser(description="Build a dynamic Agent M4L host device.")
    parser.add_argument("instance_id", nargs="?")
    parser.add_argument("--role", default="audio_effect", choices=tuple(ROLE_PRESETS))
    parser.add_argument("--name")
    parser.add_argument("--pool-size", type=int)
    parser.add_argument("--pool-prefix", default="slot")
    parser.add_argument("--no-install", action="store_true")
    args = parser.parse_args()
    if args.pool_size:
        for result in build_pool(args.role, args.pool_size, args.pool_prefix, install=not args.no_install):
            print(result["amxd_path"])
            if result["installed_path"]:
                print(result["installed_path"])
        return
    if not args.instance_id:
        parser.error("instance_id is required unless --pool-size is used")
    result = build_device(args.role, args.instance_id, args.name, install=not args.no_install)
    print(result["amxd_path"])
    if result["installed_path"]:
        print(result["installed_path"])
