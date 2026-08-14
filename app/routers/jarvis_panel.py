"""Панель Джарвиса — фаза 0 read-only + заход 1 «вид»
(docs/superpowers/specs/2026-08-14-jarvis-panel-answer-view.md).

НИ ОДНОЙ мутирующей ручки. Ни рестарта, ни kill, ни ротации ключей. Причина не
в лени: каждая мутация из веба — новый путь к проду в обход существующих
предохранителей (скоупленный kill по точному PID, дебаунс гардиана, ACL на
.secrets). Пока путь не спроектирован — кнопки нет. Кнопка «ткнути гардіана»
(вариант B) появится ПОСЛЕ журнала событий: нажатие, которого нет в журнале,
превращает вопрос «кто перезапустил» в вопрос без ответа.

Заход 1 изменил ровно три вещи и ни одной сущности не завёл:
  1. Ответ сверху считается по ЛЕСТНИЦЕ из шести уровней, а не по счётчику
     «сколько строк красных». Первый сработавший уровень выигрывает — идиома
     клиентской панели, где «немає зв'язку» перебивает очередь лидов.
  2. Аномалии отделены от состояния. Двадцать зелёных строк выделяют ровно
     ничего, поэтому состояние свёрнуто, а на виду только то, что может
     изменить поведение в ближайший час.
  3. Сбор разрезан на быстрый и медленный. Полный снапшот собирается 6.8 с
     (git по 21 worktree + PowerShell), и первый экран платил их целиком.
"""
from __future__ import annotations

import time
from dataclasses import dataclass

from fastapi import APIRouter, Depends
from fastapi.responses import HTMLResponse, JSONResponse

from app.routers.panels_auth import require_owner
from app.routers.panels_ui import ago, dot_html, esc, page
from app.services import jarvis_farm as F

router = APIRouter(prefix="/panel/jarvis", tags=["jarvis-panel"],
                   dependencies=[Depends(require_owner)])

_DOT = {"ok": "calm", "warn": "wait", "bad": "broken", "off": "off"}

# ЯЗЫК ПАНЕЛИ. Решение владельца 14.08: панель Джарвиса личная и пишется
# по-русски, клиентская остаётся украинской. Мультиязычности нет и не будет —
# `panels_ui` держит украинский умолчанием, а здесь стоит одна константа, и
# через неё проходят ровно три общие вещи: подпись состояния, суффикс возраста
# и атрибут `lang` страницы. Всё остальное написано по-русски прямо в модуле.
LANG = "ru"


def _ago(ts, now=None) -> str:
    return ago(ts, now, lang=LANG)


def _dot(state: str) -> str:
    return dot_html(state, lang=LANG)

# Подпись возраста медленной части. ОДНА строка на оба места — серверный рендер
# и ответ ручки `/slow`. Раньше их было два независимых литерала, и разъехались
# они не текстом, а УЗЛОМ: JS дописывал свою подпись рядом с серверной, и на
# экране вставало «ще не зчитанізадачі, арки, ключі: 0 с тому».
_SLOW_PREFIX = "задачи, арки, ключи"


# ─────────────────────────────── ответ ──────────────────────────────────────

@dataclass(frozen=True)
class Answer:
    level: int
    text: str
    tone: str        # broken | wait | calm
    second: str      # вторая строка: «само поднимется / не поднимется»


def _plural(n: int, one: str, few: str, many: str) -> str:
    """«1 процесс», «3 процесса», «5 процессов». Перечень проверенного читается
    вслух, а не собирается из числа и существительного в именительном."""
    tail, last = abs(n) % 100, abs(n) % 10
    if 11 <= tail <= 14:
        word = many
    elif last == 1:
        word = one
    elif 2 <= last <= 4:
        word = few
    else:
        word = many
    return f"{n} {word}"


def _lifter(proc_key: str, guards: dict):
    """Гардиан, который поднимет этот процесс. None — пары нет вовсе."""
    return guards.get(F.PAIRS.get(proc_key, ""))


