"""Phase 25 (Block D2): MCP Adapter — registration + JarvisMCPServer integration.

Phase 22 skeleton upgraded: mcp_adapter now delegates to JarvisMCPServer for
actual tool dispatch. Use scripts/run_mcp_server.py for stdio-based Claude Desktop.

In Block D2 this exposes Jarvis as a Model Context Protocol server so
Claude Desktop can call Jarvis tools (internet_research, create_table, etc.)
as native MCP tools.

Current state: registration + routing stubs; actual MCP transport in Block D2.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Registry of MCP servers (filled in Block D2)
_mcp_servers: Dict[str, Dict[str, Any]] = {}


def register_mcp_server(
    name: str,
    command: str,
    args: Optional[List[str]] = None,
    env: Optional[Dict[str, str]] = None,
) -> None:
    """Register an MCP server. Called at startup (Block D2)."""
    _mcp_servers[name] = {
        "command": command,
        "args": args or [],
        "env": env or {},
        "active": False,
    }
    logger.info("MCP server registered: %s", name)


def call_mcp_tool(
    server: str,
    tool: str,
    params: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Call a tool on an MCP server.

    For the built-in 'jarvis' server, dispatches directly to JarvisMCPServer.
    For external servers, would start via stdio (future).
    """
    if server not in _mcp_servers:
        return {"error": f"MCP server '{server}' not registered"}

    # Built-in jarvis server → direct dispatch
    if server == "jarvis":
        try:
            from app.services.mcp_server import JarvisMCPServer
            srv = JarvisMCPServer()
            request = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": tool, "arguments": params or {}},
            }
            response = srv.handle_request(request)
            if response and "result" in response:
                return response["result"]
            if response and "error" in response:
                return {"error": response["error"]["message"]}
        except Exception as exc:
            return {"error": str(exc)}

    return {
        "result": None,
        "_external_stdio": True,
        "note": f"External MCP server '{server}' requires stdio transport (not yet implemented)",
    }


def list_mcp_servers() -> List[str]:
    """Return names of all registered MCP servers."""
    return list(_mcp_servers.keys())


def is_mcp_available(server: str) -> bool:
    """Return True if the server is registered and active."""
    return _mcp_servers.get(server, {}).get("active", False)
