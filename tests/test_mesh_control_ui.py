"""Tests for Phase 20 (Block D1): Mesh Control UI with inline keyboards."""
from __future__ import annotations

import importlib
import json
import os
import sys
import uuid
from pathlib import Path
from unittest.mock import MagicMock, call, patch

ROOT = Path(__file__).parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.mesh_settings import (
    load_mesh_settings,
    reset_to_defaults,
    save_mesh_settings,
    should_confirm_task,
    update_setting,
    _DEFAULTS,
)


# ---------------------------------------------------------------------------
# mesh_settings.py tests
# ---------------------------------------------------------------------------

class TestMeshSettings:
    def setup_method(self, tmp_path_factory=None):
        import tempfile
        self._tmp = Path(tempfile.mkdtemp())
        import app.services.mesh_settings as ms_mod
        self._orig_path = ms_mod._SETTINGS_PATH
        ms_mod._SETTINGS_PATH = self._tmp / "mesh_settings.json"

    def teardown_method(self):
        import app.services.mesh_settings as ms_mod
        ms_mod._SETTINGS_PATH = self._orig_path
        import shutil
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _get_path(self):
        import app.services.mesh_settings as ms_mod
        return ms_mod._SETTINGS_PATH

    def test_load_defaults_when_no_file(self):
        s = load_mesh_settings()
        assert s["mode"] == "auto"
        assert s["confirm_before_mesh"] == "big_tasks"
        assert s["max_agents_per_task"] == 5

    def test_save_and_reload(self):
        s = load_mesh_settings()
        s["mode"] = "always"
        save_mesh_settings(s)
        s2 = load_mesh_settings()
        assert s2["mode"] == "always"

    def test_update_setting_single_key(self):
        s = update_setting("mode", "simple")
        assert s["mode"] == "simple"
        s2 = load_mesh_settings()
        assert s2["mode"] == "simple"

    def test_reset_to_defaults(self):
        update_setting("mode", "always")
        s = reset_to_defaults()
        assert s["mode"] == "auto"

    def test_all_defaults_present(self):
        s = load_mesh_settings()
        for key in _DEFAULTS:
            assert key in s

    def test_unknown_key_in_file_ignored(self):
        self._get_path().write_text(
            json.dumps({"mode": "simple", "unknown_key_xyz": "foo"}), encoding="utf-8"
        )
        s = load_mesh_settings()
        assert s["mode"] == "simple"


class TestShouldConfirmTask:
    def setup_method(self, tmp_path_factory=None):
        import tempfile
        self._tmp = Path(tempfile.mkdtemp())
        import app.services.mesh_settings as ms_mod
        self._orig_path = ms_mod._SETTINGS_PATH
        ms_mod._SETTINGS_PATH = self._tmp / "mesh_settings.json"

    def teardown_method(self):
        import app.services.mesh_settings as ms_mod
        ms_mod._SETTINGS_PATH = self._orig_path

    def test_always_confirm(self):
        update_setting("confirm_before_mesh", "always")
        assert should_confirm_task(["a"], 0.0) is True

    def test_never_confirm(self):
        update_setting("confirm_before_mesh", "never")
        assert should_confirm_task(["a", "b", "c"], 0.10) is False

    def test_big_tasks_high_cost(self):
        update_setting("confirm_before_mesh", "big_tasks")
        assert should_confirm_task(["a"], 0.10) is True  # cost > 0.05

    def test_big_tasks_many_agents(self):
        update_setting("confirm_before_mesh", "big_tasks")
        assert should_confirm_task(["a", "b", "c"], 0.01) is True  # 3 agents > 2

    def test_big_tasks_small_task_no_confirm(self):
        update_setting("confirm_before_mesh", "big_tasks")
        assert should_confirm_task(["a"], 0.01) is False  # small + 1 agent


# ---------------------------------------------------------------------------
# Bot module: Mesh UI functions
# ---------------------------------------------------------------------------

def _get_bot_module():
    spec = importlib.util.spec_from_file_location(
        "jarvis_tg_ctrl_mesh",
        ROOT / "tools" / "jarvis_smart_telegram_control.py",
    )
    mod = importlib.util.module_from_spec(spec)
    with patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "test", "TELEGRAM_ALLOWED_CHAT_ID": "123"}):
        spec.loader.exec_module(mod)
    return mod