def _lift_line(bad_rows, guards: dict) -> str:
    """Вторая строка: по одному приговору на каждый упавший процесс.

    Разница между «подниметься сам» и «поднимать некому» — это разница между
    «посмотрю через минуту» и «встаю и иду», и она обязана стоять там же, где
    ответ, а не в списке ниже."""
    parts = []
    for r in bad_rows:
        g = _lifter(r.key, guards)
        if g is not None and g.state == "ok":
            eta = F.GUARDIAN_ETA.get(g.key)
            eta_txt = f", ~{eta} с" if eta else ""
            parts.append(f"{r.label}: поднимется сам ({g.key}{eta_txt})")
        elif g is not None:
            parts.append(f"{r.label}: сам не поднимется — {g.key} {g.detail}")
        else:
            parts.append(f"{r.label}: гардиана нет, поднимется только руками")
    return " · ".join(parts)


def _checked_line(fast: dict, slow: dict | None) -> str:
    """Обоснование спокойного ответа: ЧТО именно проверено.

    Дата последнего падения была бы честнее и полезнее, но без журнала событий
    мы её не знаем, а печатать её значит соврать. Заход 2 её заменит."""
    parts = [_plural(len(fast["processes"]), "процесс", "процесса", "процессов"),
             _plural(len(fast["guardians"]), "гардиан", "гардиана", "гардианов")]
    if slow:
        parts.append(_plural(len(slow["tasks"]), "задача", "задачи", "задач"))
    return "проверено: " + ", ".join(parts)


def _answer(fast: dict, slow: dict | None) -> Answer:
    """Лестница из шести уровней. Порядок = приоритет, первый сработавший
    выигрывает, остальные не считаются.

    Два условия в ответ НЕ поднимаются намеренно:
      · «внешний сторож не настроен» — истинно всегда со дня рождения панели.
        Ответ, который не меняется, перестаёт быть ответом: это ровно тот
        вечно-красный, из-за которого красный теряет смысл. Остаётся note.
      · «⚠️ ключ потребує ротації» — висит месяцами; живёт в аномалиях.
        В ответ ключ попадает только СРОКОМ (L5).
    """
    procs = list(fast["processes"])
    guards_list = list(fast["guardians"])
    guards = {g.key: g for g in guards_list}

    # L1. Сборщик ослеп. `processes()` отдаёт единственную строку-заглушку с
    # ключом `procs`, когда psutil недоступен; при этом guardians() показывает
    # четыре жёлтых расхождения, которых на самом деле нет — судить по ним
    # ферму нельзя.
    if any(r.key == "procs" for r in procs):
        blind = next(r for r in procs if r.key == "procs")
        return Answer(1, "Не вижу ферму", "broken",
                      f"{blind.detail} — состояние ниже собрано наполовину")

    bad = [r for r in procs if r.state == "bad"]

    # L2. Деньги. Упавший раннер chatter — это лиды, которым никто не отвечает;
    # backend и бот такого класса не имеют.
    if any(r.key == "chatter" for r in bad):
        return Answer(2, "Лиды без ответа", "broken", _lift_line(bad, guards))

    # L3. Упало прочее. Две половины одного уровня: 14.08 в 00:45 раннер упал и
    # вернулся сам за 61 с — это НЕ то же событие, что падение без живого
    # гардиана, и ответ обязан различать их тоном.
    if bad:
        orphan = [r for r in bad
                  if not (_lifter(r.key, guards) and _lifter(r.key, guards).state == "ok")]
        if orphan:
            return Answer(3, f"Упало: {len(bad)}, сам не поднимется", "broken",
                          _lift_line(bad, guards))
        return Answer(3, f"Упало: {len(bad)}, поднимется сам", "wait",
                      _lift_line(bad, guards))

    # L4. Сторожа. Мёртвый гардиан при живом процессе — не авария сейчас, а
    # снятая страховка; расхождение «процесс есть, heartbeat протух» — это
    # мониторинг, который врёт сам себе, и класс он хуже честного bad.
    dead_g = [g for g in guards_list if g.state == "bad"]
    if dead_g:
        return Answer(4, f"Некому поднять: {dead_g[0].label}", "wait",
                      " · ".join(f"{g.label}: {g.detail}" for g in dead_g))
    warn_g = [g for g in guards_list if g.state == "warn"]
    if warn_g:
        return Answer(4, "Сторож говорит одно, система другое", "wait",
                      " · ".join(f"{g.label}: {g.detail}" for g in warn_g))

    # Задачи и ключи живут в МЕДЛЕННОЙ части. Кэш не свежий → уровень
    # пропускается, а под ответом печатается «ще не зчитані»: пропустить молча
    # значит выдать «Ферма ціла» за проверенное утверждение.
    if slow:
        bad_tasks = [t for t in slow["tasks"] if F.is_anomaly("task", t)]
        if bad_tasks:
            return Answer(4, f"Автоматика: {bad_tasks[0].label}", "wait",
                          " · ".join(f"{t.label}: {t.detail}" for t in bad_tasks))
        # L5. Ключ. Порог решением владельца: 7 дней — в ответ, 30 — в аномалии.
        soon = sorted(
            (k for k in slow["keys"]
             if k.get("days_left") is not None and k["days_left"] < F.KEY_EXPIRY_ANSWER),
            key=lambda k: k["days_left"])
        if soon:
            k = soon[0]
            return Answer(5, f"Ключ {k['name']}: {k['days_left']} дн", "wait",
                          f"{k['purpose']} · обновление: {k.get('auto') or 'руками'}")

    return Answer(6, "Ферма цела", "calm", _checked_line(fast, slow))


