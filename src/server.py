from __future__ import annotations

import json
import hashlib
import socket
import time
from pathlib import Path
from typing import Any

from bridge import AbletonBridgeClient, BridgeConfig
from agent_m4l import build_device, command_file as agent_m4l_command_file, device_name as agent_m4l_device_name, infer_device_bounds, normalize_role, slugify, status_file as agent_m4l_status_file, udp_port as agent_m4l_udp_port, write_webui, write_webui_asset_files
from mcp import StdioMcpServer, Tool
from similar_sounds import find_similar_sounds


__version__ = "0.1.0"


ABLETON_AGENT_GUIDE = "General Live object-model bridge; examples are heuristics, not limits."
AGENT_M4L_MAX_UDP_BYTES = 8192
ABLETON_MCP_INSTRUCTIONS = (
    "General Live bridge; not a limited recipe API. "
    "Prefer installed content/Packs/user assets/samples/presets/devices and indexed third-party audio plugins unless asked. "
    "Discover with live_browser_capabilities/live_browser_roots/live_browser_search roots:['plugins']; SKU/indexing vary. "
    "Existing sets: start with live_set_summary; use expected_set_signature for destructive edits. "
    "Prefer compact live_exec/live_batch, property lists, child limits, and JSON-safe clip helpers. "
    "find_similar_sounds requires Live 12+ analysis data. "
    "AgentAudioTap: prefer master tap + solo target; start with path. "
    "Idle sockets auto-retry; fresh AMXD loads retry. "
    "M4L: live_agent_m4l_device hot-reloads arbitrary native/web/mixed UI; use wait_status and require matching command_id/last_reload_command_id. Supports file-backed updates, UDP hints, set/status skip build, midiin+midiparse, rect-driven devicewidth/openrect sizing, ui_bindings, agent-settable UI, retrying early web ack, set_silent/batches, audio buses, jweb/jbrowser aliases. In stressed sets, no ack means reload/simplify or validate a fresh host. "
    "Avoid broad browser/device dumps. Gotchas: live_eval is expression-only; use live_exec for statements; Live numeric args are JSON numbers; Simpler.sample is not generally settable; use ids from summaries, not raw _live_ptr values. "
    "Hints only; the full Live object model remains available through paths, ids, calls, properties, children, listeners, and eval."
)


