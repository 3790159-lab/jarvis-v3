"""Phase 25: MCP Server — Jarvis tools exposed as MCP server via stdio transport.

JSON-RPC 2.0 over stdin/stdout. Supports:
  initialize     → handshake
  tools/list     → return TOOLS registry
  tools/call     → dispatch to backend

Claude Desktop config:
{
  "mcpServers": {
    "jarvis": {
      "command": "python",
      "args": ["scripts/run_mcp_server.py"],
      "env": {"JARVIS_BACKEND": "http://127.0.0.1:8010"}
    }
  }
}
"""
from __future__ import annotations

import json
import logging
import os
import sys
import urllib.request
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

MCP_PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "jarvis"
SERVER_VERSION = "1.0.0"

TOOLS: List[Dict[str, Any]] = [
    {
        "name": "jarvis_research",
        "description": "Search the web and return a concise research report via Perplexity.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query"}
            },
            "required": ["query"],
        },
    },
    {
        "name": "jarvis_create_table",
        "description": "Create an Excel/CSV table from a query. Returns a download path.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Table description or data query"}
            },
            "required": ["query"],
        },
    },
    {
        "name": "jarvis_parse_file",
        "description": "Parse the content of a PDF, DOCX, or XLSX file and extract key information.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "file_path": {"type": "string", "description": "Absolute path to the file"},
                "query": {"type": "string", "description": "Optional question about the file"},
            },
            "required": ["file_path"],
        },
    },
]

# Map tool name → backend endpoint
_TOOL_ENDPOINTS: Dict[str, str] = {
    "jarvis_research": "/api/jarvis/tools/internet/research",
    "jarvis_create_table": "/api/jarvis/tools/table/create",
    "jarvis_parse_file": "/api/jarvis/tools/file/parse",
}


def _backend() -> str:
    return (
        os.environ.get("JARVIS_BACKEND")
        or os.environ.get("BACKEND_BASE_URL")
        or "http://127.0.0.1:8010"
    ).rstrip("/")


def _call_backend(endpoint: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    url = _backend() + endpoint
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        url, data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            return json.loads(resp.read())
    except Exception as exc:
        return {"_error": str(exc)}


class JarvisMCPServer:
    """MCP server that exposes Jarvis tools via JSON-RPC 2.0 over stdio."""

    def __init__(self, tools: Optional[List[Dict[str, Any]]] = None) -> None:
        self.tools = tools if tools is not None else TOOLS
        self._initialized = False

    # ------------------------------------------------------------------
    # JSON-RPC dispatch
    # ------------------------------------------------------------------

    def handle_request(self, request: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Process one JSON-RPC request and return the response dict (or None for notifications)."""
        req_id = request.get("id")
        method = request.get("method", "")
        params = request.get("params") or {}

        try:
            if method == "initialize":
                result = self._handle_initialize(params)
            elif method == "notifications/initialized":
                self._initialized = True
                return None  # notification, no response
            elif method == "tools/list":
                result = self._handle_tools_list(params)
            elif method == "tools/call":
                result = self._handle_tools_call(params)
            elif method == "ping":
                result = {}
            else:
                return self._error_response(req_id, -32601, f"Method not found: {method}")

            return {"jsonrpc": "2.0", "id": req_id, "result": result}

        except Exception as exc:
            logger.exception("Error handling %s", method)
            return self._error_response(req_id, -32603, str(exc))

    def _handle_initialize(self, params: Dict[str, Any]) -> Dict[str, Any]:
        self._initialized = True
        return {
            "protocolVersion": MCP_PROTOCOL_VERSION,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
        }

    def _handle_tools_list(self, params: Dict[str, Any]) -> Dict[str, Any]:
        return {"tools": self.tools}

    def _handle_tools_call(self, params: Dict[str, Any]) -> Dict[str, Any]:
        tool_name = params.get("name", "")
        arguments = params.get("arguments") or {}

        endpoint = _TOOL_ENDPOINTS.get(tool_name)
        if not endpoint:
            raise ValueError(f"Unknown tool: {tool_name}")

        raw = _call_backend(endpoint, arguments)
        if raw.get("_error"):
            return {
                "content": [{"type": "text", "text": f"Error: {raw['_error']}"}],
                "isError": True,
            }

        answer = raw.get("answer") or raw.get("result") or json.dumps(raw)
        return {
            "content": [{"type": "text", "text": str(answer)}],
            "isError": False,
        }

    @staticmethod
    def _error_response(req_id: Any, code: int, message: str) -> Dict[str, Any]:
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "error": {"code": code, "message": message},
        }

    # ------------------------------------------------------------------
    # Stdio transport
    # ------------------------------------------------------------------

    def serve_stdio(self) -> None:
        """Run MCP server: read JSON-RPC from stdin, write responses to stdout."""
        logger.info("Jarvis MCP server started (stdio transport)")
        while True:
            try:
                line = sys.stdin.readline()
                if not line:
                    break
                line = line.strip()
                if not line:
                    continue
                request = json.loads(line)
                response = self.handle_request(request)
                if response is not None:
                    sys.stdout.write(json.dumps(response) + "\n")
                    sys.stdout.flush()
            except KeyboardInterrupt:
                break
            except Exception as exc:
                error = {
                    "jsonrpc": "2.0",
                    "id": None,
                    "error": {"code": -32700, "message": f"Parse error: {exc}"},
                }
                sys.stdout.write(json.dumps(error) + "\n")
                sys.stdout.flush()
