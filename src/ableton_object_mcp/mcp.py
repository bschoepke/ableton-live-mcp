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
    def __init__(self, name: str, version: str) -> None:
        self.name = name
        self.version = version
        self.tools: dict[str, Tool] = {}

    def add_tool(self, tool: Tool) -> None:
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
                return self._result(req_id, {
                    "protocolVersion": request.get("params", {}).get("protocolVersion", "2024-11-05"),
                    "serverInfo": {"name": self.name, "version": self.version},
                    "capabilities": {"tools": {}},
                })
            if method == "notifications/initialized":
                return None
            if method == "tools/list":
                return self._result(req_id, {"tools": [tool.as_mcp() for tool in self.tools.values()]})
            if method == "tools/call":
                params = request.get("params", {})
                name = params.get("name")
                if name not in self.tools:
                    raise ValueError(f"Unknown tool: {name}")
                result = self.tools[name].handler(params.get("arguments", {}) or {})
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
