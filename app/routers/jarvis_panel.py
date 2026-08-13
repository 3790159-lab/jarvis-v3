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


# ─────────────────────────────── ответ ──────────────────────────────────────

@dataclass(frozen=True)
class Answer:
    level: int
    text: str
    tone: str        # broken | wait | calm
    second: str      # вторая строка: «само поднимется / не поднимется»


def _plural(n: int, one: str, few: str, many: str) -> str:
    """«1 процес», «3 процеси», «5 процесів». Перечень проверенного читается
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
            parts.append(f"{r.label}: підніметься сам ({g.key}{eta_txt})")
        elif g is not None:
            parts.append(f"{r.label}: сам не підніметься — {g.key} {g.detail}")
        else:
            parts.append(f"{r.label}: гардіана немає, підніметься тільки руками")
    return " · ".join(parts)


def _checked_line(fast: dict, slow: dict | None) -> str:
    """Обоснование спокойного ответа: ЧТО именно проверено.

    Дата последнего падения была бы честнее и полезнее, но без журнала событий
    мы её не знаем, а печатать её значит соврать. Заход 2 её заменит."""
    parts = [_plural(len(fast["processes"]), "процес", "процеси", "процесів"),
             _plural(len(fast["guardians"]), "гардіан", "гардіани", "гардіанів")]
    if slow:
        parts.append(_plural(len(slow["tasks"]), "задача", "задачі", "задач"))
    return "перевірено: " + ", ".join(parts)


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
        return Answer(1, "Не бачу ферму", "broken",
                      f"{blind.detail} — стан нижче зібраний наполовину")

    bad = [r for r in procs if r.state == "bad"]

    # L2. Деньги. Упавший раннер chatter — это лиды, которым никто не отвечает;
    # backend и бот такого класса не имеют.
    if any(r.key == "chatter" for r in bad):
        return Answer(2, "Ліди без відповіді", "broken", _lift_line(bad, guards))

    # L3. Упало прочее. Две половины одного уровня: 14.08 в 00:45 раннер упал и
    # вернулся сам за 61 с — это НЕ то же событие, что падение без живого
    # гардиана, и ответ обязан различать их тоном.
    if bad:
        orphan = [r for r in bad
                  if not (_lifter(r.key, guards) and _lifter(r.key, guards).state == "ok")]
        if orphan:
            return Answer(3, f"Впало: {len(bad)}, сам не підніметься", "broken",
                          _lift_line(bad, guards))
        return Answer(3, f"Впало: {len(bad)}, підніметься сам", "wait",
                      _lift_line(bad, guards))

    # L4. Сторожа. Мёртвый гардиан при живом процессе — не авария сейчас, а
    # снятая страховка; расхождение «процесс есть, heartbeat протух» — это
    # мониторинг, который врёт сам себе, и класс он хуже честного bad.
    dead_g = [g for g in guards_list if g.state == "bad"]
    if dead_g:
        return Answer(4, f"Немає кому підняти: {dead_g[0].label}", "wait",
                      " · ".join(f"{g.label}: {g.detail}" for g in dead_g))
    warn_g = [g for g in guards_list if g.state == "warn"]
    if warn_g:
        return Answer(4, "Сторож каже одне, система інше", "wait",
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
                          f"{k['purpose']} · оновлення: {k.get('auto') or 'руками'}")

    return Answer(6, "Ферма ціла", "calm", _checked_line(fast, slow))


# ─────────────────────────────── разметка ───────────────────────────────────

def _rows_html(rows, now: float) -> str:
    out = []
    for r in rows:
        since = r.extra.get("since")
        tail = (f"<span class='sub'>з {esc(time.strftime('%d.%m %H:%M', time.localtime(since)))}</span>"
                if since else "")
        out.append(
            f"<div class='row' style='padding:7px 0;border-bottom:1px solid var(--line)'>"
            # dot_html сам переводит СОСТОЯНИЕ в цвет по смыслу. Прогонять его
            # через _DOT второй раз означало отдать ему уже готовый класс: 'ok'
            # превращался в 'calm', а 'calm' — в «вимкнено», и живой процесс
            # оказывался выключенным.
            f"<div>{dot_html(r.state)}{esc(r.label)}"
            f"<div class='sub' style='margin-left:17px'>{esc(r.detail)}</div></div>{tail}</div>")
    return "".join(out)


def _card(rows, now: float) -> str:
    return f"<div class='card'>{_rows_html(rows, now)}</div>" if rows else ""


def _arc_table(arcs: list[dict]) -> str:
    body = "".join(
        f"<tr><td><b>{esc(a.get('branch', '—'))}</b>"
        f"<div class='sub mono'>{esc(a.get('path', ''))}</div></td>"
        f"<td data-l='Дерево'>{'🔴 брудне' if a.get('dirty') else '—'}</td>"
        f"<td data-l='Змерджена'>{'✅' if a.get('merged') else '—'}</td>"
        f"<td data-l='Вік' class='sub'>{esc(a.get('age_days'))} дн</td></tr>"
        for a in arcs)
    if not body:
        return ""
    return ("<div class='card'><table><thead><tr><th>Гілка</th><th>Стан дерева</th>"
            f"<th>Змерджена</th><th>Вік</th></tr></thead><tbody>{body}</tbody></table></div>")


def _key_table(keys: list[dict]) -> str:
    body = "".join(
        f"<tr><td><span class='dot {_DOT.get(k['state'], 'off')}'></span>{esc(k['name'])}</td>"
        f"<td data-l='Призначення' class='sub'>{esc(k['purpose'])}</td>"
        f"<td data-l='Термін'>{esc(k['expires'] or '—')}"
        + (f" <span class='pill'>{k['days_left']} дн</span>" if k.get("days_left") is not None else "")
        + f"</td><td data-l='Авто' class='sub'>{esc(k['auto'] or '—')}</td>"
        f"<td data-l='Нотатка' class='sub'>{esc(k['note'])}</td></tr>"
        for k in keys)
    if not body:
        return ""
    return ("<div class='card'><div class='sub' style='margin-bottom:8px'>Значення ключів "
            "тут не зберігаються, не розшифровуються і не показуються — лише метадані.</div>"
            "<table><thead><tr><th>Ключ</th><th>Призначення</th><th>Термін</th>"
            f"<th>Авто</th><th>Нотатка</th></tr></thead><tbody>{body}</tbody></table></div>")


def _events_table(events: list[dict], now: float) -> str:
    body = "".join(
        f"<tr><td><b>{esc(e['kind'])}</b></td>"
        f"<td data-l='Джерело' class='sub'>{esc(e['src'])}</td>"
        f"<td data-l='Деталь' class='sub'>{esc((e['detail'] or '')[:110])}</td>"
        f"<td data-l='Коли' class='sub'>{esc(ago(e['ts'], now)) if e['ts'] else '—'}</td></tr>"
        for e in events[:25]) or "<tr><td colspan=4 class='empty'>тихо</td></tr>"
    return ("<div class='card'><table><thead><tr><th>Подія</th><th>Джерело</th>"
            f"<th>Деталь</th><th>Коли</th></tr></thead><tbody>{body}</tbody></table>"
            "<div class='sub' style='margin-top:8px'>⚠️ «Пораховано» ≠ «доїхало»: доставка "
            "алертів сьогодні не журналюється — це відомий пробіл, а не тиша.</div></div>")


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
        f"<h2>Автоматизації</h2>{_card(tasks_bad, now)}" if tasks_bad else "",
        f"<h2>Ключі</h2>{_key_table(keys_bad)}" if keys_bad else "",
        f"<h2>Брудні дерева</h2>{_arc_table(arcs_bad)}" if arcs_bad else "",
    ])
    state = "".join([
        f"<h2>Автоматизації</h2>{_card(tasks_ok, now)}" if tasks_ok else "",
        f"<h2>Арки в роботі</h2>{_arc_table(arcs_ok)}" if arcs_ok else "",
        f"<h2>Ключі API</h2>{_key_table(keys_ok)}" if keys_ok else "",
        f"<h2>Стрічка подій</h2>{_events_table(slow['events'], now)}",
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
var wide=window.matchMedia('(min-width:730px)');
function fold(){var d=document.querySelector('details.state'); if(d) d.open=wide.matches;}
wide.addEventListener('change',function(){fold();vw();});
window.addEventListener('resize',vw);
vw();fold();
// Медленная часть (git по всем worktree + PowerShell) догружается ПОСЛЕ
// первого экрана. Ответ сверху её не ждёт и без неё честно говорит, чего не
// знает.
var slot=document.getElementById('slowload');
if(slot){fetch('/panel/jarvis/slow').then(function(r){return r.json()}).then(function(j){
  document.getElementById('slowanom').innerHTML=j.anomalies;
  document.getElementById('slowstate').innerHTML=j.state;
  slot.textContent=j.note;
  fold();
}).catch(function(){slot.textContent='задачі, арки та ключі прочитати не вдалося';});}
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
        slow_note = f"задачі, арки, ключі: {esc(ago(slow['collected_at'] - 1, now))}"
        loader = ""
    else:
        # Не молчание: «Ферма ціла» без этой строки тихо означало бы «про задачи
        # и ключи не знаю», а выглядело бы как проверенное утверждение.
        slow_note = "задачі, арки, ключі: ще не зчитані"
        loader = "<span id='slowload'></span>"

    anomalies = "".join([
        f"<h2>Процеси</h2>{_card(procs_bad, now)}" if procs_bad else "",
        f"<h2>Гардіани</h2>{_card(guards_bad, now)}" if guards_bad else "",
        slow_anom,
        "<div id='slowanom'></div>",
    ])
    if not (procs_bad or guards_bad or slow_anom):
        anomalies = ("<h2>Потребує уваги</h2><div class='card'>"
                     "<div class='empty'>Аномалій немає — усе нижче просто працює.</div>"
                     "</div>" + anomalies)

    state_summary = ", ".join(filter(None, [
        _plural(len(procs_ok), "процес", "процеси", "процесів"),
        _plural(len(guards_ok), "гардіан", "гардіани", "гардіанів"),
        _plural(len(slow["tasks"]), "задача", "задачі", "задач") if slow else "",
        _plural(len(slow["arcs"]), "гілка", "гілки", "гілок") if slow else "",
    ]))

    # Слот на строку захода 2 («что изменилось с прошлого захода») сознательно
    # НЕ рендерится: пустая рамка читается как «ничего не случилось», а мы
    # этого не знаем — знать будет журнал событий.
    body = f"""
