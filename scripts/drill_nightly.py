"""Регресс дрила по расписанию (стенд v2, Э5b).

Между Планировщиком и живым прогоном: проверяет, что прогон вообще имеет
право состояться, готовит предсказуемый старт (сброс дрил-контакта) и зовёт
`drill_runner --auto-lead`. Вердикт уходит владельцу в Telegram — молчаливый
ночной прогон бесполезен: о поломке узнают из тишины.

Зачем ночной режим вообще, если есть гейт перед мержем: гейт ловит НАШИ
правки, а классификатор 26.07 упал не от правки. Дрейф модели и внешние
поломки видит только регулярный живой прогон.

🔴 ПОРЯДОК ВАЖЕН: все отказы — ДО сброса. Сброс стирает переписку контакта,
и упереться в пустой allowlist после него значит потерять состояние впустую.

Окно: решение владельца (спека §11 п.2) — прогон только внутри рабочих часов
персоны. Оговорка к спеке: ночью бот НЕ молчит, `is_night` лишь умножает
паузы чтения на `night_multiplier` (2.5 у volska) — то есть ночной прогон не
упёрся бы в тишину, он просто был бы медленнее и меньше похож на живой
трафик. Окно держим как решение владельца, а не как техническое условие.

    python scripts/drill_nightly.py            # всё по умолчанию (volska/Д-10)
"""
from __future__ import annotations

import argparse
import importlib.util
import logging
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

logger = logging.getLogger("jarvis.drill_nightly")

TZ = ZoneInfo("Europe/Kyiv")
DEFAULT_CLIENT_DIR = _ROOT / "chatter" / "clients" / "volska"
DEFAULT_DB = _ROOT / ".secrets" / "demo.db"
DEFAULT_SCENARIO = _ROOT / "docs" / "chatter" / "drills" / "d10-obligations.yaml"
DEFAULT_CONTACT = "telegram:8849893367:volska"
PEERS_FILE = Path(".secrets") / "drill_lead_peers.txt"
LEAD_SESSION = Path(".secrets") / "drill_lead.session"

# Коды: 0/1/2 — вердикт прогона (как у харнесса), 3 — прогона не было по
# расписанию. Три отделено намеренно: «не время» это не «провалился».
CODE_OUT_OF_WINDOW = 3
CODE_REFUSED = 2


def say(text: str) -> None:
    print(text, flush=True)


def send_telegram(text: str) -> bool:
    """Best-effort уведомление владельцу; повторяет контракт digest-джоб —
    сама спрашивает guard изоляции, иначе тесты слали бы живые сообщения."""
    import os
    import urllib.parse
    import urllib.request
    try:
        from app.core.notify_isolation import telegram_send_blocked
        if telegram_send_blocked():
            logger.info("drill_nightly: send suppressed under test isolation")
            return False
    except Exception:
        logger.info("drill_nightly: isolation guard unavailable — not sending")
        return False
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_ALLOWED_CHAT_ID", "").strip()
    if not token or not chat_id:
        logger.warning("drill_nightly: no TELEGRAM_BOT_TOKEN/CHAT_ID — skipping")
        return False
    try:
        data = urllib.parse.urlencode({"chat_id": chat_id, "text": text}).encode()
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{token}/sendMessage", data=data)
        with urllib.request.urlopen(req, timeout=20) as resp:
            return 200 <= resp.status < 300
    except Exception as exc:                          # DEV-18: не молча
        logger.warning("drill_nightly: telegram send failed: %s", exc)
        return False


