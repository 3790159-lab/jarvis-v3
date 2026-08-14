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
  * secrets bundle     — .jrvbak не отстал от материала (.env/сессии/entropy)
                         больше чем на 7 суток. Экспорт РУЧНОЙ (пароль вводит
                         владелец) — сторож лишь напоминает, что копия
                         отстала; без бандла смерть диска = потеря сессий
                         всех клиентов
  * live tree state    — C:/jarvis на транке и чисто по tracked-файлам. Дерево
                         это деплой-путь гардиана: `git checkout` в нём = тихий
                         деплой. 2026-08-10 оно дважды осталось не в том
                         состоянии, и оба раза это поймало внимание, а не сторож

Dedup/recovery: per-check state (consecutive-fail count, an ``alerted`` flag and
the ``alerted_reason`` already announced) persists in
``state/ops_watchdog_state.json``. A check alerts once on the DOWN transition
(after ``DEBOUNCE`` consecutive failures, so a legitimate ~45s deploy restart
never pages), once more with ✅ when it recovers, и ещё раз — если, оставаясь
красной, она сменила ПРИЧИНУ.

Дедуп по причине введён 2026-08-11 по факту: чек worktree простоял красным 1669
циклов из-за законной правки тумблера пультом. `alerted` был одним булевым, и
недеплоенный код, приехавший следом, второго алерта уже не дал бы — сторож,
поставленный ровно на это, был выключен собственным законным срабатыванием.
Сравнивается грубый стабильный ``reason`` от пробы, а НЕ ``detail``: в тексте
живут гигабайты и секунды, и дедуп по нему давал бы алерт раз в 30 секунд.

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

# Живое дерево — ДЕПЛОЙ-ПУТЬ гардиана: он поднимает раннер из того, что здесь
# лежит, поэтому `git checkout` в этом каталоге = тихий деплой через ~90 с.
# Путь задан ЯВНО, а не через ROOT: ROOT — это каталог скрипта, и в worktree он
# указывал бы на worktree, то есть проверка следила бы не за тем деревом.
LIVE_TREE = Path("C:/jarvis")
TRUNK_BRANCH = "phase-4.0-unified-jarvis"
# `--untracked-files=no` не оптимизация: в живом дереве постоянно лежат
# артефакты и отчёты (на момент внедрения — 12 записей). Без этого флага
# проверка была бы красной ВСЕГДА, а вечно красную лампу перестают читать.
GIT_STATUS_ARGS = ("status", "--porcelain", "--untracked-files=no")

# Копия секретов. Бандл .jrvbak — ЕДИНСТВЕННЫЙ путь восстановления после смерти
# диска или профиля: DPAPI привязан к учётке+машине, и без бандла теряются
# сессии ВСЕХ клиентов (ONBOARDING_MANUAL §12). Экспорт ручной — пароль вводит
# владелец, — поэтому сторож ничего не копирует, а напоминает, что копия отстала.
#
# Мануал требует держать бандл ВНЕ машины. Смотрим на локальное зеркало
# OneDrive: файл там — свидетельство, что экспорт БЫЛ, а синхронизация унесла
# копию за пределы диска.
BUNDLE_DIRS = (Path("C:/Users/Admin/OneDrive/jarvis-recovery"),)
BUNDLE_GLOB = "*.jrvbak"
BUNDLE_MAX_LAG_DAYS = 7.0
# Что реально едет в бандл (collect_secrets): .env/.env.enc, сессии клиентов,
# entropy.bin. Сравнивать только с `.env` было бы мало: логин НОВОГО клиента
# без переэкспорта — незакрытый онбординг, и он остался бы невидимым.
BUNDLE_MATERIAL_GLOBS = ("*.session.enc", "*.session", "entropy.bin")
GIT_TIMEOUT_S = 10
DIRTY_SHOWN = 3              # сколько файлов называть в алерте
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
    # ЛОВУШКА 2 спеки: о смерти раннера при живом гардиане скажут ОБА канала
    # (chatter_watch_check и этот). Тексты обязаны различаться ИСТОЧНИКОМ,
    # иначе владелец решит, что упало дважды.
    "chatter_runner": "CHATTER раннер (независимый сторож)",
    "chatter_guardian": "CHATTER гардиан (независимый сторож)",
    "worktree": "ЖИВОЕ ДЕРЕВО C:/jarvis (не транк или грязное)",
    "secrets_bundle": "КОПИЯ СЕКРЕТОВ (.jrvbak отстал или его нет)",
}

