from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from typing import Any, Callable, TextIO


Json = dict[str, Any]
Handler = Callable[[Json], Any]


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    input_schema: Json
    handler: Handler

    def as_mcp(self) -> Json:
        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": self.input_schema,
        }


class StdioMcpServer:
    def __init__(self, name: str, version: str, instructions: str | None = None) -> None:
        self.name = name
        self.version = version
        self.instructions = instructions
        self.tools: dict[str, Tool] = {}

    def add_tool(self, tool: Tool) -> None:
        # The MCP spec requires every tool's inputSchema to be a JSON Schema
        # object (type == "object"). Strict clients (e.g. Claude Code) reject the
        # entire tools/list when any single tool violates this, which silently
        # disables every tool on the server. Fail loudly at registration so a
        # malformed schema surfaces at startup on any client -- including lenient
        # ones -- instead of only when a strict client connects.
        schema = tool.input_schema
        if not isinstance(schema, dict) or schema.get("type") != "object":
            raise ValueError(
                f"Tool {tool.name!r} has an invalid inputSchema: MCP requires a "
                f'JSON Schema object with "type": "object" (got {schema!r}). '
                "Build it with schema(...) or loose_schema()."
            )
        self.tools[tool.name] = tool

    def serve(self, stdin: TextIO | None = None, stdout: TextIO | None = None) -> None:
        in_stream = stdin or sys.stdin
        out_stream = stdout or sys.stdout
        for line in in_stream:
            if not line.strip():
                continue
            response = self.handle(json.loads(line))
            if response is not None:
                out_stream.write(json.dumps(response, separators=(",", ":")) + "\n")
                out_stream.flush()

    def handle(self, request: Json) -> Json | None:
        req_id = request.get("id")
        method = request.get("method")
        try:
            if method == "initialize":
                result = {
                    "protocolVersion": request.get("params", {}).get("protocolVersion", "2024-11-05"),
                    "serverInfo": {"name": self.name, "version": self.version},
                    "capabilities": {"tools": {}},
                }
                if self.instructions:
                    result["instructions"] = self.instructions
                return self._result(req_id, result)
            if method == "notifications/initialized":
                return None
            if method == "tools/list":
                return self._result(req_id, {"tools": [tool.as_mcp() for tool in self.tools.values()]})
            if method == "tools/call":
                params = request.get("params", {})
                name = params.get("name")
                if name not in self.tools:
                    raise ValueError(f"Unknown tool: {name}")
                tool = self.tools[name]
                arguments = params.get("arguments", {}) or {}
                _validate(arguments, tool.input_schema, "arguments")
                result = tool.handler(arguments)
                if not isinstance(result, dict):
                    # MCP requires structuredContent to be a JSON object. Tools can
                    # return non-object values -- live_call on a void setter like
                    # delete_device yields None; live_eval/live_exec yield whatever
                    # the code evaluates to (str/int/bool/list); collection tools
                    # return lists -- which would otherwise emit a non-object
                    # structuredContent and fail strict client validation (Claude
                    # Code rejects the whole call: "expected record, received
                    # <type>"), even though the call ran. Wrap any non-object return
                    # in a success record so the value is always addressable under
                    # .result and never mistaken for a failure. None is just the
                    # void case of this rule -> {"ok": true, "result": null}.
                    result = {"ok": True, "result": result}
                return self._result(req_id, {
                    "content": [{"type": "text", "text": json.dumps(result, separators=(",", ":"))}],
                    "structuredContent": result,
                })
            raise ValueError(f"Unsupported MCP method: {method}")
        except Exception as exc:  # MCP requires structured errors instead of crashing stdio.
            return {"jsonrpc": "2.0", "id": req_id, "error": {"code": -32000, "message": str(exc)}}

    @staticmethod
    def _result(req_id: Any, result: Any) -> Json:
        return {"jsonrpc": "2.0", "id": req_id, "result": result}


def _validate(value: Any, schema: Json, path: str) -> None:
    if not schema:
        return
    if "oneOf" in schema:
        errors = []
        for option in schema["oneOf"]:
            try:
                _validate(value, option, path)
                return
            except ValueError as exc:
                errors.append(str(exc))
        raise ValueError("%s does not match any allowed shape: %s" % (path, "; ".join(errors)))
    expected = schema.get("type")
    if expected:
        _validate_type(value, expected, path)
    if "enum" in schema and value not in schema["enum"]:
        raise ValueError("%s must be one of: %s" % (path, ", ".join(map(str, schema["enum"]))))
    if "minimum" in schema and value < schema["minimum"]:
        raise ValueError("%s must be >= %s" % (path, schema["minimum"]))
    if expected == "object":
        if schema.get("additionalProperties") is False:
            allowed = set(schema.get("properties", {}).keys())
            extra = sorted(set(value.keys()) - allowed)
            if extra:
                raise ValueError("%s has unknown fields: %s" % (path, ", ".join(extra)))
        for name in schema.get("required", []):
            if name not in value:
                raise ValueError("%s.%s is required" % (path, name))
        for name, item in value.items():
            child_schema = schema.get("properties", {}).get(name)
            if child_schema:
                _validate(item, child_schema, "%s.%s" % (path, name))
            elif isinstance(schema.get("additionalProperties"), dict):
                _validate(item, schema["additionalProperties"], "%s.%s" % (path, name))
    elif expected == "array" and "items" in schema:
        for index, item in enumerate(value):
            _validate(item, schema["items"], "%s[%s]" % (path, index))


def _validate_type(value: Any, expected: str, path: str) -> None:
    checks = {
        "object": lambda item: isinstance(item, dict),
        "array": lambda item: isinstance(item, list),
        "string": lambda item: isinstance(item, str),
        "integer": lambda item: isinstance(item, int) and not isinstance(item, bool),
        "number": lambda item: isinstance(item, (int, float)) and not isinstance(item, bool),
        "boolean": lambda item: isinstance(item, bool),
    }
    check = checks.get(expected)
    if check and not check(value):
        raise ValueError("%s must be %s" % (path, expected))