# ─────────────────────────────── разметка ───────────────────────────────────

def _rows_html(rows, now: float) -> str:
    out = []
    for r in rows:
        since = r.extra.get("since")
        tail = (f"<span class='sub'>с {esc(time.strftime('%d.%m %H:%M', time.localtime(since)))}</span>"
                if since else "")
        out.append(
            f"<div class='row' style='padding:7px 0;border-bottom:1px solid var(--line)'>"
            # dot_html сам переводит СОСТОЯНИЕ в цвет по смыслу. Прогонять его
            # через _DOT второй раз означало отдать ему уже готовый класс: 'ok'
            # превращался в 'calm', а 'calm' — в «вимкнено», и живой процесс
            # оказывался выключенным.
            f"<div>{_dot(r.state)}{esc(r.label)}"
            f"<div class='sub' style='margin-left:17px'>{esc(r.detail)}</div></div>{tail}</div>")
    return "".join(out)


def _card(rows, now: float) -> str:
    return f"<div class='card'>{_rows_html(rows, now)}</div>" if rows else ""


# Начиная со скольких одинаковых аномалий показываем СВОДКУ вместо списка.
#
# Живой прогон 14.08: 11 грязных worktree из 17 заняли весь первый экран и
# вытеснили с него ответ. Грязное дерево остаётся аномалией — оно слепит
# мерж-гейт, — но одиннадцать одинаковых аномалий это ОДНА проблема со
# счётчиком, а не одиннадцать проблем. Порог именно 3, а не 1: свернуть две
# строки значит оставить на экране одно число и потребовать лишний тап там,
# где всё помещалось.
GROUP_FROM = 3


def _group(summary: str, inner: str, count: int) -> str:
    """Сводка со счётчиком вместо длинного одинакового списка (принцип Sentry:
    не 1000 ошибок, а 5 проблем). Список никуда не девается — он под сводкой."""
    if count < GROUP_FROM:
        return inner
    return f"<details class='grp'><summary>{esc(summary)}</summary>{inner}</details>"


def _arc_table(arcs: list[dict]) -> str:
    body = "".join(
        f"<tr><td><b>{esc(a.get('branch', '—'))}</b>"
        f"<div class='sub mono'>{esc(a.get('path', ''))}</div></td>"
        f"<td data-l='Дерево'>{'🔴 грязное' if a.get('dirty') else '—'}</td>"
        f"<td data-l='Смержена'>{'✅' if a.get('merged') else '—'}</td>"
        f"<td data-l='Возраст' class='sub'>{esc(a.get('age_days'))} дн</td></tr>"
        for a in arcs)
    if not body:
        return ""
    return ("<div class='card'><table><thead><tr><th>Ветка</th><th>Состояние дерева</th>"
            f"<th>Смержена</th><th>Возраст</th></tr></thead><tbody>{body}</tbody></table></div>")


