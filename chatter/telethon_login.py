from __future__ import annotations
import os
import sys
from pathlib import Path
from typing import Mapping

# Repo-root .env, the same file every other jarvis entry point reads from --
# so `python -m chatter.telethon_login` just works without the user having to
# `export` TELEGRAM_API_ID/TELEGRAM_API_HASH by hand first (spec S2).
DEFAULT_ENV_FILE = Path(__file__).resolve().parent.parent / ".env"
DEFAULT_SESSION_PATH = str(Path(".secrets") / "chatter_telethon.session")


class CredentialsError(RuntimeError):
    """Raised when TELEGRAM_API_ID / TELEGRAM_API_HASH can't be resolved
    from either the environment or the .env fallback."""


def _parse_env_file(path: Path) -> dict[str, str]:
    """Minimal, tolerant KEY=VALUE .env parser (same shape as
    app/env_bootstrap.py's `_manual_load`): skips blank lines and comments,
    strips surrounding quotes/CR from values. Returns {} if the file doesn't
    exist -- callers treat a missing .env as "no fallback available", not an
    error (the real env might already have everything it needs)."""
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, val = line.split("=", 1)
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        if key:
            values[key] = val
    return values


def load_api_credentials(env: Mapping[str, str],
                         env_file_path: Path | None) -> tuple[str, str]:
    """Resolves (TELEGRAM_API_ID, TELEGRAM_API_HASH). A real environment
    variable always wins over the .env fallback (matches
    app/env_bootstrap.py's precedence); each of the two keys is resolved
    independently, so a partially-exported environment still falls back to
    .env for whichever one is missing. Raises CredentialsError with a clear,
    actionable message if either is still missing after both sources.

    env_file_path=None ОТКЛЮЧАЕТ файловый фолбэк: после успешного
    bootstrap_env из .enc чтение лежащего рядом plaintext-бэкапа было бы
    тихим plaintext-путём (P1P2, слой процесса) — неполный .enc обязан
    давать явную ошибку, а не молча доукомплектовываться из .env."""
    api_id = env.get("TELEGRAM_API_ID")
    api_hash = env.get("TELEGRAM_API_HASH")

    if (not api_id or not api_hash) and env_file_path is not None:
        file_values = _parse_env_file(Path(env_file_path))
        api_id = api_id or file_values.get("TELEGRAM_API_ID")
        api_hash = api_hash or file_values.get("TELEGRAM_API_HASH")

    missing = [
        name for name, val in (("TELEGRAM_API_ID", api_id), ("TELEGRAM_API_HASH", api_hash))
        if not val
    ]
    if missing:
        source_hint = (f"in {env_file_path}" if env_file_path is not None
                       else "in .env.enc (шифрованные секреты, P1P2)")
        raise CredentialsError(
            f"missing {', '.join(missing)} -- set them in the environment or "
            f"{source_hint} (get an API ID/hash for your account at "
            "https://my.telegram.org/apps)"
        )
    return api_id, api_hash


def resolve_session_path(env: Mapping[str, str]) -> str:
    """`TELETHON_SESSION` env override, else the same default telethon_run.py
    uses -- so a login done with defaults and a run done with defaults talk
    to the same session file."""
    return env.get("TELETHON_SESSION", DEFAULT_SESSION_PATH)


def _live_session_to_string(session) -> str:
    from telethon.sessions import StringSession
    return StringSession.save(session)


def persist_login_session(client_session, enc_path: str,
                          *, session_to_string=None) -> str:
    """После интерактивного логина: сессия из памяти → StringSession-строка
    → DPAPI `.enc`. Plaintext `.session` на диск не пишется никогда
    (P1P2_SPEC §2.2). Пустая строка = логин не дал auth_key → явная ошибка,
    а не тихий пустой .enc."""
    from chatter.security.secret_loader import (
        SecretLoaderError, save_string_session,
    )
    string = (session_to_string or _live_session_to_string)(client_session)
    if not string:
        raise SecretLoaderError(
            "логин не дал auth_key — сессия пустая, сохранять нечего "
            "(логин прерван до кода/2FA?)")
    save_string_session(enc_path, string)
    return str(enc_path)


