# -*- coding: utf-8 -*-
"""Мост подтверждений: вопрос хука уходит в телеграм, решение возвращается хуку.

Повод: `PreToolUse`-хук поднимает диалог в терминале, и если владельца там нет,
автономная работа встаёт. 17.08 это случилось дважды за день.

## Что показал замер Б1 (спека §2, п.1-4) — и почему конструкция такая

| замер | результат |
|---|---|
| хук не уложился в `timeout` | его убивают, решение теряется, **вызов ВСЁ РАВНО исполняется** |
| `timeout` в settings.json | применяется на лету, 300 с принимаются, рестарт сессии не нужен |
| хук в субагенте | **срабатывает**, субагент ждёт и задержки НЕ ЗАМЕЧАЕТ |
| видно ли у экрана | не измерено (владельца не было у экрана) |

Первая строка — центральная: **на таймаут оболочки полагаться нельзя**, он
означает РАЗРЕШЕНИЕ. Поэтому мост решает сам и всегда успевает: ждёт
`--wait` (60-90 с) при `timeout` хука в 300 с, и на любое сомнение печатает
отказ. Т1 спеки («таймаут = DENY») выполняется независимо от оболочки.

## Два правила владельца, встроенные в конструкцию

1. **Вопросы субагентов в пульт НЕ идут.** Пять параллельных агентов дали бы
   пять вопросов, на которые владелец отвечает вслепую. Субагент получает
   отказ с причиной, решение принимает основная сессия. Признак измерен, а не
   придуман: в payload субагента есть `agent_id`/`agent_type`, в основной
   сессии их нет вовсе.
2. **Отказ обязан называть себя ВРЕМЕННЫМ.** Иначе исполнитель, получив «нет»,
   пойдёт другим путём и обойдёт защиту из вежливости.

Сети здесь ровно столько, сколько уже есть у `ask_owner` (свой бот, свой
токен) — второго `getUpdates` на токене пульта не появляется, 409 невозможен.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.ask_owner import ask as _ask_owner  # noqa: E402

ASKS_DIR = ROOT / "state" / "hook_asks"
LOCK_PATH = ASKS_DIR / ".lock"

ALLOW = "allow"
DENY = "deny"

YES = "✅ Да, выполняй"
NO = "❌ Нет"

# Сколько ждать ответа. 60-90 с при `timeout` хука 300 с: сессия стоит всё это
# время, поэтому ожидание короткое, а «нет» — временное.
DEFAULT_WAIT_S = 75

TEMPORARY_SUFFIX = (
    "Это ВРЕМЕННЫЙ отказ: разрешение можно дать позже — ответь в телеграме и "
    "повтори ТУ ЖЕ команду. Обходить её другим путём нельзя."
)


def _lock_is_stale(path: Path, *, now: float, max_age_s: float) -> bool:
    try:
        return (now - path.stat().st_mtime) > max_age_s
    except OSError:
        return True


def take_lock(path: Path = LOCK_PATH, *, now: float | None = None,
              max_age_s: float = 600.0) -> bool:
    """Один вопрос за раз. Два одновременных `ask_owner` на одном боте дают 409
    и теряют вопрос — поэтому второй вопрос не задаётся, а отклоняется.

    Протухший замок снимается: иначе упавший хук закрыл бы канал навсегда, и
    любое подтверждение стало бы отказом.
    """
    now = time.time() if now is None else now
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and _lock_is_stale(path, now=now, max_age_s=max_age_s):
        path.unlink(missing_ok=True)
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        return False
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(f"{os.getpid()} {int(now)}\n")
    return True


def release_lock(path: Path = LOCK_PATH) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def build_question(tool: str, command: str, reason: str, cwd: str) -> str:
    """Т2 спеки: текст называет КОМАНДУ, а не «tool call».

    Владелец на прогулке видит только это сообщение — оно и есть весь контекст.
    Длинную команду режем явным многоточием: молча обрезанная команда выглядит
    как другая команда.
    """
    cut = command if len(command) <= 600 else command[:600] + " …(обрезано)"
    return (f"🔐 Подтверждение\n\n"
            f"Команда ({tool}):\n{cut}\n\n"
            f"Причина: {reason}\n"
            f"Где: {cwd}")


def record(path: Path, **fields) -> None:
    """Журнал вопроса (Т3): оба конца пишутся отдельными строками, чтобы
    вопрос без ответа был ВИДЕН как вопрос без ответа."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(fields, ensure_ascii=False) + "\n")
    except OSError:
        pass


