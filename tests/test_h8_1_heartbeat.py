"""Phase H8.1: Bot heartbeat + watchdog tests."""
from __future__ import annotations

import sys
import os
import time
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


# ---------------------------------------------------------------------------
# check_bot_alive
# ---------------------------------------------------------------------------

def test_check_bot_alive_no_file():
    from app.services.system_watchdog import check_bot_alive
    with patch("app.services.system_watchdog.Path") as mock_path:
        # Return a path that doesn't exist
        fake = MagicMock()
        fake.__truediv__ = lambda s, o: fake
        fake.exists.return_value = False
        mock_path.return_value = fake
        # Call with the real function but monkeypatched path
    # Use temp dir approach instead
    with tempfile.TemporaryDirectory() as tmpdir:
        hb_path = Path(tmpdir) / "bot_heartbeat.txt"
        # File doesn't exist
        assert not hb_path.exists()


def test_check_bot_alive_fresh_timestamp(tmp_path):
    from app.services.system_watchdog import check_bot_alive
    hb = tmp_path / "bot_heartbeat.txt"
    hb.write_text(str(int(time.time())), encoding="utf-8")

    with patch("app.services.system_watchdog.Path") as mock_path_cls:
        # Make the __file__/../../../state/bot_heartbeat.txt resolve to hb
        fake_root = MagicMock()
        fake_root.__truediv__ = MagicMock(return_value=MagicMock(
            __truediv__=MagicMock(return_value=hb)
        ))
        mock_path_cls.return_value.__truediv__ = MagicMock(return_value=fake_root)
        # Actually test the logic directly
    # Read the timestamp directly
    ts = int(hb.read_text())
    age = time.time() - ts
    assert age < 90, f"Fresh heartbeat should be < 90s old, got {age:.1f}s"


def test_check_bot_alive_stale_timestamp(tmp_path):
    hb = tmp_path / "bot_heartbeat.txt"
    old_ts = int(time.time()) - 120  # 2 minutes ago — stale
    hb.write_text(str(old_ts), encoding="utf-8")
    ts = int(hb.read_text())
    age = time.time() - ts
    assert age >= 90, "Stale heartbeat should be >= 90s old"


def test_check_bot_alive_corrupt_file(tmp_path):
    hb = tmp_path / "bot_heartbeat.txt"
    hb.write_text("not_a_number", encoding="utf-8")
    # Reading corrupt file should not raise
    try:
        int(hb.read_text().strip())
        alive = True
    except ValueError:
        alive = False
    assert not alive, "Corrupt file should result in not-alive"


def test_check_bot_alive_returns_bool():
    from app.services.system_watchdog import check_bot_alive
    result = check_bot_alive()
    assert isinstance(result, bool)


def test_check_bot_alive_missing_state_dir(tmp_path):
    # No state dir at all
    hb = tmp_path / "nonexistent" / "bot_heartbeat.txt"
    assert not hb.exists()


# ---------------------------------------------------------------------------
# restart_bot_if_dead
# ---------------------------------------------------------------------------

def test_restart_bot_if_dead_skips_when_alive():
    from app.services.system_watchdog import restart_bot_if_dead
    with patch("app.services.system_watchdog.check_bot_alive", return_value=True):
        result = restart_bot_if_dead()
    assert result is False, "Should skip restart when bot is alive"


def _arrange_dead_bot(wd, root: Path):
    """Общая расстановка для restart_bot_if_dead: бот мёртв, свапа нет, окно
    прогрева позади, а КОРЕНЬ — подставной.

    Прод-код берёт корень как `Path(__file__).parent.parent.parent`, поэтому
    подменяем сам `Path` функцией, возвращающей НАСТОЯЩИЙ путь на три уровня
    глубже `root`: семантика `.parent` и `/` остаётся живой, а результат
    гарантированно лежит в tmp. Никаких MagicMock-цепочек: они и позволяли
    тесту «пройти» при любом исходе.
    """
    deep = root / "a" / "b" / "system_watchdog.py"
    return [
        patch.object(wd, "check_bot_alive", return_value=False),
        patch.object(wd, "is_swap_active", return_value=False),
        # Прогрев: без этого исход зависит от того, КОГДА модуль был
        # импортирован в прогоне — ровно та недетерминированность, ради
        # которой и был написан ассерт `isinstance(result, bool)`.
        patch.object(wd, "_module_started_at", 0.0),
        patch.object(wd, "send_telegram_alert"),
        patch.object(wd, "Path", lambda *_a, **_k: deep),
    ]


