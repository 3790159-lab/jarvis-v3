"""Общий загрузчик секретов Jarvis (P1/P2, решение O1).

`.env.enc` → DPAPI-decrypt в память → environ; StringSession → `.enc`.
Plaintext-секретов на диске нет: ни `.env`, ни `.session` (спека §2.2/2.3).

Фолбэк на plaintext — ВРЕМЕННЫЙ инструмент поэтапной миграции O1 (сервисы
переезжают по одному), включается только явным параметром и удаляется в
конце Спринта 0. Молчаливого подхвата лежащего рядом plaintext нет — иначе
откат на незашифрованное прошёл бы незамеченным.
"""
from __future__ import annotations

import json
import logging
import sys
import urllib.request
from pathlib import Path
from typing import Callable, Mapping, MutableMapping

from chatter.security.crypto import (
    CryptoError, decrypt_from_file, encrypt_to_file,
)

log = logging.getLogger("chatter.security.secret_loader")

# Дефолтный получатель алертов — тот же, что у ops_watchdog.
_ADMIN_CHAT_ID = "237616472"


class SecretLoaderError(RuntimeError):
    """Секрет не загружен. Явная ошибка старта, не тихий фейл (DEV-18)."""


def _urllib_post(url: str, payload: bytes) -> None:
    req = urllib.request.Request(
        url, data=payload, headers={"Content-Type": "application/json"})
    urllib.request.urlopen(req, timeout=10).read()


def _send_fallback_alert(
    plaintext_path: Path,
    enc_path: Path,
    environ: Mapping[str, str],
    transport: Callable[[str, bytes], None],
) -> None:
    """Фолбэк ДОЛЖЕН КРИЧАТЬ (требование Даниила 2026-07-22): тихий фолбэк
    = «думаем, что зашифровано, а оно нет». Каждое фактическое использование
    plaintext → алерт в Telegram. Сбой алерта не роняет старт, но громко
    логируется (DEV-18) — блокировать загрузку секретов из-за сети = свой
    собственный отказ в обслуживании."""
    token = (environ.get("CHATTER_CONTROL_BOT_TOKEN")
             or environ.get("TELEGRAM_BOT_TOKEN"))
    if not token:
        log.error(
            "PLAINTEXT-ФОЛБЭК СЕКРЕТОВ БЕЗ АЛЕРТА: нет TELEGRAM_BOT_TOKEN/"
            "CHATTER_CONTROL_BOT_TOKEN — некому кричать про %s",
            plaintext_path)
        return
    chat_id = environ.get("JARVIS_ADMIN_CHAT_ID", _ADMIN_CHAT_ID)
    text = (
        "🔓⚠️ СЕКРЕТЫ ИЗ PLAINTEXT-ФОЛБЭКА\n"
        f"Процесс: {Path(sys.argv[0]).name or 'python'}\n"
        f"Нет: {enc_path}\n"
        f"Загружено из plaintext: {plaintext_path}\n"
        "Это ВРЕМЕННЫЙ режим миграции O1 — если видишь этот алерт после "
        "конца Спринта 0, шифрование НЕ работает (P1P2_SPEC §6)."
    )
    payload = json.dumps({"chat_id": chat_id, "text": text}).encode("utf-8")
    try:
        transport(f"https://api.telegram.org/bot{token}/sendMessage", payload)
    except Exception as exc:  # noqa: BLE001 - любой сбой алерта: лог, не крэш
        log.error("алерт о plaintext-фолбэке не отправлен: %s", exc)


def parse_env_text(text: str) -> dict[str, str]:
    """Толерантный KEY=VALUE парсер — та же семантика, что
    telethon_login._parse_env_file / app\\env_bootstrap._manual_load
    (пропуск пустых/комментариев, срез кавычек), но по строке из памяти:
    расшифрованный .env на диск не пишется никогда."""
    values: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, val = line.split("=", 1)
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        if key:
            values[key] = val
    return values


def load_env(
    enc_path: str | Path,
    *,
    environ: MutableMapping[str, str],
    fallback_plaintext: str | Path | None = None,
    alert_transport: Callable[[str, bytes], None] = _urllib_post,
) -> dict[str, str]:
    """Грузит секреты в environ. Реальная переменная окружения всегда
    побеждает файл (прецедент load_api_credentials/env_bootstrap).

    Возвращает распарсенные значения файла (для диагностики/тестов)."""
    enc = Path(enc_path)
    used_fallback: Path | None = None
    if enc.exists():
        try:
            text = decrypt_from_file(enc).decode("utf-8-sig")
        except CryptoError as exc:
            raise SecretLoaderError(f"{enc}: {exc}") from exc
        values = parse_env_text(text)
    elif fallback_plaintext is not None and Path(fallback_plaintext).exists():
        # Временный фолбэк миграции O1 — шумим в лог И кричим в Telegram
        # (после применения values: токен для крика может лежать в них же).
        used_fallback = Path(fallback_plaintext)
        log.warning(
            "plaintext-фолбэк секретов: %s (нет %s) — временно, до конца "
            "Спринта 0", fallback_plaintext, enc)
        values = parse_env_text(used_fallback.read_text(encoding="utf-8-sig"))
    else:
        raise SecretLoaderError(
            f"нет зашифрованных секретов {enc} (и plaintext-фолбэк не "
            "разрешён/не найден) — прогнать миграцию секретов, см. "
            "docs/chatter/P1P2_SPEC.md §6")
    for key, val in values.items():
        environ.setdefault(key, val)
    if used_fallback is not None:
        _send_fallback_alert(used_fallback, enc, environ, alert_transport)
    return values


