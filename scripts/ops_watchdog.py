#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Native ops watchdog — the independent monitor DEV-17 designed the
``/api/jarvis/ops/*`` endpoints for, implemented as a scheduled task instead of
Uptime Kuma (Docker Desktop is not a service, so a Kuma container would not
survive an unattended reboot — exactly the 03:14-backend-death scenario this
guards against).

STANDALONE and stdlib-ONLY on purpose (like ``boot_watch_check.py`` /
``regress_watch_check.py``): it must be able to Telegram the admin that the
backend or bot is DOWN precisely when those processes — and anything that shares
their Python env — are the thing that died. The JarvisOpsWatchdog scheduled task
(S4U / Highest, AtStartup) invokes this once per cycle:

    python scripts/ops_watchdog.py

Each cycle probes:
  * backend liveness   — GET 127.0.0.1:8010/health           (the 03:14 catch)
  * bot heartbeat      — /api/jarvis/ops/heartbeat  (only if backend is up)
  * cloudflared tunnel — /api/jarvis/ops/cloudflared          (503 => down)
  * bot restart-storm  — /api/jarvis/ops/restarts             (503 => >3/hr)
  * free disk on C:    — local shutil.disk_usage, threshold 10GB (worktrees
                         have filled C: before; checked locally so it works even
                         when the backend is down)

Dedup/recovery: per-check state (consecutive-fail count + an ``alerted`` flag)
persists in ``state/ops_watchdog_state.json``. A check alerts once on the
DOWN transition (after ``DEBOUNCE`` consecutive failures, so a legitimate ~45s
deploy restart never pages) and once more with ✅ when it recovers — never
repeatedly while it stays in the same state.

failed-dev_task and failed-IG-publish already have their own bot-side Telegram
alerts (DEV-17), fired by the live bot; they are intentionally NOT duplicated
here — this watchdog covers the complementary gap (backend/bot/tunnel DOWN,
disk, restart-storm), the cases the bot itself cannot report because it is dead.
"""
import json
import re
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATE_PATH = ROOT / "state" / "ops_watchdog_state.json"
ENV_PATH = ROOT / ".env"
ADMIN_CHAT_ID = "237616472"

BASE_URL = "http://127.0.0.1:8010"
MIN_DISK_GB = 10.0
DISK_PATH = "C:/"
DEBOUNCE = 2                 # consecutive failed cycles before a DOWN alert
HTTP_TIMEOUT_S = 8

# Human labels used in alert text (check_key -> label).
LABELS = {
    "backend": "BACKEND (:8010 /health)",
    "bot_heartbeat": "БОТ (heartbeat устарел)",
    "cloudflared": "Cloudflared туннель (не Running)",
    "restarts": "Бот рестартит >3/час (restart-storm)",
    "disk": "Мало места на диске C:",
}

# Ops sub-checks, probed only when the backend itself answers (they are served
# BY the backend, so when it is down they are unreachable, not "recovered").
_OPS_ENDPOINTS = {
    "bot_heartbeat": "/api/jarvis/ops/heartbeat",
    "cloudflared": "/api/jarvis/ops/cloudflared",
    "restarts": "/api/jarvis/ops/restarts",
}


# ── pure decision / text functions (unit-tested) ───────────────────────────
def build_alert(check: str, kind: str, detail: str) -> str:
    """kind in {"down", "recovered"}."""
    label = LABELS.get(check, check)
    if kind == "recovered":
        return "✅ Восстановлено: %s. %s" % (label, detail)
    return "🚨 DOWN: %s. %s" % (label, detail)


def evaluate(prev_state: dict, probes: dict, debounce: int = DEBOUNCE,
             suppress_down: bool = False):
    """Pure core: fold this cycle's probe results into per-check state and emit
    the alerts the transitions warrant.

    ``probes``     : {check_key: {"ok": bool, "detail": str}} — only the checks
                     actually run this cycle (down backend omits its sub-checks).
    ``prev_state`` : {check_key: {"fail": int, "alerted": bool}}
    Returns ``(alerts: list[str], new_state: dict)``. Checks absent from
    ``probes`` keep their prior state verbatim (frozen, never spuriously
    recovered).
    """
    new_state = {k: dict(v) for k, v in prev_state.items()}
    alerts = []
    for check, res in probes.items():
        st = dict(new_state.get(check, {"fail": 0, "alerted": False}))
        if res.get("ok"):
            if st.get("alerted"):
                alerts.append(build_alert(check, "recovered", res.get("detail", "")))
            st = {"fail": 0, "alerted": False}
        else:
            st["fail"] = st.get("fail", 0) + 1
            if st["fail"] >= debounce and not st.get("alerted") and not suppress_down:
                alerts.append(build_alert(check, "down", res.get("detail", "")))
                st["alerted"] = True
            # suppress_down: считаем, но молчим. `alerted` НЕ ставим — поэтому
            # (а) после окна загрузки не поднявшийся сервис немедленно даст
            # 🚨 (debounce уже набран), (б) поднявшийся не даст ✅ о том, о чём
            # владельцу не сообщали.
        new_state[check] = st
    return alerts, new_state


# ── DEV-24: алерт «машина перезагрузилась» ────────────────────────────────
# Инцидент 2026-07-15: Windows Update ребутнул прод дважды за три минуты
# (Event 1074 в 03:14:09 и 03:16:48), погибла аудит-задача, узнали утром.
# Остальные проверки этого не ловят по построению: они спрашивают «сервис
# отвечает?», а после ребута сервисы поднимаются гардианами — и всё выглядит
# нормой. Простой и потеря работы проходят бесследно.
#
# Сигнал по СМЕНЕ ЗАГРУЗКИ, а не по порогу аптайма: порог пропустил бы ребут,
# если watchdog стартовал с задержкой, и слил бы двойной ребут в один алерт.
BOOT_KEY = "_boot"          # служебный ключ в том же файле состояния, НЕ проверка
# Окно, в котором падения сервисов ПОСЛЕ ЗАГРУЗКИ ожидаемы: гардианы ещё
# поднимают backend/бота/раннер. Утренний ребут 2026-07-19 дал 🚨 в 07:01-07:03
# (три штуки) плюс ✅ на каждую — до шести сообщений об ОДНОМ событии, и ни
# одно не называло причину. 5 минут с запасом покрывают этот разброс.
BOOT_GRACE_S = 300

# Инцидент 2026-07-21/22: ложный «🔄 перезагрузилась в 07:00 (аптайм 74 ч)»
# два дня подряд, 7-11 повторов, реального ребута не было. psutil.boot_time()
# на Windows = time.time() - GetTickCount64()/1000; тик-каунтер не получает
# NTP-коррекций, оценка дрейфует ~секунду в сутки. Раз в сутки дробь переползает
# целочисленную границу, int() флипается → «смена загрузки»; пока дрейф в зоне
# шума замера вокруг границы, каждый 30с-тик флипает туда-сюда → россыпь
# повторов. Отсюда три предохранителя (все ниже, в detect_reboot):
#   допуск дрейфа — сдвиг boot_id в его пределах не «смена загрузки». Реальный
#   ребут двигает boot_time минимум на прежний аптайм (на порядки больше);
#   допуск ОБЯЗАН быть меньше 159с — межребутного зазора инцидента 15.07,
#   иначе второй ребут был бы съеден (тест держит);
BOOT_JITTER_TOLERANCE_S = 60
#   sanity-guard: не заявлять ребут при аптайме > 30 мин. Реальный ребут
#   watchdog видит на первом же 30с-тике; «перезагрузилась в 07:00 при аптайме
#   74 ч» — противоречие прямо в тексте алерта, каким инцидент и заметили.
#   Цена: ребут, который watchdog проспал дольше 30 мин, алерта не даёт
#   (осознанное решение владельца 2026-07-22); подавление не молчит (DEV-18).
REBOOT_CLAIM_MAX_UPTIME_S = 30 * 60


def within_boot_grace(boot_time: float, now: float, grace: float = BOOT_GRACE_S) -> bool:
    """Идёт ли ещё загрузочное окно. В нём 🚨 по сервисам подавляются в пользу
    одного осмысленного «машина перезагрузилась»."""
    return (now - boot_time) <= grace


def _int_or_none(value):
    """JSON мог сохранить число строкой; кривой стейт не должен ни падать,
    ни давать ложный 🔄 (DEV-18: не молча — но и не умирая)."""
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def detect_reboot(prev_state: dict, boot_time: float, now: float,
                  warn=None) -> tuple[bool, dict]:
    """(нужен_ли_алерт, новое_состояние). Чистая функция.

    Первый запуск не алертит — предыдущей загрузки мы не видели, это база.
    Дальше алертим на смену идентификатора загрузки БОЛЬШЕ допуска дрейфа
    (см. BOOT_JITTER_TOLERANCE_S), поэтому два ребута подряд честно дают два
    алерта, а суточное дрожание psutil.boot_time() не даёт ни одного.
    Сверху sanity-guard по аптайму (REBOOT_CLAIM_MAX_UPTIME_S, подавление
    репортится в ``warn``) и дедуп once-per-boot-id (``alerted_boot_id`` в
    стейте: об одной загрузке — один пуш)."""
    boot_id = int(boot_time)
    new_state = {k: (dict(v) if isinstance(v, dict) else v)
                 for k, v in prev_state.items()}

    entry = prev_state.get(BOOT_KEY)
    entry = entry if isinstance(entry, dict) else {}
    prev_boot = _int_or_none(entry.get("boot_id"))
    alerted = _int_or_none(entry.get("alerted_boot_id"))

    new_entry = {"boot_id": boot_id}
    if alerted is not None:
        new_entry["alerted_boot_id"] = alerted
    new_state[BOOT_KEY] = new_entry

    if prev_boot is None:
        return False, new_state                     # база, а не ложный алерт
    if abs(boot_id - prev_boot) <= BOOT_JITTER_TOLERANCE_S:
        return False, new_state                     # дрейф замера, не ребут

    uptime = now - boot_time
    if uptime > REBOOT_CLAIM_MAX_UPTIME_S:
        # boot_time сдвинулся ощутимо (степ часов?), но машина давно работает —
        # «перезагрузилась (аптайм 74 ч)» было бы ложью. Базу принимаем,
        # молчим НЕ молча.
        if warn:
            warn("boot_time сместился на %dс при аптайме %s — считаю сдвигом "
                 "часов, не ребутом; алерт подавлен"
                 % (boot_id - prev_boot, humanize_uptime(uptime)))
        return False, new_state

    if alerted is not None and abs(boot_id - alerted) <= BOOT_JITTER_TOLERANCE_S:
        return False, new_state                     # об этой загрузке уже пушили
    new_entry["alerted_boot_id"] = boot_id
    return True, new_state


def humanize_uptime(seconds: float) -> str:
    """Алерт читают в стрессе — «48323 с» разбирать некогда."""
    s = max(0, int(seconds))
    if s < 90:
        return "%s с" % s
    if s < 5400:
        return "%s мин" % (s // 60)
    return "%s ч" % round(s / 3600)


def reboot_alert_text(boot_time: float, now: float, localtime=time.localtime,
                      grace: float = BOOT_GRACE_S) -> str:
    """ОДНО сообщение вместо россыпи 🚨 по каждому сервису. Явно говорит, что
    тишина дальше — намеренная, иначе подавление само выглядит как поломка."""
    hhmm = time.strftime("%H:%M", localtime(boot_time))
    return ("🔄 Машина перезагрузилась в %s (аптайм %s). "
            "Гардианы поднимают сервисы — тревоги по ним молчат %s мин. "
            "Если что-то не встанет, придёт отдельный 🚨. "
            "Проверь, не погибла ли долгая задача."
            % (hhmm, humanize_uptime(now - boot_time), int(grace // 60)))


def _boot_time() -> float | None:
    """Момент загрузки по psutil. None — если получить не удалось: тогда
    ребут-детект молча пропускается, но остальные проверки обязаны идти."""
    try:
        import psutil
        return float(psutil.boot_time())
    except Exception:
        log_exc = getattr(sys, "stderr", None)
        if log_exc:
            print("[ops_watchdog] не смог определить время загрузки", file=sys.stderr)
        return None


def parse_token(env_text: str) -> str:
    m = re.search(
        r'^\s*(?:TELEGRAM_BOT_TOKEN|BOT_TOKEN)\s*=\s*"?([^"\r\n]+)"?',
        env_text, re.M)
    return m.group(1).strip() if m else ""


# ── probe layer (injectable IO -> probes dict; unit-tested via fakes) ──────
def probe_all(http_get, disk_usage, min_disk_gb: float = MIN_DISK_GB) -> dict:
    """Compose the cycle's probes. ``http_get(path) -> int|None`` (HTTP status,
    or None on connection refused/timeout); ``disk_usage(path) -> (total, used,
    free)`` (shutil.disk_usage-shaped)."""
    probes = {}

    status = http_get("/health")
    backend_ok = status == 200
    probes["backend"] = {
        "ok": backend_ok,
        "detail": "HTTP %s" % status if status is not None else "no response (refused/timeout)",
    }
    if backend_ok:
        for key, path in _OPS_ENDPOINTS.items():
            s = http_get(path)
            probes[key] = {"ok": s == 200, "detail": "HTTP %s" % s}

    try:
        _total, _used, free = disk_usage(DISK_PATH)
        free_gb = free / (1024 ** 3)
        probes["disk"] = {
            "ok": free_gb >= min_disk_gb,
            "detail": "%.1fGB free (min %.1fGB)" % (free_gb, min_disk_gb),
        }
    except Exception as exc:
        probes["disk"] = {"ok": False, "detail": "disk check failed: %s" % exc}
    return probes


# ── io helpers (stdlib only; exercised live, not unit-tested) ──────────────
def _http_get(path: str):
    try:
        with urllib.request.urlopen(BASE_URL + path, timeout=HTTP_TIMEOUT_S) as r:
            return r.getcode()
    except urllib.error.HTTPError as e:
        return e.code                       # 503 etc. are meaningful, not errors
    except Exception:
        return None                         # refused / timeout / DNS -> down


def _disk_usage(path):
    import shutil
    return shutil.disk_usage(path)


def _bot_token() -> str:
    try:
        return parse_token(ENV_PATH.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return ""


def _send_tg(text: str) -> bool:
    token = _bot_token()
    if not token:
        return False
    payload = json.dumps({"chat_id": ADMIN_CHAT_ID, "text": text}).encode()
    req = urllib.request.Request(
        "https://api.telegram.org/bot%s/sendMessage" % token,
        data=payload, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read()).get("ok", False)
    except Exception:
        return False


def _read_state() -> dict:
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _write_state(state: dict) -> None:
    try:
        STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        STATE_PATH.write_text(
            json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


def main() -> int:
    state = _read_state()

    # DEV-24: ребут проверяем ПЕРВЫМ и шлём отдельным сообщением. Он не
    # зависит от здоровья сервисов — наоборот, чаще всего они уже здоровы
    # (гардианы подняли), и именно поэтому раньше ребут проходил незаметно.
    reboot_text = None
    in_boot_grace = False
    boot_time = _boot_time()
    if boot_time is not None:
        fired, state = detect_reboot(
            state, boot_time, time.time(),
            warn=lambda msg: print("[ops_watchdog] %s" % msg, file=sys.stderr))
        if fired:
            reboot_text = reboot_alert_text(boot_time, time.time())
        # Окно проверяем ОТДЕЛЬНО от факта детекта: 🚨 прилетали в 07:01-07:03,
        # то есть на нескольких тиках подряд, а не только на том, где сменился
        # boot_id. Подавлять надо всё окно, иначе дедупликация ничего не даст.
        in_boot_grace = within_boot_grace(boot_time, time.time())

    probes = probe_all(_http_get, _disk_usage)
    alerts, state = evaluate(state, probes, suppress_down=in_boot_grace)

    if reboot_text:
        _send_tg(reboot_text)
    for text in alerts:
        _send_tg(text)
    _write_state(state)
    return 0


if __name__ == "__main__":
    sys.exit(main())