def schema(properties: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    return {"type": "object", "properties": properties, "required": required or [], "additionalProperties": False}


def loose_schema() -> dict[str, Any]:
    return {}


def make_server(client: AbletonBridgeClient | None = None) -> StdioMcpServer:
    bridge = client or AbletonBridgeClient(BridgeConfig.from_env())
    server = StdioMcpServer("ableton-live-mcp", __version__, ABLETON_MCP_INSTRUCTIONS)

    def forward(method: str):
        return lambda args: bridge.request(method, args)

    ref = {
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "id": {"type": "integer"},
        },
        "additionalProperties": False,
    }

    timeout_control = {"timeout": {"type": "number", "description": "Seconds to wait for Live's main thread."}}
    server.add_tool(Tool("live_ping", "Check bridge health/version.", schema(timeout_control), forward("ping")))
    response_controls = {
        "detail": {"type": "boolean"},
        "max_items": {"type": "integer"},
        "max_depth": {"type": "integer"},
        "max_string_length": {"type": "integer"},
        "timeout": {"type": "number", "description": "Seconds to wait for Live's main thread."},
    }
    mutation_controls = {
        "timeout": response_controls["timeout"],
        "expected_set_signature": {"type": "string"},
    }
    guarded_response_controls = dict(response_controls)
    guarded_response_controls["expected_set_signature"] = {"type": "string"}
    strict_timeout_control = {"strict_timeout": {"type": "boolean"}}
    server.add_tool(Tool("live_get", "Resolve object; read selected properties/children.", schema({
        "ref": ref,
        "properties": {"type": "array", "items": {"type": "string"}},
        "children": {"oneOf": [
            {"type": "array", "items": {"type": "string"}},
            {"type": "object", "additionalProperties": {"type": "integer"}},
        ]},
        "child_limit": {"type": "integer", "minimum": 0},
        **response_controls,
    }, ["ref"]), forward("get")))
    server.add_tool(Tool("live_set_summary", "Compact set summary.", schema({
        "track_limit": {"type": "integer"},
        "clip_slot_limit": {"type": "integer"},
        "device_limit": {"type": "integer"},
        "arrangement_clip_limit": {"type": "integer"},
        "track_query": {"type": "string"},
        "include_return_tracks": {"type": "boolean"},
        "include_master_track": {"type": "boolean"},
        **response_controls,
    }), forward("set_summary")))
    server.add_tool(Tool("live_set", "Set a writable Live object property.", schema({
        "ref": ref,
        "property": {"type": "string"},
        "value": {},
        **mutation_controls,
    }, ["ref", "property", "value"]), forward("set")))
    server.add_tool(Tool("live_call", "Call one Live object method.", schema({
        "ref": ref,
        "method": {"type": "string"},
        "args": {"type": "array"},
        "kwargs": {"type": "object"},
        **mutation_controls,
    }, ["ref", "method"]), forward("call")))
    server.add_tool(Tool("live_children", "List child objects from a collection.", schema({
        "ref": ref,
        "child": {"type": "string"},
        "limit": {"type": "integer", "minimum": 0},
        **response_controls,
    }, ["ref", "child"]), forward("children")))
    server.add_tool(Tool("live_device_parameters", "Compact Device parameter metadata.", schema({
        "ref": ref,
        "query": {"type": "string", "description": "Terms matched against parameter names."},
        "limit": {"type": "integer", "minimum": 0},
        **response_controls,
    }, ["ref"]), forward("device_parameters")))
    server.add_tool(Tool("live_parameter_set", "Set one DeviceParameter value with min/max and quantized validation.", schema({
        "ref": ref,
        "value": {"type": "number"},
        "coerce": {"type": "boolean", "description": "Clamp to min/max and round quantized values instead of rejecting."},
        **mutation_controls,
    }, ["ref", "value"]), forward("parameter_set")))
    server.add_tool(Tool("live_clip_notes", "List MIDI notes from a clip compactly.", schema({
        "ref": ref,
        "limit": {"type": "integer", "minimum": 0},
        "start_time": {"type": "number"},
        "end_time": {"type": "number"},
        **response_controls,
    }, ["ref"]), forward("clip_notes")))
    server.add_tool(Tool("live_clip_update_notes", "Update existing MIDI notes by note_id.", schema({
        "ref": ref,
        "updates": {"type": "array", "items": {"type": "object", "properties": {
            "note_id": {"type": "integer"},
            "pitch": {"type": "integer"},
            "start_time": {"type": "number"},
            "duration": {"type": "number"},
            "velocity": {"type": "number"},
            "mute": {"type": "boolean"},
            "probability": {"type": "number"},
            "velocity_deviation": {"type": "number"},
            "release_velocity": {"type": "number"},
        }, "required": ["note_id"], "additionalProperties": False}},
        **mutation_controls,
    }, ["ref", "updates"]), forward("clip_update_notes")))
    note_spec = {"type": "object", "properties": {
        "pitch": {"type": "integer"},
        "start_time": {"type": "number"},
        "duration": {"type": "number"},
        "velocity": {"type": "number"},
        "mute": {"type": "boolean"},
    }, "required": ["pitch", "start_time", "duration", "velocity"], "additionalProperties": False}
    server.add_tool(Tool("live_clip_add_notes", "Add MIDI notes to a clip from JSON note specs.", schema({
        "ref": ref,
        "notes": {"type": "array", "items": note_spec},
        "clear": {"type": "boolean"},
        "clear_range": {"type": "object", "properties": {
            "from_pitch": {"type": "integer"},
            "pitch_span": {"type": "integer"},
            "from_time": {"type": "number"},
            "time_span": {"type": "number"},
        }, "required": ["from_pitch", "pitch_span", "from_time", "time_span"], "additionalProperties": False},
        **mutation_controls,
    }, ["ref", "notes"]), forward("clip_add_notes")))
    server.add_tool(Tool("live_clip_duplicate_to_arrangement", "Duplicate a Session clip to Arrangement on a target track.", schema({
        "track": ref,
        "clip": ref,
        "destination_time": {"type": "number"},
        **mutation_controls,
    }, ["track", "clip", "destination_time"]), forward("clip_duplicate_to_arrangement")))
    server.add_tool(Tool("live_clip_envelope", "Inspect or edit a clip automation envelope for one parameter.", schema({
        "ref": ref,
        "parameter": ref,
        "create": {"type": "boolean"},
        "clear": {"type": "boolean"},
        "delete_range": {"type": "object", "properties": {
            "start_time": {"type": "number"},
            "end_time": {"type": "number"},
        }, "required": ["start_time", "end_time"], "additionalProperties": False},
        "insert_steps": {"type": "array", "items": {"type": "object", "properties": {
            "time": {"type": "number"},
            "duration": {"type": "number"},
            "value": {"type": "number"},
        }, "required": ["time", "duration", "value"], "additionalProperties": False}},
        "start_time": {"type": "number"},
        "end_time": {"type": "number"},
        "limit": {"type": "integer", "minimum": 0},
        "expected_set_signature": {"type": "string"},
    }, ["ref", "parameter"]), forward("clip_envelope")))
    server.add_tool(Tool("live_clip_velocity_envelope", "Create parameter automation from MIDI note velocities in a clip.", schema({
        "ref": ref,
        "parameter": ref,
        "min_value": {"type": "number"},
        "max_value": {"type": "number"},
        "invert": {"type": "boolean"},
        "clear": {"type": "boolean"},
        "step_duration": {"type": "number"},
        "start_time": {"type": "number"},
        "end_time": {"type": "number"},
        "limit": {"type": "integer", "minimum": 0},
        "expected_set_signature": {"type": "string"},
    }, ["ref", "parameter"]), forward("clip_velocity_envelope")))
    server.add_tool(Tool("live_clip_warp_markers", "Inspect or edit audio clip warp state and markers.", schema({
        "ref": ref,
        "warping": {"type": "boolean"},
        "warp_mode": {"type": "integer"},
        "add_markers": {"type": "array", "items": {"type": "object", "properties": {
            "sample_time": {"type": "number"},
            "beat_time": {"type": "number"},
        }, "required": ["sample_time", "beat_time"], "additionalProperties": False}},
        "move_markers": {"type": "array", "items": {"type": "object", "properties": {
            "beat_time": {"type": "number"},
            "beat_time_delta": {"type": "number"},
        }, "required": ["beat_time", "beat_time_delta"], "additionalProperties": False}},
        "remove_beat_times": {"type": "array", "items": {"type": "number"}},
        "limit": {"type": "integer", "minimum": 0},
        "expected_set_signature": {"type": "string"},
    }, ["ref"]), forward("clip_warp_markers")))
    server.add_tool(Tool("live_track_create_audio_clip", "Create an Arrangement audio clip on a track from a local audio file.", schema({
        "ref": ref,
        "file_path": {"type": "string"},
        "destination_time": {"type": "number"},
        "name": {"type": "string"},
        **mutation_controls,
    }, ["ref", "file_path", "destination_time"]), forward("track_create_audio_clip")))
    server.add_tool(Tool("live_track_insert_device", "Insert a named built-in Live device on a track.", schema({
        "ref": ref,
        "device_name": {"type": "string"},
        "device_index": {"type": "integer"},
        **mutation_controls,
    }, ["ref", "device_name"]), forward("track_insert_device")))
    server.add_tool(Tool("live_agent_audio_tap", "", loose_schema(), forward("agent_audio_tap")))
    server.add_tool(Tool("live_agent_audio_tap_setup", "", loose_schema(), forward("agent_audio_tap_setup")))
    def agent_m4l_device(args):
        built = None
        webui = None
        params = dict(args)
        wait_status = bool(params.pop("wait_status", False))
        status_timeout = float(params.pop("status_timeout", 2.0))
        status_poll_interval = float(params.pop("status_poll_interval", 0.05))
        load_retry_timeout_arg = params.pop("load_retry_timeout", None)
        load_retry_interval = float(params.pop("load_retry_interval", 0.25))
        previous_status_mtime = _file_mtime(str(params.get("status_file") or ""))

        def remember_webui(webui_key: str, rendered: Any) -> None:
            nonlocal webui
            if webui is None:
                webui = {}
            webui[webui_key] = rendered

        instance_id = str(params.get("instance_id") or params.get("name") or "device")
        for webui_key in ("webui", "webuis"):
            if params.get(webui_key):
                rendered = materialize_agent_m4l_webui(instance_id, params[webui_key])
                params[webui_key] = rendered
                remember_webui(webui_key, rendered)
        for patch_key in ("patch", "spec"):
            patch = params.get(patch_key)
            if not isinstance(patch, dict):
                continue
            rendered_patch = None
            for webui_key in ("webui", "webuis"):
                if patch.get(webui_key):
                    rendered = materialize_agent_m4l_webui(instance_id, patch[webui_key])
                    if rendered_patch is None:
                        rendered_patch = dict(patch)
                    rendered_patch[webui_key] = rendered
                    remember_webui(webui_key, rendered)
            if rendered_patch is not None:
                params[patch_key] = rendered_patch
        if webui is not None:
            if params.get("patch"):
                params["patch"] = dict(params["patch"])
                for webui_key, rendered in webui.items():
                    params["patch"][webui_key] = rendered
        device_bounds = infer_agent_m4l_bounds(params)
        apply_agent_m4l_bounds(params, device_bounds)
        should_build = should_build_agent_m4l(params)
        if should_build:
            built = build_device(
                str(params.get("role") or "audio_effect"),
                str(params.get("instance_id") or params.get("name") or "device"),
                params.get("name"),
                bool(params.get("install", True)),
                device_bounds["width"],
                device_bounds["height"],
            )
            params["device_name"] = built["name"]
            params["instance_id"] = built["instance_id"]
            params["command_file"] = built["command_file"]
            params["status_file"] = built["status_file"]
            previous_status_mtime = _file_mtime(params["status_file"])
        if not should_build and should_handle_agent_m4l_direct(params):
            result = handle_agent_m4l_direct(params)
        else:
            result = bridge.request("agent_m4l_device", params)
            load_retry_timeout = float(load_retry_timeout_arg) if load_retry_timeout_arg is not None else (6.0 if built is not None else 0.0)
            if load_retry_timeout > 0 and _should_retry_agent_m4l_load(params, result):
                result = retry_agent_m4l_load(bridge, params, result, load_retry_timeout, load_retry_interval)
        if wait_status:
            result["status"] = wait_agent_m4l_status(
                str(result.get("status_file") or params.get("status_file") or ""),
                previous_status_mtime,
                str(result.get("command_id") or ""),
                status_timeout,
                status_poll_interval,
                expected_agent_m4l_status_event(str(result.get("command") or agent_m4l_command(params))),
            )
        if built is not None:
            result["built"] = built
        if webui is not None:
            response_webui = webui["webui"] if set(webui) == {"webui"} else webui
            result["webui"] = summarize_agent_m4l_webui(response_webui)
        return result

    server.add_tool(Tool("live_agent_m4l_device", "", loose_schema(), agent_m4l_device))
    server.add_tool(Tool("live_transport", "", loose_schema(), forward("transport")))
    server.add_tool(Tool("live_batch", "Batch bridge operations.", schema({
        "operations": {"type": "array", "items": {"type": "object", "properties": {
            "method": {"type": "string", "description": "Bridge method name such as get, set, call, children, or eval."},
            "params": {"type": "object"},
        }, "required": ["method"], "additionalProperties": False}},
        "continue_on_error": {"type": "boolean"},
        "include_traceback": {"type": "boolean"},
        "expected_set_signature": {"type": "string"},
        **response_controls,
        **strict_timeout_control,
    }, ["operations"]), forward("batch")))
    browser_item_ref = {
        "type": "object",
        "properties": {
            "id": {"type": "integer", "description": "Browser item id returned by live_browser_search."},
            "uri": {"type": "string", "description": "Stable BrowserItem uri returned by live_browser_search, when Live exposes it."},
            "path": {"type": "string", "description": "Browser path returned by live_browser_search; fallback for stale ids."},
        },
        "additionalProperties": False,
    }
    server.add_tool(Tool("live_browser_roots", "List app.browser roots.", schema({}), forward("browser_roots")))
    server.add_tool(Tool("live_browser_capabilities", "Browser roots/filter types/semantic API exposure.", schema({}), forward("browser_capabilities")))
    server.add_tool(Tool("live_browser_search", "Bounded app.browser search; returns BrowserItem ids.", schema({
        "query": {"type": "string", "description": "Search terms."},
        "roots": {"type": "array", "items": {"type": "string"}, "description": "Roots: instruments, drums, samples, plugins, etc."},
        "limit": {"type": "integer", "minimum": 1, "description": "Max matches."},
        "max_depth": {"type": "integer", "minimum": 0, "description": "Max depth."},
        "max_visited": {"type": "integer", "minimum": 1, "description": "Max visited."},
        "loadable_only": {"type": "boolean"},
        "include_folders": {"type": "boolean"},
        "stop_on_limit": {"type": "boolean"},
        "stop_score": {"type": "integer", "description": "0 exact, 1 query in name, 3 path."},
        "match_all_terms": {"type": "boolean"},
    }), forward("browser_search")))
    server.add_tool(Tool("live_browser_load", "Load BrowserItem from search by id, uri, or path.", schema({
        "item": browser_item_ref,
        "target_track": ref,
        **mutation_controls,
    }, ["item"]), forward("browser_load")))
    server.add_tool(Tool("live_browser_preview", "Preview or stop previewing a BrowserItem.", schema({
        "item": browser_item_ref,
        "stop": {"type": "boolean"},
    }), forward("browser_preview")))
    server.add_tool(Tool("find_similar_sounds", "Find similar sounds from Live 12+ local sound-analysis DB.", schema({
        "base": {"type": "string"},
        "query": {"type": "string"},
        "limit": {"type": "integer", "minimum": 1},
        "include_self": {"type": "boolean"},
        "db_path": {"type": "string"},
    }), find_similar_sounds))
    server.add_tool(Tool("live_eval", (
        "Evaluate a Python expression inside Live with song, app, obj, and Live bindings. "
        + ABLETON_AGENT_GUIDE
        + " Use live_exec for statements; prefer installed browser/library assets before generated assets unless asked."
    ), schema({
        "expr": {"type": "string"},
        "ref": ref,
        **response_controls,
    }, ["expr"]), forward("eval")))
    server.add_tool(Tool("live_exec", (
        "Execute Python statements inside Live with song, app, obj, this, Live, and result bindings. "
        + "Set result to a compact dict/list summary to return it. "
        + ABLETON_AGENT_GUIDE
    ), schema({
        "code": {"type": "string"},
        "ref": ref,
        **guarded_response_controls,
        **strict_timeout_control,
    }, ["code"]), forward("exec")))
    server.add_tool(Tool("live_observe", "Add or remove a listener for an object's property.", schema({
        "ref": ref,
        "property": {"type": "string"},
        "enabled": {"type": "boolean"},
    }, ["ref", "property", "enabled"]), forward("observe")))
    server.add_tool(Tool("live_events", "Drain retained Live listener events.", schema({
        "limit": {"type": "integer", "minimum": 1},
    }), forward("events")))
    return server


