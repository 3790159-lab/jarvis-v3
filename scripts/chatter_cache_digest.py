"""Суточная проверка здоровья prompt-кэша chatter (спека 2026-07-25 §6).

Печатает hit-rate РАЗДЕЛЬНО по brain/classifier за последние сутки и с базой
(«90% на пятидесяти» и «100% на двух» — разные утверждения), а владельцу шумит
только когда сработала сигнатура регрессии: N промахов подряд при ЖИВОМ кэше.

Зачем: 2026-07-23 18:23 профиль въехал в кэшируемый блок классификатора, кэш
умер (1 попадание из 22), счёт вырос на треть — и сутки об этом никто не знал,
потому что смотреть было некуда. Этот скрипт и есть «куда смотреть».

Запуск: раз в сутки отдельным S4U-таском (как error_digest/morning_digest).
Ручной прогон: python scripts/chatter_cache_digest.py [--db .secrets/demo.db]
"""
from __future__ import annotations

import argparse
import logging
import sqlite3
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

from chatter.core.cache_health import (  # noqa: E402
    REGRESSION_STREAK, hit_rate, is_regression,
)

logger = logging.getLogger("jarvis.chatter_cache_digest")

DEFAULT_DB = _ROOT / ".secrets" / "demo.db"
DEFAULT_CLIENT_DIR = _ROOT / "chatter" / "clients" / "volska"
WINDOW_SEC = 24 * 3600
_TAGS = ("brain", "classifier", "classifier_retry")
_TG_TIMEOUT = 20


def send_telegram(text: str) -> bool:
    """Best-effort уведомление владельцу. Никогда не бросает.

    Standalone-джоба: сама спрашивает общий guard изоляции, как
    morning_digest.send_telegram — иначе тесты слали бы живые сообщения."""
    import os
    try:
        from app.core.notify_isolation import telegram_send_blocked
        if telegram_send_blocked():
            logger.info("chatter_cache_digest: send suppressed under test isolation")
            return False
    except Exception:
        # Нет app-слоя (голый прогон) — считаем, что отправка запрещена:
        # молчание безопаснее случайного сообщения владельцу.
        logger.info("chatter_cache_digest: isolation guard unavailable — not sending")
        return False
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_ALLOWED_CHAT_ID", "").strip()
    if not token or not chat_id:
        logger.warning("chatter_cache_digest: no TELEGRAM_BOT_TOKEN/CHAT_ID — skipping")
        return False
    try:
        data = urllib.parse.urlencode({"chat_id": chat_id, "text": text}).encode()
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{token}/sendMessage", data=data)
        with urllib.request.urlopen(req, timeout=_TG_TIMEOUT) as resp:
            return 200 <= resp.status < 300
    except Exception as exc:      # DEV-18: не молча
        logger.warning("chatter_cache_digest: telegram send failed: %s", exc)
        return False


def config_change_times(client_dir=DEFAULT_CLIENT_DIR) -> list[float]:
    """Моменты смены конфига = имена снимков версий (`<int(ts)>`).

    Смена префикса после `/reload` плейбука — ЗАКОННЫЙ промах, и подавляется
    отдельно от сигнатуры регрессии (иначе правка базы знаний = алерт)."""
    root = Path(client_dir) / ".versions"
    if not root.is_dir():
        return []
    return sorted(float(p.name) for p in root.iterdir()
                  if p.is_dir() and p.name.isdigit())


# ── P16(г): кого раннер обслуживает на самом деле ────────────────────────────
# Форензика 2026-07-25: с 22.07 раннер обслуживал только `volska`, а
# `active.yaml` по-прежнему объявлял `demo, demo2`. Состояние было ЗАКОННЫМ
# (semidemo-флаг), но не видно нигде — то есть «by design» неотличимо от
# аварии, и отвалившийся клиент выглядел бы точно так же. Сводка обязана
# называть расхождение вслух.

SEMIDEMO_FLAG = "chatter_semidemo_volska.flag"
SEMIDEMO_PERSONA = "volska"


