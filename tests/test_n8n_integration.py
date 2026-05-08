"""Tests for Phase 24: n8n Deep Integration."""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch, call

ROOT = Path(__file__).parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _make_mock_client(list_result=None, trigger_result=None, execution_result=None):
    mock = MagicMock()
    mock.list_workflows.return_value = list_result or {"data": []}
    mock.trigger_webhook.return_value = trigger_result or {"execution_id": "exec_1"}
    mock._request.return_value = execution_result or {"status": "success", "finished": True, "data": {}}
    mock.activate_workflow.return_value = {"active": True}
    mock.base_url = "https://daniliyc.app.n8n.cloud"
    mock._api_headers.return_value = {"X-N8N-API-KEY": "test"}
    return mock


# Ensure module is loaded
import app.services.n8n_integration as n8n_mod


# ─── discover_n8n_workflows ───────────────────────────────────────────────────

class TestDiscoverN8nWorkflows:
    def test_returns_list(self):
        client = _make_mock_client(list_result=[
            {"id": "1", "name": "Daily Report", "active": True, "nodes": []},
        ])
        with patch.object(n8n_mod, "_client", return_value=client):
            result = n8n_mod.discover_n8n_workflows()
        assert isinstance(result, list)
        assert len(result) == 1

    def test_workflow_fields(self):
        client = _make_mock_client(list_result=[
            {"id": "42", "name": "Email Digest", "active": False, "nodes": []},
        ])
        with patch.object(n8n_mod, "_client", return_value=client):
            result = n8n_mod.discover_n8n_workflows()
        assert result[0]["id"] == "42"
        assert result[0]["name"] == "Email Digest"
        assert result[0]["active"] is False

    def test_empty_list_raw(self):
        client = _make_mock_client(list_result=[])
        with patch.object(n8n_mod, "_client", return_value=client):
            result = n8n_mod.discover_n8n_workflows()
        assert result == []

    def test_data_envelope(self):
        client = _make_mock_client(list_result={"data": [
            {"id": "5", "name": "Wf5", "active": True, "nodes": []},
        ]})
        with patch.object(n8n_mod, "_client", return_value=client):
            result = n8n_mod.discover_n8n_workflows()
        assert len(result) == 1
        assert result[0]["name"] == "Wf5"

    def test_webhook_url_extracted_from_nodes(self):
        client = _make_mock_client(list_result=[{
            "id": "7",
            "name": "Webhook Wf",
            "active": True,
            "nodes": [{"type": "n8n-nodes-base.Webhook", "parameters": {"path": "my-hook"}}],
        }])
        with patch.object(n8n_mod, "_client", return_value=client):
            with patch.dict("os.environ", {"N8N_BASE_URL": "https://n8n.example.com"}):
                result = n8n_mod.discover_n8n_workflows()
        assert "my-hook" in result[0]["trigger_url"]

    def test_client_error_propagates(self):
        client = MagicMock()
        client.list_workflows.side_effect = Exception("auth error")
        import pytest
        with patch.object(n8n_mod, "_client", return_value=client):
            with pytest.raises(Exception, match="auth error"):
                n8n_mod.discover_n8n_workflows()

    def test_multiple_workflows(self):
        client = _make_mock_client(list_result=[
            {"id": "1", "name": "A", "active": True, "nodes": []},
            {"id": "2", "name": "B", "active": False, "nodes": []},
        ])
        with patch.object(n8n_mod, "_client", return_value=client):
            result = n8n_mod.discover_n8n_workflows()
        assert len(result) == 2


# ─── trigger_workflow ─────────────────────────────────────────────────────────

class TestTriggerWorkflow:
    def test_returns_execution_id(self):
        client = _make_mock_client(trigger_result={"execution_id": "exec_99"})
        with patch.object(n8n_mod, "_client", return_value=client):
            result = n8n_mod.trigger_workflow("123")
        assert result.get("execution_id") == "exec_99"

    def test_passes_payload_to_webhook(self):
        client = _make_mock_client(trigger_result={"execution_id": "e1"})
        with patch.object(n8n_mod, "_client", return_value=client):
            n8n_mod.trigger_workflow("1", payload={"key": "val"})
        client.trigger_webhook.assert_called_once_with({"key": "val"})

    def test_name_to_id_resolution(self):
        """Non-numeric workflow_id: try to discover by name."""
        client = _make_mock_client(
            list_result=[{"id": "77", "name": "Daily Report", "active": True, "nodes": []}],
            trigger_result={"execution_id": "e77"},
        )
        with patch.object(n8n_mod, "_client", return_value=client):
            result = n8n_mod.trigger_workflow("daily")
        assert result.get("execution_id") == "e77"

    def test_empty_payload_default(self):
        client = _make_mock_client(trigger_result={"execution_id": "e2"})
        with patch.object(n8n_mod, "_client", return_value=client):
            n8n_mod.trigger_workflow("1")
        client.trigger_webhook.assert_called_once_with({})

    def test_numeric_id_not_resolved(self):
        """Pure numeric id skips name resolution → list_workflows not called."""
        client = _make_mock_client(trigger_result={"execution_id": "direct"})
        with patch.object(n8n_mod, "_client", return_value=client):
            n8n_mod.trigger_workflow("42")
        client.list_workflows.assert_not_called()


