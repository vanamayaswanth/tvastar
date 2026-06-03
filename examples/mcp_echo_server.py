"""A minimal, real MCP server over stdio — pure stdlib, no dependencies.

It implements just enough of the Model Context Protocol (initialize,
tools/list, tools/call) to be a genuine MCP server. Tvastar connects to it via
``connect_mcp_server(command="python", args=["examples/mcp_echo_server.py"])``.

Tools exposed:
    add(a, b)        -> a + b
    upper(text)      -> text.upper()
    weather(city)    -> a canned forecast (stands in for a real API call)

Run standalone to sanity-check it speaks JSON-RPC:
    echo '{"jsonrpc":"2.0","id":1,"method":"tools/list"}' | python examples/mcp_echo_server.py
"""

from __future__ import annotations

import json
import sys

TOOLS = [
    {
        "name": "add",
        "description": "Add two numbers and return the sum.",
        "inputSchema": {
            "type": "object",
            "properties": {"a": {"type": "number"}, "b": {"type": "number"}},
            "required": ["a", "b"],
        },
    },
    {
        "name": "upper",
        "description": "Uppercase a string.",
        "inputSchema": {
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        },
    },
    {
        "name": "weather",
        "description": "Get a (canned) weather forecast for a city.",
        "inputSchema": {
            "type": "object",
            "properties": {"city": {"type": "string"}},
            "required": ["city"],
        },
    },
]


def call_tool(name: str, args: dict) -> dict:
    if name == "add":
        return _text(str(args["a"] + args["b"]))
    if name == "upper":
        return _text(str(args["text"]).upper())
    if name == "weather":
        return _text(f"{args['city']}: 22°C, clear skies, light breeze.")
    return {"content": [{"type": "text", "text": f"unknown tool {name}"}], "isError": True}


def _text(s: str) -> dict:
    return {"content": [{"type": "text", "text": s}]}


def handle(msg: dict):
    method = msg.get("method")
    mid = msg.get("id")
    if method == "initialize":
        return _ok(
            mid,
            {
                "protocolVersion": msg.get("params", {}).get("protocolVersion", "2025-06-18"),
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "tvastar-echo-mcp", "version": "1.0.0"},
            },
        )
    if method == "notifications/initialized":
        return None  # notification: no response
    if method == "tools/list":
        return _ok(mid, {"tools": TOOLS})
    if method == "tools/call":
        params = msg.get("params", {})
        try:
            return _ok(mid, call_tool(params["name"], params.get("arguments", {})))
        except Exception as e:  # surface as a JSON-RPC error
            return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32000, "message": str(e)}}
    if mid is not None:
        return {
            "jsonrpc": "2.0",
            "id": mid,
            "error": {"code": -32601, "message": "method not found"},
        }
    return None


def _ok(mid, result) -> dict:
    return {"jsonrpc": "2.0", "id": mid, "result": result}


def main() -> None:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        resp = handle(msg)
        if resp is not None:
            sys.stdout.write(json.dumps(resp) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