def should_build_agent_m4l(params: dict[str, Any]) -> bool:
    if "build" in params:
        return bool(params["build"])
    if params.get("patch") is not None or params.get("spec") is not None or params.get("webui") is not None or params.get("webuis") is not None:
        return True
    if params.get("values") is not None or params.get("parameters") is not None:
        return False
    if str(params.get("command") or "").lower() in ("set", "status", "clear"):
        return False
    return True


def infer_agent_m4l_bounds(params: dict[str, Any]) -> dict[str, int]:
    spec: dict[str, Any] = {}
    for key in ("device_width", "devicewidth", "width"):
        if params.get(key) is not None:
            spec["device_width"] = params[key]
            break
    for key in ("device_height", "deviceheight", "height"):
        if params.get(key) is not None:
            spec["device_height"] = params[key]
            break
    objects: list[Any] = []
    webuis: list[dict[str, Any]] = []
    for patch_key in ("patch", "spec"):
        patch = params.get(patch_key)
        if not isinstance(patch, dict):
            continue
        if "device_width" not in spec:
            for key in ("device_width", "devicewidth", "width"):
                if patch.get(key) is not None:
                    spec["device_width"] = patch[key]
                    break
        if "device_height" not in spec:
            for key in ("device_height", "deviceheight", "height"):
                if patch.get(key) is not None:
                    spec["device_height"] = patch[key]
                    break
        if isinstance(patch.get("objects"), list):
            objects.extend(patch["objects"])
        webuis.extend(agent_m4l_webui_items(patch.get("webuis") or patch.get("webui")))
    webuis.extend(agent_m4l_webui_items(params.get("webuis") or params.get("webui")))
    if objects:
        spec["objects"] = objects
    if webuis:
        spec["webuis"] = webuis
    return infer_device_bounds(spec)


