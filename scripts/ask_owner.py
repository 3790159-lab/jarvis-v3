#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Спросить владельца кнопками в Телеграме и ДОЖДАТЬСЯ ответа.

    python scripts/ask_owner.py --question "Мержу ветку в транк?" \
        --context "git merge --ff-only feat/ask-owner" \
        --option "Да" --option "Нет" --timeout 300

stdout — ТЕКСТ выбранного варианта, код возврата 0.
Таймаут  — печатает `__TIMEOUT__`, код возврата 2.
Отказ транспорта — `__ERROR__`, код возврата 3.
Коды 2 и 3 означают одно: НЕ ДЕЛАЕМ.

🔴 СВОЙ ТОКЕН — ЭТО НЕ УДОБСТВО, А УСЛОВИЕ РАБОТОСПОСОБНОСТИ.
Telegram отдаёт long-poll ровно одному потребителю на токен. Второй
`getUpdates` на токене контрол-бота вернул бы 409 и уронил ЖИВОЙ пульт
владельца — не этот скрипт, а пульт. Отдельный бот = отдельная очередь
апдейтов, конфликт невозможен по конструкции. Тот же инвариант уже выписан
в дрил-харнессе, там его держит тест.

Токен — в переменной окружения `JARVIS_ASK_BOT_TOKEN` (значение вводит
владелец через scripts/add_secret.ps1, сюда оно не попадает и в argv не
светится).

Стиль как у chatter_watch_check.py: stdlib-only в горячем пути, чтение
секрета — попытка через `.env.enc`, при любом сбое громкий фолбэк на
plaintext `.env`. Скрипт обязан работать, когда пакет `chatter` сломан:
именно тогда вопрос владельцу нужен чаще всего.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOKEN_ENV = "JARVIS_ASK_BOT_TOKEN"
OWNER_CHAT_ID = "237616472"
JOURNAL_PATH = ROOT / "state" / "ask_owner.jsonl"
OFFSET_PATH = ROOT / "state" / "ask_owner_offset.json"
ENV_PATH = ROOT / ".env"
ENV_ENC_PATH = ROOT / ".env.enc"

DEFAULT_TIMEOUT_S = 300
POLL_TIMEOUT_S = 25          # long-poll Telegram; < HTTP-таймаута ниже
HTTP_TIMEOUT_S = 40
TEXT_LIMIT = 3500            # у Telegram 4096; запас на разметку
TIMEOUT_DECISION = "__TIMEOUT__"
ERROR_DECISION = "__ERROR__"


class AskError(Exception):
    """Отказ, который обязан быть громким. Молчаливый дефолт на канале
    подтверждений — это способ однажды «разрешить» то, о чём не спрашивали."""


# ── чистые функции (под pytest) ───────────────────────────────────────────

def make_question_id(question: str, *, now: float) -> str:
    """Короткий идентификатор вопроса.

    Время входит в хэш НАМЕРЕННО: два одинаковых вопроса подряд обязаны
    получить разные id, иначе нажатие по прошлому закроет новый.

    Длина 12 символов — чтобы `ask:<id>:<idx>` уложился в лимит
    `callback_data` (64 байта), который Telegram молча отвергает."""
    raw = f"{now:.3f}|{question}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:12]


def build_keyboard(qid: str, options: list[str]) -> dict:
    if not options:
        raise AskError("вопрос без вариантов ответа — отвечать нечем")
    return {"inline_keyboard": [
        [{"text": opt, "callback_data": f"ask:{qid}:{i}"}]
        for i, opt in enumerate(options)]}


def build_text(question: str, context: str | None, deadline_ts: float) -> str:
    """Текст вопроса. Обязан называть СУТЬ или КОМАНДУ: по «tool call»
    решение принять нельзя, а владелец видит только это сообщение."""
    when = time.strftime("%H:%M:%S", time.localtime(deadline_ts))
    parts = ["\U0001F510 Нужно решение", "", question]
    if context:
        ctx = context if len(context) <= TEXT_LIMIT else context[:TEXT_LIMIT] + "…"
        parts += ["", "```", ctx, "```"]
    parts += ["", f"Ответить до {when} — иначе считаю за НЕТ и не делаю."]
    return "\n".join(parts)


