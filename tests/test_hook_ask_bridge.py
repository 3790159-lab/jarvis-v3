# -*- coding: utf-8 -*-
"""Мост подтверждений: сторожа на решения, а не на текст сообщения.

Замер Б1 показал главное: хук, не уложившийся в `timeout`, убивают, а вызов
ВСЁ РАВНО исполняется. То есть «не ответили» на уровне оболочки означает
РАЗРЕШЕНИЕ. Поэтому мост обязан решать сам и всегда успевать — и каждый
сомнительный случай обязан кончаться `deny`.

Два правила владельца проверяются здесь же:
  * вопросы субагентов в пульт не идут (пять агентов = пять слепых вопросов);
  * отказ обязан называть себя ВРЕМЕННЫМ, иначе исполнитель пойдёт другим
    путём и обойдёт защиту из вежливости.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from scripts.hook_ask_bridge import (  # noqa: E402
    ALLOW, DENY, NO, YES, build_question, decide, take_lock,
)


def _kw(tmp_path, **over):
    base = dict(tool="PowerShell", command="git merge --ff-only fix/x",
                reason="мерж в ветку", cwd=r"C:\jarvis",
                journal_path=tmp_path / "asks.jsonl",
                lock_fn=lambda: True, unlock_fn=lambda: None)
    base.update(over)
    return base


def test_a_yes_button_allows(tmp_path):
    """Положительная сторона: без неё мост можно «починить», навсегда
    запретив всё, и сторожа отказа этого не заметят."""
    got, why = decide(ask_fn=lambda *a, **k: (YES, "telegram"), **_kw(tmp_path))
    assert got == ALLOW, why


def test_a_no_button_denies(tmp_path):
    got, why = decide(ask_fn=lambda *a, **k: (NO, "telegram"), **_kw(tmp_path))
    assert got == DENY
    assert "нет" in why.lower()


def test_a_timeout_denies_and_says_it_is_temporary(tmp_path):
    """🔴 Т1 спеки. Оболочка на своём таймауте РАЗРЕШАЕТ (замер Б1), поэтому
    решение обязан принять мост — и назвать отказ временным, иначе следующим
    шагом станет обход защиты «вежливым» путём."""
    got, why = decide(ask_fn=lambda *a, **k: ("__TIMEOUT__", "timeout"),
                      **_kw(tmp_path))
    assert got == DENY
    assert "ВРЕМЕННЫЙ" in why and "повтори ТУ ЖЕ команду" in why


def test_a_broken_channel_denies_loudly(tmp_path):
    """Канал упал — это отказ, а не разрешение. Молчащий транспорт не имеет
    права открывать дорогу необратимому."""
    def boom(*a, **k):
        raise RuntimeError("сеть легла")

    got, why = decide(ask_fn=boom, **_kw(tmp_path))
    assert got == DENY
    assert "RuntimeError" in why and "ВРЕМЕННЫЙ" in why


def test_a_subagent_never_reaches_the_pult(tmp_path):
    """Правило владельца: пять параллельных агентов дали бы пять вопросов, на
    которые он отвечает вслепую. Субагент получает отказ, решает основная
    сессия."""
    sent = []
    got, why = decide(ask_fn=lambda *a, **k: sent.append(a) or (YES, "telegram"),
                      agent_id="aa25bdcf", **_kw(tmp_path))
    assert got == DENY
    assert sent == [], "вопрос субагента уехал в телеграм"
    assert "субагент" in why and "основная сессия" in why


def test_a_second_question_is_refused_not_queued(tmp_path):
    """Два одновременных вопроса на одном боте теряются (409 на getUpdates).
    Второй обязан получить отказ, а не встать в очередь: очередь на
    блокирующем хуке — это две замороженные сессии."""
    got, why = decide(ask_fn=lambda *a, **k: (YES, "telegram"),
                      **_kw(tmp_path, lock_fn=lambda: False))
    assert got == DENY
    assert "уже висит" in why and "ВРЕМЕННЫЙ" in why


def test_the_question_names_the_command_not_the_tool_call(tmp_path):
    """Т2 спеки: по «tool call» решение принять нельзя — владелец видит только
    это сообщение."""
    q = build_question("PowerShell", "git push origin main", "пуш в origin",
                       r"C:\jarvis")
    assert "git push origin main" in q and "пуш в origin" in q and "C:\\jarvis" in q


def test_a_long_command_is_cut_visibly(tmp_path):
    """Молча обрезанная команда выглядит как ДРУГАЯ команда — и решение
    принимается не про то."""
    q = build_question("Bash", "x" * 900, "причина", "/tmp")
    assert "…(обрезано)" in q and len(q) < 900


def test_both_ends_reach_the_journal(tmp_path):
    """Т3: вопрос без пары «решение» — это улика, а не пробел в данных."""
    journal = tmp_path / "asks.jsonl"
    decide(ask_fn=lambda *a, **k: (YES, "telegram"),
           **_kw(tmp_path, journal_path=journal))
    lines = journal.read_text(encoding="utf-8").strip().splitlines()
    assert any('"event": "asked"' in ln or '"event":"asked"' in ln for ln in lines)
    assert any('"decided"' in ln for ln in lines)


def test_a_stale_lock_does_not_close_the_channel_forever(tmp_path):
    """Ловит: замок, переживший упавший хук.

    Без снятия протухшего замка ОДИН убитый хук превратил бы все будущие
    подтверждения в отказы — то есть сломал бы ровно то, что чинит мост.
    """
    lock = tmp_path / ".lock"
    lock.write_text("999999 1\n", encoding="utf-8")
    # Возраст считается от mtime файла, поэтому «сейчас» берём ОТ НЕГО, а не
    # выдуманным числом: иначе тест проверял бы арифметику часов, а не правило.
    stale_now = lock.stat().st_mtime + 700
    assert take_lock(lock, now=stale_now, max_age_s=600) is True


def test_a_fresh_lock_is_respected(tmp_path):
    """Парная: свежий замок обязан держать, иначе два вопроса уйдут разом."""
    lock = tmp_path / ".lock"
    assert take_lock(lock) is True
    assert take_lock(lock) is False
