from __future__ import annotations

from typing import Any

from . import __version__
from .bridge import AbletonBridgeClient, BridgeConfig
from .mcp import StdioMcpServer, Tool


ABLETON_AGENT_GUIDE = "General Live object-model bridge; examples are heuristics, not limits."
ABLETON_MCP_INSTRUCTIONS = (
    "Use this as a general-purpose bridge to Ableton Live's object model, not a limited recipe API. "
    "Prefer installed Ableton browser content, Packs, user-library assets, samples, presets, devices, and indexed third-party audio plugins before synthesizing or generating assets, unless the user asks otherwise. "
    "Discover runtime availability with live_browser_capabilities/live_browser_roots/live_browser_search, including roots:['plugins'] for AU/VST/plugin content; Live version, SKU, Packs, user folders, and plugin indexing vary, so fall back gracefully. "
    "For existing projects, start with live_set_summary before custom inspection. "
    "For speed and low token use, prefer one compact live_exec summary for coherent edits, live_batch for independent generic get/set/call/children/eval/exec operations, specific property lists, child limits, max_items, and max_depth. "
    "Avoid broad browser/device dumps. Common gotchas: live_eval is expression-only; use live_exec for statements; Live numeric args must be JSON numbers; Simpler.sample is not generally settable, so load samples/devices via the browser or create audio clips; use ids from bridge summaries, not raw _live_ptr values; object summaries are compact unless detail:true is requested. "
    "These are workflow hints only; the full Live object model remains available through paths, ids, calls, properties, children, listeners, and eval."
)


def schema(properties: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    return {"type": "object", "properties": properties, "required": required or [], "additionalProperties": False}


def make_server(client: AbletonBridgeClient | None = None) -> StdioMcpServer:
    bridge = client or AbletonBridgeClient(BridgeConfig.from_env())
    server = StdioMcpServer("ableton-object-mcp", __version__, ABLETON_MCP_INSTRUCTIONS)

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
        "timeout": {"type": "number"},
    }
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
        **response_controls,
    }, ["ref", "property", "value"]), forward("set")))
    server.add_tool(Tool("live_call", "Call one Live object method.", schema({
        "ref": ref,
        "method": {"type": "string"},
        "args": {"type": "array"},
        "kwargs": {"type": "object"},
        **response_controls,
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
        **response_controls,
    }, ["ref", "updates"]), forward("clip_update_notes")))
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
    }, ["ref", "parameter"]), forward("clip_envelope")))
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
    }, ["ref"]), forward("clip_warp_markers")))
    server.add_tool(Tool("live_batch", "Run multiple generic bridge operations in one Live main-thread request; preserves full object-model flexibility.", schema({
        "operations": {"type": "array", "items": {"type": "object", "properties": {
            "method": {"type": "string", "description": "Bridge method name such as get, set, call, children, or eval."},
            "params": {"type": "object"},
        }, "required": ["method"], "additionalProperties": False}},
        "continue_on_error": {"type": "boolean"},
        "include_traceback": {"type": "boolean"},
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
        **response_controls,
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