def _run_cmd(cmd) -> tuple[int, str]:
    p = subprocess.run([str(c) for c in cmd], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def in_window(hour: int, *, start: int, end: int) -> bool:
    """Та же арифметика, что у `humanizer.is_night`: [start, end)."""
    return start <= hour < end


def read_work_hours(client_dir: Path) -> tuple[int, int]:
    """work_hours из settings.yaml. Блок необязателен (онбординг-дырка №1) —
    без него окна нет, и это ЯВНЫЙ отказ, а не «круглосуточно»."""
    import yaml
    raw = yaml.safe_load((Path(client_dir) / "settings.yaml").read_text(
        encoding="utf-8")) or {}
    wh = raw.get("work_hours")
    if not isinstance(wh, dict) or "start" not in wh or "end" not in wh:
        raise ValueError(f"{client_dir}/settings.yaml: нет work_hours — окно "
                         f"прогона неизвестно")
    return int(wh["start"]), int(wh["end"])


def _drill_contacts() -> frozenset[str]:
    path = Path(__file__).resolve().parent / "drill_reset.py"
    spec = importlib.util.spec_from_file_location("_dn_drill_reset", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.DRILL_CONTACTS


def resolve_peer(root: Path, explicit: int | None) -> int:
    """Кому пишет лид. Без явного `--lead-peer` — единственный id из файла
    разрешений: догадываться, кому из двух слать, скрипт не имеет права, а
    держать id в командной строке таска значит светить его в XML и в списке
    процессов."""
    path = Path(root) / PEERS_FILE
    if not path.is_file():
        raise ValueError(f"нет файла разрешений {path} — некому слать")
    peers = []
    for n, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        try:
            peers.append(int(line))
        except ValueError:
            raise ValueError(f"{path}:{n}: «{line}» — не id") from None
    if not peers:
        raise ValueError(f"{path} пуст — вписать id аккаунта, которому пишет "
                         f"тестовый лид (по одному в строке)")
    if explicit is not None:
        if explicit not in peers:
            raise ValueError(f"--lead-peer {explicit} не в {path}")
        return explicit
    if len(peers) > 1:
        raise ValueError(f"{path}: разрешённых получателей {len(peers)} — "
                         f"задать --lead-peer явно")
    return peers[0]


def main(argv=None, *, run=None, hour=None, send=None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    ap = argparse.ArgumentParser(description="Регресс дрила по расписанию (Э5b).")
    ap.add_argument("--root", default=str(_ROOT))
    ap.add_argument("--client-dir", default=str(DEFAULT_CLIENT_DIR))
    ap.add_argument("--db", default=str(DEFAULT_DB))
    ap.add_argument("--scenario", default=str(DEFAULT_SCENARIO))
    ap.add_argument("--contact", default=DEFAULT_CONTACT)
    ap.add_argument("--lead-peer", type=int, default=None)
    ap.add_argument("--step-timeout", type=float, default=600.0)
    a = ap.parse_args(argv)

    if send is None:
        # Живой прогон под Планировщиком: env таска собирается из реестра, а
        # токен пульта лежит в .env.enc — без bootstrap'а вердикт уехал бы «в
        # логи», то есть никуда. Тесты передают свой send и сюда не заходят.
        try:
            from chatter.security.secret_loader import bootstrap_env
            bootstrap_env(_ROOT / ".env", environ=__import__("os").environ)
        except Exception as exc:                      # noqa: BLE001 — DEV-18
            say(f"⚠️ секреты не загрузились ({exc}) — вердикт может не уйти в TG")
    run = run or _run_cmd
    send = send or send_telegram
    root = Path(a.root)
    now_hour = hour if hour is not None else datetime.now(TZ).hour

    # 1. окно — раньше всего: вне окна не тратим ни цента и ничего не стираем.
    try:
        start, end = read_work_hours(Path(a.client_dir))
    except Exception as exc:                          # noqa: BLE001 — DEV-18
        say(f"⛔ ОТКАЗ: {exc}")
        return CODE_REFUSED
    if not in_window(now_hour, start=start, end=end):
        say(f"⏭️ вне рабочего окна {start}:00–{end}:00 (сейчас {now_hour}:xx) — "
            f"прогон не запускался")
        return CODE_OUT_OF_WINDOW

    # 2. предполётные — ВСЕ до сброса.
    try:
        if a.contact not in _drill_contacts():
            raise ValueError(f"контакт «{a.contact}» не дрил-контакт")
        if not Path(a.scenario).is_file():
            raise ValueError(f"нет сценария {a.scenario}")
        session = root / LEAD_SESSION
        if not Path(str(session) + ".enc").is_file():
            raise ValueError(f"нет зашифрованной сессии лида {session}.enc — "
                             f"тестовый аккаунт не залогинен (Э1)")
        peer = resolve_peer(root, a.lead_peer)
    except Exception as exc:                          # noqa: BLE001 — DEV-18
        say(f"⛔ ОТКАЗ: {exc}")
        send(f"🔴 Дрил-регресс НЕ запущен: {exc}")
        return CODE_REFUSED

    # 3. предсказуемый старт.
    say(f"сброс дрил-контакта {a.contact}")
    code, out = run([sys.executable, str(_ROOT / "scripts" / "drill_reset.py"),
                     a.db, "--contact", a.contact, "--apply"])
    if code != 0:
        say(f"⛔ сброс не удался (код {code}):\n{out}")
        send(f"🔴 Дрил-регресс: сброс контакта не удался (код {code}) — "
             f"прогон не запускался")
        return CODE_REFUSED

    # 4. прогон.
    started = time.time()
    code, out = run([sys.executable, str(_ROOT / "scripts" / "drill_runner.py"),
                     a.scenario, "--db", a.db, "--yes", "--auto-lead",
                     # Контакт передаём ЯВНО: сброс и судья обязаны говорить об
                     # одном чате. 06.08 сброс чистил тестовый аккаунт, а судья
                     # читал контакт из yaml — прогон не состоялся при живом
                     # боте, живом лиде и списанных $0.128.
                     "--contact", a.contact,
                     "--lead-peer", str(peer),
                     "--lead-session", str(session),
                     "--step-timeout", str(a.step_timeout)])
    mins = (time.time() - started) / 60
    mark = {0: "✅", 1: "🔴", 2: "⛔"}.get(code, "❓")
    verdict = {0: "зелёный", 1: "красные проверки",
               2: "ПРОГОН НЕ СОСТОЯЛСЯ"}.get(code, f"код {code}")
    tail = "\n".join(out.strip().splitlines()[-12:])
    say(out)
    send(f"{mark} Дрил-регресс ({Path(a.scenario).stem}): {verdict}, "
         f"{mins:.1f} мин\n\n{tail}")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