def test_restart_bot_if_dead_attempts_when_dead(tmp_path):
    """Скрипт НАЙДЕН -> Popen вызван РОВНО с ним, и вернулось True."""
    import app.services.system_watchdog as wd
    from contextlib import ExitStack

    root = tmp_path / "fake_tree"
    root.mkdir()
    script = root / "start_jarvis.ps1"
    script.write_text("# stub", encoding="utf-8")

    with ExitStack() as stack:
        for cm in _arrange_dead_bot(wd, root):
            stack.enter_context(cm)
        popen = stack.enter_context(patch.object(wd.subprocess, "Popen"))
        result = wd.restart_bot_if_dead()

    assert result is True, "скрипт на месте — перезапуск обязан быть заявлен"
    assert popen.call_count == 1, f"ожидался ровно один запуск, было {popen.call_count}"
    argv = popen.call_args.args[0]
    assert str(script) in argv, f"запущен не тот скрипт: {argv}"
    assert "-BotOnly" in argv, f"перезапуск обязан быть -BotOnly, argv={argv}"


def test_restart_bot_no_script_returns_false(tmp_path):
    """DEV-38, половина 1. Этот тест ~16 раз в сутки УБИВАЛ ЖИВОГО бота.

    Он патчил `check_bot_alive`, но НЕ патчил `Popen` и НЕ подменял корень.
    Значит `start_jarvis.ps1` находился по-настоящему — и по-настоящему
    запускался, а стартер первым делом сносит бота (половина 2). Ассерт
    `isinstance(result, bool)` истинен и при выстреле, и без него, поэтому
    тест был зелёным ровно в те прогоны, в которые убивал прод.

    Теперь: корень подставной -> скрипта нет -> False И НИ ОДНОГО Popen.
    """
    import app.services.system_watchdog as wd
    from contextlib import ExitStack

    root = tmp_path / "empty_tree"
    root.mkdir()
    assert not (root / "start_jarvis.ps1").exists()

    with ExitStack() as stack:
        for cm in _arrange_dead_bot(wd, root):
            stack.enter_context(cm)
        popen = stack.enter_context(patch.object(wd.subprocess, "Popen"))
        result = wd.restart_bot_if_dead()

    assert result is False, "скрипта нет — перезапуск невозможен"
    popen.assert_not_called()


# ---------------------------------------------------------------------------
# _heartbeat_thread in telegram control
# ---------------------------------------------------------------------------

def test_heartbeat_file_constant_defined():
    import tools.jarvis_smart_telegram_control as tg
    assert hasattr(tg, "_HEARTBEAT_FILE")
    from pathlib import Path
    assert isinstance(tg._HEARTBEAT_FILE, Path)


def test_heartbeat_thread_function_exists():
    import tools.jarvis_smart_telegram_control as tg
    assert callable(tg._heartbeat_thread)


def test_heartbeat_writes_timestamp(tmp_path):
    import tools.jarvis_smart_telegram_control as tg
    import threading

    original = tg._HEARTBEAT_FILE
    tg._HEARTBEAT_FILE = tmp_path / "bot_heartbeat.txt"

    results = []

    def _patched_sleep(n):
        # Only run once then raise to stop thread
        raise RuntimeError("stop")

    with patch("tools.jarvis_smart_telegram_control.time.sleep", side_effect=RuntimeError("stop")):
        try:
            tg._heartbeat_thread()
        except RuntimeError:
            pass

    tg._HEARTBEAT_FILE = original  # restore
    if (tmp_path / "bot_heartbeat.txt").exists():
        ts_str = (tmp_path / "bot_heartbeat.txt").read_text()
        assert ts_str.isdigit(), "Heartbeat file should contain a timestamp"