# Явный opt-in plaintext-режима (задача 5 слоя процесса): выставляется
# оператором осознанно (сервис ещё не мигрирован, O1 этап 2). Каждый старт
# в этом режиме кричит в Telegram; снимается в конце Спринта 0.
PLAINTEXT_OPTIN_ENV = "JARVIS_ALLOW_PLAINTEXT_ENV"


def bootstrap_env(
    env_file: str | Path,
    *,
    environ: MutableMapping[str, str],
    alert_transport: Callable[[str, bytes], None] = _urllib_post,
) -> dict[str, str]:
    """Стартовая точка процесса Jarvis (§2.1/§2.3): секреты с диска — в
    память процесса, plaintext на диск не ложится.

    Порядок (тихих plaintext-путей НЕТ):
    1. `<env_file>.enc` есть → decrypt в environ. Ошибка decrypt/entropy —
       явная ошибка старта.
    2. Только plaintext → авто-миграция §4.5 (зашифровать в .enc, plaintext
       оставить бэкапом до cutover п.9) и грузиться уже из .enc. Нет
       entropy → явная ошибка с ремедиацией, НЕ тихий plaintext.
       Исключение: явный opt-in `JARVIS_ALLOW_PLAINTEXT_ENV=1` → plaintext
       в память + громкий TG-алерт на каждый старт (сервисы O1 этапа 2).
    3. Нет ни того ни другого → no-op: ключи могут жить в реальном environ
       (прецедент load_api_credentials: missing .env = нет фолбэка).
    """
    plain = Path(env_file)
    enc = Path(str(env_file) + ".enc")
    optin = environ.get(PLAINTEXT_OPTIN_ENV) == "1"
    if not enc.exists() and plain.exists():
        if optin:
            # Осознанный plaintext-режим: НЕ шифруем сам (миграция — решение
            # оператора), грузим plaintext через громкий путь load_env.
            return load_env(enc, environ=environ, fallback_plaintext=plain,
                            alert_transport=alert_transport)
        try:
            migrate_plaintext_file(plain, enc)
        except CryptoError as exc:
            raise SecretLoaderError(
                f"авто-миграция {plain} -> {enc} не удалась: {exc}. "
                "Setup-шаг cutover: chatter.security.crypto."
                "generate_entropy() + ACL (P1P2_SPEC §6 п.4); аварийный "
                f"plaintext-режим: {PLAINTEXT_OPTIN_ENV}=1 (кричит в TG)."
            ) from exc
        log.warning("P1/P2-миграция: %s -> %s (plaintext оставлен бэкапом "
                    "до cutover п.9)", plain, enc)
    if not enc.exists():
        log.info("bootstrap_env: нет ни %s, ни %s — секреты ожидаются в "
                 "реальном environ", enc, plain)
        return {}
    return load_env(enc, environ=environ, alert_transport=alert_transport)


def derive_session_enc_path(session_path: str) -> str:
    """`.secrets/<slug>.session` → `.secrets/<slug>.session.enc`. Живёт здесь
    (не в telethon_run), чтобы telethon_login мог импортировать без цикла."""
    return session_path + ".enc"


def save_string_session(enc_path: str | Path, session: str) -> None:
    """StringSession → DPAPI → .enc. Plaintext-сессии на диске нет никогда."""
    p = Path(enc_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    encrypt_to_file(p, session.encode("utf-8"))


def load_string_session(enc_path: str | Path) -> str:
    p = Path(enc_path)
    if not p.exists():
        raise SecretLoaderError(
            f"нет зашифрованной сессии {p} — залогиниться через "
            "telethon_login (сохранит .enc) или мигрировать legacy-сессию "
            "(P1P2_SPEC §6 п.4)")
    try:
        return decrypt_from_file(p).decode("utf-8")
    except CryptoError as exc:
        raise SecretLoaderError(f"{p}: {exc}") from exc


def migrate_plaintext_file(plaintext_path: str | Path,
                           enc_path: str | Path) -> bool:
    """Одноразовая миграция legacy plaintext → .enc (спека §4.5).

    Plaintext НЕ удаляется — он бэкап отката до cutover п.8 (шред руками
    после верификации). Существующий .enc не затирается (повтор = no-op).
    Возвращает True, если миграция выполнена."""
    plain, enc = Path(plaintext_path), Path(enc_path)
    if enc.exists():
        return False
    if not plain.exists():
        raise SecretLoaderError(
            f"мигрировать нечего: нет ни {enc}, ни plaintext {plain}")
    encrypt_to_file(enc, plain.read_bytes())
    return True