def _key_table(keys: list[dict]) -> str:
    body = "".join(
        f"<tr><td><span class='dot {_DOT.get(k['state'], 'off')}'></span>{esc(k['name'])}</td>"
        f"<td data-l='Назначение' class='sub'>{esc(k['purpose'])}</td>"
        f"<td data-l='Срок'>{esc(k['expires'] or '—')}"
        + (f" <span class='pill'>{k['days_left']} дн</span>" if k.get("days_left") is not None else "")
        + f"</td><td data-l='Авто' class='sub'>{esc(k['auto'] or '—')}</td>"
        f"<td data-l='Заметка' class='sub'>{esc(k['note'])}</td></tr>"
        for k in keys)
    if not body:
        return ""
    return ("<div class='card'><div class='sub' style='margin-bottom:8px'>Значения ключей "
            "тут не хранятся, не расшифровываются и не показываются — только метаданные.</div>"
            "<table><thead><tr><th>Ключ</th><th>Назначение</th><th>Срок</th>"
            f"<th>Авто</th><th>Заметка</th></tr></thead><tbody>{body}</tbody></table></div>")


def _events_table(events: list[dict], now: float) -> str:
    body = "".join(
        # `<wbr>` после подчёркиваний: `kind` клиентских событий — машинные
        # имена вроде `stale_reply_cancelled`, а `overflow-wrap:anywhere`
        # рвёт их посреди слова («classifier_err/or»). Подсказка даёт браузеру
        # законное место переноса, и слово остаётся словом. Вставляется ПОСЛЕ
        # экранирования — `_` не экранируется, так что разметку это не рушит.
        f"<tr><td><b>{esc(e['kind']).replace('_', '_<wbr>')}</b></td>"
        # `nobreak` — набор источников закрытый и короткий («chatter»,
        # «гардиан», «панель»), а колонка ужимается под широкую «Деталь».
        f"<td data-l='Источник' class='sub nobreak'>{esc(e['src'])}</td>"
        # Предел длины — тот же, что на источнике (`F.FEED_DETAIL_LIMIT`), а не
        # свой. Здесь стояло 110 против 120 у ленты, и это ровно та же болезнь,
        # что и два числа окна: меньший из двух пределов делает больший
        # невидимым, а разъезжаются они молча.
        f"<td data-l='Деталь' class='sub'>{esc((e['detail'] or '')[:F.FEED_DETAIL_LIMIT])}</td>"
        f"<td data-l='Когда' class='sub nobreak'>{esc(_ago(e['ts'], now)) if e['ts'] else '—'}</td></tr>"
        # Рендерер рисует то, что ему дали, и своего мнения о размере окна не
        # имеет. Собственный `[:25]` здесь был ВТОРЫМ срезом ленты — уже после
        # общей сортировки, то есть чисто по свежести, — и сводил дележ окна
        # между источниками на нет. Окно называется один раз, в `F.FEED_LIMIT`.
        for e in events) or "<tr><td colspan=4 class='empty'>тихо</td></tr>"
    return ("<div class='card'><table><thead><tr><th>Событие</th>"
            "<th class='nobreak'>Источник</th>"
            f"<th>Деталь</th><th class='nobreak'>Когда</th></tr></thead>"
            f"<tbody>{body}</tbody></table>"
            "<div class='sub' style='margin-top:8px'>⚠️ «Посчитано» ≠ «доехало»: доставка "
            "алертов сегодня не журналируется — это известный пробел, а не тишина.</div></div>")


def _slow_parts(slow: dict | None, now: float) -> tuple[str, str]:
    """Медленная часть, разложенная на аномалии и состояние.

    Возвращает готовые куски разметки для двух колонок — тот же код обслуживает
    и серверный рендер (кэш свежий), и ответ ручки `/slow` (кэш пуст)."""
    if not slow:
        return "", ""
    tasks_bad = [t for t in slow["tasks"] if F.is_anomaly("task", t)]
    tasks_ok = [t for t in slow["tasks"] if not F.is_anomaly("task", t)]
    keys_bad = [k for k in slow["keys"] if F.is_anomaly("key", k)]
    keys_ok = [k for k in slow["keys"] if not F.is_anomaly("key", k)]
    arcs_bad = [a for a in slow["arcs"] if F.is_anomaly("arc", a)]
    arcs_ok = [a for a in slow["arcs"] if not F.is_anomaly("arc", a)]

    anomalies = "".join([
        f"<h2>Автоматизации</h2>" + _group(
            _plural(len(tasks_bad), "задача", "задачи", "задач") + " не в норме",
            _card(tasks_bad, now), len(tasks_bad)) if tasks_bad else "",
        f"<h2>Ключи</h2>" + _group(
            _plural(len(keys_bad), "ключ", "ключа", "ключей") + " требуют внимания",
            _key_table(keys_bad), len(keys_bad)) if keys_bad else "",
        f"<h2>Грязные деревья</h2>" + _group(
            _plural(len(arcs_bad), "грязное дерево", "грязных дерева", "грязных деревьев")
            + " — мерж-гейт слепнет", _arc_table(arcs_bad), len(arcs_bad)) if arcs_bad else "",
    ])
    state = "".join([
        f"<h2>Автоматизации</h2>{_card(tasks_ok, now)}" if tasks_ok else "",
        f"<h2>Арки в работе</h2>{_arc_table(arcs_ok)}" if arcs_ok else "",
        f"<h2>Ключи API</h2>{_key_table(keys_ok)}" if keys_ok else "",
        f"<h2>Лента событий</h2>{_events_table(slow['events'], now)}",
    ])
    return anomalies, state


