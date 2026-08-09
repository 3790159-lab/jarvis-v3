"""Отправитель реплик лида (стенд v2, Э2).

Отдельный процесс с СОБСТВЕННОЙ (тестовой) сессией. Умеет ровно одно: сказать
текст в один заранее разрешённый чат. Сценария не знает, апдейты не читает,
решений не принимает — судья это `drill_runner.py`.

🔴 ИНВАРИАНТ v2 (спека §3): боевая сессия и сессия лида НИКОГДА не живут в
одном процессе. Свести их значит однажды отправить реплику лида из аккаунта
Ольги — на живом клиенте это необратимо, сообщение уже увидели. Здесь это
предохранитель `DRILL_SESSION_STEMS`, срабатывающий ДО открытия сессии.

Протокол (stdin, по строке на команду):
    SAY <текст>   → пауза, отправка, печать `SENT <unix_ts>`
    QUIT          → выход 0
EOF без QUIT — тоже выход 0 (оркестратор гасит лида в `finally`).

Предохранители (все с тестами, `tests/test_drill_lead.py`):
  1. peer-allowlist: `--peer` обязан быть в списке разрешённых; пустой список
     = отказ, а не «разрешено всё» (молчаливый дефолт — класс бага, P17);
  2. только текст: ни файлов, ни медиа, ни пересылок — нечем;
  3. потолок сообщений на прогон (`--max-messages`, дефолт 15);
  4. человеческие паузы 5–25 с перед КАЖДОЙ отправкой;
  5. своя сессия, только зашифрованная (`.enc`), 2FA на аккаунте.

    python scripts/drill_lead.py --session .secrets/drill_lead.session --peer <id>
"""
from __future__ import annotations

import argparse
import random
import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

from chatter.security.secret_loader import (  # noqa: E402
    derive_session_enc_path, load_string_session,
)
from chatter.telethon_login import load_api_credentials  # noqa: E402

# Какие сессии этому процессу вообще разрешено открывать. Список ПО ИМЕНИ
# файла, потому что путь можно передать любой: `--session .secrets/demo.session`
# запустил бы лида из аккаунта клиента, и первая же реплика сценария ушла бы
# живому лиду от имени Ольги.
DRILL_SESSION_STEMS = frozenset({"drill_lead"})

# Кому лиду разрешено писать. В коде пусто НАМЕРЕННО: id — это живой человек,
# а диапазоны коммитов сканируются на 9–12-значные id перед пушем (и однажды
# id живого клиента уже вычищали из спеки, `3df83f92`). Фактический список
# лежит рядом с секретами и в git не попадает; формат — по одному id в
# строке, `#` — коммент.
ALLOWED_PEERS: frozenset[int] = frozenset()
PEERS_FILE = Path(".secrets") / "drill_lead_peers.txt"

DEFAULT_MAX_MESSAGES = 15
PAUSE_MIN, PAUSE_MAX = 5.0, 25.0


class LeadError(Exception):
    """Всё, из-за чего лид обязан не отправить ничего и выйти ненулевым."""


# ── разрешения ───────────────────────────────────────────────────────────────


def load_allowed_peers(root: Path, *, code_peers=ALLOWED_PEERS) -> frozenset[int]:
    """Разрешённые получатели = список в коде ∪ список рядом с секретами.

    Мусорная строка — ГРОМКАЯ ошибка: молча пропустить её значит однажды
    получить пустой список там, где владелец был уверен, что id вписан."""
    peers = set(code_peers)
    path = Path(root) / PEERS_FILE
    if path.is_file():
        for n, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            line = raw.split("#", 1)[0].strip()
            if not line:
                continue
            try:
                peers.add(int(line))
            except ValueError:
                raise LeadError(
                    f"{path}:{n}: «{line}» — не id. Файл разрешений читается "
                    f"целиком или не читается вовсе") from None
    return frozenset(peers)


def check_peer_allowed(peer: int, allowed: frozenset[int]) -> None:
    if not allowed:
        raise LeadError(
            f"список разрешённых получателей ПУСТ (ни в коде, ни в "
            f"{PEERS_FILE}). Отправлять некому: пустой список — это не "
            f"«можно всем»")
    if peer not in allowed:
        raise LeadError(
            f"peer {peer} не в списке разрешённых — отказ. Опечатка в id не "
            f"должна становиться сообщением незнакомому человеку")


def check_session_allowed(session_path: str | Path) -> Path:
    """Сессия обязана быть дрил-сессией и обязана быть зашифрованной."""
    p = Path(session_path)
    stem = p.name[:-len(".session")] if p.name.endswith(".session") else p.stem
    if stem not in DRILL_SESSION_STEMS:
        raise LeadError(
            f"сессия «{stem}» не дрил-сессия (разрешены: "
            f"{', '.join(sorted(DRILL_SESSION_STEMS))}). Лид НИКОГДА не "
            f"работает боевой сессией клиента — инвариант v2 §3")
    enc = Path(derive_session_enc_path(str(p)))
    if not enc.is_file():
        raise LeadError(
            f"нет зашифрованной сессии {enc} — залогинить тестовый аккаунт "
            f"через telethon_login (сохранит .enc). Plaintext-сессии у лида "
            f"не бывает")
    return enc


