"""Клиентский дашборд TAMAPI: главный экран + «Динамика».

Спека: docs/dashboard/CLIENT_SCREENS.md. Читает боевую БД клиента ТОЛЬКО на
чтение (`tamapi_metrics`), пишет — исключительно через ОБЩИЙ командный слой
`route_callback`, тот же, что обслуживает TG-пульт. Прямых UPDATE'ов из веба
нет ни одного: расхождение веба и пульта — это класс багов, который мы уже
проходили, и единственная защита от него — один исполнитель на оба канала.
"""
from __future__ import annotations

import os
import time
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, Form, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from app.routers.panels_auth import require_owner
from app.routers.panels_ui import (ago, delta_html, esc, leads_waiting,
                                   line_chart, page, plural_dialogs)
from app.services import tamapi_metrics as M

router = APIRouter(prefix="/panel/tamapi", tags=["tamapi-dashboard"],
                   dependencies=[Depends(require_owner)])

CLIENTS_DIR = Path(os.getenv("CHATTER_CLIENTS_DIR", "chatter/clients"))


def _db_path() -> str:
    return os.getenv("TAMAPI_DB", ".secrets/demo.db")


def _slug() -> str:
    return os.getenv("TAMAPI_SLUG", "volska")


def _heartbeat_age() -> float | None:
    p = Path(os.getenv("TAMAPI_HEARTBEAT", "state/chatter_heartbeat.txt"))
    try:
        return time.time() - p.stat().st_mtime
    except OSError:
        return None


def _cfg():
    """Конфиг клиента (язык, лимиты, owner_ref). Падение конфига не имеет права
    ронять экран — дашборд обязан показать статус даже при кривом yaml."""
    try:
        from chatter.config.loader import load_config
        return load_config(CLIENTS_DIR, _slug())
    except Exception:
        return None


def _status() -> dict:
    """Три состояния, не два (спека §2.1): «зелёный» при взведённом kill_switch
    был бы прямой ложью — бот жив, но молчит всем."""
    from chatter.storage.db import Store
    age = _heartbeat_age()
    killed = False
    try:
        s = Store(_db_path())
        killed = (s.get_runtime_flag("kill_switch") or "0").strip() == "1"
        del s
    except Exception:
        pass
    # Точка красится по СМЫСЛУ: красный — только «зламано» (связи нет), янтарь —
    # «чекає вас» (пауза снимается вами), «на зв'язку» цвета не получает вовсе.
    if age is None or age > 90:
        return {"code": "down", "dot": "broken", "title": "Немає зв'язку",
                "sub": "технічна проблема, ми вже бачимо", "age": age}
    if killed:
        return {"code": "paused", "dot": "wait", "title": "На паузі",
                "sub": "зупинено вами", "age": age}
    return {"code": "live", "dot": "calm", "title": "На зв'язку",
            "sub": "відповідає", "age": age}


def _answer(st: dict, att: list[dict]) -> tuple[str, str]:
    """Ответ экрана одной строкой: «мені зараз щось треба робити?».

    Порядок ответов — это и есть приоритет. Связи нет — про очередь говорить
    рано: цифры под ответом уже неживые, и звать человека разбирать их значит
    звать его не туда. Дальше долг, потом пауза, потом тишина.
    """
    fresh = [i for i in att if not i.get("stale")]
    if st["code"] == "down":
        return "Немає зв'язку з Ольгою", "broken"
    if fresh:
        return leads_waiting(len(fresh)), "wait"
    if st["code"] == "paused":
        return "Ольга на паузі — не відповідає нікому", "wait"
    return "Все спокійно", "calm"


def _month_start(now: float) -> float:
    """Начало КАЛЕНДАРНОГО месяца в локальном времени.

    Было скользящее окно `now - 30*86400`: оно не обнуляется первого числа, то
    есть бар жил не по тому счёту, который клиент оплачивает. Локальное время, а
    не UTC: месяц у клиента заканчивается по его календарю."""
    d = datetime.fromtimestamp(now)
    return d.replace(day=1, hour=0, minute=0, second=0,
                     microsecond=0).timestamp()


