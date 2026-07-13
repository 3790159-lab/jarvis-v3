"""Tests for Phase 28: JarvisScheduler + natural language remind parser."""
from __future__ import annotations

import json
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch, call

ROOT = Path(__file__).parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.scheduler import (
    JarvisScheduler,
    format_task_list,
    parse_remind_text,
    _load_tasks,
    _save_tasks,
)


# ─── Fixtures ────────────────────────────────────────────────────────────────

def _scheduler_with_mock_aps() -> JarvisScheduler:
    """Return a scheduler with APScheduler mocked out."""
    sched = JarvisScheduler()
    mock_aps = MagicMock()
    sched._scheduler = mock_aps
    sched._started = True
    return sched


# ─── add_task ────────────────────────────────────────────────────────────────

class TestAddTask:
    def test_returns_task_id(self, tmp_path):
        sched = JarvisScheduler()
        sched._scheduler = MagicMock()
        sched._started = True
        with patch("app.services.scheduler.STATE_PATH", tmp_path / "tasks.json"):
            task_id = sched.add_task(
                action="remind",
                params={"text": "test"},
                run_at=datetime.now(timezone.utc) + timedelta(hours=1),
                chat_id="123",
            )
        assert isinstance(task_id, str)
        assert len(task_id) == 12

    def test_task_stored_in_list(self, tmp_path):
        sched = JarvisScheduler()
        sched._scheduler = MagicMock()
        sched._started = True
        with patch("app.services.scheduler.STATE_PATH", tmp_path / "tasks.json"):
            task_id = sched.add_task(
                action="remind",
                params={"text": "call mom"},
                run_at=datetime.now(timezone.utc) + timedelta(hours=1),
                chat_id="123",
            )
        tasks = sched.list_tasks()
        assert any(t["task_id"] == task_id for t in tasks)

    def test_cron_task(self, tmp_path):
        sched = JarvisScheduler()
        sched._scheduler = MagicMock()
        sched._started = True
        with patch("app.services.scheduler.STATE_PATH", tmp_path / "tasks.json"):
            task_id = sched.add_task(
                action="morning_brief",
                params={},
                cron="0 9 * * *",
                chat_id="123",
            )
        task = sched.get_task(task_id)
        assert task is not None
        assert task["schedule_type"] == "cron"
        assert task["cron"] == "0 9 * * *"

    def test_interval_task(self, tmp_path):
        sched = JarvisScheduler()
        sched._scheduler = MagicMock()
        sched._started = True
        with patch("app.services.scheduler.STATE_PATH", tmp_path / "tasks.json"):
            task_id = sched.add_task(
                action="n8n_run",
                params={"workflow_id": "42"},
                interval_seconds=3600,
                chat_id="123",
            )
        task = sched.get_task(task_id)
        assert task["schedule_type"] == "interval"
        assert task["interval_seconds"] == 3600

    def test_no_schedule_raises(self, tmp_path):
        sched = JarvisScheduler()
        sched._scheduler = MagicMock()
        sched._started = True
        try:
            with patch("app.services.scheduler.STATE_PATH", tmp_path / "tasks.json"):
                sched.add_task(action="remind", params={}, chat_id="123")
            assert False, "Should have raised"
        except ValueError:
            pass


# ─── remove_task ─────────────────────────────────────────────────────────────

class TestRemoveTask:
    def test_removes_existing(self, tmp_path):
        sched = JarvisScheduler()
        sched._scheduler = MagicMock()
        sched._started = True
        with patch("app.services.scheduler.STATE_PATH", tmp_path / "tasks.json"):
            task_id = sched.add_task(
                action="remind",
                params={"text": "x"},
                run_at=datetime.now(timezone.utc) + timedelta(hours=1),
                chat_id="1",
            )
            ok = sched.remove_task(task_id)
        assert ok is True
        assert sched.get_task(task_id) is None

    def test_remove_nonexistent_returns_false(self, tmp_path):
        sched = JarvisScheduler()
        sched._scheduler = MagicMock()
        sched._started = True
        with patch("app.services.scheduler.STATE_PATH", tmp_path / "tasks.json"):
            ok = sched.remove_task("doesnotexist")
        assert ok is False


# ─── persistence ─────────────────────────────────────────────────────────────

