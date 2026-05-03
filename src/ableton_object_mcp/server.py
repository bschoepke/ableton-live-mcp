from __future__ import annotations

import os
from typing import Any

from . import __version__
from .bridge import AbletonBridgeClient, BridgeConfig
from .mcp import StdioMcpServer, Tool


ABLETON_AGENT_GUIDE = "General Live object-model bridge; examples are heuristics, not limits."
ABLETON_MCP_INSTRUCTIONS = (
    "Use this as a general-purpose bridge to Ableton Live's object model, not a limited recipe API. "
    "Prefer installed Ableton browser content, Packs, user-library assets, samples, presets, devices, and indexed third-party audio plugins before synthesizing or generating assets, unless the user asks otherwise. "
    "Discover runtime availability with live_browser_roots/live_browser_search, including roots:['plugins'] for AU/VST/plugin content; Live version, SKU, Packs, user folders, and plugin indexing vary, so fall back gracefully. "
        "For speed and low token use, prefer one compact live_exec summary for coherent edits, live_batch for independent generic get/set/call/children/eval/exec operations, specific property lists, child limits, max_items, and max_depth. "
        "Avoid broad browser/device dumps. Common gotchas: live_eval is expression-only; use live_exec for statements; Live numeric args must be JSON numbers; Simpler.sample is not generally settable, so load samples/devices via the browser or create audio clips; object summaries are compact unless detail:true is requested. "
    "These are workflow hints only; the full Live object model remains available through paths, ids, calls, properties, children, listeners, and eval."
)


def schema(properties: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    return {"type": "object", "properties": properties, "required": required or [], "additionalProperties": False}


def make_server(client: AbletonBridgeClient | None = None) -> StdioMcpServer:
    bridge = client or AbletonBridgeClient(BridgeConfig(
        host=os.environ.get("ABLETON_MCP_HOST", "127.0.0.1"),
        port=int(os.environ.get("ABLETON_MCP_PORT", "8765")),
        timeout=float(os.environ.get("ABLETON_MCP_TIMEOUT", "10")),
    ))
    server = StdioMcpServer("ableton-object-mcp", __version__, ABLETON_MCP_INSTRUCTIONS)

    def forward(method: str):
        return lambda args: bridge.request(method, args)

    ref = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Live API path, e.g. 'live_set tracks 0 clip_slots 0'."},
            "id": {"type": "integer", "description": "Live object id returned by the bridge."},
        },
        "additionalProperties": False,
    }

    server.add_tool(Tool("live_ping", "Check Ableton bridge health and Live version.", schema({}), forward("ping")))
    response_controls = {
        "detail": {"type": "boolean", "description": "Include repr/canonical_path in Live object summaries. Default false for compact output."},
        "max_items": {"type": "integer", "description": "Maximum encoded list items. Use -1 for unbounded. Default 200."},
        "max_depth": {"type": "integer", "description": "Maximum encoded nesting depth. Default 8."},
        "max_string_length": {"type": "integer", "description": "Maximum encoded string length. Use -1 for unbounded. Default 4096."},
        "timeout": {"type": "number", "description": "Main-thread timeout in seconds for long Live operations. Default 10."},
    }
    server.add_tool(Tool("live_get", "Resolve an object and read selected properties/children. Use child_limit/detail/max_items to control output size.", schema({
        "ref": ref,
        "properties": {"type": "array", "items": {"type": "string"}},
        "children": {"oneOf": [
            {"type": "array", "items": {"type": "string"}},
            {"type": "object", "additionalProperties": {"type": "integer"}},
        ]},
        "child_limit": {"type": "integer", "minimum": 0},
        **response_controls,
    }, ["ref"]), forward("get")))
    server.add_tool(Tool("live_set", "Set a writable Live object property.", schema({
        "ref": ref,
        "property": {"type": "string"},
        "value": {},
        **response_controls,
    }, ["ref", "property", "value"]), forward("set")))
    server.add_tool(Tool("live_call", "Call one Live object method. Numeric args must be JSON numbers, not strings; batch repeated edits with live_eval/live_batch.", schema({
        "ref": ref,
        "method": {"type": "string"},
        "args": {"type": "array"},
        "kwargs": {"type": "object"},
        **response_controls,
    }, ["ref", "method"]), forward("call")))
    server.add_tool(Tool("live_children", "List child objects from a Live object child collection. Use limit/detail to avoid large browser/library dumps.", schema({
        "ref": ref,
        "child": {"type": "string"},
        "limit": {"type": "integer", "minimum": 0},
        **response_controls,
    }, ["ref", "child"]), forward("children")))
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
        },
        "additionalProperties": False,
    }
    server.add_tool(Tool("live_browser_roots", "List available app.browser root categories. Convenience only; full browser objects remain accessible via live_eval/live_call.", schema({}), forward("browser_roots")))
    server.add_tool(Tool("live_browser_search", "General bounded search over app.browser roots. Returns BrowserItem ids for load or deeper object-model inspection.", schema({
        "query": {"type": "string", "description": "Space-separated search terms matched against browser item names and paths. Empty browses top items."},
        "roots": {"type": "array", "items": {"type": "string"}, "description": "Browser roots such as instruments, audio_effects, drums, samples, sounds, packs, plugins, user_library. Default common library roots."},
        "limit": {"type": "integer", "minimum": 1, "description": "Maximum matches returned. Default 25."},
        "max_depth": {"type": "integer", "minimum": 0, "description": "Maximum browser traversal depth. Default 8."},
        "max_visited": {"type": "integer", "minimum": 1, "description": "Maximum browser items visited. Default 5000."},
        "loadable_only": {"type": "boolean", "description": "Only return loadable browser items. Default true."},
        "include_folders": {"type": "boolean", "description": "Include matching folders. Default false."},
        "stop_on_limit": {"type": "boolean", "description": "Stop traversal as soon as limit matches are found. Faster but less globally ranked. Default false."},
        "stop_score": {"type": "integer", "description": "With stop_on_limit, only stop after this match quality or better. 0 exact name, 1 query in name, 2 all terms in name, 3 query in path. Default 0; use 1 for first-good sample/plugin hunts."},
        "match_all_terms": {"type": "boolean", "description": "Require every query term to match. Default true."},
    }), forward("browser_search")))
    server.add_tool(Tool("live_browser_load", "Load a BrowserItem returned by live_browser_search. Convenience only; app.browser.load_item remains available through live_eval.", schema({
        "item": browser_item_ref,
        "target_track": ref,
    }, ["item"]), forward("browser_load")))
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