def parse_decision(updates, *, qid: str, owner_chat_id: str,
                   options: list[str]) -> str | None:
    """Решение из пачки апдейтов, либо None.

    Три отсева, и каждый закрывает свой способ ошибиться:
      * не callback — обычное сообщение «да» ответом не является;
      * чужой отправитель — решать может только владелец;
      * ЧУЖОЙ qid — устаревшая кнопка от прошлого вопроса не отвечает на
        текущий (иначе «Да», нажатое полчаса назад, разрешит то, о чём не
        спрашивали).
    Индекс вне диапазона отбрасывается, а НЕ приводится к границе: «вариант
    5» из трёх не означает «последний»."""
    decision = None
    for upd in sorted(updates, key=lambda u: u.get("update_id", 0)):
        cq = upd.get("callback_query")
        if not cq:
            continue
        if str((cq.get("from") or {}).get("id")) != str(owner_chat_id):
            continue
        # Алфавит qid НЕ ограничиваем шестнадцатеричным: настоящую работу
        # делает точное сравнение ниже, а зашитый алфавит связал бы парсер с
        # текущей реализацией make_question_id и сломался бы при её замене.
        m = re.fullmatch(r"ask:([0-9A-Za-z]+):(\d+)", str(cq.get("data") or ""))
        if not m or m.group(1) != qid:
            continue
        idx = int(m.group(2))
        if 0 <= idx < len(options):
            decision = options[idx]          # последнее валидное нажатие
    return decision


def next_offset(updates, current: int) -> int:
    """Смещение getUpdates. Назад не ходим никогда — иначе один и тот же
    апдейт читается вечно."""
    if not updates:
        return current
    return max(current, max(u.get("update_id", 0) for u in updates) + 1)


def journal(path, event: str, **fields) -> None:
    """Одна строка JSON на событие. Пишем ОБА конца — «спросил» и
    «получил»: строка без пары это улика (вопрос остался без ответа), а не
    пробел в данных."""
    rec = {"ts": time.time(), "event": event}
    rec.update(fields)
    p = Path(path)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception as exc:                 # noqa: BLE001
        # Журнал недоступен — говорим вслух. Тихо продолжить значит потерять
        # ответ на вопрос «кто разрешил» (DEV-18).
        print(f"[ask_owner] ЖУРНАЛ НЕДОСТУПЕН ({exc}): {rec}", file=sys.stderr)


# ── IO (проверяется живьём, не юнит-тестами) ──────────────────────────────

def _token_from_enc() -> str:
    if not ENV_ENC_PATH.exists():
        return ""
    try:
        if str(ROOT) not in sys.path:
            sys.path.insert(0, str(ROOT))
        from chatter.security.crypto import decrypt_from_file
        from chatter.security.secret_loader import parse_env_text
        entropy = os.environ.get("JARVIS_ENTROPY_FILE") \
            or str(ROOT / ".secrets" / "entropy.bin")
        values = parse_env_text(
            decrypt_from_file(ENV_ENC_PATH, entropy_path=entropy)
            .decode("utf-8-sig"))
        return values.get(TOKEN_ENV, "")
    except Exception as exc:                 # noqa: BLE001
        print(f"[ask_owner] .env.enc недоступен ({type(exc).__name__}: {exc}) "
              "— пробую plaintext .env", file=sys.stderr)
        return ""


def _token() -> str:
    tok = os.environ.get(TOKEN_ENV) or _token_from_enc()
    if tok:
        return tok
    try:
        env = ENV_PATH.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""
    m = re.search(rf'^\s*{TOKEN_ENV}\s*=\s*"?([^"\r\n]+)"?', env, re.M)
    return m.group(1).strip() if m else ""


def _api(token: str, method: str, payload: dict, *, timeout=HTTP_TIMEOUT_S):
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/{method}",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def _load_offset() -> int:
    try:
        return int(json.loads(OFFSET_PATH.read_text("utf-8"))["offset"])
    except Exception:
        return 0


