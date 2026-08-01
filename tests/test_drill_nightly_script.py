# -*- coding: utf-8 -*-
"""Обёртка регресса по расписанию (стенд v2, Э5b).

Скрипт стоит между Планировщиком и живым прогоном, который тратит деньги и
шлёт сообщения в живой Telegram. Поэтому тесты — про ПОРЯДОК проверок:
предполётные отказы обязаны случиться ДО того, как сброс сотрёт состояние
контакта, а сам прогон — ДО того, как будет потрачен первый цент.

$0: подпроцессы инъектируются, Telegram — заглушка.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "drill_nightly.py"

DRILL = "8849893367:volska"


def _load():
    spec = importlib.util.spec_from_file_location("drill_nightly_script", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["drill_nightly_script"] = mod
    spec.loader.exec_module(mod)
    return mod


class _Runs:
    def __init__(self, codes=None):
        self.cmds: list[list[str]] = []
        self._codes = list(codes or [])

    def __call__(self, cmd):
        self.cmds.append([str(c) for c in cmd])
        code = self._codes.pop(0) if self._codes else 0
        return code, "вердикт: ок"

    @property
    def kinds(self) -> list[str]:
        out = []
        for c in self.cmds:
            out.append("reset" if any("drill_reset" in x for x in c)
                       else "run" if any("drill_runner" in x for x in c)
                       else "?")
        return out


@pytest.fixture()
def env(tmp_path):
    """Полностью годная к прогону обстановка — от неё отнимают по одному."""
    (tmp_path / ".secrets").mkdir()
    (tmp_path / ".secrets" / "drill_lead.session.enc").write_bytes(b"x")
    (tmp_path / ".secrets" / "drill_lead_peers.txt").write_text(
        "777000\n", encoding="utf-8")
    (tmp_path / "client").mkdir()
    (tmp_path / "client" / "settings.yaml").write_text(
        "work_hours: {start: 9, end: 20}\n", encoding="utf-8")
    sc = tmp_path / "d10.yaml"
    sc.write_text(f"name: д10\ncontact: {DRILL}\nsteps:\n  - say: \"привіт\"\n",
                  encoding="utf-8")
    (tmp_path / "demo.db").write_bytes(b"")
    return tmp_path


def _argv(env, **over):
    args = {
        "--root": str(env),
        "--client-dir": str(env / "client"),
        "--db": str(env / "demo.db"),
        "--scenario": str(env / "d10.yaml"),
        "--contact": DRILL,
    }
    args.update(over)
    out = []
    for k, v in args.items():
        out += [k, str(v)]
    return out


def _run(mod, env, *, hour=10, runs=None, argv=None, sent=None):
    runs = runs if runs is not None else _Runs()
    sent = sent if sent is not None else []
    code = mod.main(argv if argv is not None else _argv(env),
                    run=runs, hour=hour, send=sent.append)
    return code, runs, sent


# ── рабочее окно ─────────────────────────────────────────────────────────────


def test_outside_work_window_spends_nothing(env):
    """Решение владельца (§11 п.2): регресс по расписанию — только внутри
    рабочего окна персоны. Вне окна не тратим ни цента и ничего не стираем."""
    mod = _load()
    code, runs, sent = _run(mod, env, hour=3)
    assert code == 3
    assert runs.cmds == []
    assert sent == [] or "окн" in sent[0].lower()


def test_window_edges(env):
    mod = _load()
    assert mod.in_window(9, start=9, end=20) is True
    assert mod.in_window(19, start=9, end=20) is True
    assert mod.in_window(20, start=9, end=20) is False
    assert mod.in_window(8, start=9, end=20) is False


def test_inside_window_runs(env):
    mod = _load()
    code, runs, sent = _run(mod, env, hour=10)
    assert code == 0
    assert runs.kinds == ["reset", "run"]


# ── предполётные отказы — ДО сброса ──────────────────────────────────────────


def test_empty_peers_file_refuses_before_reset(env):
    """Сброс стирает переписку. Упереться в пустой allowlist ПОСЛЕ сброса
    значит потерять состояние впустую."""
    mod = _load()
    (env / ".secrets" / "drill_lead_peers.txt").write_text("", encoding="utf-8")
    code, runs, sent = _run(mod, env)
    assert code == 2
    assert runs.cmds == [], "ни сброса, ни прогона"


def test_missing_peers_file_refuses(env):
    mod = _load()
    (env / ".secrets" / "drill_lead_peers.txt").unlink()
    code, runs, _ = _run(mod, env)
    assert code == 2
    assert runs.cmds == []


def test_ambiguous_peers_refuse(env):
    """Два разрешённых получателя — и «кому слать» становится догадкой."""
    mod = _load()
    (env / ".secrets" / "drill_lead_peers.txt").write_text(
        "777000\n777001\n", encoding="utf-8")
    code, runs, _ = _run(mod, env)
    assert code == 2
    assert runs.cmds == []


def test_explicit_peer_resolves_ambiguity(env):
    mod = _load()
    (env / ".secrets" / "drill_lead_peers.txt").write_text(
        "777000\n777001\n", encoding="utf-8")
    code, runs, _ = _run(mod, env, argv=_argv(env, **{"--lead-peer": "777001"}))
    assert code == 0
    assert "777001" in " ".join(runs.cmds[1])


def test_explicit_peer_outside_allowlist_refuses(env):
    mod = _load()
    code, runs, _ = _run(mod, env, argv=_argv(env, **{"--lead-peer": "999999"}))
    assert code == 2
    assert runs.cmds == []


def test_missing_lead_session_refuses(env):
    mod = _load()
    (env / ".secrets" / "drill_lead.session.enc").unlink()
    code, runs, _ = _run(mod, env)
    assert code == 2
    assert runs.cmds == []


def test_non_drill_contact_refuses(env):
    """Тот же предохранитель, что в сбросе и в автолиде — третьим слоем."""
    mod = _load()
    code, runs, _ = _run(mod, env,
                         argv=_argv(env, **{"--contact": "999999999:volska"}))
    assert code == 2
    assert runs.cmds == []


def test_missing_scenario_refuses(env):
    mod = _load()
    (env / "d10.yaml").unlink()
    code, runs, _ = _run(mod, env)
    assert code == 2
    assert runs.cmds == []


# ── порядок и передача вердикта ──────────────────────────────────────────────


def test_reset_failure_stops_before_paying(env):
    """Сброс не удался — старт непредсказуем, и прогон проверял бы осадок за
    свои деньги."""
    mod = _load()
    code, runs, sent = _run(mod, env, runs=_Runs([2]))
    assert code == 2
    assert runs.kinds == ["reset"], "прогон не должен стартовать"


def test_verdict_code_is_passed_through(env):
    mod = _load()
    for verdict in (0, 1, 2):
        code, runs, _ = _run(mod, env, runs=_Runs([0, verdict]))
        assert code == verdict


def test_owner_gets_the_verdict(env):
    mod = _load()
    code, runs, sent = _run(mod, env, runs=_Runs([0, 1]))
    assert sent, "молчаливый ночной прогон бесполезен"
    assert "🔴" in sent[0] or "провал" in sent[0].lower()


def test_run_command_is_auto_lead(env):
    mod = _load()
    code, runs, _ = _run(mod, env)
    run_cmd = " ".join(runs.cmds[1])
    assert "--auto-lead" in run_cmd
    assert "--yes" in run_cmd
    assert "--lead-peer 777000" in run_cmd


def test_reset_command_is_apply_on_the_drill_contact(env):
    mod = _load()
    code, runs, _ = _run(mod, env)
    reset_cmd = " ".join(runs.cmds[0])
    assert "--apply" in reset_cmd
    assert DRILL in reset_cmd
