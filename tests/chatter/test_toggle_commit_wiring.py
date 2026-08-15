# -*- coding: utf-8 -*-
"""Три тумблера пульта пишут своё состояние в историю — и кричат, если не смогли.

Здесь проверяется ПУТЬ целиком: команда владельца → правка файла → reload →
коммит → текст, который владелец реально прочитает. Проверять коммит отдельно от
ответа было бы той же ошибкой, ради которой писался сквозной тест Ф0: обе
половины зелёные, а между ними ничего.

Главный тест файла — не удачный путь, а ПРОВАЛ коммита. К этому моменту тумблер
уже применён и уже работает; промолчать о грязном дереве значит вернуть ровно то
состояние, из-за которого проверка worktree простояла красной ~14 часов и
ослепла к настоящему недеплоенному коду.
"""
from __future__ import annotations

import asyncio
import shutil
import subprocess
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
import yaml

from chatter.storage.db import Store
from chatter.telethon_run import build_runner

SRC = Path(__file__).resolve().parents[2] / "chatter" / "clients"
TRUNK = "phase-4.0-unified-jarvis"

PAYMENTS_BLOCK = """
payments:
  enabled: false
  channels:
    - id: iban_main
      kind: bank_transfer
      mode: manual
      currency: USD
      requisites_template: iban_main
      display: "Банківський переказ"
"""
REQUISITES = "templates:\n  iban_main:\n    body: |\n      IBAN UA00 0000 0000\n"


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=repo, capture_output=True,
                          text=True, encoding="utf-8", errors="replace")


@pytest.fixture()
def repo(tmp_path) -> Path:
    """Клиенты ВНУТРИ git-репозитория на транке — как в живом дереве."""
    r = tmp_path / "jarvis"
    r.mkdir()
    _git(tmp_path, "init", "-q", "-b", TRUNK, str(r))
    _git(r, "config", "user.email", "t@example.com")
    _git(r, "config", "user.name", "T")
    dst = r / "chatter" / "clients"
    dst.parent.mkdir(parents=True)
    shutil.copytree(SRC, dst, ignore=shutil.ignore_patterns(".versions"))
    p = dst / "demo" / "settings.yaml"
    p.write_text(p.read_text(encoding="utf-8") + PAYMENTS_BLOCK, encoding="utf-8")
    (dst / "demo" / "requisites.yaml").write_text(REQUISITES, encoding="utf-8")
    _git(r, "add", "-A")
    _git(r, "commit", "-q", "-m", "init")
    return r


def _clients(repo: Path) -> Path:
    return repo / "chatter" / "clients"


def _runner(repo: Path):
    c = MagicMock()
    c.send_message = AsyncMock(return_value=MagicMock(id=1))
    return build_runner(
        client=c, clients_dir=_clients(repo), persona_slugs=["demo", "demo2"],
        store=Store(":memory:"), loop=asyncio.new_event_loop(), llm_mode="fake")


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def _settings(repo: Path) -> Path:
    return _clients(repo) / "demo" / "settings.yaml"


def _dirty(repo: Path) -> list[str]:
    return [l for l in _git(repo, "status", "--porcelain", "-uno").stdout.splitlines()
            if l.strip()]


def _break_the_commit(repo: Path) -> None:
    """Упавший pre-commit hook — самый честный способ сломать ровно коммит,
    ничего не ломая до него: правка файла и reload проходят как обычно."""
    hooks = Path(_git(repo, "rev-parse", "--absolute-git-dir").stdout.strip()) / "hooks"
    hooks.mkdir(parents=True, exist_ok=True)
    (hooks / "pre-commit").write_text(
        "#!/bin/sh\necho 'ХУК: коммит отклонён' >&2\nexit 1\n", encoding="utf-8")


# ── удачный путь: состояние прода доезжает до истории ──────────────────────

def test_payments_toggle_lands_in_history_and_leaves_the_tree_clean(repo):
    out = _run(_runner(repo).handle_config_command("payments", "on confirm", language="ru"))

    assert "✅" in out and "⚠️" not in out
    assert yaml.safe_load(_settings(repo).read_text(encoding="utf-8"))["payments"]["enabled"] is True
    assert _dirty(repo) == [], "дерево осталось грязным — сторож даст красное"
    assert "payments.enabled=true" in _git(repo, "log", "-1", "--format=%s").stdout