def infer_agent_m4l_width(params: dict[str, Any]) -> int:
    return infer_agent_m4l_bounds(params)["width"]


def apply_agent_m4l_bounds(params: dict[str, Any], device_bounds: dict[str, int]) -> None:
    params["device_width"] = device_bounds["width"]
    params["device_height"] = device_bounds["height"]
    for patch_key in ("patch", "spec"):
        patch = params.get(patch_key)
        if not isinstance(patch, dict):
            continue
        updated = dict(patch)
        changed = False
        if any(patch.get(key) is not None for key in ("device_width", "devicewidth", "width")):
            pass
        else:
            updated["device_width"] = device_bounds["width"]
            changed = True
        if any(patch.get(key) is not None for key in ("device_height", "deviceheight", "height")):
            pass
        else:
            updated["device_height"] = device_bounds["height"]
            changed = True
        if changed:
            params[patch_key] = updated


def apply_agent_m4l_width(params: dict[str, Any], device_width: int) -> None:
    apply_agent_m4l_bounds(params, {"width": device_width, "height": infer_agent_m4l_bounds(params)["height"]})


def agent_m4l_webui_items(webui: Any) -> list[dict[str, Any]]:
    if isinstance(webui, list):
        return [item for item in webui if isinstance(item, dict)]
    if isinstance(webui, dict):
        return [webui]
    return []