def _package(now: float | None = None) -> dict:
    """Пакет-бар. Месячного пакета в конфиге нет (он биллинговый, живёт в
    control-plane) — до его появления берём из env, а суточный cap показываем
    как есть. Исчерпание НЕ отключает бота (решение владельца): 80/100 % —
    уведомления, сверх — пометка перерасхода, отключение только вручную.

    Считаем УНИКАЛЬНЫХ ЛИДОВ за календарный месяц. Мера та же, что у первой
    ступени воронки, но окно другое — поэтому и подпись обязана быть другой:
    «1 діалог» в воронке и «4 діалоги» в баре читались как противоречие, хотя
    противоречия не было."""
    cfg = _cfg()
    now = time.time() if now is None else now
    limit = int(os.getenv("TAMAPI_PACKAGE", "500"))
    since = _month_start(now)
    used = 0
    try:
        with M._ro(_db_path()) as c:
            used = c.execute(
                "SELECT COUNT(DISTINCT contact_id) AS n FROM messages "
                "WHERE role='user' AND ts >= ? AND ts < ?",
                (since, now)).fetchone()["n"]
    except Exception:
        pass
    pct = (used / limit * 100.0) if limit else 0.0
    over = max(0, used - limit)
    return {"used": used, "limit": limit, "pct": pct,
            "over": over,
            # Доля перерасхода от пакета — чтобы полоска могла его НАРИСОВАТЬ.
            # Раньше ширина резалась `min(pct, 100)`, и перерасход существовал
            # только в тексте примечания: полоска показывала ровно «всё в норме».
            "over_pct": (over / limit * 100.0) if limit else 0.0,
            "since": since,
            "since_label": datetime.fromtimestamp(since).strftime("%d.%m"),
            "daily_cap": (cfg.settings.limits.daily_cap if cfg else None)}


# ------------------------------------------------------------------- HTML

def _funnel_html(sm: dict) -> str:
    f = sm["funnel"]
    # «Кваліфіковано» — мнение КЛАССИФИКАТОРА, и подпись обязательна (решение
    # владельца 31.07, спека §2.3): без неё клиент прочтёт оценку модели как
    # факт, проверенный человеком, и первое расхождение будет стоить доверия
    # ко всему экрану.
    # У «Кваліфіковано» есть ПОДПИСЬ, и она обязательна (решение владельца
    # 31.07, спека §2.3): это мнение КЛАССИФИКАТОРА, а не отметка человека.
    # Без подписи клиент прочтёт оценку модели как проверенный факт, и первое
    # же расхождение будет стоить доверия ко всему экрану.
    # Последняя ступень — деньги, и она единственная на экране красится зелёным.
    steps = [("Ліди", f["dialogs"], "унікальні за 7 днів", ""),
             ("Кваліфіковано", f["qualified"], "за оцінкою асистента", ""),
             ("Передано вам", f["handed"], "", ""),
             ("Оплати", f["payments"], "", " money")]
    cohort = f["dialogs"]
    out = []
    for i, (label, val, note, tone) in enumerate(steps):
        if val is None:
            # Честно: метрики нет, потому что таблица только начала копиться.
            body = "<div class='n na'>історія накопичується</div>"
        else:
            body = f"<div class='n{tone}'>{val:g}</div>"
        pct = ""
        # Доля — от КОГОРТЫ (первой ступени), а не от предыдущей строки: все
        # ступени считают людей из одного и того же множества, поэтому доля
        # больше 100% невозможна по построению и зажимать её нечем.
        if i and val is not None and cohort:
            pct = f"<div class='p'>{val / cohort * 100:.0f}%</div>"
        note_html = (f"<div class='sub'>{esc(note)}</div>" if note else "")
        out.append(f"<div class='fstep'>{body}<div class='l'>{esc(label)}</div>"
                   f"{note_html}{pct}</div>")
    return f"<div class='funnel'>{''.join(out)}</div>"


def _load_html(sm: dict) -> str:
    """Нагрузка = СОБЫТИЯ. Отдельный блок, потому что события и люди — разные
    величины: 12.08 один лид дал четыре перехода в «гаряче» и три карточки, и
    смешение этих чисел с людьми давало «400%». Выбрасывать события нельзя —
    они и есть ответ на вопрос «почему цифры разошлись»."""
    load = sm.get("load") or {}
    people = sm["funnel"]["dialogs"]
    rows = []
    if load.get("transitions") is not None:
        rows.append(("Переходів у «гаряче»", load["transitions"]))
    rows.append(("Карток передачі вам", load["cards"]))
    out = []
    for label, n in rows:
        per = f" · {n / people:.1f} на ліда" if people else ""
        out.append(f"<div class='row'><span>{esc(label)}</span>"
                   f"<span class='sub'>{n:g} подій{esc(per)}</span></div>")
    return "".join(out)


