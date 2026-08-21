"""Shared pytest fixtures / test-environment hardening.

Several application modules (e.g. ``app.services.llm_client``,
``app.core.env_bootstrap``) call ``load_dotenv()`` at import time. During a
combined test run the first such import populates ``os.environ`` from the
project ``.env`` — including the *production* flag ``JARVIS_ROUTER_ENABLED=1``.
That value then leaks into router-naive tests (e.g. the whitelist dispatch
tests), which assume the bot's default behaviour where the unified LLM router
is **off** (it is opt-in: ``os.getenv("JARVIS_ROUTER_ENABLED", "0")``). The
result is order-dependent failures: plain text gets routed instead of reaching
``handle``.

The autouse fixture below pins the router to its documented default (off) for
every test, making the suite deterministic regardless of import order. Tests
that genuinely exercise the router still opt in explicitly via
``monkeypatch.setenv("JARVIS_ROUTER_ENABLED", "1")``; because that call runs
after this autouse fixture, it correctly overrides the default for those tests.
"""

import subprocess

import pytest


@pytest.fixture(autouse=True)
def _router_disabled_by_default(monkeypatch):
    """Default the unified LLM router to OFF so .env's prod flag can't leak in.

    Router-exercising tests override this with their own
    ``monkeypatch.setenv("JARVIS_ROUTER_ENABLED", "1")``, which wins.
    """
    monkeypatch.setenv("JARVIS_ROUTER_ENABLED", "0")


@pytest.fixture(autouse=True)
def _isolate_users_file(monkeypatch, tmp_path):
    """Point the multi-user store at a per-test tmp file so no test can write
    to the live ``state/users.json``.

    ``users_store._state_file()`` falls back to the *relative* default
    ``state/users.json`` when ``JARVIS_USERS_FILE`` is unset. Any test that
    drives ``process_update`` from a non-whitelisted user reaches
    ``add_pending`` and writes through to that prod file (proven: a stray
    ``pending`` entry id=999 accumulated request_count=11 from test runs).
    Cost state (``JARVIS_COST_FILE``) was already isolated per-file; this gives
    the users store the same guarantee globally. Tests that set their own
    ``JARVIS_USERS_FILE`` run after this fixture, so their path still wins.
    """
    monkeypatch.setenv("JARVIS_USERS_FILE", str(tmp_path / "users.json"))


@pytest.fixture(autouse=True)
def _isolate_ig_accounts_file(monkeypatch, tmp_path):
    """Point the IG multi-account store at a per-test tmp file so no test can
    read/write the live ``state/ig_accounts.json`` (same rationale as
    ``_isolate_users_file`` above — ``ig_accounts._state_file()`` falls back to
    the *relative* default when ``IG_ACCOUNTS_FILE`` is unset, and credential
    resolution can auto-migrate/write on first touch)."""
    monkeypatch.setenv("IG_ACCOUNTS_FILE", str(tmp_path / "ig_accounts.json"))


@pytest.fixture(autouse=True)
def _isolate_ig_schedule_file(monkeypatch, tmp_path):
    """Point the IG schedule queue at a per-test tmp file so no test can
    read/write the live ``state/ig_scheduled_posts.json`` (same rationale as
    ``_isolate_ig_accounts_file`` above — ``ig_schedule._state_file()`` falls
    back to the *relative* default when ``IG_SCHEDULE_FILE`` is unset)."""
    monkeypatch.setenv("IG_SCHEDULE_FILE", str(tmp_path / "ig_scheduled_posts.json"))


@pytest.fixture(autouse=True)
def _isolate_ig_hashtag_history_file(monkeypatch, tmp_path):
    """Point the IG hashtag-rotation history at a per-test tmp file so no test
    can read/write the live ``state/ig_hashtag_history.json`` (same rationale
    as ``_isolate_ig_schedule_file`` above — ``ig_caption._hashtag_history_file()``
    falls back to the *relative* default when ``IG_HASHTAG_HISTORY_FILE`` is
    unset)."""
    monkeypatch.setenv("IG_HASHTAG_HISTORY_FILE", str(tmp_path / "ig_hashtag_history.json"))


@pytest.fixture(autouse=True)
def _blank_fal_key(monkeypatch):
    """Blank ``FAL_KEY`` for every test so no code path can reach the real fal
    API (FLUX.2 persona gen, ``FalImageClient``) and spend money.

    ``.env`` is loaded at import time (see module docstring) → ``FAL_KEY`` would
    otherwise leak into ``os.environ`` and a test that drives the real vera
    brand (``engine: flux2``) through ``_ig_gen_dispatch`` without mocking the
    N-best gen would fire real ~$0.02×N generations. ``FalImageClient()`` raises
    ``RuntimeError`` on empty key → any such unmocked path fails safely.
    Unit tests that pass an explicit ``api_key=...`` are unaffected (they don't
    read env). Mirrors the money-safety layers above.
    """
    monkeypatch.delenv("FAL_KEY", raising=False)