def materialize_agent_m4l_webui(instance_id: str, webui: Any) -> Any:
    if isinstance(webui, list):
        rendered = []
        for index, item in enumerate(webui):
            item_id = item.get("id") if isinstance(item, dict) else None
            suffix = slugify(str(item_id or index))
            rendered.append(materialize_agent_m4l_webui("%s_%s" % (instance_id, suffix), item))
        return rendered
    if not isinstance(webui, dict):
        return webui
    result = dict(webui)
    if _should_write_agent_m4l_webui(result):
        rendered = write_webui(instance_id, result)
        result = materialized_agent_m4l_webui(result, rendered)
    elif _has_agent_m4l_webui_source_assets(result):
        result = materialized_agent_m4l_webui(result, {
            "assets": write_webui_asset_files(instance_id, result.get("assets")),
        })
    return result


def materialized_agent_m4l_webui(source: dict[str, Any], rendered: dict[str, Any]) -> dict[str, Any]:
    keep = (
        "id",
        "object",
        "presentation",
        "presentation_rect",
        "patching_rect",
        "attrs",
        "attributes",
        "box_attrs",
        "boxAttrs",
        "args",
        "text",
        "audio_out",
        "rendermode",
        "html_path",
        "css_path",
        "js_path",
        "url",
        "path",
    )
    result = {key: source[key] for key in keep if key in source}
    result.update(rendered)
    result["assets"] = summarize_agent_m4l_assets(result.get("assets"))
    return result