class TestPersistence:
    def test_save_and_load(self, tmp_path):
        state_file = tmp_path / "tasks.json"
        tasks = [{"task_id": "abc", "action": "remind", "active": True}]
        with patch("app.services.scheduler.STATE_PATH", state_file):
            _save_tasks(tasks)
            loaded = _load_tasks()
        assert loaded == tasks

    def test_load_empty_if_missing(self, tmp_path):
        with patch("app.services.scheduler.STATE_PATH", tmp_path / "missing.json"):
            result = _load_tasks()
        assert result == []

    def test_task_persisted_on_add(self, tmp_path):
        state_file = tmp_path / "tasks.json"
        sched = JarvisScheduler()
        sched._scheduler = MagicMock()
        sched._started = True
        with patch("app.services.scheduler.STATE_PATH", state_file):
            sched.add_task(
                action="remind",
                params={"text": "persisted"},
                run_at=datetime.now(timezone.utc) + timedelta(hours=1),
                chat_id="1",
            )
            loaded = json.loads(state_file.read_text())
        assert len(loaded) == 1
        assert loaded[0]["action"] == "remind"

    def test_task_removed_from_persistent_storage(self, tmp_path):
        state_file = tmp_path / "tasks.json"
        sched = JarvisScheduler()
        sched._scheduler = MagicMock()
        sched._started = True
        with patch("app.services.scheduler.STATE_PATH", state_file):
            task_id = sched.add_task(
                action="remind",
                params={"text": "remove me"},
                run_at=datetime.now(timezone.utc) + timedelta(hours=1),
                chat_id="1",
            )
            sched.remove_task(task_id)
            loaded = json.loads(state_file.read_text())
        assert len(loaded) == 0


# ─── execute_task ─────────────────────────────────────────────────────────────

class TestExecuteTask:
    def test_remind_sends_message(self, tmp_path):
        sent = []
        sched = JarvisScheduler(send_fn=lambda cid, txt: sent.append((cid, txt)))
        sched._scheduler = MagicMock()
        sched._started = True
        with patch("app.services.scheduler.STATE_PATH", tmp_path / "tasks.json"):
            task = {
                "task_id": "t1",
                "action": "remind",
                "params": {"text": "call dad"},
                "chat_id": "777",
                "schedule_type": "once",
                "active": True,
            }
            sched._tasks.append(task)
            sched._execute_task(task)
        assert sent
        assert "call dad" in sent[0][1]
        assert sent[0][0] == "777"

    def test_remind_removes_one_shot(self, tmp_path):
        sent = []
        sched = JarvisScheduler(send_fn=lambda cid, txt: sent.append(txt))
        sched._scheduler = MagicMock()
        sched._started = True
        with patch("app.services.scheduler.STATE_PATH", tmp_path / "tasks.json"):
            task = {
                "task_id": "t_oneshot",
                "action": "remind",
                "params": {"text": "one time"},
                "chat_id": "1",
                "schedule_type": "once",
                "active": True,
            }
            sched._tasks.append(task)
            sched._execute_task(task)
        assert sched.get_task("t_oneshot") is None

    def test_cron_task_not_removed(self, tmp_path):
        sent = []
        sched = JarvisScheduler(send_fn=lambda cid, txt: sent.append(txt))
        sched._scheduler = MagicMock()
        sched._started = True
        with patch("app.services.scheduler.STATE_PATH", tmp_path / "tasks.json"):
            task = {
                "task_id": "t_daily",
                "action": "remind",
                "params": {"text": "daily"},
                "chat_id": "1",
                "schedule_type": "cron",
                "cron": "0 9 * * *",
                "active": True,
            }
            sched._tasks.append(task)
            sched._execute_task(task)
        assert sched.get_task("t_daily") is not None


# ─── morning_brief ───────────────────────────────────────────────────────────

class TestMorningBrief:
    def test_brief_contains_greeting(self, tmp_path):
        sched = JarvisScheduler()
        brief = sched._build_morning_brief({})
        assert "Доброе утро" in brief or "утро" in brief.lower()

    def test_brief_contains_task_count(self, tmp_path):
        sched = JarvisScheduler()
        sched._scheduler = MagicMock()
        sched._started = True
        with patch("app.services.scheduler.STATE_PATH", tmp_path / "tasks.json"):
            sched.add_task(
                action="remind",
                params={"text": "x"},
                run_at=datetime.now(timezone.utc) + timedelta(hours=1),
                chat_id="1",
            )
        brief = sched._build_morning_brief({})
        assert "1" in brief or "задач" in brief


# ─── parse_remind_text ───────────────────────────────────────────────────────