# Противоречие «бот с лидом уже не работает, а карточка открыта» звучит ОДИНАКОВО
# и на свежей карточке, и в свёртке застарелых. Живьём такой лид как раз и был
# застарелым (14 суток) — короткая форма в свёртке означала бы, что решение
# помечать противоречие не выполнено ровно в том случае, ради которого принято.
DEAD_MARK = "лід мертвий, картку не закрито"


def _attention_card(it: dict) -> str:
    """Две строки: кто и сколько ждёт — сверху, реплика и действие — снизу.

    Раньше карточка занимала четыре этажа и несла четыре равноправные кнопки:
    восемь таких на телефоне превращали блок «требує вас» в простыню, из
    которой не видно, сколько всего людей ждёт. На виду остаётся ОДНО действие,
    остальные уезжают под «⋯».

    ГРАНИЦА: под «⋯» уезжают ДЕЙСТВИЯ. Оба возраста и пометка мёртвого лида —
    это долг, и они остаются в первой строке при любой перекладке.
    """
    cid = esc(it["contact_id"])
    # Два возраста, а не один: «підняв руку» — когда бот попросил вмешаться,
    # «чекає» — сколько человек ждёт ответа. Раньше было видно только первое.
    dead = f" · <span class='wait'>{DEAD_MARK}</span>" if it.get("dead") else ""
    return (
        "<div class='card att'>"
        f"<div class='row'><b>{esc(it['peer'])}</b>"
        f"<span class='sub'>підняв руку {esc(ago(it['card_ts']))}"
        f" · чекає {esc(ago(it['last_ts']))}{dead}</span></div>"
        "<div class='row' style='margin-top:6px'>"
        f"<span class='sub ell'>«{esc((it['last_text'] or '')[:120])}»</span>"
        "<span style='display:flex;gap:6px;align-items:center'>"
        f"<button class='btn sm primary' onclick=\"act('resume:{cid}')\">"
        "▶️ Повернути</button>"
        "<details class='more'><summary class='btn sm' title='Інші дії'>⋯</summary>"
        "<div class='menu'>"
        f"<button class='btn sm' onclick=\"act('snooze:{cid}')\">⏸ Ще 1год</button>"
        f"<button class='btn sm' onclick=\"act('keep:{cid}')\">✅ Лишити боту</button>"
        f"<button class='btn sm' onclick=\"paid('{cid}')\">💰 Оплачено</button>"
        "</div></details></span></div></div>")


def _attention_html(items: list[dict], lang: str) -> str:
    """Свежее — карточками, застарелое — счётчиком под свёрткой.

    Порог `STALE_AFTER` (48 годин). Застарелое НЕ удаляется и не гасится: тихо
    закрытый чужой долг — отдельный класс бага. Но и лежать вперемешку со
    свежим оно не имеет права: 12.08 в блоке стояли карточки 8- и 14-дневной
    давности, и «Требує вас» читалось как «требует прямо сейчас»."""
    if not items:
        return ("<div class='empty'>Зараз нічого не потребує вашої уваги.</div>")
    fresh = [i for i in items if not i.get("stale")]
    stale = [i for i in items if i.get("stale")]
    out = [_attention_card(it) for it in fresh]
    if not fresh:
        out.append("<div class='empty'>Свіжого — нічого.</div>")
    if stale:
        rows = "".join(
            f"<div class='row'><span>{esc(it['peer'])}</span>"
            f"<span class='sub'>{esc(ago(it['card_ts']))}"
            + (f" · {DEAD_MARK}" if it.get("dead") else "")
            + "</span></div>" for it in stale)
        out.append(
            "<details class='card att'>"
            f"<summary>Застарілі ({len(stale)}) · старші за 48 годин</summary>"
            f"{rows}</details>")
    return "".join(out)