# Порог тот же, что у гардиана (HeartbeatMaxAgeSec=180). Разные пороги = два
# сторожа, спорящих о том, кто DOWN — инцидент 13:06, когда chatter_watch_check
# выводил вердикт по своему порогу и противоречил гардиану.
CHATTER_BEAT_MAX_AGE_S = 180
CHATTER_RUNNER_MARKER = "chatter.telethon_run"

# Ops sub-checks, probed only when the backend itself answers (they are served
# BY the backend, so when it is down they are unreachable, not "recovered").
_OPS_ENDPOINTS = {
    "bot_heartbeat": "/api/jarvis/ops/heartbeat",
    "cloudflared": "/api/jarvis/ops/cloudflared",
    "restarts": "/api/jarvis/ops/restarts",
}


# ── pure decision / text functions (unit-tested) ───────────────────────────
def build_alert(check: str, kind: str, detail: str) -> str:
    """kind in {"down", "recovered", "changed"}.

    `changed` отличается от `down` текстом НАМЕРЕННО: второе 🚨 по той же
    проверке иначе читается как «упало ещё раз», хотя оно не падало — у него
    добавилась вторая причина."""
    label = LABELS.get(check, check)
    if kind == "recovered":
        return "✅ Восстановлено: %s. %s" % (label, detail)
    if kind == "changed":
        return "🚨 Новая причина: %s. %s" % (label, detail)
    return "🚨 DOWN: %s. %s" % (label, detail)


def transitions(prev_state: dict, probes: dict, debounce: int = DEBOUNCE,
                suppress_down: bool = False, now=None):
    """Чистое ядро: свернуть пробы этого цикла в состояние и вернуть ПЕРЕХОДЫ.

    Отделено от `evaluate()` 14.08, когда переходы понадобились журналу панели
    структурой, а не текстом: по фразе с эмодзи нельзя ни отсортировать, ни
    сгруппировать, ни схлопнуть пару «подавлено → подтверждено». Второго
    анализатора состояний в системе при этом не появилось — `evaluate()` стала
    тонкой обёрткой, и вся логика дебаунса и дедупа живёт здесь.

    ``probes``     : {check_key: {"ok": bool, "detail": str, "reason": str}} —
                     только проверки, реально выполненные в этом цикле (мёртвый
                     бэкенд не даёт своих под-проверок).
    ``prev_state`` : {check_key: {"fail": int, "alerted": bool,
                                  "alerted_reason": str}}

    Возвращает `(transitions: list[dict], new_state: dict)`. Переход:
    `{"ts", "check", "kind", "reason", "detail"}`, kind ∈
    {down, recovered, changed, suppressed}. Проверки, отсутствующие в
    `probes`, сохраняют прежнее состояние дословно (заморожены, никогда не
    «восстанавливаются» сами).

    ДЕДУП ПО ПРИЧИНЕ, а не по факту (дефект найден фактом 2026-08-11): чек
    worktree простоял красным 1669 циклов из-за законной правки тумблера, и
    приехавший следом недеплоенный код второго алерта уже НЕ дал бы — сторож,
    поставленный ровно на это, был выключен собственным законным
    срабатыванием. Причина — грубый стабильный ключ от пробы, а НЕ `detail`: в
    тексте живут гигабайты и секунды, дедуп по нему давал бы алерт раз в 30
    секунд. Проба, не объявившая причину, ведёт себя ровно как раньше (один
    алерт на падение).
    """
    now = time.time() if now is None else now
    new_state = {k: dict(v) for k, v in prev_state.items()}
    out = []

    def fire(check, kind, res, reason):
        out.append({"ts": now, "check": check, "kind": kind,
                    "reason": reason, "detail": res.get("detail", "")})

    for check, res in probes.items():
        st = dict(new_state.get(check, {"fail": 0, "alerted": False}))
        reason = str(res.get("reason") or check)
        if res.get("ok"):
            if st.get("alerted"):
                fire(check, "recovered", res, reason)
            # Причина забывается вместе с алертом: оставить её значило бы
            # промолчать о следующем падении по той же причине.
            st = {"fail": 0, "alerted": False}
        else:
            st["fail"] = st.get("fail", 0) + 1
            if suppress_down:
                # Окно загрузки: считаем, но молчим. Ни `alerted`, ни причину не
                # ставим — поэтому (а) после окна не поднявшийся сервис немедленно
                # даст 🚨 (debounce уже набран), (б) поднявшийся не даст ✅ о том,
                # о чём владельцу не сообщали, и (в) причина, о которой не
                # сказали, не считается объявленной.
                #
                # В ЖУРНАЛ это попадает: падение, о котором не сообщили, — ровно
                # то событие, ради которого журнал и заводится.
                if st["fail"] >= debounce:
                    fire(check, "suppressed", res, reason)
            elif not st.get("alerted"):
                if st["fail"] >= debounce:
                    fire(check, "down", res, reason)
                    st["alerted"] = True
                    st["alerted_reason"] = reason
            elif "alerted_reason" not in st:
                # Стейт с диска старого формата. Причину принимаем МОЛЧА: иначе
                # первый же цикл после выкатки разошлёт 🚨 по каждой красной
                # проверке — шторм ровно за то, что мы здесь чиним.
                st["alerted_reason"] = reason
            elif st["alerted_reason"] != reason:
                fire(check, "changed", res, reason)
                st["alerted_reason"] = reason
        new_state[check] = st
    return out, new_state