def _should_write_agent_m4l_webui(webui: dict[str, Any]) -> bool:
    if any(key in webui for key in ("html", "css", "js", "controls", "title")):
        return True
    return not any(key in webui for key in ("html_path", "path", "url"))


def _has_agent_m4l_webui_source_assets(webui: dict[str, Any]) -> bool:
    assets = webui.get("assets")
    if isinstance(assets, dict):
        for asset in assets.values():
            if isinstance(asset, str):
                return True
            if isinstance(asset, dict) and any(key in asset for key in ("content", "text", "base64")):
                return True
        return False
    if isinstance(assets, list):
        for asset in assets:
            if not isinstance(asset, dict):
                continue
            if any(key in asset for key in ("content", "text", "base64")):
                return True
    return False


def summarize_agent_m4l_assets(assets: Any) -> dict[str, Any]:
    if isinstance(assets, dict) and "count" in assets:
        return assets
    items = [asset for asset in assets if isinstance(asset, dict)] if isinstance(assets, list) else []
    relative_paths = [str(asset["relative_path"]) for asset in items if asset.get("relative_path")]
    total_bytes = 0
    for asset in items:
        try:
            total_bytes += int(asset.get("bytes") or 0)
        except (TypeError, ValueError):
            pass
    summary: dict[str, Any] = {
        "count": len(items),
        "bytes": total_bytes,
    }
    if relative_paths:
        summary["relative_paths"] = relative_paths[:8]
        if len(relative_paths) > 8:
            summary["truncated"] = True
    return summary


def summarize_agent_m4l_webui(webui: Any) -> Any:
    if isinstance(webui, list):
        return [summarize_agent_m4l_webui(item) for item in webui]
    if not isinstance(webui, dict):
        return webui
    if "webui" in webui or "webuis" in webui:
        return {key: summarize_agent_m4l_webui(value) for key, value in webui.items()}
    keep = (
        "id",
        "object",
        "title",
        "presentation_rect",
        "patching_rect",
        "html_path",
        "css_path",
        "js_path",
        "url",
        "path",
    )
    result = {key: webui[key] for key in keep if key in webui}
    if "assets" in webui:
        result["assets"] = summarize_agent_m4l_assets(webui["assets"])
    controls = webui.get("controls")
    if isinstance(controls, list):
        result["controls"] = len(controls)
    return result


def _should_retry_agent_m4l_load(params: dict[str, Any], result: dict[str, Any]) -> bool:
    if params.get("load") is False:
        return False
    if not (params.get("target_track") or params.get("ref")):
        return False
    return result.get("loaded") is False and bool(result.get("load_error"))


def should_handle_agent_m4l_direct(params: dict[str, Any]) -> bool:
    if params.get("target_track") or params.get("ref"):
        return False
    command = agent_m4l_command(params)
    return command in ("update", "set", "status", "clear")