_JS = """
// Ширина экрана в футере — служебная строка для следующей вёрстки: реальные
// числа с обоих экранов Fold нужны измеренными, а не угаданными.
function vw(){var e=document.getElementById('vw');
  if(e) e.textContent=window.innerWidth;}
// Свёртка состояния — по МЕСТУ, а не по устройству: развернулся Fold — второй
// столбец появился и состояние раскрылось; сложился — снова свёрнуто. Порог
// один и тот же в CSS и здесь, потому что он выведен из ширины колонки.
var wide=window.matchMedia('(min-width:690px)');
function fold(){var d=document.querySelector('details.state'); if(d) d.open=wide.matches;}
wide.addEventListener('change',function(){fold();vw();});
window.addEventListener('resize',vw);
vw();fold();
// Медленная часть (git по всем worktree + PowerShell) догружается ПОСЛЕ
// первого экрана. Ответ сверху её не ждёт и без неё честно говорит, чего не
// знает.
// Подпись возраста медленной части — ОДИН узел, и догруженная подпись ЗАМЕНЯЕТ
// серверную, а не встаёт рядом. 14.08 их было два span'а подряд, и на экране
// получалось «ще не зчитанізадачі, арки, ключі: 0 с тому»: слипшаяся строка,
// в которой одно и то же сказано дважды и противоположным образом.
//
// Узел записан здесь АДРЕСОМ, а не через переменную, намеренно: сторож обязан
// прочитать из самого JS, в какой элемент уходит подпись, и сверить его с тем,
// где стоит серверная. Через алиас эта сверка невозможна.
var age=document.getElementById('slowage');
if(age&&age.dataset.load){fetch('/panel/jarvis/slow')
 .then(function(r){return r.json()}).then(function(j){
  document.getElementById('slowanom').innerHTML=j.anomalies;
  document.getElementById('slowstate').innerHTML=j.state;
  document.getElementById('slowage').textContent=j.note;
  age.removeAttribute('data-load');
  fold();
}).catch(function(){document.getElementById('slowage').textContent=
  'задачи, арки и ключи прочитать не удалось';});}
"""


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
def panel():
    """СИНХРОННАЯ намеренно: сбор фермы — psutil и файловые `stat`, и в
    `async def` эти миллисекунды отбирались бы у event loop'а целиком. Гардиан
    бэкенда пингует /health с таймаутом 3 с и убивает процесс на первом
    провале — открытие панели с телефона роняло прод. У обычной `def` Starlette
    уносит ручку в свой threadpool, и loop остаётся свободен. Сторож —
    tests/chatter/test_panel_event_loop.py.

    Медленную часть эта ручка НЕ собирает вовсе — только читает кэш, если он
    свежий. Поэтому git и PowerShell не могут ни задержать первый экран, ни
    уронить страницу вместе с ответом."""
    now = time.time()
    fast = F.snapshot_fast()
    slow = F.slow_cached()
    ans = _answer(fast, slow)

    ext = fast["external"]
    ext_html = (f"<div class='note {_DOT.get(ext.state, 'off')}'>"
                f"<b>{esc(ext.label)}:</b> {esc(ext.detail)}</div>")

    procs_bad = [r for r in fast["processes"] if F.is_anomaly("process", r)]
    procs_ok = [r for r in fast["processes"] if not F.is_anomaly("process", r)]
    guards_bad = [r for r in fast["guardians"] if F.is_anomaly("guardian", r)]
    guards_ok = [r for r in fast["guardians"] if not F.is_anomaly("guardian", r)]

    slow_anom, slow_state = _slow_parts(slow, now)
    if slow:
        slow_note = f"{_SLOW_PREFIX}: {esc(_ago(slow['collected_at'] - 1, now))}"
        need_load = ""
    else:
        # Не молчание: «Ферма ціла» без этой строки тихо означало бы «про задачи
        # и ключи не знаю», а выглядело бы как проверенное утверждение.
        slow_note = f"{_SLOW_PREFIX}: ещё не прочитаны"
        need_load = " data-load='1'"

    anomalies = "".join([
        f"<h2>Процессы</h2>{_card(procs_bad, now)}" if procs_bad else "",
        f"<h2>Гардианы</h2>{_card(guards_bad, now)}" if guards_bad else "",
        slow_anom,
        "<div id='slowanom'></div>",
    ])
    if not (procs_bad or guards_bad or slow_anom):
        anomalies = ("<h2>Требует внимания</h2><div class='card'>"
                     "<div class='empty'>Аномалий нет — всё ниже просто работает.</div>"
                     "</div>" + anomalies)

    state_summary = ", ".join(filter(None, [
        _plural(len(procs_ok), "процесс", "процесса", "процессов"),
        _plural(len(guards_ok), "гардиан", "гардиана", "гардианов"),
        _plural(len(slow["tasks"]), "задача", "задачи", "задач") if slow else "",
        _plural(len(slow["arcs"]), "ветка", "ветки", "веток") if slow else "",
    ]))

    # Слот на строку захода 2 («что изменилось с прошлого захода») сознательно
    # НЕ рендерится: пустая рамка читается как «ничего не случилось», а мы
    # этого не знаем — знать будет журнал событий.
    body = f"""
<h1 class='ans {ans.tone}'>{esc(ans.text)}</h1>
<div class='sub second'>{esc(ans.second)}</div>
<div class='sub'>Панель Джарвиса · фаза 0 · read-only ·
 ферма собрана {esc(_ago(fast['collected_at'] - 1, now))} ·
 <span id='slowage'{need_load}>{slow_note}</span></div>

<div style='margin-top:14px'>{ext_html}</div>

<div class='two'>
 <div>{anomalies}</div>
 <div>
  <details class='state'><summary>Состояние: {esc(state_summary)}</summary>
   {f"<h2>Процессы</h2>{_card(procs_ok, now)}" if procs_ok else ""}
   {f"<h2>Гардианы</h2>{_card(guards_ok, now)}" if guards_ok else ""}
   {slow_state}
   <div id='slowstate'></div>
  </details>
 </div>
</div>

<div class='sub' style='margin-top:20px'>Фаза 0 — только чтение. Кнопок управления
 тут нет намеренно.</div>
<div class='sub'>ширина экрана: <span id='vw'>—</span> px</div>

<div style='margin-top:16px'><a href='/panel/tamapi'>← Клиенты</a></div>
"""
    return HTMLResponse(page("Панель Джарвиса", body, extra_js=_JS, lang=LANG))