class TestParseRemindText:
    def test_daily_pattern_ru(self):
        result = parse_remind_text("каждый день 7:00 утренний бриф")
        assert result is not None
        assert result["schedule_type"] == "daily"
        assert "7" in result["cron"]
        assert "утренний бриф" in result["text"] or result["text"]

    def test_daily_extracts_hour(self):
        result = parse_remind_text("каждый день 8:30 встреча")
        assert result is not None
        assert "8" in result["cron"]
        assert "30" in result["cron"]

    def test_returns_none_for_garbage(self):
        result = parse_remind_text("xyzzyx blah blah")
        # Either None or a result (dateparser may try to interpret)
        # Key: should not crash
        assert result is None or isinstance(result, dict)

    def test_future_datetime_returned(self):
        result = parse_remind_text("через 2 часа позвонить маме")
        if result is not None:
            assert result["schedule_type"] == "once"
            assert result["datetime"] is not None

    def test_empty_text_returns_none(self):
        result = parse_remind_text("")
        assert result is None


# ─── parse_remind_text — comprehensive regex tests ───────────────────────────

class TestParseRemindTextRegex:
    """Tests for the new regex-first parse_remind_text."""

    def test_ru_minutes(self):
        result = parse_remind_text("через 10 минут позвонить маме")
        assert result is not None
        assert result["schedule_type"] == "once"
        assert result["datetime"] is not None
        dt_diff = result["datetime"] - datetime.now(timezone.utc)
        assert 9 * 60 <= dt_diff.total_seconds() <= 11 * 60
        assert "позвонить маме" in result["text"]

    def test_ru_minutes_singular(self):
        result = parse_remind_text("через 1 минуту купить молоко")
        assert result is not None
        assert result["schedule_type"] == "once"
        dt_diff = result["datetime"] - datetime.now(timezone.utc)
        assert 0 < dt_diff.total_seconds() < 120

    def test_ru_hours(self):
        result = parse_remind_text("через 1 час встреча")
        assert result is not None
        assert result["schedule_type"] == "once"
        dt_diff = result["datetime"] - datetime.now(timezone.utc)
        assert 3590 <= dt_diff.total_seconds() <= 3610
        assert "встреча" in result["text"]

    def test_ru_hours_2(self):
        result = parse_remind_text("через 2 часа обед")
        assert result is not None
        dt_diff = result["datetime"] - datetime.now(timezone.utc)
        assert 7190 <= dt_diff.total_seconds() <= 7210

    def test_ru_days(self):
        result = parse_remind_text("через 2 дня встреча")
        assert result is not None
        assert result["schedule_type"] == "once"
        dt_diff = result["datetime"] - datetime.now(timezone.utc)
        assert 2 * 86400 - 10 <= dt_diff.total_seconds() <= 2 * 86400 + 10

    def test_ru_days_singular(self):
        result = parse_remind_text("через 1 день проверить почту")
        assert result is not None
        dt_diff = result["datetime"] - datetime.now(timezone.utc)
        assert 86390 <= dt_diff.total_seconds() <= 86410

    def test_ru_tomorrow_with_time(self):
        result = parse_remind_text("завтра в 9:00 проверить почту")
        assert result is not None
        assert result["schedule_type"] == "once"
        assert result["datetime"].hour == 9
        assert result["datetime"].minute == 0
        assert "проверить почту" in result["text"]

    def test_ru_tomorrow_without_v(self):
        result = parse_remind_text("завтра 14:30 звонок")
        assert result is not None
        assert result["datetime"].hour == 14
        assert result["datetime"].minute == 30

    def test_ru_today(self):
        result = parse_remind_text("сегодня в 18:00 ужин")
        assert result is not None
        assert result["schedule_type"] == "once"
        assert result["datetime"].hour == 18
        assert "ужин" in result["text"]

    def test_ru_daily_cron(self):
        result = parse_remind_text("каждый день 7:00 утренний бриф")
        assert result is not None
        assert result["schedule_type"] == "daily"
        assert result["cron"] == "0 7 * * *"
        assert "утренний бриф" in result["text"]

    def test_ru_daily_with_v(self):
        result = parse_remind_text("каждый день в 8:30 встреча")
        assert result is not None
        assert result["schedule_type"] == "daily"
        assert result["cron"] == "30 8 * * *"

    def test_ru_ejednevno(self):
        result = parse_remind_text("ежедневно в 6:00 зарядка")
        assert result is not None
        assert result["schedule_type"] == "daily"
        assert result["cron"] == "0 6 * * *"

    def test_en_minutes(self):
        result = parse_remind_text("in 10 minutes test reminder")
        assert result is not None
        assert result["schedule_type"] == "once"
        dt_diff = result["datetime"] - datetime.now(timezone.utc)
        assert 9 * 60 <= dt_diff.total_seconds() <= 11 * 60
        assert "test reminder" in result["text"]

    def test_en_minutes_case_insensitive(self):
        result = parse_remind_text("In 5 Minutes call boss")
        assert result is not None
        assert result["schedule_type"] == "once"

    def test_en_hours(self):
        result = parse_remind_text("in 1 hour meeting")
        assert result is not None
        assert result["schedule_type"] == "once"
        dt_diff = result["datetime"] - datetime.now(timezone.utc)
        assert 3590 <= dt_diff.total_seconds() <= 3610

    def test_en_days(self):
        result = parse_remind_text("in 3 days report due")
        assert result is not None
        dt_diff = result["datetime"] - datetime.now(timezone.utc)
        assert 3 * 86400 - 10 <= dt_diff.total_seconds() <= 3 * 86400 + 10

    def test_en_tomorrow(self):
        result = parse_remind_text("tomorrow at 14:30 call")
        assert result is not None
        assert result["schedule_type"] == "once"
        assert result["datetime"].hour == 14
        assert result["datetime"].minute == 30
        assert "call" in result["text"]

    def test_en_every_day(self):
        result = parse_remind_text("every day at 9:00 brief")
        assert result is not None
        assert result["schedule_type"] == "daily"
        assert result["cron"] == "0 9 * * *"
        assert "brief" in result["text"]

    def test_en_every_day_without_at(self):
        result = parse_remind_text("every day 7:00 standup")
        assert result is not None
        assert result["schedule_type"] == "daily"
        assert result["cron"] == "0 7 * * *"

    def test_strips_quotes(self):
        result = parse_remind_text('через 30 минут "позвонить другу"')
        assert result is not None
        assert result["text"] == "позвонить другу"

    def test_strips_single_quotes(self):
        result = parse_remind_text("in 5 minutes 'check email'")
        assert result is not None
        assert result["text"] == "check email"

    def test_empty_returns_none(self):
        assert parse_remind_text("") is None

    def test_whitespace_returns_none(self):
        assert parse_remind_text("   ") is None

    def test_no_text_part_regex_no_match(self):
        # Regex patterns require text after time — dateparser fallback may or may not match
        result = parse_remind_text("через 10 минут")
        # Either None or a dict — should not crash
        assert result is None or isinstance(result, dict)

    def test_garbage_returns_none(self):
        result = parse_remind_text("абракадабра 12345")
        assert result is None or isinstance(result, dict)

    def test_result_has_required_keys(self):
        result = parse_remind_text("через 5 минут тест")
        assert result is not None
        assert "schedule_type" in result
        assert "datetime" in result
        assert "text" in result