class TestMeshControlPanelText:
    def setup_method(self):
        self.bot = _get_bot_module()

    def test_mesh_control_text_contains_mode(self):
        state = {"mesh_enabled": True, "mesh_history": []}
        text = self.bot._mesh_control_text(state)
        assert "AUTO" in text or "SIMPLE" in text or "ALWAYS" in text

    def test_mesh_control_text_shows_smart_router(self):
        state = {"mesh_enabled": True, "mesh_history": []}
        text = self.bot._mesh_control_text(state)
        assert "Smart Router" in text or "Router" in text

    def test_mesh_control_text_shows_history_count(self):
        state = {"mesh_enabled": True, "mesh_history": [{"q": "x"}, {"q": "y"}]}
        text = self.bot._mesh_control_text(state)
        assert "2" in text

    def test_mesh_control_keyboard_has_3_modes(self):
        state = {"mesh_enabled": True, "mesh_history": []}
        kb = self.bot._mesh_control_keyboard(state)
        first_row = kb[0]
        assert len(first_row) == 3

    def test_mesh_control_keyboard_mode_callbacks(self):
        state = {"mesh_enabled": True, "mesh_history": []}
        kb = self.bot._mesh_control_keyboard(state)
        callbacks = [btn["callback_data"] for btn in kb[0]]
        assert "mesh:mode:simple" in callbacks
        assert "mesh:mode:auto" in callbacks
        assert "mesh:mode:always" in callbacks

    def test_mesh_control_keyboard_has_settings_button(self):
        state = {"mesh_enabled": True, "mesh_history": []}
        kb = self.bot._mesh_control_keyboard(state)
        all_callbacks = [btn["callback_data"] for row in kb for btn in row]
        assert "mesh:settings" in all_callbacks

    def test_mesh_control_keyboard_has_history_button(self):
        state = {"mesh_enabled": True, "mesh_history": []}
        kb = self.bot._mesh_control_keyboard(state)
        all_callbacks = [btn["callback_data"] for row in kb for btn in row]
        assert "mesh:history" in all_callbacks


class TestMeshSettingsPanel:
    def setup_method(self):
        self.bot = _get_bot_module()

    def test_settings_text_contains_confirm_field(self):
        s = {"mode": "auto", "confirm_before_mesh": "big_tasks",
             "max_agents_per_task": 5, "cost_limit_per_task": 0.10, "cowork_delegation": "auto"}
        text = self.bot._mesh_settings_text(s)
        assert "Подтвердить" in text or "confirm" in text.lower()

    def test_settings_text_shows_cost(self):
        s = {"mode": "auto", "confirm_before_mesh": "big_tasks",
             "max_agents_per_task": 5, "cost_limit_per_task": 0.10, "cowork_delegation": "auto"}
        text = self.bot._mesh_settings_text(s)
        assert "$0.10" in text or "0.10" in text

    def test_settings_keyboard_has_5_rows(self):
        s = {"mode": "auto", "confirm_before_mesh": "big_tasks",
             "max_agents_per_task": 5, "cost_limit_per_task": 0.10, "cowork_delegation": "auto"}
        kb = self.bot._mesh_settings_keyboard(s)
        assert len(kb) == 5

    def test_settings_keyboard_has_save_button(self):
        s = {"mode": "auto", "confirm_before_mesh": "big_tasks",
             "max_agents_per_task": 5, "cost_limit_per_task": 0.10, "cowork_delegation": "auto"}
        kb = self.bot._mesh_settings_keyboard(s)
        all_callbacks = [btn["callback_data"] for row in kb for btn in row]
        assert "mesh:cfg:save" in all_callbacks

    def test_settings_keyboard_has_reset_button(self):
        s = {"mode": "auto", "confirm_before_mesh": "big_tasks",
             "max_agents_per_task": 5, "cost_limit_per_task": 0.10, "cowork_delegation": "auto"}
        kb = self.bot._mesh_settings_keyboard(s)
        all_callbacks = [btn["callback_data"] for row in kb for btn in row]
        assert "mesh:cfg:reset" in all_callbacks

    def test_settings_keyboard_checkmark_on_current(self):
        s = {"mode": "auto", "confirm_before_mesh": "always",
             "max_agents_per_task": 5, "cost_limit_per_task": 0.10, "cowork_delegation": "auto"}
        kb = self.bot._mesh_settings_keyboard(s)
        # First row: confirm buttons. "always" should have checkmark
        always_btn = next(b for b in kb[0] if "always" in b["callback_data"])
        assert "✓" in always_btn["text"]