def handle_agent_m4l_direct(params: dict[str, Any]) -> dict[str, Any]:
    role = normalize_role(str(params.get("role") or "audio_effect"))
    raw_instance = str(params.get("instance_id") or params.get("name") or "device")
    instance_id = slugify(raw_instance)
    title = str(params.get("name")) if params.get("name") else None
    command_path = str(params.get("command_file") or agent_m4l_command_file(instance_id))
    status_path = str(params.get("status_file") or agent_m4l_status_file(instance_id))
    command = agent_m4l_command(params)
    patch = params.get("patch") or params.get("spec")
    if patch is None and (params.get("webui") or params.get("webuis")):
        patch = {}
    if patch is not None and params.get("webui"):
        patch = dict(patch)
        patch["webui"] = params.get("webui")
    if patch is not None and params.get("webuis"):
        patch = dict(patch)
        patch["webuis"] = params.get("webuis")
    if patch is not None and params.get("device_width") is not None:
        patch = dict(patch)
        patch.setdefault("device_width", params.get("device_width"))
    if patch is not None and params.get("device_height") is not None:
        patch = dict(patch)
        patch.setdefault("device_height", params.get("device_height"))
    if patch is None and command in ("set", "status"):
        patch = agent_m4l_recovery_patch(command_path)
    values = params.get("values")
    parameters = params.get("parameters")
    command_hash = {
        "command": command,
        "instance_id": instance_id,
        "patch": patch,
        "values": values,
        "parameters": parameters,
        "webui": params.get("webui"),
        "webuis": params.get("webuis"),
        "nonce": time.time(),
    }
    command_id = str(params.get("id") or hashlib.sha1(json.dumps(command_hash, sort_keys=True).encode("utf-8")).hexdigest())
    payload = {
        "id": command_id,
        "command": command,
        "role": role,
        "instance_id": instance_id,
        "patch": patch,
        "values": values,
        "parameters": parameters,
        "webui": params.get("webui"),
        "webuis": params.get("webuis"),
    }
    write_command_file = params.get("write_command_file")
    if write_command_file is None:
        write_command_file = True
    if write_command_file:
        Path(command_path).parent.mkdir(parents=True, exist_ok=True)
        Path(command_path).write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
    port = int(params.get("port") or agent_m4l_udp_port(instance_id))
    sent = False
    if params.get("udp", True):
        sent = send_agent_m4l_udp(instance_id, port, agent_m4l_udp_payload(payload))
    return {
        "sent": sent,
        "command": command,
        "role": role,
        "instance_id": instance_id,
        "device_name": str(params.get("device_name") or agent_m4l_device_name(role, raw_instance, title)),
        "command_id": command_id,
        "command_file": command_path,
        "command_file_written": bool(write_command_file),
        "status_file": status_path,
        "port": port,
        "loaded": False,
        "direct": True,
    }


def agent_m4l_command(params: dict[str, Any]) -> str:
    if params.get("command"):
        return str(params["command"])
    if params.get("values") or params.get("parameters"):
        return "set"
    if params.get("patch") or params.get("spec") or params.get("webui") or params.get("webuis"):
        return "update"
    return "status"


def agent_m4l_recovery_patch(command_path: str) -> Any:
    try:
        payload = json.loads(Path(command_path).read_text(encoding="utf-8"))
    except Exception:
        return None
    patch = payload.get("patch") or payload.get("spec")
    if not patch and (payload.get("objects") or payload.get("webui") or payload.get("webuis")):
        patch = payload
    return patch


def agent_m4l_udp_payload(payload: dict[str, Any]) -> dict[str, Any]:
    if payload.get("command") not in ("set", "status"):
        return payload
    slim = dict(payload)
    for key in ("patch", "spec", "webui", "webuis"):
        slim.pop(key, None)
    return slim


def send_agent_m4l_udp(instance_id: str, port: int, payload: dict[str, Any]) -> bool:
    raw = json.dumps(payload, separators=(",", ":"))
    message = osc_message("/agent_m4l", [instance_id, raw])
    if len(message) > AGENT_M4L_MAX_UDP_BYTES:
        return False
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        try:
            sock.sendto(message, ("127.0.0.1", port))
            return True
        except OSError:
            return False
    finally:
        sock.close()


def osc_message(address: str, args: list[Any]) -> bytes:
    def pad(value: str) -> bytes:
        data = value.encode("utf-8") + b"\x00"
        return data + (b"\x00" * ((4 - (len(data) % 4)) % 4))

    payload = pad(address) + pad("," + ("s" * len(args)))
    for arg in args:
        payload += pad(str(arg))
    return payload


def retry_agent_m4l_load(bridge: AbletonBridgeClient, params: dict[str, Any], result: dict[str, Any], timeout: float, interval: float) -> dict[str, Any]:
    deadline = time.time() + timeout
    attempts = 0
    retry_params = {
        "role": params.get("role"),
        "instance_id": params.get("instance_id"),
        "device_name": params.get("device_name"),
        "target_track": params.get("target_track"),
        "ref": params.get("ref"),
        "device_index": params.get("device_index"),
        "load": True,
        "command": "status",
        "command_file": params.get("command_file"),
        "status_file": params.get("status_file"),
        "write_command_file": False,
        "udp": False,
        "id": result.get("command_id"),
        "timeout": params.get("timeout"),
    }
    retry_params = {key: value for key, value in retry_params.items() if value is not None}
    while time.time() < deadline:
        attempts += 1
        retry = bridge.request("agent_m4l_device", retry_params)
        if retry.get("loaded"):
            result["loaded"] = True
            result["track"] = retry.get("track")
            result.pop("load_error", None)
            break
        if retry.get("load_error"):
            result["load_error"] = retry["load_error"]
        if interval <= 0:
            break
        time.sleep(interval)
    result["load_retry_attempts"] = attempts
    return result