@router.get("/slow")
def slow_fragment():
    """Медленная часть отдельным запросом — за той же дверью `require_owner`.

    Синхронная по той же причине, что и `panel()`: внутри PowerShell и git."""
    now = time.time()
    slow = F.slow_cached() or F.snapshot_slow(force=True)
    anomalies, state = _slow_parts(slow, now)
    return JSONResponse({
        "anomalies": anomalies,
        "state": state,
        "note": (f"{_SLOW_PREFIX}: {_ago(slow['collected_at'] - 1, now)}"
                 " — обновите страницу, чтобы ответ их учёл"),
    })


@router.get("/api/snapshot")
def api_snapshot():
    """Полный снапшот одним куском. Собирает и медленное тоже — в отличие от
    страницы: у машинного потребителя нет первого экрана, который ждёт."""
    snap = F.snapshot()
    return JSONResponse({
        "collected_at": snap["collected_at"],
        "slow_collected_at": snap["slow_collected_at"],
        "external": snap["external"].__dict__,
        "processes": [r.__dict__ for r in snap["processes"]],
        "guardians": [r.__dict__ for r in snap["guardians"]],
        "tasks": [r.__dict__ for r in snap["tasks"]],
        "arcs": snap["arcs"],
        "keys": snap["keys"],
        "events": snap["events"],
    })
