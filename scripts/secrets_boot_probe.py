#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""P1/P2 задача 4 (TEMP): boot-probe приёмки §8.1 — «AtStartup-процесс
расшифровал machine-scope+entropy секреты без участия владельца».

Гоняет ПРОДАКШН-пути расшифровки (chatter.security: load_env,
load_string_session) на ОДНОРАЗОВЫХ фикстурах в отдельном каталоге —
живые .secrets/.env/.session не трогаются никогда. Каталог фикстур закрыт
тем же ACL (SYSTEM+Administrators), что и боевой .secrets: probe заодно
доказывает, что S4U-таск под членом Administrators читает entropy сквозь
ACL (риск №1 cutover, спека §7).

Использование:
    python scripts/secrets_boot_probe.py --setup   # фикстуры + ACL (из сессии)
    python scripts/secrets_boot_probe.py           # прогон, append в лог

Регистрируется TEMP-таском AtStartup/S4U (register_secrets_boot_probe_TEMP.ps1).
Вердикт после ребута — state/secrets_probe/probe_result.log: строка с
временем, аптаймом (сек от бута), пользователем, срезом quser и OK/FAIL.
Probe не умирает молча: любой сбой = FAIL-строка в лог (DEV-18).
"""
import argparse
import ctypes
import datetime
import getpass
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DIR = ROOT / "state" / "secrets_probe"
RESULT_LOG_NAME = "probe_result.log"

# Ожидаемые значения фикстур — НЕ секреты (одноразовые тест-данные);
# сверка по ним отличает «расшифровалось в исходник» от «вернулся мусор».
_ENV_FIXTURE = b"PROBE_KEY=boot-probe-ok\n"
_ENV_EXPECT = ("PROBE_KEY", "boot-probe-ok")
_SESSION_FIXTURE = "probe-string-session"


def _ensure_import_root() -> None:
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))


def setup_fixtures(probe_dir, *, acl=None) -> list[str]:
    """Одноразовые фикстуры: свой entropy + тест-.enc; ACL на каталог.
    Идемпотентен: существующий entropy НЕ перезаписывается (иначе блобы
    стали бы нечитаемы), существующие .enc не трогаются."""
    _ensure_import_root()
    from chatter.security.crypto import (
        encrypt_to_file, generate_entropy, restrict_to_system_admins,
    )
    from chatter.security.secret_loader import save_string_session

    probe_dir = Path(probe_dir)
    probe_dir.mkdir(parents=True, exist_ok=True)
    report = []
    entropy = probe_dir / "entropy.bin"
    created = generate_entropy(entropy)
    report.append("entropy: " + ("создан" if created else "уже есть"))
    env_enc = probe_dir / "probe.env.enc"
    if not env_enc.exists():
        encrypt_to_file(env_enc, _ENV_FIXTURE, entropy_path=entropy)
        report.append(f"{env_enc.name}: создан")
    sess_enc = probe_dir / "probe.session.enc"
    if not sess_enc.exists():
        save_string_session(sess_enc, _SESSION_FIXTURE, entropy_path=entropy)
        report.append(f"{sess_enc.name}: создан")
    (acl or restrict_to_system_admins)(probe_dir)
    report.append("ACL: SYSTEM+Administrators, наследование срезано")
    return report


def _uptime_seconds() -> int:
    try:
        return int(ctypes.windll.kernel32.GetTickCount64() // 1000)
    except Exception:
        return -1


def _quser_snapshot() -> str:
    """Срез интерактивных сессий на момент прогона (доказательная база
    «до логона»). quser без сессий возвращает не-0 — это тоже ответ."""
    try:
        res = subprocess.run(["quser"], capture_output=True, text=True,
                             timeout=10)
        out = (res.stdout or res.stderr).strip().replace("\n", " / ")
        return out[:200] or "quser: пусто"
    except Exception as exc:
        return f"quser недоступен ({type(exc).__name__})"


def run_probe(probe_dir) -> tuple[bool, str]:
    """Прогон продакшн-путей расшифровки. Никогда не бросает: (ok, строка)."""
    ts = datetime.datetime.now().isoformat(timespec="seconds")
    prefix = (f"{ts} | uptime={_uptime_seconds()}s | "
              f"user={getpass.getuser()} | sessions=[{_quser_snapshot()}]")
    probe_dir = Path(probe_dir)
    try:
        _ensure_import_root()
        from chatter.security.secret_loader import (
            load_env, load_string_session,
        )
        entropy = probe_dir / "entropy.bin"
        if not entropy.exists():
            raise FileNotFoundError(f"нет entropy-файла {entropy}")
        environ: dict = {}
        load_env(probe_dir / "probe.env.enc", environ=environ,
                 entropy_path=entropy)
        key, expect = _ENV_EXPECT
        env_ok = environ.get(key) == expect
        sess_ok = load_string_session(
            probe_dir / "probe.session.enc",
            entropy_path=entropy) == _SESSION_FIXTURE
        if env_ok and sess_ok:
            return True, f"{prefix} | OK | env+session расшифрованы верно"
        return False, (f"{prefix} | FAIL | расшифровка дала не исходник: "
                       f"env_ok={env_ok} session_ok={sess_ok}")
    except Exception as exc:  # noqa: BLE001 - probe обязан оставить строку
        return False, f"{prefix} | FAIL | {type(exc).__name__}: {exc}"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dir", default=str(DEFAULT_DIR),
                    help="каталог фикстур probe (дефолт state/secrets_probe)")
    ap.add_argument("--setup", action="store_true",
                    help="создать фикстуры + ACL (запускать из сессии)")
    args = ap.parse_args(argv)
    probe_dir = Path(args.dir)
    if args.setup:
        for line in setup_fixtures(probe_dir):
            print(f"[secrets_boot_probe] {line}")
        return 0
    ok, line = run_probe(probe_dir)
    log = probe_dir / RESULT_LOG_NAME
    try:
        with open(log, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except Exception as exc:  # noqa: BLE001 - лог недоступен: хотя бы stdout
        print(f"[secrets_boot_probe] лог {log} недоступен: {exc}")
    print(line)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
