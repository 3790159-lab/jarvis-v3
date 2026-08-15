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
import os
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


def _transitions(prev_state: dict, probes: dict, debounce: int = DEBOUNCE,
                 suppress_down: bool = False, now: float | None = None):
    """Единственный анализатор состояний. Возвращает
    `(journal, to_owner, new_state)`.

    ДВА списка, а не один, — потому что журнал и канал алертов расходятся
    РОВНО В ОДНОМ месте, и место это названо здесь явно: подъём после
    ПОДАВЛЕННОГО падения (см. `journal_only` ниже). Списки держат одни и те же
    объекты переходов, второго анализатора не появляется: `transitions()`
    берёт первый, `evaluate()` — второй.

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
    # isinstance-щит — зеркало `detect_reboot()` ниже, и по той же причине.
    # Цена падения здесь несоразмерна: `main()` исключение не ловит, а обёртка
    # `ops_watchdog_detached.ps1` пишет heartbeat ДО цикла. Traceback не
    # пропадает — он уезжает в `state/logs/ops_watchdog.stdout.log`, — но в тот
    # лог никто не смотрит, пока не заподозрит неладное. Петля жива, heartbeat
    # свеж, все наблюдатели видят ЗДОРОВЫЙ сторож, а алертов нет НИКОГДА. Живой
    # стейт сегодня из одних словарей, но журнал кладёт в тот же файл служебные
    # ключи.
    new_state = {k: (dict(v) if isinstance(v, dict) else v)
                 for k, v in prev_state.items()}
    out = []
    to_owner = []

    def fire(check, kind, res, reason, journal_only=False):
        t = {"ts": now, "check": check, "kind": kind,
             "reason": reason, "detail": res.get("detail", "")}
        out.append(t)
        if not journal_only:
            to_owner.append(t)

    for check, res in probes.items():
        entry = new_state.get(check)
        # Второй щит, на взятии записи: верхний оставил не-словарь как есть,
        # и `dict(scalar)` упал бы уже здесь.
        st = dict(entry) if isinstance(entry, dict) else {"fail": 0, "alerted": False}
        reason = str(res.get("reason") or check)
        if res.get("ok"):
            if st.get("alerted"):
                fire(check, "recovered", res, reason)
            elif isinstance(st.get("fail"), (int, float)) and st["fail"] > 0:
                # Исход «поднялось само» из §4.6. Писателю он был НЕДОСТИЖИМ:
                # `recovered` стоял за `alerted`, которого `suppress_down`
                # намеренно не ставит, — и проба, у которой в журнале были
                # только `suppressed`, при подъёме не давала НИЧЕГО. Инцидент,
                # рассосавшийся сам, навсегда оставался на экране как «исход
                # пока неизвестен».
                #
                # РАЗДЕЛЕНИЕ ЯВНОЕ: в ЖУРНАЛ пишем по факту «этот чек был
                # красным», ВЛАДЕЛЬЦУ не говорим. ✅ о подъёме того, о падении
                # чего мы промолчали, — это ✅ ни о чём, и контракт §3 оно бы
                # сломало.
                fire(check, "recovered", res, reason, journal_only=True)
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
                # то событие, ради которого журнал и заводится. Но РОВНО ОДИН
                # РАЗ (ловушка 4 обещает две записи на падение, не десять):
                #
                #   `alerted` тут не ставится по построению, значит дедупить им
                #   нечего, а нового ключа в состоянии заводить нельзя —
                #   `evaluate()` возвращает это состояние наружу, и её контракт
                #   §3 неприкосновенен. Поэтому дедуп по МОМЕНТУ пересечения
                #   порога: `fail` растёт по единице и обнуляется только
                #   восстановлением, так что равенство наступает ровно один раз
                #   за падение. До правки это было `>=`, и при BOOT_GRACE_S=300
                #   с циклом 30 с одно падение давало девять записей, а после
                #   ребута красны все девять проб — под 80 записей на инцидент
                #   при потолке журнала 5000 (§2.4).
                #
                #   А о падении, про которое владельцу УЖЕ сказали, `suppressed`
                #   не пишется вовсе: §2.3 определяет его как «событие, о
                #   котором НЕ сообщили». Состояние переживает ребут, и без
                #   этого условия проверка, объявленная красной днями раньше,
                #   рисовала бы на панели новый инцидент про старое падение.
                if not st.get("alerted") and st["fail"] == debounce:
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
    return out, to_owner, new_state


def transitions(prev_state: dict, probes: dict, debounce: int = DEBOUNCE,
                suppress_down: bool = False, now: float | None = None):
    """ЖУРНАЛ: свернуть пробы этого цикла в состояние и вернуть ПЕРЕХОДЫ.

    Отделено от `evaluate()` 14.08, когда переходы понадобились журналу панели
    структурой, а не текстом: по фразе с эмодзи нельзя ни отсортировать, ни
    сгруппировать, ни схлопнуть пару «подавлено → подтверждено».

    ``probes``     : {check_key: {"ok": bool, "detail": str, "reason": str}} —
                     только проверки, реально выполненные в этом цикле (мёртвый
                     бэкенд не даёт своих под-проверок).
    ``prev_state`` : {check_key: {"fail": int, "alerted": bool,
                                  "alerted_reason": str}}

    Возвращает `(transitions: list[dict], new_state: dict)`. Переход несёт
    РОВНО пять полей §2.2 — `{"ts", "check", "kind", "reason", "detail"}`, и
    шестого не заводится: PID, `hb_age` и `age_days` меняются каждый цикл и не
    значат ничего. kind ∈ {down, recovered, changed, suppressed}. Проверки,
    отсутствующие в `probes`, сохраняют прежнее состояние дословно
    (заморожены, никогда не «восстанавливаются» сами).
    """
    journal, _to_owner, new_state = _transitions(
        prev_state, probes, debounce, suppress_down, now)
    return journal, new_state


# Виды переходов, о которых владельцу СООБЩАЮТ. `suppressed` сюда не входит по
# определению: это падение в загрузочном окне, о котором мы намеренно молчим.
ALERTING_KINDS = ("down", "recovered", "changed")


def evaluate(prev_state: dict, probes: dict, debounce: int = DEBOUNCE,
             suppress_down: bool = False):
    """Тексты алертов из переходов. Обёртка над тем же ядром, что и
    `transitions()`; сигнатура и поведение неизменны — на них стоят тесты и
    мутационный гейт (`scripts/mutate_ops_watchdog.py`).

    Берётся `to_owner`, а не весь журнал: единственный переход, который
    существует ТОЛЬКО для журнала, — подъём после подавленного падения.
    `ALERTING_KINDS` при этом остаётся вторым, независимым фильтром: `suppressed`
    в `to_owner` попадает и отсеивается здесь.

    Returns ``(alerts: list[str], new_state: dict)``.
    """
    _journal, to_owner, new_state = _transitions(
        prev_state, probes, debounce, suppress_down)
    alerts = [build_alert(t["check"], t["kind"], t["detail"])
              for t in to_owner if t["kind"] in ALERTING_KINDS]
    return alerts, new_state


# ── Журнал переходов для панели Джарвиса (спека 2026-08-14, §2) ────────────
#
# Панель отвечает «что сейчас» и не умеет ответить «что случилось, пока меня не
# было»: падение, поднятое гардианом за минуту, для неё неотличимо от того, что
# не случалось. Переходы уже вычислены выше — журнал лишь перестаёт их забывать.
JOURNAL_PATH = ROOT / "state" / "panel_events.jsonl"
JOURNAL_BEAT = ROOT / "state" / "panel_events.heartbeat"
JOURNAL_MAX_AGE_S = 30 * 86400          # 30 суток
JOURNAL_MAX_RECORDS = 5000              # предохранитель на шторм рестартов
# `math` ради одной проверки не импортируется: граница §2.1 держится тем, что
# шапка этого файла остаётся такой, какой её читает сторож stdlib-only.
_TS_INF = float("inf")


def _record_ts(rec) -> float | None:
    """Время записи как число — или None, если его нет и получить неоткуда.

    Отдельной функцией, потому что голый `float(rec.get("ts") or 0.0)` из этого
    файла БРОСАЕТ, и цена несоразмерна: `main()` исключение не ловит, а обёртка
    `ops_watchdog_detached.ps1` пишет heartbeat ДО цикла. Traceback уезжает в
    `state/logs/ops_watchdog.stdout.log` и там лежит непрочитанным. Петля жива,
    heartbeat свеж — все наблюдатели видят ЗДОРОВЫЙ сторож, который не алертит
    НИКОГДА. Проверено фактом: `ts: "вчера"` даёт ValueError.

    Вход не гипотетический: файл живёт 30 суток, переживает выкатки и читается
    поколением кода, которое его не писало. `True` и `NaN` названы явно —
    `float()` их принимает (1.0 и nan), и запись уехала бы в журнал с временем,
    которого у неё нет.

    `OverflowError` в перехвате не для симметрии: `float()` на большом ЦЕЛОМ
    бросает именно его, а не ValueError — `float(10**400)` даёт
    «int too large to convert to float» (проверено). Строка `{"ts": 10**400}`
    в jsonl законна, и без этого имени докстринг выше обещал бы то, чего нет.
    """
    try:
        ts = rec.get("ts")
    except AttributeError:
        return None                     # это вообще не запись
    if ts is None or isinstance(ts, bool):
        return None
    try:
        ts = float(ts)
    except (TypeError, ValueError, OverflowError):
        return None
    # Временем считаем только КОНЕЧНОЕ число. NaN не сравнивается сам с собой,
    # а ±inf приезжает из настоящей строки: `json.loads('{"ts": 1e400}')` даёт
    # inf, туда же строка "1e400" (проверено). Пустить его дальше значит завести
    # бессмертную запись: +inf по возрасту не истечёт НИКОГДА и под потолком
    # сортируется как самая свежая — вытесняя настоящую; -inf, наоборот, уедет
    # с маркером «старше 30 сут», хотя времени у неё нет.
    if ts != ts or ts == _TS_INF or ts == -_TS_INF:
        return None
    return ts


def _plural(n: int, one: str, few: str, many: str) -> str:
    """Форма слова при числе: 1 запись, 2 записи, 5 записей, 11 записей.

    Заведена не ради красоты: числа в маркере не константы — потолок приходит
    аргументом, и на нестандартном потолке выходило «в 3 записей». В сторожевом
    сообщении несогласование читается как опечатка, а не как факт.
    """
    tail = abs(n) % 100
    if 11 <= tail <= 14:                # 11-14 идут по «многим», а не по цифре
        return many
    tail %= 10
    if tail == 1:
        return one
    return few if 2 <= tail <= 4 else many


def journal_trim(records: list, now: float,
                 max_age_s: float = JOURNAL_MAX_AGE_S,
                 max_records: int = JOURNAL_MAX_RECORDS) -> tuple[list, int, str]:
    """(оставшиеся, сколько отброшено, чем именно резали).

    Два предохранителя, «что раньше»: возраст — естественная единица для
    вопроса «что было с прошлого раза», объём — защита от шторма рестартов,
    который набьёт тысячи строк за сутки.

    Пустое `why` означает «ничего не отброшено», и маркер тогда НЕ пишется:
    маркер на каждой дозаписи — шум, а не сигнал. Причины в `why` названы
    порознь: «отброшено 400» без второй причины отправит владельца искать
    шторм рестартов там, где его не было.

    Порядок записей сохраняется как в файле — обрезка только УБИРАЕТ строки и
    не берётся заодно пересортировать журнал.
    """
    kept, by_age, by_no_ts = [], 0, 0
    for rec in records:
        ts = _record_ts(rec)
        if ts is None:
            by_no_ts += 1               # не «старая» — про неё нечего сказать
        elif (now - ts) > max_age_s:
            by_age += 1
        else:
            # Запись из будущего попадает сюда же: `now - ts` у неё отрицателен.
            # Часы прыгают после загрузки (NTP), а записи о ребуте пишутся в
            # первые же минуты — такая запись «не старая», а не «старее всех».
            kept.append((ts, rec))

    by_count = max(0, len(kept) - max_records)
    if by_count:
        # Режем САМЫЕ СТАРЫЕ ПО ВРЕМЕНИ, а не первые по файлу. Это одно и то же
        # только на отсортированном входе, а гарантии сортировки нет ниоткуда:
        # файл переживает ребуты и сползание часов. Проверено фактом — срез
        # `kept[by_count:]` на неотсортированном журнале оставил обе древние
        # записи и выбросил две свежие, то есть ровно то, ради чего журнал
        # заводился. Тайбрейка по позиции в ключе нет намеренно: `sorted`
        # стабилен, а сортируется `range(len(kept))` — значит одинаковые времена
        # и так остаются в порядке возрастания позиции. Ключ `(ts, i)` стоял
        # здесь ради определённости, которой он не добавлял: 20 000 случайных
        # входов с ничьими дали 0 расхождений между двумя ключами.
        order = sorted(range(len(kept)), key=lambda i: kept[i][0])
        doomed = set(order[:by_count])
        kept = [pair for i, pair in enumerate(kept) if i not in doomed]
    kept = [rec for _ts, rec in kept]

    dropped = by_age + by_no_ts + by_count
    if not dropped:
        return kept, 0, ""
    parts = []
    if by_age:
        # Дословно по образцу §2.4: «отброшено 812 записей старше 30 сут».
        # Слово «записей» не украшение — писатель подставляет `why` в текст для
        # владельца, и без него маркер приезжает к нему обрубком.
        parts.append("%d %s старше %d сут"
                     % (by_age, _plural(by_age, "запись", "записи", "записей"),
                        int(max_age_s // 86400)))
    if by_no_ts:
        # «строк», а не «записей»: сюда попадает и запись с нечитаемым `ts`, и
        # то, что записью не является вовсе (None, строка, список). Строкой
        # файла оно было в обоих случаях, а вот назвать его записью значило бы
        # соврать — тем же самым способом, каким врало бы «старше 30 сут».
        #
        # Оговорка после появления `_journal_read_counted`: ИЗ ФАЙЛА не-запись
        # сюда больше не доходит — её отсеивают раньше и считают отдельно
        # (в маркере она названа «нечитаемой строкой»). Формулировка оставлена
        # потому, что контракт функции шире её нынешнего вызова: список ей
        # передают любой, и «мусор» на входе остаётся законным случаем, а не
        # фантазией.
        parts.append("%d %s без пригодного времени"
                     % (by_no_ts, _plural(by_no_ts, "строка", "строки", "строк")))
    if by_count:
        parts.append("%d %s сверх потолка в %d"
                     % (by_count, _plural(by_count, "запись", "записи", "записей"),
                        max_records))
    return kept, dropped, " и ".join(parts)


# Служебная «проверка» маркера ротации — тем же приёмом, что и `BOOT_KEY`:
# ключ, который проверкой фермы не является. Панель обязана отличать его от
# настоящей пробы: маркер с `check: "backend"` она нарисовала бы как инцидент
# бэкенда, которого не было.
JOURNAL_SELF = "_journal"
# А вот ВИД записи вынесен константой по другой причине: на него смотрят ДВА
# места — сборка маркера и его отсев перед обрезкой, — и разъехавшись они дали
# бы ровно ту аварию, которую §7 запрещает (маркеры перестали бы схлопываться и
# заняли бы слоты потолка). У `JOURNAL_SELF` такой пары нет: место одно.
JOURNAL_ROTATED = "rotated"


def _journal_line(rec) -> str | None:
    """Одна строка jsonl — или None, если запись не сериализуется вовсе.

    `json.dumps` БРОСАЕТ на bytes, множестве, нестроковом ключе и цикличной
    ссылке, а цена исключения здесь та же, что у `_record_ts`: `main()` его не
    ловит, а обёртка `ops_watchdog_detached.ps1` пишет heartbeat ДО цикла. Сам
    traceback не пропадает — обёртка сливает stderr в конвейер, и он уезжает в
    `state/logs/ops_watchdog.stdout.log` строкой «py: ...», — но в этот лог
    никто не смотрит, пока не заподозрит неладное. Петля жива, heartbeat свеж,
    алертов нет НИКОГДА: сторож выглядит здоровым.

    `default=repr` — выбор цены: поле, которое json не умеет, стоит своего
    `repr`, а не всей записи о падении. ЗНАЧЕНИЕ при этом остаётся видимым.
    Парного ему `skipkeys=True` здесь нет намеренно: он не оставлял ничего, а
    СТИРАЛ поле с нестроковым ключом — молча, с возвратом `True` и обновлённым
    маркером живости. Это была единственная молчаливая потеря во всём писателе,
    и мутация «снят только skipkeys» переживала весь гейт. Теперь такая запись
    идёт по громкому пути ниже. Цикличную ссылку не спасает ничто — о ней
    говорят вслух (DEV-18), см. `journal_append`.

    Не-словарь отвергается ЗДЕСЬ, хотя `json.dumps` его и написал бы: обратно
    его не прочитает никто — `journal_read` пропускает всё, что не словарь, — и
    строка `42` в файле была бы не записью, а молчаливой потерей. Отказ
    симметричен чтению и слышен на stderr.
    """
    if not isinstance(rec, dict):
        return None
    try:
        return json.dumps(rec, ensure_ascii=False, default=repr) + "\n"
    except (TypeError, ValueError, RecursionError):
        return None


def _journal_read_counted(path) -> tuple[list, int]:
    """Записи журнала И число строк, которые записями не стали.

    Счётчик нужен ровно одному месту — перезаписи при обрезке (`journal_append`):
    она эти строки СТИРАЕТ, и без счёта потеря была бы молчаливой, то есть ровно
    тем классом дефекта, ради которого журнал и заводится. Воспроизведено
    фактом: три нечитаемых строки, включая огрызок настоящего события, ушли из
    файла без единого слова.

    Читателю (панели) счётчик не нужен и врал бы: последняя строка живого файла
    бывает без `\\n` просто потому, что её дописывают прямо сейчас. Поэтому
    считает только писатель — и только после того, как сам дописал `\\n`.
    """
    out, unreadable = [], 0
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return out, 0
    for line in text.split("\n"):
        # BOM в списке: файл живёт 30 суток и переживает открытие человеком, а
        # Блокнот и PowerShell `>` ставят его в начало. `json.loads` на нём
        # бросает, и ценой была бы ПЕРВАЯ запись файла — самая старая, то есть
        # ровно та, ради которой в журнал и заглядывают.
        line = line.strip(" \t\r\ufeff")
        if not line:
            continue                    # хвостовой перевод строки — не потеря
        try:
            rec = json.loads(line)
        except ValueError:
            unreadable += 1
            continue
        if isinstance(rec, dict):
            out.append(rec)
        else:
            unreadable += 1             # `42` в файле записью не станет никогда
    return out, unreadable


def journal_read(path) -> list:
    """Записи журнала. Битые строки пропускаются молча и ПО ОДНОЙ: панель читает
    файл, который в этот момент дописывает этот процесс, и последняя строка
    может быть без `\\n`. Она приедет целиком через секунду.

    «Молча» — это про ЧТЕНИЕ. Стирает такие строки перезапись при обрезке, и там
    они названы числом: см. `_journal_read_counted` и маркер в `journal_append`.

    Пропуск — `continue`, а не выход: остановка на первой непонятной строке
    стоила бы всего журнала ПОСЛЕ неё, а следом за чтением идёт перезапись —
    непрочитанное было бы стёрто.

    Делим РОВНО по `"\\n"`, а не `splitlines()`. Формат — newline-delimited
    JSON, а `splitlines()` режет текст ещё и по U+2028/U+2029/U+0085, которые
    `json.dumps(ensure_ascii=False)` пишет В СЫРОМ ВИДЕ (проверено фактом):
    запись с таким символом в `detail` уезжала одной строкой файла и читалась
    как две битых, то есть событие терялось молча.
    """
    return _journal_read_counted(path)[0]


def _needs_seam(path) -> bool:
    """Кончается ли файл ОБОРВАННОЙ строкой, к которой нельзя дописывать встык.

    Огрызок без `\\n` оставляет смерть процесса посреди записи. Без шва
    следующая строка приклеивается к нему, и битой становится ОНА тоже: одна
    оборванная запись стоила бы двух — старой и новой.
    """
    try:
        with open(path, "rb") as fh:
            if fh.seek(0, 2) == 0:
                return False            # файла нет или он пуст — дописывать не к чему
            fh.seek(-1, 2)
            return fh.read(1) != b"\n"
    except OSError:
        return False


def _rotated_detail(why: str, collapsed: int, unreadable: int) -> str:
    """Текст маркера: что отрезано ПЛЮС что стёрто перезаписью заодно.

    Перезапись при обрезке уносит из файла не только то, что посчитала
    `journal_trim`. Уходят прежние маркеры (их схлопывают, иначе они копятся и
    заливают первый экран) и строки, которые `journal_read` не смог разобрать
    (огрызок убитой записи, `42`, мусор). Обе потери молчали, а молчаливая
    потеря — ровно тот класс дефекта, ради которого журнал заведён. Занижение
    было не мелким: потолок 20 и 50 дозаписей давали маркер «отброшено 1 запись
    сверх потолка в 20» при 50 выброшенных настоящих записях (проверено фактом).

    Прежние отметки названы ЧИСЛОМ, а не суммой: каждая отметка — это одна
    состоявшаяся обрезка, то есть не меньше одной потерянной записи. Это честная
    нижняя граница. Точная сумма потребовала бы либо шестого поля (§2.2 называет
    ровно пять), либо разбора собственного текста обратно в число — и то, и
    другое дороже, чем ответ «обрезка уже случалась раньше», которого владельцу
    здесь не хватало.
    """
    detail = "отброшено " + why
    also = []
    if collapsed:
        also.append("%d %s об обрезке (каждая — не меньше одной потерянной "
                    "записи)"
                    % (collapsed, _plural(collapsed, "прежняя отметка",
                                          "прежние отметки", "прежних отметок")))
    if unreadable:
        also.append("%d %s"
                    % (unreadable, _plural(unreadable, "нечитаемая строка",
                                           "нечитаемые строки",
                                           "нечитаемых строк")))
    if also:
        detail += "; перезаписью стёрто ещё " + " и ".join(also)
    return detail


def journal_append(records: list, *, path=None, now=None, mkdir: bool = True,
                   max_age_s: float = JOURNAL_MAX_AGE_S,
                   max_records: int = JOURNAL_MAX_RECORDS,
                   trim_report: dict | None = None) -> bool:
    """Дозаписать переходы и при необходимости обрезать журнал.

    True — записали (или писать было нечего); False — не смогли, целиком или
    частью. Возврат важен: маркер живости обновляется ТОЛЬКО при True, иначе
    провал записи утонул бы в тишине (§2.5 спеки).

    Провал ОБРЕЗКИ в этот ответ не входит — он громкий на stderr, но записи
    доехали, и следующий цикл дорежет. Почему разведено именно так, см.
    перехват `OSError` вокруг перезаписи ниже: приравняв обрезку к записи, мы
    дарили владельцу ложную 🚨 каждый раз, когда панель открывала журнал в тот
    же миг, в который его резали.

    Обрезка идёт только в момент дозаписи, а дозапись — редкое событие (единицы
    в сутки), поэтому в обычном цикле она ничего не стоит: файла даже не
    открываем.

    МАРКЕРЫ НЕ ЗАНИМАЮТ МЕСТА ПОД ПОТОЛКОМ, и в файле их не больше одного.
    Причина не в аккуратности, а в арифметике (воспроизведено фактом): маркер
    дописывается в ТОТ ЖЕ файл, а потолок режет самые СТАРЫЕ записи — значит
    маркер, самая свежая запись, не вымывается никогда. Журнал на потолке терял
    по ДВЕ настоящих записи за дозапись ради одной новой, и за восемь циклов в
    файле оказывалось восемь маркеров: ровно в шторме рестартов — сценарии,
    ради которого потолок и заведён, — журнал деградировал к «журнал обрезан
    ×8» вместо событий.

    Из двух средств, названных в плане, выбрано СХЛОПЫВАНИЕ (маркеры отсеиваются
    перед обрезкой и заменяются одним новым), а не только «считать потолок по
    не-`rotated`»: второе лечит вытеснение событий, но не копление самих
    маркеров — их не убирает ничто, кроме возраста, и восемь строк «журнал
    обрезан» подряд всё равно уехали бы на первый экран. Схлопывание даёт оба
    свойства сразу и лечит файл, доставшийся от прежнего писателя.
    Цена названа прямо: числа ПРЕЖНИХ ротаций не суммируются — маркер отвечает
    на вопрос «этому списку не хватает записей», а не «сколько их выпало за
    месяц»; накопительный итог потребовал бы либо шестого поля (§2.2 называет
    ровно пять), либо разбора собственного текста обратно в число. Но само
    ЧИСЛО схлопнутых отметок в новый маркер входит: каждая из них — это одна
    состоявшаяся обрезка, то есть честная нижняя граница потери. См.
    `_rotated_detail`.
    """
    now = time.time() if now is None else now
    # Вердикт об ОБРЕЗКЕ отдаётся отдельным каналом, а не возвратом: возврат
    # отвечает на «записали ли», он под мутационным гейтом, и приравнять к нему
    # обрезку значило бы дарить владельцу ложную 🚨 на каждом миллисекундном
    # пересечении с панелью. Считает серию `note_trim_health`.
    if trim_report is not None:
        trim_report.update({"attempted": False, "ok": False, "error": ""})
    if not records:
        return True                      # незачем заводить файл ради пустоты
    p = Path(path or JOURNAL_PATH)

    lines, lost = [], 0
    for rec in records:
        line = _journal_line(rec)
        if line is None:
            lost += 1
            print("[ops_watchdog] запись журнала не сериализуется: %.200r" % (rec,),
                  file=sys.stderr)
        else:
            lines.append(line)
    if not lines:
        return False                     # всё, что было, уже названо на stderr

    try:
        if mkdir:
            p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "a", encoding="utf-8", newline="") as f:
            f.write(("\n" if _needs_seam(p) else "") + "".join(lines))
    except OSError as exc:
        print("[ops_watchdog] журнал не записан: %s" % exc, file=sys.stderr)
        return False

    try:
        on_disk, unreadable = _journal_read_counted(p)
        events = [r for r in on_disk if r.get("kind") != JOURNAL_ROTATED]
        collapsed = len(on_disk) - len(events)
        kept, dropped, why = journal_trim(events, now, max_age_s, max_records)
        if dropped:
            kept.append({"ts": now, "check": JOURNAL_SELF, "kind": JOURNAL_ROTATED,
                         "reason": "trim",
                         "detail": _rotated_detail(why, collapsed, unreadable)})
            # Отдельный `.tmp` + `os.replace`, а НЕ `open(p, "w")` поверх живого
            # файла: смерть процесса посреди перезаписи оставила бы журнал
            # усечённым тем, что не доехало. Здесь тот же kill оставляет прежний
            # файл ЦЕЛЫМ (и необрезанным — лишние записи не потеря, следующий
            # цикл дорежет), а `.tmp` подберёт `open(..., "w")` следующего
            # прогона. `os.replace` атомарен в пределах тома — `.tmp` лежит
            # рядом с журналом именно поэтому.
            if trim_report is not None:
                trim_report["attempted"] = True
            tmp = p.with_name(p.name + ".tmp")
            rewritten = []
            for rec in kept:
                line = _journal_line(rec)
                if line is None:
                    # Ветка почти недостижима ИЗ ФАЙЛА: `journal_read` отдаёт
                    # только словари, а всё, что приехало из json, в json и
                    # уедет. Она стоит здесь не для симметрии — без неё `join`
                    # получил бы None и бросил TypeError МИМО `except OSError`
                    # ниже, то есть цикл сторожа умер бы целиком при живом
                    # heartbeat. Но молчать ей нельзя: молчаливая потеря — это
                    # ровно тот дефект, ради которого журнал заведён, поэтому
                    # запись названа вслух и учтена в возврате.
                    lost += 1
                    print("[ops_watchdog] запись не пережила перезапись "
                          "журнала: %.200r" % (rec,), file=sys.stderr)
                else:
                    rewritten.append(line)
            with open(tmp, "w", encoding="utf-8", newline="") as f:
                f.write("".join(rewritten))
            os.replace(tmp, p)
            if trim_report is not None:
                trim_report["ok"] = True
    except OSError as exc:
        # ПРОВАЛ ОБРЕЗКИ — НЕ ПРОВАЛ ЗАПИСИ, и мешать их дорого. На Windows
        # `os.replace` бросает `[WinError 5] Отказано в доступе`, если приёмник
        # ОТКРЫТ любым читателем, — а читатель этого журнала есть, это панель
        # (проверено фактом: открытый на чтение файл даёт PermissionError,
        # журнал при этом цел, `.tmp` остаётся). Записи в файле, следующий цикл
        # дорежет; вернуть здесь False значит послать владельцу 🚨 «журнал не
        # пишется» и НЕ обновить маркер живости — панель ещё 180 с будет писать
        # «писатель молчит» из-за миллисекундного пересечения с самой собой.
        # Вероятность максимальна ровно в шторме рестартов: журнал на потолке
        # режется на КАЖДОЙ дозаписи, а панель в этот момент обновляют непрерывно.
        # Молчать при этом нельзя (DEV-18): необрезанный журнал растёт. Но и
        # stderr МАЛО — в этот лог никто не смотрит, пока не заподозрит
        # неладное. Провал уезжает вызывающему, и `note_trim_health` поднимает
        # тревогу на серии: одиночные промахи — гонка, десять подряд — блокировка.
        if trim_report is not None:
            trim_report.update({"attempted": True, "ok": False, "error": str(exc)})
        print("[ops_watchdog] журнал не обрезан: %s" % exc, file=sys.stderr)
    return not lost


def touch_beat(path=None, now: float | None = None) -> bool:
    """Маркер живости писателя. Раз в цикл, В КОНЦЕ и только при успехе.

    Пустой журнал двусмыслен: «переходов не было» и «писатель молчит» выглядят
    одинаково. Свежий маркер + пустой журнал = настоящая тишина; протухший
    маркер = писатель мёртв или не пишет.

    Существующий `state/ops_watchdog_heartbeat.txt` для этого не годится: его
    пишет PowerShell-обёртка, то есть он доказывает живость ОБЁРТКИ, а не то,
    что питоновский цикл дошёл до конца.

    Панель читает mtime, а не содержимое: время внутри — для человека с `type`,
    и оборванная на середине числа запись не должна ничего значить.
    """
    p = Path(path or JOURNAL_BEAT)
    stamp = time.time() if now is None else now
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "w", encoding="ascii", newline="") as f:
            f.write(str(int(stamp)))
        return True
    except OSError as exc:
        print("[ops_watchdog] маркер живости не обновлён: %s" % exc, file=sys.stderr)
        return False


JOURNAL_BROKEN_ALERT = "🚨 Журнал панели не пишется — списку событий верить нельзя"
JOURNAL_FIXED_ALERT = "✅ Журнал панели снова пишется"

# Служебный ключ счётчика провалов обрезки — тем же приёмом, что `BOOT_KEY`.
JOURNAL_TRIM_KEY = "_journal_trim"
# Сколько провалов ПОДРЯД считать блокировкой, а не гонкой. Число не на глаз:
# замер 15.08 на журнале под потолок дал самую длинную серию подряд = 1 при
# трёх браузерах, жмущих обновление раз в секунду (доля времени, когда файл
# открыт читателем, — 5%). 10 циклов по 30 с — это пять минут СПЛОШНОЙ
# блокировки, с гонкой чтения не пересекается ни при каком темпе.
JOURNAL_TRIM_FAIL_STREAK = 10
JOURNAL_TRIM_STUCK_ALERT = (
    "🚨 Журнал панели не обрезается %d циклов подряд (%s). Файл растёт без "
    "предела, а первый экран платит за его разбор. Скорее всего, "
    "state/panel_events.jsonl держит открытым чужой процесс.")
JOURNAL_TRIM_FIXED_ALERT = "✅ Журнал панели снова обрезается"


def note_journal_health(prev_state: dict, ok: bool) -> tuple[list, dict]:
    """(что сказать владельцу, новое состояние). Чистая функция.

    Провал записи — СОСТОЯНИЕ, а не событие: диск полон и через минуту, и через
    час. Без дедупа владелец получал бы 🚨 каждые 30 секунд — 120 сообщений в
    час ровно за тот дефект, который этот файл дедупом по причине уже чинил
    (чек worktree, 1669 циклов, 11.08). Правдивость каждого отдельного 🚨 не
    спасает: заваленный ими владелец перестаёт читать все.

    Парное ✅ обязательно и не для симметрии: молчание после 🚨 неотличимо от
    «всё ещё сломано», и владелец либо ходит проверять руками, либо перестаёт
    верить и молчанию тоже.

    Ключ `JOURNAL_SELF` в файле состояния — тем же приёмом, что и `BOOT_KEY`:
    проверки с таким именем нет, а `transitions()` трогает только ключи из
    `probes` и остальные копирует дословно.
    """
    new_state = {k: (dict(v) if isinstance(v, dict) else v)
                 for k, v in prev_state.items()}
    entry = prev_state.get(JOURNAL_SELF)
    entry = entry if isinstance(entry, dict) else {}
    was_broken = bool(entry.get("alerted"))

    if ok:
        new_state.pop(JOURNAL_SELF, None)
        return ([JOURNAL_FIXED_ALERT] if was_broken else []), new_state
    new_state[JOURNAL_SELF] = {"alerted": True}
    return ([] if was_broken else [JOURNAL_BROKEN_ALERT]), new_state


def note_trim_health(prev_state: dict, report: dict,
                     streak: int = JOURNAL_TRIM_FAIL_STREAK) -> tuple[list, dict]:
    """(что сказать владельцу, новое состояние). Чистая функция.

    Провал ОБРЕЗКИ намеренно не приравнен к провалу записи: записи доехали, а
    гонка чтения с панелью даёт одиночные промахи, и 🚨 на каждый был бы шумом
    (измерено: 1.5% попыток при трёх браузерах). Но «следующий цикл дорежет» —
    механизм только пока провалы ОДИНОЧНЫЕ. Внешняя блокировка (файл открыт
    инструментом, который держит хэндл) ломает его насовсем, и без счётчика об
    этом говорил бы ровно один stderr, в который никто не смотрит, пока не
    заподозрит неладное. Журнал при этом рос бы без предела.

    ТРИ исхода цикла, а не два, и различать их обязательно:
      · обрезка прошла  → серия забыта, и о починке сказано, если жаловались;
      · обрезка провалилась → +1 к серии;
      · обрезка НЕ ПОНАДОБИЛАСЬ → счётчик ЗАМОРОЖЕН. «Не понадобилась» это не
        «прошла»: сбросив серию здесь, мы гасили бы тревогу тишиной ровно
        тогда, когда переходов нет, а файл лежит заблокированным.

    Счётчик с диска читается щитом: файл состояния переживает выкатки и правки
    руками, а голый `int()` на строке уронил бы цикл при живом heartbeat.
    """
    new_state = {k: (dict(v) if isinstance(v, dict) else v)
                 for k, v in prev_state.items()}
    entry = prev_state.get(JOURNAL_TRIM_KEY)
    entry = entry if isinstance(entry, dict) else {}
    fails = _int_or_none(entry.get("fails")) or 0
    alerted = bool(entry.get("alerted"))

    if not report.get("attempted"):
        return [], new_state
    if report.get("ok"):
        new_state.pop(JOURNAL_TRIM_KEY, None)
        return ([JOURNAL_TRIM_FIXED_ALERT] if alerted else []), new_state

    fails += 1
    out = []
    if fails >= streak and not alerted:
        out.append(JOURNAL_TRIM_STUCK_ALERT
                   % (fails, report.get("error") or "причина не названа"))
        alerted = True
    new_state[JOURNAL_TRIM_KEY] = {"fails": fails, "alerted": alerted}
    return out, new_state


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


def reboot_record(boot_time: float, now: float) -> dict:
    """Ребут как запись журнала. Отдельной функцией, потому что приходит он
    ВНЕ `transitions()` — и журнал, собранный только из неё, потерял бы ровно
    то событие, ради которого заводился DEV-24: сервисы после ребута подняты
    гардианами, то есть все пробы зелёные и переходов нет вовсе.

    `check` — тот же `BOOT_KEY`, что и в стейте: проверки с таким именем не
    существует, и в `LABELS` его нет намеренно (о ребуте говорит своё
    сообщение, не `build_alert`). Пять полей §2.2, шестого нет.

    Текст берётся у `reboot_alert_text()`, а не пишется заново: одна
    формулировка на пуш и на журнал значит, что владелец, сверяя телефон с
    панелью, читает одно и то же событие одними словами.
    """
    return {"ts": now, "check": BOOT_KEY, "kind": "reboot", "reason": "boot_id",
            "detail": reboot_alert_text(boot_time, now)}


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
    # Ядро зовётся НАПРЯМУЮ, а не через `transitions()` + `evaluate()`: второе
    # свернуло бы пробы в состояние ДВАЖДЫ, и владелец получил бы по два 🚨 на
    # падение. Тексты берутся из `to_owner`, а не из журнала: единственный
    # переход, существующий только для журнала, — подъём после ПОДАВЛЕННОГО
    # падения, и ✅ о том, о чём не было 🚨, читается как «чинили без меня».
    journal, to_owner, state = _transitions(
        state, probes, suppress_down=in_boot_grace)
    alerts = [build_alert(t["check"], t["kind"], t["detail"]) for t in to_owner]

    if reboot_text and boot_time is not None:
        # В НАЧАЛО списка: ребут объясняет всё, что записано следом.
        journal.insert(0, reboot_record(boot_time, time.time()))

    if reboot_text:
        _send_tg(reboot_text)
    for text in alerts:
        _send_tg(text)

    # Маркер живости — В КОНЦЕ и только при успехе: провал записи обязан
    # показывать себя протухающим маркером, а не тонуть в тишине (§2.5).
    trim_report = {}
    written = journal_append(journal, trim_report=trim_report)
    if written:
        touch_beat()
    journal_alerts, state = note_journal_health(state, written)
    trim_alerts, state = note_trim_health(state, trim_report)
    for text in journal_alerts + trim_alerts:
        _send_tg(text)

    # Стейт пишется ПОСЛЕДНИМ: в нём теперь живёт и дедуп жалобы на журнал,
    # а он обязан пережить цикл, иначе 🚨 повторится через 30 секунд.
    _write_state(state)
    return 0


if __name__ == "__main__":
    sys.exit(main())