# Виды переходов, о которых владельцу СООБЩАЮТ. `suppressed` сюда не входит по
# определению: это падение в загрузочном окне, о котором мы намеренно молчим.
ALERTING_KINDS = ("down", "recovered", "changed")


def evaluate(prev_state: dict, probes: dict, debounce: int = DEBOUNCE,
             suppress_down: bool = False):
    """Тексты алертов из переходов. Обёртка над `transitions()`; сигнатура и
    поведение неизменны — на них стоят тесты и мутационный гейт.

    Returns ``(alerts: list[str], new_state: dict)``.
    """
    trs, new_state = transitions(prev_state, probes, debounce, suppress_down)
    alerts = [build_alert(t["check"], t["kind"], t["detail"])
              for t in trs if t["kind"] in ALERTING_KINDS]
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
# ── P16-а: chatter под независимым наблюдением ────────────────────────────
# Мотив: `chatter_watch_check.py` зовёт ЕДИНСТВЕННОЕ место — сам гардиан-скрипт.
# Умер гардиан → умер и алертер, и тишина неотличима от здоровья (20–22.07
# раннер пролежал ~44 часа молча). Эти две пробы дают канал, который не
# является ни раннером, ни гардианом.
#
# Обе — ЧИСТЫЕ функции над снимком: дебаунс, алерты и парное ✅ уже реализованы
# в evaluate() и переиспользуются без единой правки.

def _norm(text: str) -> str:
    return (text or "").replace("\\", "/").lower()


def probe_chatter_runner(processes, *, beat_age, root, semidemo_flag=False):
    """Раннер жив? Процесс И свежий heartbeat — оба условия обязательны:
    живой процесс с протухшим beat это зависший раннер, а не здоровье.

    ⚠️ Матч ТОЛЬКО по python-процессу и ТОЛЬКО с нашим ROOT в командной строке.
    Без первого условия проба ловит того, кто её же и выполняет (строка-маркер
    попадает в командную строку искателя — ложное «раннер жив», стоило разбора
    29.07). Без второго — чужой раннер из worktree разработчика сойдёт за
    боевой, и сторож замолчит на мёртвом проде.

    `semidemo_flag` принимается и НАМЕРЕННО игнорируется (ловушка 4 спеки):
    проба меряет ПРОЦЕСС; отключённые флагом клиенты — предмет (б)/(г), и
    алерт по ним превратил бы сторожа в постоянный крик на законное состояние.
    """
    # Со СЛЕШЕМ на конце: голая подстрока "c:/jarvis" сидит внутри
    # "c:/jarvis_worktrees/panels/...", и раннер из чужого worktree сходил за
    # боевой — сторож замолчал бы на мёртвом проде, пока рядом крутится тест.
    root_n = _norm(str(root)).rstrip("/") + "/"
    alive = [
        p for p in (processes or [])
        if (p.get("name") or "").lower().startswith("python")
        and CHATTER_RUNNER_MARKER in _norm(p.get("cmdline"))
        and root_n in _norm(p.get("cmdline"))
    ]
    if not alive:
        return {"ok": False, "detail": "процес раннера не знайдено",
                "reason": "no_process"}
    if beat_age is None:
        return {"ok": False, "detail": "heartbeat відсутній",
                "reason": "no_heartbeat"}
    if beat_age > CHATTER_BEAT_MAX_AGE_S:
        # Причина БЕЗ секунд: возраст растёт каждый цикл, и текст в ключе
        # дедупа означал бы алерт раз в 30 секунд.
        return {"ok": False, "reason": "stale_heartbeat",
                "detail": "heartbeat %.0fс тому (поріг %ds)" % (beat_age, CHATTER_BEAT_MAX_AGE_S)}
    return {"ok": True, "detail": "PID %s, heartbeat %.0fс тому" % (alive[0].get("pid"), beat_age)}