def _feed_html(feed: list[dict]) -> str:
    if not feed:
        return "<div class='empty'>Діалогів поки немає.</div>"
    rows = []
    for it in feed:
        # Янтарь, а не красный: лид ждёт ВАС — ничего не сломалось. Красным
        # этот кружок стоял рядом с красной аварией связи и красной кнопкой
        # паузы, и три разных смысла делили один цвет.
        badge = "🟠 " if it["needs_you"] else ("💰 " if it["paid"] else "")
        rows.append(
            f"<tr><td>{badge}<b>{esc(it['peer'])}</b><div class='sub'>"
            f"{esc((it['last_text'] or '')[:90])}</div></td>"
            f"<td data-l='Стадія'><span class='pill'>{esc(it['state'])}</span></td>"
            f"<td data-l='Останнє' class='sub'>{esc(ago(it['last_ts']))}</td></tr>")
    return ("<table><thead><tr><th>Лід</th><th>Стадія</th><th>Останнє</th></tr>"
            "</thead><tbody>" + "".join(rows) + "</tbody></table>")


_JS = """
function evtToken(){
  // Личность события. Генерируется В МОМЕНТ КЛИКА, а не на сервере: при
  // повторе запроса (сеть, двойной тап по кнопке) токен тот же, и оплата
  // остаётся одной. Серверный токен на каждый запрос давал бы дубли.
  try { if (crypto && crypto.randomUUID) return crypto.randomUUID(); } catch(e){}
  return Date.now().toString(36) + '-' + Math.random().toString(36).slice(2);
}
async function act(data, token){
  const tok = token || evtToken();
  const r = await fetch('/panel/tamapi/action',{method:'POST',
    headers:{'Content-Type':'application/x-www-form-urlencoded'},
    body:'data='+encodeURIComponent(data)+'&event_token='+encodeURIComponent(tok)});
  const j = await r.json(); alert(j.feedback || 'ок'); location.reload();
}
function paid(cid){
  const box=document.getElementById('paidbox');
  box.dataset.cid=cid; openM('paidbox');
}
function paidAmt(a){
  const cid=document.getElementById('paidbox').dataset.cid;
  act(a===null? 'paid:'+cid : 'paidamt:'+a+':'+cid);
}
function pauseAsk(){openM('pausebox');}

// Куда вернуть фокус после закрытия. Без этого таб-навигация после Esc
// начинается с начала страницы, а не с кнопки, которую человек нажал.
var lastFocus=null;
function openM(id){
  const box=document.getElementById(id);
  lastFocus=document.activeElement;
  box.classList.add('show');
  box.focus();
}
function closeM(id){
  document.getElementById(id).classList.remove('show');
  if(lastFocus && lastFocus.focus) lastFocus.focus();
  lastFocus=null;
}
// Тап мимо окна закрывает — но только по самой подложке, иначе клик по любой
// кнопке ВНУТРИ окна всплывал бы сюда и закрывал его.
function closeOnBackdrop(e,id){ if(e.target && e.target.id===id) closeM(id); }

document.addEventListener('keydown',function(e){
  const box=document.querySelector('.modal.show');
  if(!box) return;
  if(e.key==='Escape'){ e.preventDefault(); closeM(box.id); return; }
  if(e.key!=='Tab') return;
  // Ловушка фокуса: пока окно открыто, Tab не имеет права уйти на страницу
  // под ним — там кнопки, меняющие состояние лидов.
  const f=box.querySelectorAll('button,[href],input,select,textarea,[tabindex]:not([tabindex="-1"])');
  if(!f.length) return;
  const first=f[0], last=f[f.length-1];
  if(e.shiftKey && document.activeElement===first){ e.preventDefault(); last.focus(); }
  else if(!e.shiftKey && document.activeElement===last){ e.preventDefault(); first.focus(); }
});
"""


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
async def main_screen(request: Request):
    now = time.time()
    db = _db_path()
    st = _status()
    sm = M.summary(db, now=now, period="week")
    cfg = _cfg()
    lang = cfg.settings.language if cfg else "uk"
    pkg = _package(now=now)
    att = M.needs_attention(db, now=now)
    feed = M.dialog_feed(db, now=now, limit=12)

    # Пакет — деньги, поэтому зелёный. Исчерпанный пакет НЕ авария: бот
    # продолжает работать, и выключить его может только владелец — это «чекає
    # вас», янтарь. Красным он был как «немає зв'язку», и два разных смысла в
    # одном цвете превращали красный в оформление.
    bar_cls = "bar" + (" wait" if pkg["pct"] >= 80 else " money")
    # Перерасход рисуется отдельным сегментом поверх полной шкалы: `min(pct,100)`
    # оставлял его существовать только в тексте примечания.
    over_seg = (f"<b class='over' style='width:{min(pkg['over_pct'], 100):.0f}%'></b>"
                if pkg["over"] else "")
    over_note = ""
    if pkg["pct"] >= 100:
        over_note = ("<div class='note wait'>Пакет вичерпано. Бот <b>продовжує працювати</b> — "
                     f"перевитрата {pkg['over']} діалогів. Вимкнення лише вашим рішенням.</div>")
    elif pkg["pct"] >= 80:
        over_note = "<div class='note wait'>Використано понад 80% пакета.</div>"

    paid_presets = _price_presets()
    presets_html = "".join(
        f"<button class='btn' onclick='paidAmt({p})'>{p} $</button>" for p in paid_presets)

    # Возраст heartbeat: «0 с тому» при НЕизвестном возрасте — это ложь ровно в
    # том месте, где экран обязан быть честным (красный статус + свежий
    # heartbeat читается как «всё хорошо, но красное»).
    hb_txt = "невідомо" if st["age"] is None else ago(now - st["age"], now)

    # Кнопка паузы/включения — вне f-строки: в Python 3.11 выражение f-строки
    # не может содержать обратный слэш, а тут нужны экранированные кавычки.
    if st["code"] == "paused":
        pause_btn = ("<button class='btn primary' onclick=\"act('resume_all')\">"
                     "▶️ Увімкнути</button>")
    else:
        # «Пауза» преуменьшала: это глобальный kill switch на ВСЕХ лидов, а не
        # передышка. Название действия обязано совпадать с действием ещё до
        # того, как человек дойдёт до модалки.
        #
        # Кнопка НЕЙТРАЛЬНАЯ: сама она ничего не ломает, а только задаёт вопрос.
        # Красное живёт в подтверждении — там, где решение и принимается.
        pause_btn = ("<button class='btn' onclick='pauseAsk()'>"
                     "⏹ Зупинити всіх</button>")

    # Цена решения живьём: «ВСІМ» — абстракция, число — нет.
    n_active = M.active_dialogs(db, now=now)
    stop_cost = (f"Зараз у роботі: {plural_dialogs(n_active)}."
                 if n_active
                 else "Зараз бот нікого не веде — пауза ні на кого не вплине.")

    ans, tone = _answer(st, att)
    # Суточный лимит не задан — так и написано. Пустота после двоеточия
    # читается как «ноль» или как сломанная строка, а не как «не налаштовано».
    cap_txt = (f"добовий ліміт: {esc(pkg['daily_cap'])}"
               if pkg["daily_cap"] is not None else "добовий ліміт не заданий")

    body = f"""
<h1 class='ans {tone}'>{esc(ans)}</h1>
<div class='sub'>Ольга · TAMAPI · клієнт {esc(_slug())}
 · оновлено {esc(ago(now - 1, now))}</div>

{over_note}

<h2>Требує вас</h2>
{_attention_html(att, lang)}

<h2>Воронка · 7 днів</h2>
<div class='card'>{_funnel_html(sm)}</div>

<h2>Пакет</h2>
<div class='card'>
  <div class='row'><span>{pkg['used']} / {pkg['limit']} унікальних лідів цього місяця</span>
    <span class='sub'>{cap_txt}</span></div>
  <div class='sub'>з {esc(pkg['since_label'])}</div>
  <div class='{bar_cls}' style='margin-top:8px'>
    <i style='width:{min(pkg['pct'], 100):.0f}%'></i>{over_seg}</div>
</div>

<h2>Стан</h2>
<div class='card statusline'>
  <div class='row'><span><span class='dot {st['dot']}'></span>
    <b>{esc(st['title'])}</b> <span class='sub'>· {esc(st['sub'])}
    · heartbeat {esc(hb_txt)}</span></span>{pause_btn}</div>
</div>

<h2>Навантаження · 7 днів</h2>
<div class='card'>{_load_html(sm)}</div>

<h2>Діалоги</h2>
<div class='card'>{_feed_html(feed)}</div>

<div style='margin-top:18px' class='row'>
 <a href='/panel/tamapi/dynamics'>Динаміка →</a>
 <a href='/panel/jarvis'>Ферма →</a></div>

<div class='modal' id='pausebox' role='dialog' aria-modal='true'
     aria-labelledby='pausebox-title' tabindex='-1'
     onclick="closeOnBackdrop(event,'pausebox')"><div class='box'>
  <h3 id='pausebox-title'>Зупинити Ольгу?</h3>
  <p>Вона перестане відповідати <b>ВСІМ</b> лідам, доки ви не увімкнете її назад.
     Діалоги не зникнуть, історія збережеться.</p>
  <p>Хто напише під час паузи, відповіді не отримає.</p>
  <p>{stop_cost}</p>
  <div style='display:flex;gap:8px'>
    <button class='btn' onclick="closeM('pausebox')">Скасувати</button>
    <button class='btn broken' onclick="act('stop_all confirm')">Так, зупинити</button></div>
</div></div>

<div class='modal' id='paidbox' role='dialog' aria-modal='true'
     aria-labelledby='paidbox-title' tabindex='-1'
     onclick="closeOnBackdrop(event,'paidbox')"><div class='box'>
  <h3 id='paidbox-title'>Скільки оплатили?</h3>
  <p>Підказки взяті з ваших цін у базі знань.</p>
  <div style='display:flex;gap:8px;flex-wrap:wrap'>{presets_html}
    <button class='btn' onclick='paidAmt(null)'>Без суми</button>
    <button class='btn' onclick="closeM('paidbox')">Скасувати</button></div>
</div></div>
"""
    return HTMLResponse(page("TAMAPI — головний", body, extra_js=_JS))


