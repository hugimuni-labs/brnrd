"""Small stdio MCP adapter; operations and home data do not depend on MCP."""

from __future__ import annotations

from dataclasses import asdict
import json
from pathlib import Path
import sys
from typing import TextIO

from .. import __version__
from .memory import note, obligations
from .recall import recall
from .consolidation import consolidate, proposals

TOOLS = [
    {"name": "consolidate", "description": "Ask a configured cheap limb for proposed full-file edits; never applies them.",
     "inputSchema": {"type": "object", "properties": {"since": {"type": "string"}}, "additionalProperties": False}},
    {"name": "proposals", "description": "List open consolidation proposals for explicit curation outside MCP.",
     "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False}},
    {"name": "recall", "description": "Search authored home knowledge; returns paths, lines and provenance.",
     "inputSchema": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"], "additionalProperties": False}},
    {"name": "note", "description": "Append an authored note; now records orientation and checked advancement.",
     "inputSchema": {"type": "object", "properties": {
         "kind": {"type": "string", "enum": ["notebook", "playbook", "pitfall", "knowledge", "obligation", "now"]},
         "text": {"type": "string"}}, "required": ["kind", "text"], "additionalProperties": False}},
    {"name": "obligations", "description": "Read authored commitments and their states; does not infer completion.",
     "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False}},
]
# Implement the stable stateful stdio lifecycle used by Claude Code, not the
# newer stateless draft. Echo only protocol versions this server implements.
VERSIONS = ("2024-11-05", "2025-03-26", "2025-06-18", "2025-11-25")


def call_tool(home: Path, name: str, args: dict) -> dict:
    spec = next((t for t in TOOLS if t["name"] == name), None)
    if spec is None:
        raise ValueError(f"Unknown tool: {name}")
    schema = spec["inputSchema"]
    required = set(schema.get("required", []))
    if not required <= args.keys() or args.keys() - schema["properties"].keys():
        raise ValueError(f"Invalid arguments for {name}")
    if any(not isinstance(v, str) for v in args.values()):
        raise ValueError("Tool arguments must be strings")
    if name == "recall":
        result = [{**asdict(hit), "path": str(hit.path.relative_to(home))}
                  for hit in recall(home, args["query"])]
    elif name == "note":
        result = {"path": str(note(home, args["kind"], args["text"]).relative_to(home))}
    elif name == "consolidate":
        result = {"path": str(consolidate(home, args.get("since", "last")))}
    elif name == "proposals":
        result = proposals(home)
    else:
        result = obligations(home)
    return {"content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False)}]}


def serve(home: Path, stream: TextIO | None = None, output: TextIO | None = None) -> None:
    """Newline-delimited JSON-RPC. Notifications never receive a response."""
    stream, output = stream or sys.stdin, output or sys.stdout
    initialized = False
    for line in stream:
        request = None
        try:
            request = json.loads(line)
        except json.JSONDecodeError:
            reply = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}}
        else:
            if not isinstance(request, dict) or request.get("jsonrpc") != "2.0" or not isinstance(request.get("method"), str):
                reply = {"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "Invalid request"}}
            elif "id" not in request:
                continue
            else:
                ident = request["id"]
                reply = {"jsonrpc": "2.0", "id": ident}
                method, params = request["method"], request.get("params", {})
                if not isinstance(params, dict):
                    reply["error"] = {"code": -32602, "message": "params must be an object"}
                elif method == "initialize":
                    requested = params.get("protocolVersion")
                    reply["result"] = {"protocolVersion": requested if requested in VERSIONS else VERSIONS[-1],
                                       "capabilities": {"tools": {}},
                                       "serverInfo": {"name": "brnrd-self", "version": __version__}}
                    initialized = True
                elif method == "ping":
                    reply["result"] = {}
                elif not initialized:
                    reply["error"] = {"code": -32000, "message": "initialize first"}
                elif method == "tools/list":
                    reply["result"] = {"tools": TOOLS}
                elif method == "tools/call":
                    name, args = params.get("name"), params.get("arguments", {})
                    if not isinstance(name, str) or not isinstance(args, dict):
                        reply["error"] = {"code": -32602, "message": "name and arguments required"}
                    else:
                        try:
                            reply["result"] = call_tool(home, name, args)
                        except (ValueError, OSError) as exc:
                            reply["result"] = {"isError": True, "content": [{"type": "text", "text": str(exc)}]}
                else:
                    reply["error"] = {"code": -32601, "message": f"Unknown method: {method}"}
        output.write(json.dumps(reply, ensure_ascii=False) + "\n")
        output.flush()