def probe_chatter_guardian(processes, *, lock_pid, beat_age):
    """Гардиан жив? PID из state/locks/chatter_guardian.pid ДОЛЖЕН существовать
    И быть powershell'ом.

    ⚠️ ЛОВУШКА 3 спеки: PID-файл переживает kill. Если ОС успела выдать тот же
    номер чужому процессу, «PID существует» не значит «гардиан жив» — поэтому
    сверяем имя процесса, а не только наличие номера.
    """
    if lock_pid is None:
        return {"ok": False, "detail": "PID-лок відсутній або нечитний",
                "reason": "no_lock"}
    match = next((p for p in (processes or []) if p.get("pid") == lock_pid), None)
    if match is None:
        return {"ok": False, "detail": "PID %s мертвий" % lock_pid,
                "reason": "dead_pid"}
    if not (match.get("name") or "").lower().startswith("powershell"):
        return {"ok": False, "reason": "stale_lock",
                "detail": "PID %s зайнятий чужим процесом (%s), лок протух — очікувався powershell"
                          % (lock_pid, match.get("name"))}
    if beat_age is None:
        return {"ok": False, "detail": "heartbeat гардіана відсутній",
                "reason": "no_heartbeat"}
    if beat_age > CHATTER_BEAT_MAX_AGE_S:
        return {"ok": False, "reason": "stale_heartbeat",
                "detail": "heartbeat %.0fс тому (поріг %ds)" % (beat_age, CHATTER_BEAT_MAX_AGE_S)}
    return {"ok": True, "detail": "PID %s, heartbeat %.0fс тому" % (lock_pid, beat_age)}


def probe_worktree(snapshot: dict, *, trunk: str = TRUNK_BRANCH) -> dict:
    """Восьмая проверка: живое дерево на транке и чисто по tracked-файлам.

    Почему это вообще сторож, а не дисциплина: дерево — деплой-путь гардиана.
    2026-08-10 оно дважды оказалось не в том состоянии (осталось на ветке арки
    после сессии; откат мутационного харнесса стёр незакоммиченную правку) —
    оба раза это поймало ВНИМАНИЕ, то есть ночью не поймало бы ничто.

    Обе причины называются вместе: узнав только про ветку, вернёшь её и решишь,
    что починил, — а грязные файлы останутся и уедут в деплой.

    Невозможность спросить git — тоже КРАСНОЕ. На машине, где git и есть
    механизм деплоя, «не смогли посмотреть» это аномалия, а зелёное здесь
    означало бы слепую зону ровно там, где мы её и закрываем."""
    error = (snapshot or {}).get("error")
    if error:
        return {"ok": False, "reason": "unreadable",
                "detail": "не удалось определить состояние дерева: %s" % error}

    branch = ((snapshot or {}).get("branch") or "").strip()
    dirty = list((snapshot or {}).get("dirty") or [])
    problems = []
    reasons = []

    if not branch:
        problems.append("ветка не определена")
        reasons.append("no_branch")
    elif branch != trunk:
        # Обе ветки в тексте: иначе непонятно, куда возвращать.
        problems.append("HEAD на «%s», ожидался «%s»" % (branch, trunk))
        reasons.append("branch:%s" % branch)

    if dirty:
        shown = ", ".join(line.strip() for line in dirty[:DIRTY_SHOWN])
        tail = "" if len(dirty) <= DIRTY_SHOWN else " и ещё %d" % (len(dirty) - DIRTY_SHOWN)
        problems.append("модифицировано tracked-файлов: %d (%s%s)" % (len(dirty), shown, tail))
        # Причина — ПУТИ, и все, а не количество и не первые DIRTY_SHOWN.
        # Счётчик не различал бы «один файл сменился другим», а обрезка
        # сделала бы невидимым четвёртый файл — ровно тот, который и
        # окажется недеплоенным кодом. Статус-буквы git снимаем: « M x» и
        # «M  x» — одно состояние, алерт на смену пробелов был бы шумом.
        reasons.append("dirty:" + "|".join(sorted(
            line.strip().split(None, 1)[-1] for line in dirty)))

    if problems:
        return {"ok": False, "detail": "; ".join(problems),
                "reason": "; ".join(reasons)}
    return {"ok": True, "detail": "%s, чисто" % trunk}


