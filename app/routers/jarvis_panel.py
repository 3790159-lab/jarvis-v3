"""Панель Джарвиса, фаза 0 — read-only (docs/jarvis-panel/PHASE0_READONLY.md).

НИ ОДНОЙ мутирующей ручки. Ни рестарта, ни kill, ни ротации ключей. Причина не
в лени: каждая мутация из веба — новый путь к проду в обход существующих
предохранителей (скоупленный kill по точному PID, дебаунс гардиана, ACL на
.secrets). Пока путь не спроектирован — кнопки нет.
"""
from __future__ import annotations

import time

from fastapi import APIRouter, Depends
from fastapi.responses import HTMLResponse, JSONResponse

from app.routers.panels_auth import require_owner
from app.routers.panels_ui import ago, dot_html, esc, page
from app.services import jarvis_farm as F

router = APIRouter(prefix="/panel/jarvis", tags=["jarvis-panel"],
                   dependencies=[Depends(require_owner)])

_DOT = {"ok": "calm", "warn": "wait", "bad": "broken", "off": "off"}


def _answer(snap) -> tuple[str, str]:
    """Ответ панели одной строкой: «на ферме всё цело?».

    Раньше страница начиналась с заголовка и списка из трёх десятков строк, где
    зелёная точка стояла у каждой живой — то есть выделено было всё, значит
    ничего. Считаем по строкам процессов, гардианов и автоматизаций; арки и
    ключи в ответ не входят: возраст ветки — не поломка.
    """
    rows = list(snap["processes"]) + list(snap["guardians"]) + list(snap["tasks"])
    bad = [r for r in rows if r.state == "bad"]
    warn = [r for r in rows if r.state == "warn"]
    if bad:
        return f"Впало: {len(bad)}", "broken"
    if warn:
        return f"Потребує уваги: {len(warn)}", "wait"
    return "Ферма ціла", "calm"


def _rows_html(rows, now: float) -> str:
    out = []
    for r in rows:
        since = r.extra.get("since")
        tail = f"<span class='sub'>з {esc(time.strftime('%d.%m %H:%M', time.localtime(since)))}</span>" if since else ""
        out.append(
            f"<div class='row' style='padding:7px 0;border-bottom:1px solid var(--line)'>"
            # dot_html сам переводит СОСТОЯНИЕ в цвет по смыслу. Прогонять его
            # через _DOT второй раз означало отдать ему уже готовый класс: 'ok'
            # превращался в 'calm', а 'calm' — в «вимкнено», и живой процесс
            # оказывался выключенным.
            f"<div>{dot_html(r.state)}{esc(r.label)}"
            f"<div class='sub' style='margin-left:17px'>{esc(r.detail)}</div></div>{tail}</div>")
    return "".join(out)


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
async def panel():
    now = time.time()
    snap = F.snapshot()
    ext = snap["external"]

    ext_html = (
        f"<div class='note {_DOT.get(ext.state, 'off')}'>"
        f"<b>{esc(ext.label)}:</b> {esc(ext.detail)}</div>")

    arcs = snap["arcs"]
    arc_rows = "".join(
        f"<tr><td><b>{esc(a.get('branch', '—'))}</b>"
        f"<div class='sub mono'>{esc(a.get('path', ''))}</div></td>"
        f"<td data-l='Дерево'>{'🔴 брудне' if a.get('dirty') else '—'}</td>"
        f"<td data-l='Змерджена'>{'✅' if a.get('merged') else '—'}</td>"
        f"<td data-l='Вік' class='sub'>{esc(a.get('age_days'))} дн</td></tr>"
        for a in arcs) or "<tr><td colspan=4 class='empty'>немає даних</td></tr>"

    ev_rows = "".join(
        f"<tr><td><b>{esc(e['kind'])}</b></td>"
        f"<td data-l='Джерело' class='sub'>{esc(e['src'])}</td>"
        f"<td data-l='Деталь' class='sub'>{esc((e['detail'] or '')[:110])}</td>"
        f"<td data-l='Коли' class='sub'>{esc(ago(e['ts'], now)) if e['ts'] else '—'}</td></tr>"
        for e in snap["events"][:25]) or "<tr><td colspan=4 class='empty'>тихо</td></tr>"

    key_rows = "".join(
        f"<tr><td><span class='dot {_DOT.get(k['state'],'off')}'></span>{esc(k['name'])}</td>"
        f"<td data-l='Призначення' class='sub'>{esc(k['purpose'])}</td>"
        f"<td data-l='Термін'>{esc(k['expires'] or '—')}"
        + (f" <span class='pill'>{k['days_left']} дн</span>" if k.get("days_left") is not None else "")
        + f"</td><td data-l='Авто' class='sub'>{esc(k['auto'] or '—')}</td>"
        f"<td data-l='Нотатка' class='sub'>{esc(k['note'])}</td></tr>"
        for k in snap["keys"])

    ans, tone = _answer(snap)
    body = f"""
<h1 class='ans {tone}'>{esc(ans)}</h1>
<div class='sub'>Панель Джарвіса · фаза 0 · read-only · зібрано
 {esc(ago(snap['collected_at'] - 1, now))}
 · {esc(time.strftime('%d.%m %H:%M:%S', time.localtime(snap['collected_at'])))}</div>

<div style='margin-top:14px'>{ext_html}</div>

<h2>Ферма</h2>
<div class='card'>{_rows_html(snap['processes'], now)}</div>
<h2>Гардіани</h2>
<div class='card'>{_rows_html(snap['guardians'], now)}</div>

<h2>Автоматизації</h2>
<div class='card'>{_rows_html(snap['tasks'], now)}</div>

<h2>Арки в роботі</h2>
<div class='card'><table>
<thead><tr><th>Гілка</th><th>Стан дерева</th><th>Змерджена</th><th>Вік</th></tr></thead>
<tbody>{arc_rows}</tbody></table></div>

<h2>Ключі API</h2>
<div class='card'>
  <div class='sub' style='margin-bottom:8px'>Значення ключів тут не зберігаються,
   не розшифровуються і не показуються — лише метадані.</div>
  <table><thead><tr><th>Ключ</th><th>Призначення</th><th>Термін</th><th>Авто</th>
  <th>Нотатка</th></tr></thead><tbody>{key_rows}</tbody></table>
</div>

<h2>Стрічка подій</h2>
<div class='card'><table>
<thead><tr><th>Подія</th><th>Джерело</th><th>Деталь</th><th>Коли</th></tr></thead>
<tbody>{ev_rows}</tbody></table>
<div class='sub' style='margin-top:8px'>⚠️ «Пораховано» ≠ «доїхало»: доставка
 алертів сьогодні не журналюється — це відомий пробіл, а не тиша.</div></div>

<div class='sub' style='margin-top:20px'>Фаза 0 — лише читання. Кнопок керування
 тут немає навмисно.</div>
"""
    return HTMLResponse(page("Панель Джарвіса", body))


@router.get("/api/snapshot")
async def api_snapshot():
    snap = F.snapshot()
    return JSONResponse({
        "collected_at": snap["collected_at"],
        "external": snap["external"].__dict__,
        "processes": [r.__dict__ for r in snap["processes"]],
        "guardians": [r.__dict__ for r in snap["guardians"]],
        "tasks": [r.__dict__ for r in snap["tasks"]],
        "arcs": snap["arcs"],
        "keys": snap["keys"],
        "events": snap["events"],
    })