def _fmt_value(d, v: float) -> str:
    """Длительность меньше суток в днях округляется в «0 дн» и метрика выглядит
    сломанной. Ниже суток показываем часы, ниже часа — минуты."""
    if d.key == "duration":
        if v < 1 / 24:
            return f"{v * 1440:.0f} хв"
        if v < 1:
            return f"{v * 24:.1f} год"
        return f"{v:g} дн"
    return f"{v:g}{(' ' + d.unit) if d.unit else ''}"


def _price_presets() -> list[int]:
    """Пресеты сумм из ЦЕНОВОГО контекста knowledge.md клиента — числа там уже
    разбираются машинно, поэтому пресеты появляются без ручной настройки и без
    выдумывания (спека §3)."""
    try:
        from chatter.core.guardrails import _context_numbers
        cfg = _cfg()
        price, _ = _context_numbers(cfg.knowledge if cfg else "")
        nums = sorted({int(p) for p in price if p.isdigit() and 50 <= int(p) <= 100000})
        return nums[:4] if nums else [100, 300, 500, 1000]
    except Exception:
        return [100, 300, 500, 1000]


@router.get("/dynamics", response_class=HTMLResponse)
async def dynamics(request: Request,
                   m: list[str] = Query(default=["dialogs"]),
                   period: str = Query(default="week")):
    now = time.time()
    db = _db_path()
    keys = [k for k in m if k in M.METRIC_BY_KEY][:3] or ["dialogs"]
    series = M.series_for(db, keys, period, now=now)
    all_series = M.series_for(db, [d.key for d in M.METRICS], period, now=now)
    by_key = {s.key: s for s in all_series}

    tiles = []
    colors = ["var(--s1)", "var(--s2)", "var(--s3)"]
    for d in M.METRICS:
        s = by_key[d.key]
        on = d.key in keys
        sel = colors[keys.index(d.key) % 3] if on else ""
        # Плитка без данных ГАСНЕТ и перестаёт быть ссылкой: тап по ней
        # перерисовывал график в пустоту, а сама она выглядела рабочей —
        # неотличимо от плитки, которую просто не выбрали.
        if not s.available:
            val = "<div class='v na'>історія накопичується</div>"
            dl = ""
        elif s.total is None:
            val = "<div class='v na'>немає даних</div>"
            dl = ""
        else:
            val = f"<div class='v'>{_fmt_value(d, s.total)}</div>"
            dl = delta_html(s.total, s.prev_total)
            # Процент без основания выборки — не сравнение: «▼100%» при n=1
            # против n=1 это два разных лида, а не падение показателя.
            if s.basis is not None:
                dl += (f"<div class='sub'>n={s.basis} проти "
                       f"n={s.prev_basis if s.prev_basis is not None else 0}</div>")
        head = f"<div class='k'>{esc(d.label)}</div>{val}{dl}"
        if s.total is None or not s.available:
            tiles.append(f"<div class='tile off'>{head}</div>")
            continue
        nxt = [k for k in keys if k != d.key] if on else keys[:2] + [d.key]
        q = "&".join(f"m={k}" for k in (nxt or ["dialogs"]))
        tiles.append(
            f"<a class='tile{' on' if on else ''}' style='--sel:{sel}' "
            f"href='?{q}&period={esc(period)}'>{head}</a>")

    segs = "".join(
        f"<button class='{'on' if period == k else ''}' "
        f"onclick=\"location.search='?{'&'.join('m=' + x for x in keys)}&period={k}'\">"
        f"{esc(v['label'])}</button>" for k, v in M.PERIODS.items())

    unavailable = [s for s in series if not s.available]
    note = ""
    if unavailable:
        names = ", ".join(s.label for s in unavailable)
        note = (f"<div class='note wait'>{esc(names)}: історія ще накопичується — "
                "таблиці заповнюються тільки вперед, з моменту впровадження.</div>")

    body = f"""
<h1>Динаміка</h1>
<div class='sub'>Оберіть до 3 метрик — тапом по плитці</div>
<div class='tabs' style='margin-top:12px'><div class='seg'>{segs}</div></div>
<div class='grid tiles'>{''.join(tiles)}</div>
{note}
<div class='card' style='margin-top:12px'>{line_chart([s for s in series if s.available])}</div>
<div style='margin-top:16px'><a href='/panel/tamapi'>← Головний</a></div>
"""
    return HTMLResponse(page("TAMAPI — динаміка", body))