def probe_secrets_bundle(snapshot: dict, *,
                         max_lag_days: float = BUNDLE_MAX_LAG_DAYS) -> dict:
    """Девятая проверка: копия секретов не отстала от материала.

    Красное, если бандла нет вовсе, если материал новее бандла больше чем на
    `max_lag_days`, или если каталог с бандлами не удалось прочитать. Обе
    причины называются В ОДНОМ алерте — тем же принципом, что у worktree-чека:
    узнав одну из двух, починишь её и решишь, что закрыл вопрос.

    Граница строгая (`>`): ровно на седьмые сутки владелец ещё в графике, а
    сторож, загорающийся на самой границе, приучает к тому, что он слегка врёт.

    Порог — напоминание, а не SLA: экспорт ручной, пароль вводит владелец, и
    сторож не может и не должен делать копию сам.

    ГРАНИЦА (PROBLEMS P26): сравниваются ДАТЫ, а не содержимое. Переэкспорт из
    устаревшего `.env.enc` даёт свежий mtime при старом содержимом — здесь это
    зелёное. Содержимое доказывает только `scripts/verify_bundle.py`, который
    открывает бандл паролем; гонять его после каждого экспорта."""
    error = (snapshot or {}).get("error")
    if error:
        return {"ok": False, "reason": "unreadable",
                "detail": "не удалось проверить копию секретов: %s" % error}

    material = list((snapshot or {}).get("material") or [])
    bundle = (snapshot or {}).get("bundle")
    searched = list((snapshot or {}).get("searched") or [])
    problems = []

    reasons = []
    if not material:
        # Не «нечего бэкапить», а «мы ничего не увидели»: зелёное здесь было бы
        # слепой зоной вокруг единственного пути восстановления.
        problems.append("материал секретов не найден — сравнивать не с чем")
        reasons.append("no_material")

    if bundle is None:
        where = ", ".join(searched) if searched else "каталоги не заданы"
        problems.append("бандла %s нет (искали: %s)" % (BUNDLE_GLOB, where))
        reasons.append("no_bundle")

    if problems:
        return {"ok": False, "detail": "; ".join(problems),
                "reason": "; ".join(reasons)}

    newest = max(material, key=lambda m: m.get("mtime") or 0.0)
    lag_days = ((newest.get("mtime") or 0.0) - (bundle.get("mtime") or 0.0)) / 86400.0
    lag_days = max(lag_days, 0.0)          # бандл свежее материала — не «минус дней»

    if lag_days > max_lag_days:
        others = len(material) - 1
        tail = "" if others <= 0 else " и ещё %d файл(ов)" % others
        # Причина без числа дней: отставание растёт само по себе.
        return {"ok": False, "reason": "stale", "detail":
                "бандл %s отстал на %.1f сут (порог %.0f): новее всего «%s»%s" % (
                    bundle.get("name"), lag_days, max_lag_days, newest.get("name"), tail)}

    return {"ok": True, "detail": "бандл %s, отставание %.1f сут (порог %.0f)" % (
        bundle.get("name"), lag_days, max_lag_days)}