<h1 class='ans {ans.tone}'>{esc(ans.text)}</h1>
<div class='sub second'>{esc(ans.second)}</div>
<div class='sub'>Панель Джарвіса · фаза 0 · read-only ·
 ферма зібрана {esc(ago(fast['collected_at'] - 1, now))} ·
 <span id='slowage'>{slow_note}</span>{loader}</div>

<div style='margin-top:14px'>{ext_html}</div>

<div class='two'>
 <div>{anomalies}</div>
 <div>
  <details class='state'><summary>Стан: {esc(state_summary)}</summary>
   {f"<h2>Процеси</h2>{_card(procs_ok, now)}" if procs_ok else ""}
   {f"<h2>Гардіани</h2>{_card(guards_ok, now)}" if guards_ok else ""}
   {slow_state}
   <div id='slowstate'></div>
  </details>
 </div>
</div>

<div class='sub' style='margin-top:20px'>Фаза 0 — лише читання. Кнопок керування
 тут немає навмисно.</div>
<div class='sub'>ширина екрана: <span id='vw'>—</span> px</div>

<div style='margin-top:16px'><a href='/panel/tamapi'>← Клієнти</a></div>
"""
    return HTMLResponse(page("Панель Джарвіса", body, extra_js=_JS))


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
        "note": (f"задачі, арки, ключі: {ago(slow['collected_at'] - 1, now)}"
                 " — оновіть сторінку, щоб відповідь їх врахувала"),
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