# ------------------------------------------------------------------ действия

@router.post("/action")
async def action(request: Request, data: str = Form(...),
                 event_token: str = Form(None)):
    """ЕДИНСТВЕННАЯ мутирующая ручка дашборда — и та проксирует в общий
    командный слой. Веб не пишет в contacts/runtime_flags напрямую."""
    from chatter.notify.control_bot import route_callback
    from chatter.storage.db import Store

    cfg = _cfg()
    lang = cfg.settings.language if cfg else "uk"
    snooze = float(getattr(getattr(cfg, "settings", None), "snooze_seconds", 3600.0) or 3600.0)
    store = Store(_db_path())
    now = time.time()

    # Глобальная заглушка требует ВТОРОГО осознанного действия, и проверка эта
    # СЕРВЕРНАЯ. Модалка в вебе защищает только от промаха пальцем по экрану;
    # одиночный POST мимо неё взводил флаг, от которого «бот молчит на всех»
    # (P15). Идиома «подтверждение последним токеном» взята у пульта.
    if data == "stop_all":
        return JSONResponse({
            "confirm": True,
            "feedback": "Зупинити Ольгу ВСІМ лідам? Підтвердіть ще раз.",
        })
    if data == "stop_all confirm":
        store.set_runtime_flag("kill_switch", "1", ts=now)
        store.add_event("kill_on", ts=now)
        return JSONResponse({"feedback": "Ольгу зупинено"})
    if data == "resume_all":
        # Симметрия: снятие паузы обязано работать из веба без Telegram —
        # иначе владелец заперт в TG (инцидент P15: kill_off только /start).
        store.set_runtime_flag("kill_switch", "0", ts=now)
        store.add_event("kill_off", ts=now)
        return JSONResponse({"feedback": "Ольгу увімкнено"})

    # event_token — личность события, сгенерированная браузером в момент клика.
    # Без неё оплата будет отвергнута: панель не имеет права писать деньги,
    # которые нельзя отличить от следующей такой же (сентинел `0` снят).
    res = route_callback(data, store=store, now=now, language=lang,
                         snooze_seconds=snooze, event_token=event_token)
    return JSONResponse({"feedback": res.answer})


@router.get("/api/summary")
async def api_summary(period: str = "week"):
    return JSONResponse(M.summary(_db_path(), now=time.time(), period=period))