def probe_all(http_get, disk_usage, min_disk_gb: float = MIN_DISK_GB,
              chatter_snapshot: dict | None = None,
              worktree_snapshot: dict | None = None,
              secrets_snapshot: dict | None = None) -> dict:
    """Compose the cycle's probes. ``http_get(path) -> int|None`` (HTTP status,
    or None on connection refused/timeout); ``disk_usage(path) -> (total, used,
    free)`` (shutil.disk_usage-shaped)."""
    probes = {}

    status = http_get("/health")
    backend_ok = status == 200
    probes["backend"] = {
        "ok": backend_ok,
        "detail": "HTTP %s" % status if status is not None else "no response (refused/timeout)",
        # «Не отвечает» и «отвечает 500» — разные аварии: первая про процесс,
        # вторая про код внутри живого процесса, и чинятся они по-разному.
        "reason": "no_response" if status is None else "http:%s" % status,
    }
    if backend_ok:
        for key, path in _OPS_ENDPOINTS.items():
            s = http_get(path)
            probes[key] = {"ok": s == 200, "detail": "HTTP %s" % s,
                           "reason": "no_response" if s is None else "http:%s" % s}

    try:
        _total, _used, free = disk_usage(DISK_PATH)
        free_gb = free / (1024 ** 3)
        probes["disk"] = {
            "ok": free_gb >= min_disk_gb,
            "detail": "%.1fGB free (min %.1fGB)" % (free_gb, min_disk_gb),
            # Гигабайтам в ключе дедупа не место: они дрейфуют каждый цикл.
            "reason": "low_space",
        }
    except Exception as exc:
        probes["disk"] = {"ok": False, "detail": "disk check failed: %s" % exc,
                          "reason": "unreadable"}

    # Снимок отсутствует → состав проб ПРЕЖНИЙ. Обратная совместимость тут не
    # вежливость: watchdog на старом окружении не имеет права слать DOWN о том,
    # чего он не мерил.
    if chatter_snapshot:
        cs = chatter_snapshot
        probes["chatter_runner"] = probe_chatter_runner(
            cs.get("processes"), beat_age=cs.get("runner_beat_age"),
            root=cs.get("root", ROOT))
        probes["chatter_guardian"] = probe_chatter_guardian(
            cs.get("processes"), lock_pid=cs.get("guardian_lock_pid"),
            beat_age=cs.get("guardian_beat_age"))
    if worktree_snapshot:
        probes["worktree"] = probe_worktree(worktree_snapshot)
    if secrets_snapshot:
        probes["secrets_bundle"] = probe_secrets_bundle(secrets_snapshot)
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


def _file_age(path) -> float | None:
    """Возраст файла. ⚠️ По mtime, а не по размеру: на NTFS размер файла с
    открытым write-хэндлом показывается нулём, и «пустой heartbeat» — мираж."""
    try:
        return time.time() - Path(path).stat().st_mtime
    except OSError:
        return None


def _read_lock_pid(path) -> int | None:
    try:
        return int(Path(path).read_text(encoding="utf-8", errors="ignore").strip())
    except (OSError, ValueError):
        return None


def _chatter_snapshot() -> dict | None:
    """Снимок для двух chatter-проб. psutil, а не PowerShell из питона
    (ловушка 5 спеки): psutil здесь уже используется для boot_time.

    Ошибка сбора → None, то есть пробы просто НЕ выполняются в этом цикле.
    Это осознанно: `evaluate()` замораживает состояние отсутствующих проб, и
    сбойный сбор не превращается в ложный DOWN о живом сервисе."""
    try:
        import psutil
    except Exception:
        return None
    try:
        procs = []
        for proc in psutil.process_iter(["pid", "name", "cmdline"]):
            try:
                procs.append({"pid": proc.info["pid"],
                              "name": proc.info["name"] or "",
                              "cmdline": " ".join(proc.info["cmdline"] or [])})
            except Exception:
                continue
        return {
            "processes": procs,
            "runner_beat_age": _file_age(ROOT / "state" / "chatter_heartbeat.txt"),
            "guardian_beat_age": _file_age(ROOT / "state" / "chatter_guardian_heartbeat.txt"),
            "guardian_lock_pid": _read_lock_pid(ROOT / "state" / "locks" / "chatter_guardian.pid"),
            "root": str(ROOT),
        }
    except Exception as exc:
        print("[ops_watchdog] chatter snapshot failed: %s" % exc, file=sys.stderr)
        return None