class TestCallbackDispatch:
    def setup_method(self):
        self.bot = _get_bot_module()

    def _make_cq(self, data: str, chat_id: str = "123", msg_id: int = 42) -> dict:
        return {
            "id": "cq-test-id",
            "data": data,
            "from": {"id": int(chat_id)},
            "message": {
                "message_id": msg_id,
                "chat": {"id": int(chat_id)},
            },
        }

    def test_mode_switch_updates_settings(self):
        cq = self._make_cq("mesh:mode:simple")
        state = {"mesh_enabled": True, "mesh_history": []}
        with patch.object(self.bot, "answer_callback_query") as mock_ack, \
             patch.object(self.bot, "edit_message_with_keyboard") as mock_edit, \
             patch.object(self.bot, "_save_mesh_settings") as mock_save:
            self.bot.handle_callback_query(cq, state)
            mock_ack.assert_called()
            mock_save.assert_called()
            args = mock_save.call_args[0][0]
            assert args["mode"] == "simple"

    def test_settings_button_opens_settings_panel(self):
        cq = self._make_cq("mesh:settings")
        state = {"mesh_enabled": True, "mesh_history": []}
        with patch.object(self.bot, "answer_callback_query"), \
             patch.object(self.bot, "edit_message_with_keyboard") as mock_edit:
            self.bot.handle_callback_query(cq, state)
            mock_edit.assert_called()
            # Check that settings text was used
            text_arg = mock_edit.call_args[0][2]
            assert "SETTINGS" in text_arg.upper() or "Подтвердить" in text_arg

    def test_back_button_returns_to_main_panel(self):
        cq = self._make_cq("mesh:back")
        state = {"mesh_enabled": True, "mesh_history": []}
        with patch.object(self.bot, "answer_callback_query"), \
             patch.object(self.bot, "edit_message_with_keyboard") as mock_edit:
            self.bot.handle_callback_query(cq, state)
            mock_edit.assert_called()
            text_arg = mock_edit.call_args[0][2]
            assert "MESH" in text_arg.upper()

    def test_task_cancel_clears_pending(self):
        task_id = str(uuid.uuid4())
        cq = self._make_cq(f"task:cancel:{task_id}")
        state = {
            "mesh_enabled": True,
            "mesh_history": [],
            "pending_plans": {task_id: {"query": "test", "agents": ["a"]}},
        }
        with patch.object(self.bot, "answer_callback_query"), \
             patch.object(self.bot, "edit_message_with_keyboard"), \
             patch.object(self.bot, "save_state") as mock_save:
            self.bot.handle_callback_query(cq, state)
            assert task_id not in state.get("pending_plans", {})

    def test_history_button_shows_history(self):
        cq = self._make_cq("mesh:history")
        state = {
            "mesh_enabled": True,
            "mesh_history": [{"query": "test query", "agents": "agent1"}],
        }
        with patch.object(self.bot, "answer_callback_query"), \
             patch.object(self.bot, "send") as mock_send:
            self.bot.handle_callback_query(cq, state)
            mock_send.assert_called()
            text = mock_send.call_args[0][1]
            assert "test query" in text

    def test_unknown_callback_answered(self):
        cq = self._make_cq("unknown:action:xyz")
        state = {"mesh_enabled": True, "mesh_history": []}
        with patch.object(self.bot, "answer_callback_query") as mock_ack:
            self.bot.handle_callback_query(cq, state)
            mock_ack.assert_called()


class TestTaskConfirmation:
    def setup_method(self):
        self.bot = _get_bot_module()

    def test_show_confirmation_creates_pending_plan(self):
        state = {"pending_plans": {}, "mesh_enabled": True}
        task_id = str(uuid.uuid4())
        with patch.object(self.bot, "send_with_keyboard") as mock_sw, \
             patch.object(self.bot, "save_state"), \
             patch.object(self.bot, "_load_mesh_settings", return_value={
                 "confirm_before_mesh": "always",
                 "max_agents_per_task": 5, "cost_limit_per_task": 0.10, "cowork_delegation": "auto",
             }):
            self.bot._show_task_confirmation("123", None, task_id, {
                "query": "test task",
                "agents": ["internet_research", "smart_table"],
            }, state)
            mock_sw.assert_called()

    def test_maybe_show_confirmation_returns_false_for_never(self):
        import tempfile, shutil
        tmpdir = Path(tempfile.mkdtemp())
        import app.services.mesh_settings as ms_mod
        orig = ms_mod._SETTINGS_PATH
        ms_mod._SETTINGS_PATH = tmpdir / "ms.json"
        update_setting("confirm_before_mesh", "never")
        try:
            state = {"pending_plans": {}}
            result = self.bot.maybe_show_confirmation("123", "t1", "query", ["a"], state)
            assert result is False
        finally:
            ms_mod._SETTINGS_PATH = orig
            shutil.rmtree(tmpdir, ignore_errors=True)