def test_funnel_gate_toggle_is_committed_too(repo):
    """Болезнь общая: пульт правит тот же файл ради гейта воронки."""
    out = _run(_runner(repo).handle_config_command("funnel_gate", "on confirm", language="ru"))

    assert "⚠️" not in out
    assert _dirty(repo) == []
    assert "funnel_gate=true" in _git(repo, "log", "-1", "--format=%s").stdout


def test_honesty_toggle_is_committed_too(repo):
    out = _run(_runner(repo).handle_config_command("honesty", "free confirm", language="ru"))

    assert _dirty(repo) == []
    assert "honesty_mode=free_owner_liability" in _git(repo, "log", "-1", "--format=%s").stdout


def test_the_commit_carries_the_client_slug(repo):
    """В истории обязано быть видно, ЧЕЙ конфиг переключили: на volska-раннере
    безымянное подтверждение однажды убедило владельца, что тумблер лёг в чужой
    конфиг (дрил 22.07)."""
    _run(_runner(repo).handle_config_command("payments", "on confirm", language="ru"))
    assert "demo" in _git(repo, "log", "-1", "--format=%s").stdout


# ── ГЛАВНОЕ: коммит не прошёл — владелец узнаёт об этом из ОТВЕТА ─────────

@pytest.mark.parametrize("cmd,arg", [
    ("payments", "on confirm"),
    ("funnel_gate", "on confirm"),
    ("honesty", "free confirm"),
])
def test_a_failed_commit_is_said_out_loud_in_the_reply(repo, cmd, arg):
    _break_the_commit(repo)

    out = _run(_runner(repo).handle_config_command(cmd, arg, language="ru"))

    assert "⚠️" in out, "провал коммита утонул — владелец о грязном дереве не узнает"
    assert "ХУК" in out, "причина не названа: «что-то пошло не так» нечем чинить"
    assert "git commit --" in out, "не сказано, что делать руками"


def test_a_failed_commit_does_not_undo_the_toggle(repo):
    """Тумблер уже применён и уже работает — откатывать его из-за неудавшегося
    коммита значит выключить фичу молча, по причине, к ней не относящейся."""
    _break_the_commit(repo)

    out = _run(_runner(repo).handle_config_command("payments", "on confirm", language="ru"))

    assert "✅" in out, "успех переключения перестал быть виден за предупреждением"
    assert yaml.safe_load(_settings(repo).read_text(encoding="utf-8"))["payments"]["enabled"] is True
    assert any("settings.yaml" in l for l in _dirty(repo))


def test_a_tree_off_the_trunk_is_reported_not_silently_committed(repo):
    """Чужая ветка = чья-то незакрытая сессия. Коммит про прод туда не кладём,
    но и молчать нельзя: дерево грязное."""
    _git(repo, "checkout", "-q", "-b", "feat/somebody")

    out = _run(_runner(repo).handle_config_command("payments", "on confirm", language="ru"))

    assert "⚠️" in out and "feat/somebody" in out
    assert _git(repo, "log", "-1", "--format=%s").stdout.strip() == "init"


def test_clients_outside_a_repository_stay_silent(tmp_path):
    """Chatter деплоится копией папки: конфиг может лежать вне git вообще.
    Предупреждение на каждом переключении там — ложная тревога, а привычка
    игнорировать этот канал стоит дороже самого канала."""
    dst = tmp_path / "clients"
    shutil.copytree(SRC, dst, ignore=shutil.ignore_patterns(".versions"))
    p = dst / "demo" / "settings.yaml"
    p.write_text(p.read_text(encoding="utf-8") + PAYMENTS_BLOCK, encoding="utf-8")
    (dst / "demo" / "requisites.yaml").write_text(REQUISITES, encoding="utf-8")
    c = MagicMock()
    c.send_message = AsyncMock(return_value=MagicMock(id=1))
    r = build_runner(client=c, clients_dir=dst, persona_slugs=["demo", "demo2"],
                     store=Store(":memory:"), loop=asyncio.new_event_loop(),
                     llm_mode="fake")

    out = _run(r.handle_config_command("payments", "on confirm", language="ru"))

    assert "⚠️" not in out
