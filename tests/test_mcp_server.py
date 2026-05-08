"""Tests for Phase 25: MCP Server Adapter."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.mcp_server import JarvisMCPServer, TOOLS, MCP_PROTOCOL_VERSION


# ─── Initialize handshake ─────────────────────────────────────────────────────

class TestInitialize:
    def test_initialize_returns_protocol_version(self):
        server = JarvisMCPServer()
        response = server.handle_request({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
        assert response["result"]["protocolVersion"] == MCP_PROTOCOL_VERSION

    def test_initialize_returns_server_info(self):
        server = JarvisMCPServer()
        response = server.handle_request({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
        info = response["result"]["serverInfo"]
        assert info["name"] == "jarvis"
        assert "version" in info

    def test_initialize_sets_initialized_flag(self):
        server = JarvisMCPServer()
        assert not server._initialized
        server.handle_request({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
        assert server._initialized

    def test_initialize_returns_capabilities(self):
        server = JarvisMCPServer()
        response = server.handle_request({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
        assert "capabilities" in response["result"]

    def test_notification_initialized_returns_none(self):
        server = JarvisMCPServer()
        response = server.handle_request({
            "jsonrpc": "2.0",
            "method": "notifications/initialized",
        })
        assert response is None


# ─── tools/list ───────────────────────────────────────────────────────────────

class TestToolsList:
    def test_returns_tools_key(self):
        server = JarvisMCPServer()
        response = server.handle_request({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
        assert "tools" in response["result"]

    def test_returns_3_tools(self):
        server = JarvisMCPServer()
        response = server.handle_request({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
        tools = response["result"]["tools"]
        assert len(tools) == 3

    def test_jarvis_research_tool_present(self):
        server = JarvisMCPServer()
        response = server.handle_request({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
        names = [t["name"] for t in response["result"]["tools"]]
        assert "jarvis_research" in names

    def test_jarvis_create_table_tool_present(self):
        server = JarvisMCPServer()
        response = server.handle_request({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
        names = [t["name"] for t in response["result"]["tools"]]
        assert "jarvis_create_table" in names

    def test_jarvis_parse_file_tool_present(self):
        server = JarvisMCPServer()
        response = server.handle_request({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
        names = [t["name"] for t in response["result"]["tools"]]
        assert "jarvis_parse_file" in names

    def test_tools_have_input_schema(self):
        server = JarvisMCPServer()
        response = server.handle_request({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
        for tool in response["result"]["tools"]:
            assert "inputSchema" in tool, f"Tool {tool['name']} missing inputSchema"

    def test_tools_have_description(self):
        server = JarvisMCPServer()
        response = server.handle_request({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
        for tool in response["result"]["tools"]:
            assert tool.get("description"), f"Tool {tool['name']} missing description"


# ─── tools/call ───────────────────────────────────────────────────────────────

class TestToolsCall:
    def _call_tool(self, name, arguments, backend_response=None):
        server = JarvisMCPServer()
        if backend_response is None:
            backend_response = {"answer": "mock answer"}
        with patch("app.services.mcp_server._call_backend", return_value=backend_response):
            return server.handle_request({
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": name, "arguments": arguments},
            })

    def test_research_call_returns_content(self):
        response = self._call_tool("jarvis_research", {"query": "test"})
        assert "content" in response["result"]

    def test_create_table_call_returns_content(self):
        response = self._call_tool("jarvis_create_table", {"query": "top AI"})
        assert "content" in response["result"]

    def test_parse_file_call_returns_content(self):
        response = self._call_tool("jarvis_parse_file", {"file_path": "/tmp/test.pdf"})
        assert "content" in response["result"]

    def test_answer_in_content_text(self):
        response = self._call_tool("jarvis_research", {"query": "test"}, {"answer": "great answer"})
        text = response["result"]["content"][0]["text"]
        assert "great answer" in text

    def test_backend_error_is_error_result(self):
        response = self._call_tool("jarvis_research", {"query": "test"}, {"_error": "timeout"})
        assert response["result"]["isError"] is True

    def test_unknown_tool_returns_error(self):
        server = JarvisMCPServer()
        response = server.handle_request({
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {"name": "nonexistent_tool", "arguments": {}},
        })
        assert "error" in response

    def test_backend_called_with_correct_endpoint(self):
        server = JarvisMCPServer()
        with patch("app.services.mcp_server._call_backend", return_value={"answer": "ok"}) as mock_backend:
            server.handle_request({
                "jsonrpc": "2.0", "id": 5,
                "method": "tools/call",
                "params": {"name": "jarvis_research", "arguments": {"query": "AI"}},
            })
        mock_backend.assert_called_once()
        args = mock_backend.call_args[0]
        assert "/research" in args[0] or "research" in args[0]


# ─── Protocol basics ──────────────────────────────────────────────────────────

class TestProtocol:
    def test_unknown_method_returns_error(self):
        server = JarvisMCPServer()
        response = server.handle_request({"jsonrpc": "2.0", "id": 99, "method": "unknown/method", "params": {}})
        assert "error" in response

    def test_ping_returns_empty_result(self):
        server = JarvisMCPServer()
        response = server.handle_request({"jsonrpc": "2.0", "id": 0, "method": "ping", "params": {}})
        assert response["result"] == {}

    def test_id_preserved_in_response(self):
        server = JarvisMCPServer()
        response = server.handle_request({"jsonrpc": "2.0", "id": 42, "method": "ping", "params": {}})
        assert response["id"] == 42

    def test_custom_tools_list(self):
        custom_tools = [{"name": "custom_tool", "description": "test", "inputSchema": {}}]
        server = JarvisMCPServer(tools=custom_tools)
        response = server.handle_request({"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}})
        names = [t["name"] for t in response["result"]["tools"]]
        assert "custom_tool" in names


# ─── mcp_adapter integration ──────────────────────────────────────────────────

class TestMCPAdapterIntegration:
    def test_jarvis_server_call_dispatches_to_mcp_server(self):
        from app.services import mcp_adapter
        mcp_adapter.register_mcp_server("jarvis", "python", ["scripts/run_mcp_server.py"])

        with patch("app.services.mcp_server._call_backend", return_value={"answer": "research result"}):
            result = mcp_adapter.call_mcp_tool("jarvis", "jarvis_research", {"query": "test"})

        assert result is not None
        # Should have content array (MCP result) or answer
        assert "content" in result or "isError" in result

    def test_unregistered_server_returns_error(self):
        from app.services import mcp_adapter
        result = mcp_adapter.call_mcp_tool("nonexistent_server", "some_tool", {})
        assert "error" in result

    def test_list_mcp_servers_after_register(self):
        from app.services import mcp_adapter
        mcp_adapter.register_mcp_server("test_srv", "python", [])
        servers = mcp_adapter.list_mcp_servers()
        assert "test_srv" in servers
