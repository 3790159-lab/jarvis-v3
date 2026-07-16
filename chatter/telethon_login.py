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


def load_api_credentials(env: Mapping[str, str], env_file_path: Path) -> tuple[str, str]:
    """Resolves (TELEGRAM_API_ID, TELEGRAM_API_HASH). A real environment
    variable always wins over the .env fallback (matches
    app/env_bootstrap.py's precedence); each of the two keys is resolved
    independently, so a partially-exported environment still falls back to
    .env for whichever one is missing. Raises CredentialsError with a clear,
    actionable message if either is still missing after both sources."""
    api_id = env.get("TELEGRAM_API_ID")
    api_hash = env.get("TELEGRAM_API_HASH")

    if not api_id or not api_hash:
        file_values = _parse_env_file(Path(env_file_path))
        api_id = api_id or file_values.get("TELEGRAM_API_ID")
        api_hash = api_hash or file_values.get("TELEGRAM_API_HASH")

    missing = [
        name for name, val in (("TELEGRAM_API_ID", api_id), ("TELEGRAM_API_HASH", api_hash))
        if not val
    ]
    if missing:
        raise CredentialsError(
            f"missing {', '.join(missing)} -- set them in the environment or in "
            f"{env_file_path} (get an API ID/hash for your account at "
            "https://my.telegram.org/apps)"
        )
    return api_id, api_hash


def resolve_session_path(env: Mapping[str, str]) -> str:
    """`TELETHON_SESSION` env override, else the same default telethon_run.py
    uses -- so a login done with defaults and a run done with defaults talk
    to the same session file."""
    return env.get("TELETHON_SESSION", DEFAULT_SESSION_PATH)


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
        from telethon import TelegramClient as client_cls  # noqa: N806
    return client_cls(session_path, int(api_id), api_hash)


def main(argv: list[str] | None = None) -> int:
    # Same rationale as chatter/run.py and chatter/telethon_run.py's main():
    # Windows consoles default to a legacy codepage that silently mangles
    # non-ASCII output instead of raising.
    for _stream in (sys.stdin, sys.stdout):
        if hasattr(_stream, "reconfigure"):
            _stream.reconfigure(encoding="utf-8")

    try:
        api_id, api_hash = load_api_credentials(os.environ, DEFAULT_ENV_FILE)
    except CredentialsError as e:
        print(f"[telethon_login] {e}", file=sys.stderr)
        return 1

    session_path = resolve_session_path(os.environ)
    Path(session_path).parent.mkdir(parents=True, exist_ok=True)

    client = make_client(session_path, api_id, api_hash)

    print(f"[telethon_login] session file: {session_path}")
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
    print(
        f"[telethon_login] session saved to: {session_path} -- keep this file secret, "
        "it grants FULL access to this Telegram account. It is already gitignored "
        "(see /.secrets/ in the repo-root .gitignore) -- never commit it."
    )
    client.disconnect()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