# ── протокол ─────────────────────────────────────────────────────────────────


def parse_command(line: str) -> tuple[str, str]:
    """`SAY <текст>` / `QUIT`. Регистр значим — команды приходят от кода, а не
    от человека, и «say» скорее опечатка оркестратора, чем намерение."""
    s = line.strip()
    if s == "QUIT":
        return ("QUIT", "")
    if s.startswith("SAY "):
        text = s[len("SAY "):].strip()
        if not text:
            raise LeadError("SAY без текста")
        return ("SAY", text)
    raise LeadError(f"неизвестная команда: «{s[:40]}»")


def human_pause(rnd: random.Random, *, lo: float, hi: float) -> float:
    return rnd.uniform(lo, hi)


# ── живой шов (единственное место, где есть сеть) ────────────────────────────


class _TelethonSender:
    """Открывает СВОЮ сессию и умеет только `send_message` в один peer."""

    def __init__(self, enc_path: Path, peer: int):
        # telethon.sync (а не голый telethon) — тот же приём, что в
        # telethon_login: вызовы становятся блокирующими, и лид остаётся
        # простым stdin-циклом без своего event-loop.
        import os

        from telethon.sessions import StringSession
        from telethon.sync import TelegramClient
        from chatter.telethon_identity import identity_kwargs
        api_id, api_hash = load_api_credentials(os.environ, None)
        # Тот же прибитый отпечаток, что у прод-раннера: аккаунт тестового лида
        # тоже не должен «переезжать на новое устройство» при смене хоста.
        self._client = TelegramClient(
            StringSession(load_string_session(enc_path)), int(api_id), api_hash,
            **identity_kwargs())
        self._peer = peer
        self._client.connect()
        if not self._client.is_user_authorized():
            raise LeadError("сессия лида не авторизована — перелогинить аккаунт")

    def send(self, text: str) -> int:
        msg = self._client.send_message(self._peer, text)
        return int(getattr(msg, "id", 0))

    def close(self) -> None:
        try:
            self._client.disconnect()
        except Exception as exc:                      # noqa: BLE001 — DEV-18
            print(f"[lead] disconnect: {exc}", file=sys.stderr, flush=True)


# ── цикл ─────────────────────────────────────────────────────────────────────


def main(argv=None, *, stdin=None, sender=None, sleep=None, out=None,
         root=None, rnd=None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    ap = argparse.ArgumentParser(description="Отправитель реплик лида (Э2).")
    ap.add_argument("--session", required=True)
    ap.add_argument("--peer", required=True, type=int)
    ap.add_argument("--max-messages", type=int, default=DEFAULT_MAX_MESSAGES)
    ap.add_argument("--pause-min", type=float, default=PAUSE_MIN)
    ap.add_argument("--pause-max", type=float, default=PAUSE_MAX)
    a = ap.parse_args(argv)

    stdin = stdin if stdin is not None else sys.stdin
    sleep = sleep if sleep is not None else time.sleep
    rnd = rnd if rnd is not None else random.Random()
    root = Path(root) if root is not None else _ROOT

    def emit(text: str) -> None:
        if out is None:
            print(text, flush=True)
        else:
            out.write(text + "\n")

    try:
        if a.pause_min > a.pause_max:
            raise LeadError(f"--pause-min {a.pause_min} > --pause-max {a.pause_max}")
        if a.pause_min < 0:
            raise LeadError("--pause-min отрицательный")
        if a.max_messages < 1:
            raise LeadError("--max-messages меньше единицы")
        enc = check_session_allowed(a.session)
        check_peer_allowed(a.peer, load_allowed_peers(root))
    except LeadError as exc:
        emit(f"[lead] FAIL: {exc}")
        return 2

    if sender is None:
        try:
            sender = _TelethonSender(enc, a.peer)
        except LeadError as exc:
            emit(f"[lead] FAIL: {exc}")
            return 2
    emit(f"[lead] готов: peer {a.peer}, потолок {a.max_messages} сообщ., "
         f"паузы {a.pause_min:g}–{a.pause_max:g} с")

    code = 0
    sent = 0
    try:
        for raw in stdin:
            if not raw.strip():
                continue
            try:
                cmd, text = parse_command(raw)
            except LeadError as exc:
                emit(f"[lead] FAIL: {exc}")
                code = 2
                break
            if cmd == "QUIT":
                break
            if sent >= a.max_messages:
                # Потолок держит зацикленного оркестратора: флуд с тестового
                # аккаунта ловят баном ОБА аккаунта, включая клиентский.
                emit(f"[lead] FAIL: потолок {a.max_messages} сообщений "
                     f"исчерпан, реплика НЕ отправлена")
                code = 2
                break
            pause = human_pause(rnd, lo=a.pause_min, hi=a.pause_max)
            sleep(pause)
            try:
                mid = sender.send(text)
            except Exception as exc:                  # noqa: BLE001 — DEV-18
                emit(f"[lead] FAIL: отправка не удалась: {exc}")
                code = 2
                break
            sent += 1
            emit(f"SENT {time.time():.3f} msg_id={mid}")
    finally:
        sender.close()
    emit(f"[lead] закрыт: отправлено {sent}")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
