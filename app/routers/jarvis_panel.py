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
from app.routers.panels_ui import ago, esc, page
from app.services import jarvis_farm as F

router = APIRouter(prefix="/panel/jarvis", tags=["jarvis-panel"],
                   dependencies=[Depends(require_owner)])

_DOT = {"ok": "ok", "warn": "warn", "bad": "bad", "off": "off"}

# Ствол в таблицу «Арки в роботі» не идёт: он не арка, а точка отсчёта. Из-за
# него единственная ✅ в колонке «Змерджена» была тавтологией (ветка всегда
# смержена сама в себя), и колонка читалась как сломанная. Без ствола ✅ значит
# ровно одно: смержено, worktree можно сносить.
TRUNK = "phase-4.0-unified-jarvis"


def _rows_html(rows, now: float) -> str:
    out = []
    for r in rows:
        since = r.extra.get("since")
        tail = f"<span class='sub'>з {esc(time.strftime('%d.%m %H:%M', time.localtime(since)))}</span>" if since else ""
        out.append(
            f"<div class='row' style='padding:7px 0;border-bottom:1px solid var(--line)'>"
            f"<div><span class='dot {_DOT.get(r.state,'off')}'></span>{esc(r.label)}"
            f"<div class='sub' style='margin-left:17px'>{esc(r.detail)}</div></div>{tail}</div>")
    return "".join(out)


def _arc_rows(arcs) -> str:
    rows = "".join(
        f"<tr><td><b>{esc(a.get('branch', '—'))}</b>"
        f"<div class='sub mono'>{esc(a.get('path', ''))}</div></td>"
        f"<td>{'🔴 брудне' if a.get('dirty') else '—'}</td>"
        f"<td>{'✅' if a.get('merged') else '—'}</td>"
        f"<td class='sub'>{esc(a.get('age_days'))} дн</td></tr>"
        for a in arcs if a.get("branch") != TRUNK)
    return rows or "<tr><td colspan=4 class='empty'>немає даних</td></tr>"


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
async def panel():
    now = time.time()
    snap = F.snapshot()
    ext = snap["external"]

    ext_html = (
        f"<div class='note' style='border-left-color:var("
        f"{'--bad' if ext.state == 'bad' else '--warn' if ext.state == 'warn' else '--ok'})'>"
        f"<b>{esc(ext.label)}:</b> {esc(ext.detail)}</div>")

    arc_rows = _arc_rows(snap["arcs"])

    ev_rows = "".join(
        f"<tr><td class='sub'>{esc(e['src'])}</td><td>{esc(e['kind'])}</td>"
        f"<td class='sub'>{esc((e['detail'] or '')[:110])}</td>"
        f"<td class='sub'>{esc(ago(e['ts'], now)) if e['ts'] else '—'}</td></tr>"
        for e in snap["events"][:25]) or "<tr><td colspan=4 class='empty'>тихо</td></tr>"

    key_rows = "".join(
        f"<tr><td><span class='dot {_DOT.get(k['state'],'off')}'></span>{esc(k['name'])}</td>"
        f"<td class='sub'>{esc(k['purpose'])}</td>"
        f"<td>{esc(k['expires'] or '—')}"
        + (f" <span class='pill'>{k['days_left']} дн</span>" if k.get("days_left") is not None else "")
        + f"</td><td class='sub'>{esc(k['auto'] or '—')}</td>"
        f"<td class='sub'>{esc(k['note'])}</td></tr>"
        for k in snap["keys"])

    body = f"""
<h1>Панель Джарвіса · фаза 0</h1>
<div class='sub'>read-only · зібрано {esc(ago(snap['collected_at'] - 1, now))}
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
<tr><th>Гілка</th><th>Стан дерева</th><th>Змерджена</th><th>Вік</th></tr>
{arc_rows}</table></div>

<h2>Ключі API</h2>
<div class='card'>
  <div class='sub' style='margin-bottom:8px'>Значення ключів тут не зберігаються,
   не розшифровуються і не показуються — лише метадані.</div>
  <table><tr><th>Ключ</th><th>Призначення</th><th>Термін</th><th>Авто</th><th>Нотатка</th></tr>
  {key_rows}</table>
</div>

<h2>Стрічка подій</h2>
<div class='card'><table>
<tr><th>Джерело</th><th>Подія</th><th>Деталь</th><th>Коли</th></tr>
{ev_rows}</table>
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