def _worktree_snapshot() -> dict | None:
    """Снимок живого дерева: ветка + модифицированные tracked-файлы.

    `None` возвращается ТОЛЬКО когда дерева нет на этой машине — тогда
    watchdog просто не на деплой-хосте и мерить ему нечего. Сбой самого git
    отдаётся как `error`, то есть проба будет КРАСНОЙ, а не пропущенной: это
    разные вещи, и вторую нельзя выдавать за первую."""
    if not LIVE_TREE.exists():
        return None
    import subprocess
    try:
        branch = subprocess.run(
            ["git", "-C", str(LIVE_TREE), "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True, text=True, timeout=GIT_TIMEOUT_S)
        status = subprocess.run(
            ["git", "-C", str(LIVE_TREE), *GIT_STATUS_ARGS],
            capture_output=True, text=True, timeout=GIT_TIMEOUT_S)
    except Exception as exc:
        return {"branch": None, "dirty": [], "error": "%s: %s" % (type(exc).__name__, exc)}
    if branch.returncode != 0 or status.returncode != 0:
        return {"branch": None, "dirty": [],
                "error": "git rc=%s/%s: %s" % (branch.returncode, status.returncode,
                                               (branch.stderr or status.stderr).strip()[:120])}
    dirty = [line for line in status.stdout.splitlines() if line.strip()]
    return {"branch": branch.stdout.strip(), "dirty": dirty, "error": None}


def _secrets_bundle_snapshot(live_tree: Path = LIVE_TREE,
                             bundle_dirs=BUNDLE_DIRS) -> dict | None:
    """Снимок: mtime материала бандла и самого свежего .jrvbak.

    ТОЛЬКО ЧТЕНИЕ — `stat` и `glob`, ни одной записи: сторож, трогающий
    секреты, портит ровно то, что сторожит.

    Отсутствующего каталога с бандлами достаточно, чтобы вернуть `bundle=None`
    — это и есть повод для алерта. `error` — другое: каталог ЕСТЬ, но не
    читается, и тогда мы не знаем, а не знаем-что-нет."""
    if not live_tree.exists():
        return None
    try:
        material = []
        # ОБА env-файла, а не «.env.enc, иначе .env». `collect_secrets`
        # предпочитает .enc, потому что его и экспортирует, — но проверка не о
        # том, что уедет в бандл, а о том, что УЖЕ изменилось. Живой прогон
        # 2026-08-10 показал цену разницы: .env от 08.08, .env.enc от 23.07,
        # бандл от 05.08 — версия «предпочесть .enc» рапортовала отставание
        # 0.0 суток, хотя рабочий .env ушёл вперёд бандла на двое суток.
        for name in (".env", ".env.enc"):
            f = live_tree / name
            if f.exists():
                material.append({"name": f.name, "mtime": f.stat().st_mtime})
        secrets_dir = live_tree / ".secrets"
        if secrets_dir.is_dir():
            for pattern in BUNDLE_MATERIAL_GLOBS:
                for f in secrets_dir.glob(pattern):
                    if f.is_file():
                        material.append({"name": f.name, "mtime": f.stat().st_mtime})

        newest = None
        searched = []
        for d in bundle_dirs:
            d = Path(d)
            searched.append(str(d))
            if not d.is_dir():
                continue
            for f in d.glob(BUNDLE_GLOB):
                if not f.is_file():
                    continue
                m = f.stat().st_mtime
                if newest is None or m > newest["mtime"]:
                    newest = {"name": f.name, "mtime": m}
        return {"material": material, "bundle": newest, "searched": searched, "error": None}
    except Exception as exc:
        return {"material": [], "bundle": None,
                "searched": [str(d) for d in bundle_dirs],
                "error": "%s: %s" % (type(exc).__name__, exc)}


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

    probes = probe_all(_http_get, _disk_usage,
                       chatter_snapshot=_chatter_snapshot(),
                       worktree_snapshot=_worktree_snapshot(),
                       secrets_snapshot=_secrets_bundle_snapshot())
    alerts, state = evaluate(state, probes, suppress_down=in_boot_grace)

    if reboot_text:
        _send_tg(reboot_text)
    for text in alerts:
        _send_tg(text)
    _write_state(state)
    return 0


if __name__ == "__main__":
    sys.exit(main())