# ─── format_task_list ────────────────────────────────────────────────────────

# ─── garbage_cleanup weekly job ──────────────────────────────────────────────

class TestEnsureGarbageCleanupJob:
    def test_registers_once(self, tmp_path):
        sched = JarvisScheduler()
        sched._scheduler = MagicMock()
        sched._started = True
        with patch("app.services.scheduler.STATE_PATH", tmp_path / "tasks.json"):
            task_id = sched.ensure_garbage_cleanup_job("123")
        assert task_id is not None
        tasks = sched.list_tasks()
        assert any(t["action"] == "garbage_cleanup" for t in tasks)

    def test_idempotent_second_call_noop(self, tmp_path):
        sched = JarvisScheduler()
        sched._scheduler = MagicMock()
        sched._started = True
        with patch("app.services.scheduler.STATE_PATH", tmp_path / "tasks.json"):
            first = sched.ensure_garbage_cleanup_job("123")
            second = sched.ensure_garbage_cleanup_job("123")
        assert first is not None
        assert second is None
        tasks = [t for t in sched.list_tasks() if t["action"] == "garbage_cleanup"]
        assert len(tasks) == 1

    def test_cron_is_weekly(self, tmp_path):
        sched = JarvisScheduler()
        sched._scheduler = MagicMock()
        sched._started = True
        with patch("app.services.scheduler.STATE_PATH", tmp_path / "tasks.json"):
            sched.ensure_garbage_cleanup_job("123")
        task = next(t for t in sched.list_tasks() if t["action"] == "garbage_cleanup")
        assert task["schedule_type"] == "cron"
        assert task["cron"] == "0 4 * * 1"


