# -*- coding: utf-8 -*-
"""Пульт коммитит тумблер: path-scoped, на транке, и ГРОМКО падает.

Зачем вообще. Значение тумблера в файле — ФАКТ состояния прода, а правит его
робот. Пока правка оставалась незакоммиченной, живое дерево было грязным, а
проверка worktree в ops_watchdog — красной (11.08 она простояла так ~14 часов).
«Грязное дерево = недеплоенный код» переставало быть правдой ровно там, где
это утверждение и нужно.

Главное свойство файла — НЕ успех, а поведение на отказе. Коммит может не
пройти: незавершённый merge/rebase, упавший хук, дерево не на транке. Тумблер
при этом УЖЕ применён и уже работает. Промолчать здесь значит оставить владельца
с грязным деревом, о котором он не знает, — то есть ровно с тем состоянием, ради
выхода из которого всё и делается.

Отдельно: chatter деплоится КОПИЕЙ ПАПКИ. Конфиг клиента может лежать вне
репозитория вообще — там коммитить не к чему, и крик был бы ложной тревогой.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from chatter.config.config_commit import commit_config_file

TRUNK = "phase-4.0-unified-jarvis"


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=repo, capture_output=True,
                          text=True, encoding="utf-8", errors="replace")


@pytest.fixture()
def repo(tmp_path) -> Path:
    r = tmp_path / "repo"
    (r / "clients" / "volska").mkdir(parents=True)
    _git(r.parent, "init", "-q", "-b", TRUNK, str(r))
    _git(r, "config", "user.email", "t@example.com")
    _git(r, "config", "user.name", "T")
    (r / "clients" / "volska" / "settings.yaml").write_text(
        "payments:\n  enabled: false\n", encoding="utf-8")
    (r / "other.py").write_text("x = 1\n", encoding="utf-8")
    _git(r, "add", "-A")
    _git(r, "commit", "-q", "-m", "init")
    return r


def _settings(repo: Path) -> Path:
    return repo / "clients" / "volska" / "settings.yaml"


def _toggle(repo: Path, value: str = "true") -> Path:
    p = _settings(repo)
    p.write_text("payments:\n  enabled: %s\n" % value, encoding="utf-8")
    return p


def _dirty(repo: Path) -> list[str]:
    return [l for l in _git(repo, "status", "--porcelain", "-uno").stdout.splitlines() if l.strip()]


# ── удачный путь ───────────────────────────────────────────────────────────

def test_the_toggle_lands_in_history_and_the_tree_gets_clean(repo):
    out = commit_config_file(_toggle(repo), message="chore: payments on", trunk=TRUNK)
    assert out.ok and out.committed
    assert _dirty(repo) == []
    assert "payments on" in _git(repo, "log", "-1", "--format=%s").stdout


def test_only_the_named_file_is_committed(repo):
    """Path-scoped не формальность: рядом идёт живая сессия, и коммит «заодно»
    утащил бы в транк её незаконченную работу."""
    _toggle(repo)
    (repo / "other.py").write_text("x = 2   # чужая незаконченная правка\n", encoding="utf-8")

    out = commit_config_file(_settings(repo), message="chore: payments on", trunk=TRUNK)

    assert out.ok and out.committed
    assert _dirty(repo) == [" M other.py"], "коммит забрал чужую правку"


def test_a_staged_foreign_file_is_left_staged(repo):
    """Индекс — тоже чужое состояние. Path-scoped коммит его не трогает."""
    _toggle(repo)
    (repo / "other.py").write_text("x = 3\n", encoding="utf-8")
    _git(repo, "add", "other.py")

    commit_config_file(_settings(repo), message="chore: payments on", trunk=TRUNK)

    assert "M  other.py" in _git(repo, "status", "--porcelain", "-uno").stdout


def test_no_change_is_success_and_silence(repo):
    """`/payments on` при уже включённом тумблере: файл тот же, коммитить
    нечего, дерево чистое. Кричать не о чем."""
    out = commit_config_file(_settings(repo), message="chore: payments on", trunk=TRUNK)
    assert out.ok and not out.committed
    assert out.detail == ""


def test_a_config_outside_any_repository_is_silent_success(tmp_path):
    """Chatter деплоится КОПИЕЙ ПАПКИ: конфиг клиента вполне может лежать вне
    репозитория. Там нет ни истории, ни грязного дерева — и крик был бы ложной
    тревогой на каждом переключении."""
    p = tmp_path / "clients" / "volska" / "settings.yaml"
    p.parent.mkdir(parents=True)
    p.write_text("payments:\n  enabled: true\n", encoding="utf-8")

    out = commit_config_file(p, message="chore: payments on", trunk=TRUNK)

    assert out.ok and not out.committed
    assert out.detail == ""


# ── ГЛАВНОЕ: отказ обязан быть громким ─────────────────────────────────────

def test_an_unfinished_merge_is_loud_and_leaves_the_tree_dirty(repo):
    """Незавершённый merge — не гипотеза: он остаётся после конфликта, и в этот
    момент владелец как раз щёлкает тумблером, чтобы починить прод."""
    _git(repo, "checkout", "-q", "-b", "side")
    (repo / "other.py").write_text("side\n", encoding="utf-8")
    _git(repo, "commit", "-q", "-am", "side")
    _git(repo, "checkout", "-q", TRUNK)
    (repo / "other.py").write_text("trunk\n", encoding="utf-8")
    _git(repo, "commit", "-q", "-am", "trunk")
    _git(repo, "merge", "side")          # конфликт, merge повис
    _toggle(repo)

    out = commit_config_file(_settings(repo), message="chore: payments on", trunk=TRUNK)

    assert not out.ok
    # Сверяем НАШУ формулировку, а не git'овскую: git на partial commit во время
    # merge ругается и сам, и тест, принимающий его текст, не отличает «мы
    # проверили» от «мы попробовали и нам не дали». Разница не косметическая —
    # при незавершённом rebase попытка может и пройти, дописав коммит в чужое
    # состояние (мутация DEV-26 поймала эту слепоту 11.08).
    assert "незавершённый" in out.detail, out.detail
    assert any("settings.yaml" in l for l in _dirty(repo)), \
        "тумблер обязан остаться применённым: он уже работает"


def test_a_failing_hook_is_loud_and_names_the_hook_output(repo):
    """Хук — чужой код в нашем пути. Если он завалил коммит, владелец обязан
    прочитать ПОЧЕМУ, а не «что-то пошло не так»."""
    hooks = Path(_git(repo, "rev-parse", "--absolute-git-dir").stdout.strip()) / "hooks"
    hooks.mkdir(parents=True, exist_ok=True)
    (hooks / "pre-commit").write_text(
        "#!/bin/sh\necho 'ХУК: транк защищён политикой' >&2\nexit 1\n", encoding="utf-8")
    _toggle(repo)

    out = commit_config_file(_settings(repo), message="chore: payments on", trunk=TRUNK)

    assert not out.ok
    assert "ХУК" in out.detail or "хук" in out.detail.lower()
    assert _dirty(repo), "правка потеряна — тумблер применён, а файл откатился"


def test_a_tree_off_the_trunk_refuses_to_commit(repo):
    """Дерево на чужой ветке — это чья-то незакрытая сессия. Уронить туда
    коммит про прод значит спрятать состояние прода в чужой работе."""
    _git(repo, "checkout", "-q", "-b", "feat/x")
    _toggle(repo)

    out = commit_config_file(_settings(repo), message="chore: payments on", trunk=TRUNK)

    assert not out.ok
    assert "feat/x" in out.detail and TRUNK in out.detail
    assert _git(repo, "log", "-1", "--format=%s").stdout.strip() == "init", \
        "коммит всё-таки лёг в чужую ветку"


def test_a_detached_head_refuses_too(repo):
    sha = _git(repo, "rev-parse", "HEAD").stdout.strip()
    _git(repo, "checkout", "-q", sha)
    _toggle(repo)

    out = commit_config_file(_settings(repo), message="chore: payments on", trunk=TRUNK)

    assert not out.ok
    assert out.detail


def test_git_missing_is_loud_not_silent(repo, monkeypatch):
    """«git не нашёлся» неотличимо от «коммит прошёл», если промолчать."""
    import chatter.config.config_commit as cc
    monkeypatch.setattr(cc, "GIT", "git-which-does-not-exist")
    _toggle(repo)

    out = commit_config_file(_settings(repo), message="chore: payments on", trunk=TRUNK)

    assert not out.ok and out.detail