def _file_mtime(path: str) -> float | None:
    if not path:
        return None
    try:
        return Path(path).stat().st_mtime
    except OSError:
        return None


def expected_agent_m4l_status_event(command: str) -> str | None:
    command = str(command or "").lower()
    if command == "update":
        return "reload"
    if command in ("set", "status", "clear"):
        return command
    return None


def wait_agent_m4l_status(path: str, previous_mtime: float | None, command_id: str, timeout: float, poll_interval: float, expected_event: str | None = None) -> dict[str, Any]:
    deadline = time.time() + timeout
    last_error = None
    while time.time() <= deadline:
        status, last_error = _agent_m4l_status_if_ready(path, previous_mtime, command_id, last_error, expected_event)
        if status is not None:
            return status
        time.sleep(poll_interval)
    status, last_error = _agent_m4l_status_if_ready(path, previous_mtime, command_id, last_error, expected_event)
    if status is not None:
        return status
    result: dict[str, Any] = {"timed_out": True, "path": path}
    if command_id:
        result["expected_command_id"] = command_id
    if expected_event:
        result["expected_event"] = expected_event
    last_status, read_error = _read_agent_m4l_status(path)
    if last_status is not None:
        result["last_status"] = summarize_agent_m4l_status(last_status)
        mismatch = agent_m4l_status_mismatch(last_status, command_id, expected_event)
        if mismatch:
            result["mismatch"] = mismatch
    elif read_error:
        result["error"] = read_error
    elif _file_mtime(path) is None:
        result["mismatch"] = "missing_status_file"
    else:
        result["mismatch"] = "status_file_not_updated"
    if last_error:
        result["error"] = last_error
    return result


def _agent_m4l_status_if_ready(path: str, previous_mtime: float | None, command_id: str, last_error: str | None = None, expected_event: str | None = None) -> tuple[dict[str, Any] | None, str | None]:
    current_mtime = _file_mtime(path)
    if current_mtime is None or (previous_mtime is not None and current_mtime <= previous_mtime):
        return None, last_error
    try:
        status = json.loads(Path(path).read_text(encoding="utf-8").strip())
        if _agent_m4l_status_matches(status, command_id, expected_event):
            return status, last_error
    except Exception as exc:
        last_error = str(exc)
    return None, last_error


def _read_agent_m4l_status(path: str) -> tuple[dict[str, Any] | None, str | None]:
    try:
        text = Path(path).read_text(encoding="utf-8").strip()
        if not text:
            return None, "empty_status_file"
        status = json.loads(text)
        if isinstance(status, dict):
            return status, None
        return None, "status_json_not_object"
    except FileNotFoundError:
        return None, None
    except Exception as exc:
        return None, str(exc)


def summarize_agent_m4l_status(status: dict[str, Any]) -> dict[str, Any]:
    summary: dict[str, Any] = {}
    for key in ("event", "command_id", "last_reload_command_id", "dynamic_objects", "webuis", "device_width", "device_height"):
        if key in status:
            summary[key] = status.get(key)
    state = status.get("state")
    if isinstance(state, dict):
        summary["state_keys"] = sorted(str(key) for key in state.keys())[:40]
        focused = {
            str(key): value for key, value in state.items()
            if str(key).startswith(("web_", "command_wake", "filewatch", "live_parameter"))
        }
        if focused:
            summary["state"] = focused
    if status.get("connection_errors"):
        summary["connection_errors"] = status.get("connection_errors")
    return summary


def agent_m4l_status_mismatch(status: dict[str, Any], command_id: str, expected_event: str | None) -> str:
    if command_id:
        status_command_id = str(status.get("command_id") or "")
        if status_command_id != command_id and not _agent_m4l_reload_seen(status, command_id, expected_event):
            return "command_id_mismatch"
    if expected_event:
        status_event = str(status.get("event") or "")
        if status_event != expected_event and not _agent_m4l_reload_seen(status, command_id, expected_event):
            return "event_mismatch"
    return ""


def _agent_m4l_status_matches(status: dict[str, Any], command_id: str, expected_event: str | None) -> bool:
    if command_id:
        status_command_id = str(status.get("command_id") or "")
        if status_command_id != command_id and not _agent_m4l_reload_seen(status, command_id, expected_event):
            return False
    if expected_event:
        status_event = str(status.get("event") or "")
        if status_event != expected_event and not _agent_m4l_reload_seen(status, command_id, expected_event):
            return False
    return True


def _agent_m4l_reload_seen(status: dict[str, Any], command_id: str, expected_event: str | None) -> bool:
    return expected_event == "reload" and bool(command_id) and str(status.get("last_reload_command_id") or "") == command_id


def main() -> None:
    make_server().serve()


if __name__ == "__main__":
    main()