@pytest.fixture(autouse=True)
def _silence_telegram_sends(monkeypatch):
    """Suppress every outbound Telegram send during tests so no phantom
    message (``petya (555) New user``, ``RunPod guardian pod_old`` …) leaks
    into the live admin chat.

    Mirrors :func:`_isolate_users_file`: pin a safe default via env. All
    network send-paths consult ``app.core.notify_isolation.telegram_send_blocked``,
    which honours this flag. Tests that genuinely drive the (mocked) transport
    opt back in with ``JARVIS_ALLOW_TELEGRAM_SEND=1``; that runs after this
    fixture and wins. This env layer is belt-and-suspenders to the send-path's
    own ``running_under_pytest()`` auto-detection.
    """
    monkeypatch.setenv("JARVIS_DISABLE_TELEGRAM_SEND", "1")


# ---------------------------------------------------------------------------
# DEV-38: сторож КЛАССА, а не случая
# ---------------------------------------------------------------------------

_STARTER = "start_jarvis.ps1"


def _unscoped_starter_argv(args) -> str | None:
    """Вернуть командную строку, если это запуск боевого стартера БЕЗ скоупа.

    Правило ровно одно и проверяемое: если в аргументах помянут
    ``start_jarvis.ps1``, там ОБЯЗАН быть ``-Root`` — единственное, что уводит
    стартер с живого дерева. Без ``-Root`` корнем становится каталог самого
    скрипта, то есть НАСТОЯЩЕЕ дерево, и первое, что стартер там делает —
    ``Stop-OldBot``.
    """
    if isinstance(args, (list, tuple)):
        parts = [str(a) for a in args]
    else:
        parts = [str(args)]
    blob = " ".join(parts)
    low = blob.lower()
    if _STARTER not in low:
        return None
    if "-root" in low:
        return None
    return blob


@pytest.fixture(autouse=True)
def _no_test_may_launch_the_real_starter(monkeypatch):
    """Ни один тест не имеет права запустить боевой ``start_jarvis.ps1``.

    18.08 19:25:04 ``test_restart_bot_no_script_returns_false`` пропатчил
    ``check_bot_alive``, но не ``Popen`` — и ``restart_bot_if_dead()``
    по-настоящему запустил стартер из worktree. Тот снёс ЖИВОГО бота из
    ``C:\\jarvis`` (ETW: NtTerminateProcess, цель PID 760; код выхода −1 —
    подпись ``Stop-Process``). ~16 прогонов гейта в день = ~16 смертей в день.

    Починка одного теста этот класс не закрывает: следующий такой же напишется
    завтра. Поэтому проверка стоит НА СОСТАВЕ ВЫЗОВОВ, а не на глазах ревьюера,
    и падает ГРОМКО — молчаливый пропуск здесь неотличим от старого зелёного.

    Подмена одного ``subprocess.Popen`` накрывает и ``run``/``call``/
    ``check_output``: все они разрешают ``Popen`` через глобаль модуля.
    Тестам, которым стартер нужен по делу, достаточно передать ``-Root`` на
    tmp-дерево — тогда стартер физически не может дотянуться до прода.
    """
    real_popen = subprocess.Popen

    def guarded(*args, **kwargs):
        argv = args[0] if args else kwargs.get("args")
        offender = _unscoped_starter_argv(argv)
        if offender is not None:
            raise AssertionError(
                "DEV-38: тест пытается запустить БОЕВОЙ start_jarvis.ps1 "
                "(без -Root). Стартер первым делом убивает бота этого дерева. "
                "Пропатчи Popen или передай -Root на tmp-дерево. "
                f"argv: {offender}"
            )
        return real_popen(*args, **kwargs)

    monkeypatch.setattr(subprocess, "Popen", guarded)


# ── ремень по памяти (спека 2026-08-21-suite-ram-belt) ───────────────────────
#
# Живёт ВНУТРИ процесса pytest, а не в обёртке, по двум причинам. Первая:
# снаружи не видно, КАКОЙ ТЕСТ виноват — 21.08 имя добывали час, считая точки
# в файле прогресса. Вторая: обёртку можно забыть, а упал именно прогон,
# запущенный руками обычной командой.
#
# Выключается только явным SUITE_RAM_BELT=0.

from app.services import suite_ram_belt as _suite_ram_belt   # noqa: E402

_RAM_BELT = _suite_ram_belt.install()


def pytest_runtest_logstart(nodeid, location):
    if _RAM_BELT is not None:
        _RAM_BELT.tracker.set(nodeid)


def pytest_runtest_logfinish(nodeid, location):
    if _RAM_BELT is not None:
        _RAM_BELT.tracker.clear()


def pytest_keyboard_interrupt(excinfo):
    """Напечатать отчёт СВОИМ каналом pytest.

    Живой дрил 21.08 показал: запись в ``sys.stderr`` до экрана не доходит —
    pytest перехватывает поток на уровне дескриптора, а при KeyboardInterrupt
    перехваченное отбрасывается. Отчёт оставался только в файле, то есть
    человек видел голый ``KeyboardInterrupt`` без причины.
    """
    if _RAM_BELT is not None and _RAM_BELT.sampler.last_report:
        print(_RAM_BELT.sampler.last_report)