# ─── get_workflow_status ──────────────────────────────────────────────────────

class TestGetWorkflowStatus:
    def test_returns_status(self):
        client = _make_mock_client(execution_result={"status": "success", "finished": True, "data": {}})
        with patch.object(n8n_mod, "_client", return_value=client):
            result = n8n_mod.get_workflow_status("exec_1")
        assert result["status"] == "success"
        assert result["finished"] is True

    def test_execution_id_in_result(self):
        client = _make_mock_client(execution_result={"status": "running", "finished": False})
        with patch.object(n8n_mod, "_client", return_value=client):
            result = n8n_mod.get_workflow_status("my_exec")
        assert result["execution_id"] == "my_exec"

    def test_error_returns_error_dict(self):
        client = MagicMock()
        client._request.side_effect = Exception("not found")
        client.base_url = "https://n8n.example.com"
        client._api_headers.return_value = {}
        with patch.object(n8n_mod, "_client", return_value=client):
            result = n8n_mod.get_workflow_status("bad_id")
        assert result["status"] == "error"
        assert "not found" in result["error"]

    def test_unknown_status_default(self):
        client = _make_mock_client(execution_result={"finished": False})
        with patch.object(n8n_mod, "_client", return_value=client):
            result = n8n_mod.get_workflow_status("e1")
        assert result["status"] == "unknown"


# ─── toggle_workflow ──────────────────────────────────────────────────────────

class TestToggleWorkflow:
    def test_enable_returns_true(self):
        client = _make_mock_client()
        with patch.object(n8n_mod, "_client", return_value=client):
            assert n8n_mod.toggle_workflow("1", True) is True

    def test_disable_returns_true(self):
        client = _make_mock_client()
        with patch.object(n8n_mod, "_client", return_value=client):
            assert n8n_mod.toggle_workflow("1", False) is True

    def test_error_returns_false(self):
        client = MagicMock()
        client.activate_workflow.side_effect = Exception("not found")
        with patch.object(n8n_mod, "_client", return_value=client):
            assert n8n_mod.toggle_workflow("bad", True) is False

    def test_calls_activate_with_correct_active(self):
        client = _make_mock_client()
        with patch.object(n8n_mod, "_client", return_value=client):
            n8n_mod.toggle_workflow("5", False)
        client.activate_workflow.assert_called_once_with("5", active=False)


# ─── workflow_list_text ───────────────────────────────────────────────────────

class TestWorkflowListText:
    def test_empty(self):
        text = n8n_mod.workflow_list_text([])
        assert "📭" in text

    def test_active_workflow(self):
        text = n8n_mod.workflow_list_text([{"id": "1", "name": "Daily", "active": True}])
        assert "✅" in text
        assert "Daily" in text

    def test_inactive_workflow(self):
        text = n8n_mod.workflow_list_text([{"id": "2", "name": "Draft", "active": False}])
        assert "⏸" in text

    def test_run_hint(self):
        text = n8n_mod.workflow_list_text([{"id": "1", "name": "X", "active": True}])
        assert "/n8n run" in text


# ─── Smart Router n8n_workflow ────────────────────────────────────────────────

class TestSmartRouterN8n:
    def test_n8n_workflow_agent_triggers_n8n(self):
        mock_trigger = MagicMock(return_value={"execution_id": "e_from_router"})
        with patch("app.services.n8n_integration.trigger_workflow", mock_trigger):
            import app.services.smart_router as sr
            result = sr._call_agent("n8n_workflow", "run daily report")
        assert "e_from_router" in result.get("answer", "") or result.get("execution_id") == "e_from_router"

    def test_n8n_workflow_error_returns_error_dict(self):
        with patch("app.services.n8n_integration.trigger_workflow", side_effect=Exception("no key")):
            import app.services.smart_router as sr
            result = sr._call_agent("n8n_workflow", "test")
        assert "_error" in result
