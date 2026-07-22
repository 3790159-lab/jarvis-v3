"""Общий загрузчик секретов Jarvis (P1/P2, решение O1).

`.env.enc` → DPAPI-decrypt в память → environ; StringSession → `.enc`.
Plaintext-секретов на диске нет: ни `.env`, ни `.session` (спека §2.2/2.3).

Фолбэк на plaintext — ВРЕМЕННЫЙ инструмент поэтапной миграции O1 (сервисы
переезжают по одному), включается только явным параметром и удаляется в
конце Спринта 0. Молчаливого подхвата лежащего рядом plaintext нет — иначе
откат на незашифрованное прошёл бы незамеченным.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import MutableMapping

from chatter.security.crypto import (
    CryptoError, decrypt_from_file, encrypt_to_file,
)

log = logging.getLogger("chatter.security.secret_loader")


class SecretLoaderError(RuntimeError):
    """Секрет не загружен. Явная ошибка старта, не тихий фейл (DEV-18)."""


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
) -> dict[str, str]:
    """Грузит секреты в environ. Реальная переменная окружения всегда
    побеждает файл (прецедент load_api_credentials/env_bootstrap).

    Возвращает распарсенные значения файла (для диагностики/тестов)."""
    enc = Path(enc_path)
    if enc.exists():
        try:
            text = decrypt_from_file(enc).decode("utf-8-sig")
        except CryptoError as exc:
            raise SecretLoaderError(f"{enc}: {exc}") from exc
        values = parse_env_text(text)
    elif fallback_plaintext is not None and Path(fallback_plaintext).exists():
        # Временный фолбэк миграции O1 — шумим в лог, чтобы не прижился.
        log.warning(
            "plaintext-фолбэк секретов: %s (нет %s) — временно, до конца "
            "Спринта 0", fallback_plaintext, enc)
        values = parse_env_text(
            Path(fallback_plaintext).read_text(encoding="utf-8-sig"))
    else:
        raise SecretLoaderError(
            f"нет зашифрованных секретов {enc} (и plaintext-фолбэк не "
            "разрешён/не найден) — прогнать миграцию секретов, см. "
            "docs/chatter/P1P2_SPEC.md §6")
    for key, val in values.items():
        environ.setdefault(key, val)
    return values


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