def make_client(session_path: str, api_id: str, api_hash: str, client_cls=None):
    """Constructs the Telethon client. `client_cls` is injectable so tests
    can assert (session_path, api_id, api_hash) were passed through correctly
    using a plain mock class, with NO real TelegramClient/network involved.
    Production callers (main(), below) leave it as the default, which
    deferred-imports the real `telethon.TelegramClient` -- importing telethon
    itself is inert (no network), but keeping the import inside a function
    matches telethon_run.py's convention of only ever constructing a real
    client from the interactive entry point."""
    if client_cls is None:
        # telethon.sync (not plain telethon) so this script's client.get_me()
        # / client.disconnect() run synchronously instead of returning
        # un-awaited coroutines (which printed "? (@None) id=?"). Only affects
        # THIS process; telethon_run.py imports plain telethon for its async loop.
        from telethon.sync import TelegramClient as client_cls  # noqa: N806
    return client_cls(session_path, int(api_id), api_hash)


def main(argv: list[str] | None = None) -> int:
    # Same rationale as chatter/run.py and chatter/telethon_run.py's main():
    # Windows consoles default to a legacy codepage that silently mangles
    # non-ASCII output instead of raising.
    for _stream in (sys.stdin, sys.stdout):
        if hasattr(_stream, "reconfigure"):
            _stream.reconfigure(encoding="utf-8")

    # P1/P2 слой процесса (§2.1/§2.3): секреты с диска — через .enc в память.
    # Битый секрет-слой (нет entropy, tamper) = явный отказ, не тихий plaintext.
    from chatter.security.secret_loader import SecretLoaderError, bootstrap_env
    try:
        loaded = bootstrap_env(DEFAULT_ENV_FILE, environ=os.environ)
    except SecretLoaderError as e:
        print(f"[telethon_login] {e}", file=sys.stderr)
        return 1

    try:
        # Файловый .env-фолбэк остаётся ТОЛЬКО когда bootstrap ничего не
        # загрузил (нет ни .enc, ни .env): тогда он всё равно читает пустоту,
        # но даёт привычное actionable-сообщение об ошибке.
        api_id, api_hash = load_api_credentials(
            os.environ, None if loaded else DEFAULT_ENV_FILE)
    except CredentialsError as e:
        print(f"[telethon_login] {e}", file=sys.stderr)
        return 1

    session_path = resolve_session_path(os.environ)
    Path(session_path).parent.mkdir(parents=True, exist_ok=True)

    # P1/P2: логин идёт в StringSession В ПАМЯТИ — plaintext .session на
    # диске не появляется даже на время логина (P1P2_SPEC §2.2).
    from telethon.sessions import StringSession
    from chatter.security.secret_loader import derive_session_enc_path
    enc_path = derive_session_enc_path(session_path)
    client = make_client(StringSession(), api_id, api_hash)

    print(f"[telethon_login] encrypted session target: {enc_path}")
    print(
        "[telethon_login] Telethon will now ask for your phone number, then the "
        "login code sent to Telegram, then your 2FA password if you have one set. "
        "Answer the prompts below."
    )
    client.start()  # Telethon drives the phone/code/2FA prompts itself; not reimplemented here.

    me = client.get_me()
    display_name = " ".join(
        part for part in (getattr(me, "first_name", None), getattr(me, "last_name", None)) if part
    ) or "?"
    username = getattr(me, "username", None)
    user_id = getattr(me, "id", "?")
    print(f"[telethon_login] logged in as: {display_name} (@{username}) id={user_id}")
    persist_login_session(client.session, enc_path)
    print(
        f"[telethon_login] session saved ENCRYPTED to: {enc_path} -- DPAPI "
        "machine-scope + entropy (рев. 2): расшифровывается только на этой "
        "машине процессом с доступом к entropy-файлу (ACL SYSTEM+Admins). "
        "Plaintext .session не создавался. Дальше ОБЯЗАТЕЛЬНО: "
        "экспорт бэкапа секретов (P1P2_SPEC §12 / ONBOARDING_MANUAL §1)."
    )
    client.disconnect()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