def decide(*, tool: str, command: str, reason: str, cwd: str,
           wait_s: int = DEFAULT_WAIT_S, agent_id: str = "",
           ask_fn=None, lock_fn=None, unlock_fn=None,
           journal_path: Path | None = None, now_fn=time.time) -> tuple[str, str]:
    """(решение, пояснение). Решение — только `allow` или `deny`.

    Промежуточных состояний нет намеренно: «не дождались» и «не смогли
    спросить» обязаны вести себя одинаково с «нет», иначе появляется путь, по
    которому необратимое проезжает молча.
    """
    journal_path = journal_path or (ASKS_DIR / "hook_asks.jsonl")
    ask_fn = ask_fn or _ask_owner
    lock_fn = lock_fn or take_lock
    unlock_fn = unlock_fn or release_lock

    if agent_id:
        # Правило владельца 1: субагент не спрашивает. Ответить на пять
        # одновременных вопросов вслепую нельзя, а «да» вслепую хуже «нет».
        why = (f"вопрос от субагента ({agent_id}) в пульт не отправляется — "
               f"решение принимает основная сессия. {TEMPORARY_SUFFIX}")
        record(journal_path, ts=int(now_fn()), event="skipped_subagent",
               tool=tool, reason=reason, agent_id=agent_id)
        return DENY, why

    if not lock_fn():
        why = ("другой вопрос уже висит в телеграме — два одновременных вопроса "
               f"теряются на одном боте. {TEMPORARY_SUFFIX}")
        record(journal_path, ts=int(now_fn()), event="skipped_busy",
               tool=tool, reason=reason)
        return DENY, why

    question = build_question(tool, command, reason, cwd)
    record(journal_path, ts=int(now_fn()), event="asked", tool=tool,
           reason=reason, command=command[:600], wait_s=wait_s)
    try:
        decision, by = ask_fn(question, [YES, NO], timeout_s=wait_s)
    except Exception as exc:  # noqa: BLE001 — канал упал = отказ, но громкий
        record(journal_path, ts=int(now_fn()), event="decided",
               decision=DENY, by=f"error:{type(exc).__name__}")
        return DENY, (f"канал подтверждения не ответил ({type(exc).__name__}). "
                      f"{TEMPORARY_SUFFIX}")
    finally:
        unlock_fn()

    if decision == YES:
        record(journal_path, ts=int(now_fn()), event="decided",
               decision=ALLOW, by=by)
        return ALLOW, f"владелец разрешил в телеграме ({by})"

    record(journal_path, ts=int(now_fn()), event="decided", decision=DENY,
           by=by, raw=decision)
    if decision == NO:
        return DENY, f"владелец ответил «нет» ({by})"
    return DENY, (f"ответа нет за {wait_s} с ({decision}). {TEMPORARY_SUFFIX}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="мост подтверждений хука → телеграм")
    ap.add_argument("--tool", required=True)
    ap.add_argument("--command", required=True)
    ap.add_argument("--reason", required=True)
    ap.add_argument("--cwd", default="")
    ap.add_argument("--agent-id", default="")
    ap.add_argument("--wait", type=int, default=DEFAULT_WAIT_S)
    args = ap.parse_args(argv)

    decision, why = decide(tool=args.tool, command=args.command,
                           reason=args.reason, cwd=args.cwd,
                           wait_s=args.wait, agent_id=args.agent_id)
    # Первая строка — решение для хука, вторая — пояснение для человека.
    print(decision)
    print(why)
    return 0 if decision == ALLOW else 1


if __name__ == "__main__":
    raise SystemExit(main())