def _save_offset(v: int) -> None:
    try:
        OFFSET_PATH.parent.mkdir(parents=True, exist_ok=True)
        OFFSET_PATH.write_text(json.dumps({"offset": v}), encoding="utf-8")
    except Exception:
        pass


def ask(question: str, options: list[str], *, context=None,
        timeout_s=DEFAULT_TIMEOUT_S) -> tuple[str, str]:
    """(решение, кем). Решение — текст варианта, либо TIMEOUT/ERROR."""
    token = _token()
    qid = make_question_id(question, now=time.time())
    journal(JOURNAL_PATH, "asked", qid=qid, question=question,
            context=context, options=options, timeout_s=timeout_s)

    if not token:
        journal(JOURNAL_PATH, "decided", qid=qid, decision=ERROR_DECISION,
                by="no_token")
        print(f"[ask_owner] нет токена в {TOKEN_ENV} — спросить НЕЧЕМ, "
              "считаю за отказ", file=sys.stderr)
        return ERROR_DECISION, "no_token"

    deadline = time.time() + timeout_s
    try:
        _api(token, "sendMessage", {
            "chat_id": OWNER_CHAT_ID,
            "text": build_text(question, context, deadline),
            "parse_mode": "Markdown",
            "reply_markup": build_keyboard(qid, options)})
    except Exception as exc:                 # noqa: BLE001
        journal(JOURNAL_PATH, "decided", qid=qid, decision=ERROR_DECISION,
                by=f"send_failed:{type(exc).__name__}")
        print(f"[ask_owner] вопрос НЕ ушёл ({exc}) — считаю за отказ",
              file=sys.stderr)
        return ERROR_DECISION, "send_failed"

    offset = _load_offset()
    while time.time() < deadline:
        try:
            resp = _api(token, "getUpdates",
                        {"offset": offset, "timeout": POLL_TIMEOUT_S,
                         "allowed_updates": ["callback_query"]})
            updates = resp.get("result") or []
        except Exception as exc:             # noqa: BLE001
            # Сетевой сбой опроса не отменяет вопрос: владелец мог уже нажать.
            print(f"[ask_owner] опрос сорвался ({exc}), повтор",
                  file=sys.stderr)
            time.sleep(2)
            continue
        offset = next_offset(updates, offset)
        _save_offset(offset)
        decision = parse_decision(updates, qid=qid,
                                  owner_chat_id=OWNER_CHAT_ID, options=options)
        if decision is not None:
            for upd in updates:
                cq = upd.get("callback_query")
                if cq:
                    try:
                        _api(token, "answerCallbackQuery",
                             {"callback_query_id": cq["id"],
                              "text": f"Принято: {decision}"}, timeout=10)
                    except Exception:
                        pass
            journal(JOURNAL_PATH, "decided", qid=qid, decision=decision,
                    by="telegram")
            return decision, "telegram"

    journal(JOURNAL_PATH, "decided", qid=qid, decision=TIMEOUT_DECISION,
            by="timeout")
    try:
        _api(token, "sendMessage", {
            "chat_id": OWNER_CHAT_ID,
            "text": "⌛ Ответа не было — считаю за НЕТ, действие не выполнено."},
            timeout=10)
    except Exception:
        pass
    return TIMEOUT_DECISION, "timeout"


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--question", required=True)
    p.add_argument("--option", action="append", dest="options", required=True,
                   help="вариант ответа; повторять по одному на кнопку")
    p.add_argument("--context", default=None,
                   help="КОМАНДА или суть — попадает в текст вопроса")
    p.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT_S)
    a = p.parse_args(argv)

    decision, by = ask(a.question, a.options, context=a.context,
                       timeout_s=a.timeout)
    print(decision)
    if decision == TIMEOUT_DECISION:
        return 2
    if decision == ERROR_DECISION:
        return 3
    print(f"[ask_owner] решение получено: {decision} ({by})", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