def roster_status(root=_ROOT) -> dict:
    """Объявленный состав против фактически обслуживаемого.

    Объявленный берём ТЕМ ЖЕ швом, что и раннер (`resolve_personas`) — если
    формат `active.yaml` изменится, сводка поедет за ним, а не разъедется.
    `env={}` намеренно: нас интересует, что записано в конфиге, а не что
    подсунуто окружением текущего процесса."""
    root = Path(root)
    try:
        from chatter.config.active import resolve_personas
        declared = resolve_personas(clients_dir=root / "chatter" / "clients", env={})
    except Exception as exc:                      # битый/отсутствующий конфиг
        logger.warning("chatter_cache_digest: состав клиентов не прочитан: %s", exc)
        declared = []
    override = (SEMIDEMO_PERSONA
                if (root / "state" / SEMIDEMO_FLAG).exists() else None)
    served = [override] if override else list(declared)
    return {"declared": list(declared), "served": served, "override": override,
            "muted": [c for c in declared if c not in served]}


def format_roster(st: dict) -> str:
    if not st["override"]:
        return "ростер: " + (", ".join(st["served"]) or "(пусто)")
    muted = ", ".join(st["muted"]) or "—"
    return (f"⚠️ ростер ПЕРЕОПРЕДЕЛЁН флагом: обслуживается только "
            f"{', '.join(st['served'])}; отключены: {muted} "
            f"(state/{SEMIDEMO_FLAG})")


def _rows_since(db: str, since: float) -> list[dict]:
    try:
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    except sqlite3.Error:
        return []
    try:
        conn.row_factory = sqlite3.Row
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(llm_usage)")}
        if not cols:
            return []
        return [dict(r) for r in conn.execute(
            "SELECT * FROM llm_usage WHERE ts >= ? ORDER BY ts", (since,))]
    except sqlite3.Error as exc:
        # База может быть занята/битой — суточная проверка не должна падать
        # таском (DEV-18: но и молчать нельзя).
        logger.warning("chatter_cache_digest: db read failed: %s", exc)
        return []
    finally:
        conn.close()


def build_report(db: str, *, now: float | None = None,
                 client_dir=DEFAULT_CLIENT_DIR, root=_ROOT) -> dict:
    now = time.time() if now is None else now
    rows = _rows_since(db, now - WINDOW_SEC)
    changes = config_change_times(client_dir)
    rep: dict = {t: hit_rate(rows, t) for t in _TAGS}
    rep["window_h"] = WINDOW_SEC / 3600
    rep["roster"] = roster_status(root)
    # Алертим ТОЛЬКО по классификатору: у brain кэш исправен, и его промахи
    # почти всегда — законно истёкший TTL долгого диалога.
    rep["alert"] = is_regression(rows, "classifier", config_change_ts=changes)
    return rep


def format_report(rep: dict) -> str:
    def _line(tag: str) -> str:
        hits, total = rep[tag]
        pct = f"{hits / total * 100:.0f}%" if total else "н/д"
        return f"{tag}: {hits}/{total} ({pct})"

    head = "🔴 КЭШ КЛАССИФИКАТОРА СЛОМАН" if rep["alert"] else "🧊 кэш chatter"
    lines = [f"{head} — за {rep['window_h']:.0f}ч",
             format_roster(rep["roster"]),
             _line("brain"), _line("classifier")]
    if rep["classifier_retry"][1]:
        lines.append(_line("classifier_retry"))
    if rep["alert"]:
        lines.append(
            f"{REGRESSION_STREAK}+ промахов подряд при живом кэше (интервалы <1ч, "
            f"смены конфига учтены). Похоже, в кэшируемый префикс въехало что-то "
            f"изменчивое — смотри classifier_stable_prefix, а не порог.")
    return "\n".join(lines)


def main(argv=None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(DEFAULT_DB))
    ap.add_argument("--client-dir", default=str(DEFAULT_CLIENT_DIR))
    ap.add_argument("--always-send", action="store_true",
                    help="слать сводку даже без алерта (для ручной проверки)")
    a = ap.parse_args(argv)

    try:
        import app.env_bootstrap  # noqa: F401  side-effect: loads .env
    except Exception as exc:
        logger.error("chatter_cache_digest: env bootstrap failed: %s", exc)

    rep = build_report(a.db, client_dir=a.client_dir)
    text = format_report(rep)
    print(text)
    logger.info("chatter_cache_digest: %s", text.replace("\n", " | "))
    if rep["alert"] or a.always_send:
        send_telegram(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