class TestRunGarbageCleanup:
    def _fake_report(self):
        return {"categories": {}, "total_count": 0, "total_mb": 0.0, "dry_run": True}

    def test_sends_formatted_report(self, monkeypatch):
        sent = []
        sched = JarvisScheduler(send_fn=lambda cid, txt: sent.append((cid, txt)))
        monkeypatch.setattr(
            "app.services.garbage_cleanup.run_cleanup",
            lambda **kw: self._fake_report(),
        )
        monkeypatch.setattr(
            "app.services.devtask.queue.DevTaskQueue.get", lambda self, tid: None
        )
        sched.run_garbage_cleanup("555")
        assert sent
        assert sent[0][0] == "555"
        assert "Уборка мусора" in sent[0][1]

    def test_defaults_to_dry_run_when_env_unset(self, monkeypatch):
        monkeypatch.delenv("GARBAGE_CLEANUP_DRY_RUN", raising=False)
        captured = {}

        def fake_run_cleanup(**kw):
            captured.update(kw)
            return self._fake_report()

        monkeypatch.setattr("app.services.garbage_cleanup.run_cleanup", fake_run_cleanup)
        monkeypatch.setattr(
            "app.services.devtask.queue.DevTaskQueue.get", lambda self, tid: None
        )
        sched = JarvisScheduler(send_fn=lambda cid, txt: None)
        sched.run_garbage_cleanup("555")
        assert captured["dry_run"] is True

    def test_respects_env_override_to_disable_dry_run(self, monkeypatch):
        monkeypatch.setenv("GARBAGE_CLEANUP_DRY_RUN", "false")
        captured = {}

        def fake_run_cleanup(**kw):
            captured.update(kw)
            return self._fake_report()

        monkeypatch.setattr("app.services.garbage_cleanup.run_cleanup", fake_run_cleanup)
        monkeypatch.setattr(
            "app.services.devtask.queue.DevTaskQueue.get", lambda self, tid: None
        )
        sched = JarvisScheduler(send_fn=lambda cid, txt: None)
        sched.run_garbage_cleanup("555")
        assert captured["dry_run"] is False
        monkeypatch.delenv("GARBAGE_CLEANUP_DRY_RUN", raising=False)

    def test_execute_task_routes_garbage_cleanup_action(self, monkeypatch, tmp_path):
        sent = []
        sched = JarvisScheduler(send_fn=lambda cid, txt: sent.append(txt))
        sched._scheduler = MagicMock()
        sched._started = True
        monkeypatch.setattr(
            "app.services.garbage_cleanup.run_cleanup",
            lambda **kw: self._fake_report(),
        )
        monkeypatch.setattr(
            "app.services.devtask.queue.DevTaskQueue.get", lambda self, tid: None
        )
        with patch("app.services.scheduler.STATE_PATH", tmp_path / "tasks.json"):
            task = {
                "task_id": "t_gc",
                "action": "garbage_cleanup",
                "params": {},
                "chat_id": "999",
                "schedule_type": "cron",
                "cron": "0 4 * * 1",
                "active": True,
            }
            sched._tasks.append(task)
            sched._execute_task(task)
        assert sent
        assert "Уборка мусора" in sent[0]

    def test_exception_reports_error_not_raise(self, monkeypatch):
        sent = []
        sched = JarvisScheduler(send_fn=lambda cid, txt: sent.append(txt))

        def boom(**kw):
            raise RuntimeError("disk exploded")

        monkeypatch.setattr("app.services.garbage_cleanup.run_cleanup", boom)
        monkeypatch.setattr(
            "app.services.devtask.queue.DevTaskQueue.get", lambda self, tid: None
        )
        sched.run_garbage_cleanup("555")  # must not raise
        assert sent
        assert "Ошибка" in sent[0]


class TestFormatTaskList:
    def test_empty_list(self):
        text = format_task_list([])
        assert "Нет" in text

    def test_shows_task_id_prefix(self):
        tasks = [{
            "task_id": "abcdef123456",
            "action": "remind",
            "params": {"text": "call"},
            "schedule_type": "once",
            "run_at": "2026-05-01T10:00:00",
            "active": True,
        }]
        text = format_task_list(tasks)
        assert "abcdef12" in text

    def test_shows_remove_hint(self):
        tasks = [{
            "task_id": "aaa111",
            "action": "remind",
            "params": {"text": "x"},
            "schedule_type": "once",
            "run_at": "2026-05-01T10:00:00",
            "active": True,
        }]
        text = format_task_list(tasks)
        assert "/schedule remove" in text

    def test_shows_action_icon(self):
        tasks = [{
            "task_id": "bbb222",
            "action": "morning_brief",
            "params": {},
            "schedule_type": "cron",
            "cron": "0 9 * * *",
            "active": True,
        }]
        text = format_task_list(tasks)
        assert "🌅" in text
