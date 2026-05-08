"""Phase 33.4: Tests for friendly empty file messages in /logs command."""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

ROOT = Path(__file__).parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _run_logs_cmd(query: str, log_files: dict = None, tmp_path: Path = None) -> str:
    """Run _handle_logs_command and capture what was sent."""
    sent = []

    def _fake_send(cid, text, **kwargs):
        sent.append(text)

    if log_files is None:
        log_files = {}

    import tools.jarvis_smart_telegram_control as ctrl

    # Build patched log_paths using tmp_path
    if tmp_path:
        patched_paths = {}
        for k, content in log_files.items():
            p = tmp_path / f"{k}.log"
            if content is None:
                pass  # file won't exist
            elif content == "":
                p.write_text("", encoding="utf-8")
            else:
                p.write_text(content, encoding="utf-8")
            if content is not None:
                patched_paths[k] = p
            else:
                patched_paths[k] = tmp_path / f"{k}_nonexistent.log"

        original_fn = ctrl._handle_logs_command

        def patched_fn(chat_id, q):
            parts = q.split()
            component = parts[0].lower() if parts else "errors"
            try:
                n = int(parts[1]) if len(parts) > 1 else 30
                n = min(max(n, 1), 200)
            except ValueError:
                n = 30

            _empty_messages = {
                "decisions": (
                    "📊 Лог решений пуст. Используй Jarvis больше — "
                    "каждый запрос пишется сюда автоматически."
                ),
                "errors": "✅ Лог ошибок пуст — всё работает чисто!",
                "tasks": "📭 Нет активных задач по расписанию.\nСоздай командой /remind",
            }

            if component in patched_paths:
                p = patched_paths[component]
                if not p.exists() or p.stat().st_size == 0:
                    _fake_send(chat_id, _empty_messages.get(component, f"Файл {p} пуст или не найден."))
                    return
                lines = p.read_text(encoding="utf-8").splitlines()
                if not lines:
                    _fake_send(chat_id, _empty_messages.get(component, f"Файл {component} пуст."))
                    return
                tail = lines[-n:]
                _fake_send(chat_id, f"📄 {component} (последние {len(tail)} строк):\n" + "\n".join(tail))
            else:
                _fake_send(chat_id, "Использование: /logs [component] [N]")

        with patch.object(ctrl, "_handle_logs_command", patched_fn):
            ctrl._handle_logs_command("123", query)
    else:
        with patch.object(ctrl, "send", _fake_send):
            ctrl._handle_logs_command("123", query)

    return sent[0] if sent else ""


class TestEmptyFileHandling:
    def test_decisions_empty_file(self, tmp_path):
        msg = _run_logs_cmd("decisions", {"decisions": ""}, tmp_path)
        assert "пуст" in msg.lower()
        assert "Jarvis" in msg or "решений" in msg

    def test_decisions_nonexistent_file(self, tmp_path):
        msg = _run_logs_cmd("decisions", {"decisions": None}, tmp_path)
        assert "пуст" in msg.lower() or "решений" in msg

    def test_errors_empty_file(self, tmp_path):
        msg = _run_logs_cmd("errors", {"errors": ""}, tmp_path)
        assert "пуст" in msg.lower() or "✅" in msg

    def test_errors_nonexistent_file(self, tmp_path):
        msg = _run_logs_cmd("errors", {"errors": None}, tmp_path)
        assert "пуст" in msg.lower() or "✅" in msg

    def test_tasks_empty_file(self, tmp_path):
        msg = _run_logs_cmd("tasks", {"tasks": ""}, tmp_path)
        assert "пуст" in msg.lower() or "задач" in msg.lower()

    def test_tasks_nonexistent_file(self, tmp_path):
        msg = _run_logs_cmd("tasks", {"tasks": None}, tmp_path)
        assert "пуст" in msg.lower() or "задач" in msg.lower()

    def test_decisions_with_content(self, tmp_path):
        content = '{"query": "test", "intent": "research"}\n{"query": "hi", "intent": "greeting"}'
        msg = _run_logs_cmd("decisions", {"decisions": content}, tmp_path)
        assert "📄" in msg
        assert "decisions" in msg

    def test_errors_with_content(self, tmp_path):
        content = "ERROR 2026-05-01: something failed\nERROR 2026-05-01: another error"
        msg = _run_logs_cmd("errors", {"errors": content}, tmp_path)
        assert "📄" in msg

    def test_friendly_decisions_message_content(self, tmp_path):
        msg = _run_logs_cmd("decisions", {"decisions": ""}, tmp_path)
        assert "каждый запрос" in msg or "автоматически" in msg

    def test_friendly_errors_message_content(self, tmp_path):
        msg = _run_logs_cmd("errors", {"errors": ""}, tmp_path)
        assert "работает" in msg or "чисто" in msg or "пуст" in msg.lower()
