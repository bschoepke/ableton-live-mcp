from __future__ import annotations

from typing import Any

from bridge import AbletonBridgeClient, BridgeConfig
from mcp import StdioMcpServer, Tool


__version__ = "0.1.0"


ABLETON_AGENT_GUIDE = "General Live object-model bridge; examples are heuristics, not limits."
ABLETON_MCP_INSTRUCTIONS = (
    "General Live object-model bridge, not a limited recipe API. "
    "Prefer installed browser content, Packs, user assets, samples, presets, devices, and indexed third-party audio plugins before generated assets unless asked. "
    "Discover runtime availability with live_browser_capabilities/live_browser_roots/live_browser_search, including roots:['plugins']; Live SKU, Packs, user folders, and plugin indexing vary. "
    "For existing projects, start with live_set_summary; for collaborative sessions, pass expected_set_signature to destructive edits and re-read if it changed. "
    "For speed, prefer compact live_exec summaries, live_batch for independent operations, specific property lists, child limits, max_items, and max_depth. "
    "For common clip work, prefer JSON-safe helpers like live_clip_add_notes, live_clip_duplicate_to_arrangement, and live_track_create_audio_clip before hand-coding C++ signatures. "
    "Avoid broad browser/device dumps. Common gotchas: live_eval is expression-only; use live_exec for statements; Live numeric args must be JSON numbers; Simpler.sample is not generally settable, so load samples/devices via the browser or create audio clips; use ids from bridge summaries, not raw _live_ptr values; object summaries are compact unless detail:true is requested. "
    "These are hints only; the full Live object model remains available through paths, ids, calls, properties, children, listeners, and eval."
)


def schema(properties: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    return {"type": "object", "properties": properties, "required": required or [], "additionalProperties": False}


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

    server.add_tool(Tool("live_ping", "Check bridge health/version.", schema({}), forward("ping")))
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
    server.add_tool(Tool("live_batch", "Run multiple generic bridge operations in one Live main-thread request; preserves full object-model flexibility.", schema({
        "operations": {"type": "array", "items": {"type": "object", "properties": {
            "method": {"type": "string", "description": "Bridge method name such as get, set, call, children, or eval."},
            "params": {"type": "object"},
        }, "required": ["method"], "additionalProperties": False}},
        "continue_on_error": {"type": "boolean"},
        "include_traceback": {"type": "boolean"},
        "expected_set_signature": {"type": "string"},
        **response_controls,
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


def main() -> None:
    make_server().serve()


if __name__ == "__main__":
    main()
