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

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from app.routers.panels_auth import require_owner
from app.routers.panels_ui import (ago, delta_html, esc, leads_waiting,
                                   line_chart, page, plural_dialogs)
from app.services import tamapi_metrics as M
from chatter.core.console import DIALOG_PATH_PREFIX as console_dialog_prefix

router = APIRouter(prefix="/panel/tamapi", tags=["tamapi-dashboard"],
                   dependencies=[Depends(require_owner)])

CLIENTS_DIR = Path(os.getenv("CHATTER_CLIENTS_DIR", "chatter/clients"))


def _db_path() -> str:
    return os.getenv("TAMAPI_DB", ".secrets/demo.db")


def _slug() -> str:
    return os.getenv("TAMAPI_SLUG", "volska")


# ДВЕ ФОРМЫ ОТМЕТКИ ЖИВОСТИ, и знать нужно обе — та же грабля, что в
# `ops_watchdog.py` (чинилась 16.08, см. CHATTER_BEAT_LEGACY_NAME там же).
#   легаси          — один безымянный раннер писал state/chatter_heartbeat.txt
#   мультиклиентная — раннер клиента пишет state/chatter_heartbeat_<slug>.txt
# 16.08 17:08 поднялся мультиклиентный гардиан, легаси-файл с той минуты не
# трогает НИКТО — а панель читала только его и вторые сутки показывала клиенту
# «Немає зв'язку» при живом боте. Экран, всегда красный при нормальной работе,
# — это не сторож, а фон, и клиент перестаёт ему верить ровно к тому дню,
# когда связь оборвётся по-настоящему.
BEAT_LEGACY_NAME = "chatter_heartbeat.txt"


def _beat_candidates() -> list[Path]:
    """Обе формы, в порядке «своя, потом общая». Порядок здесь ни на что не
    влияет (побеждает свежесть, а не позиция) — он только для читаемости."""
    state = Path("state")
    return [state / f"chatter_heartbeat_{_slug()}.txt", state / BEAT_LEGACY_NAME]


def _heartbeat() -> tuple[float | None, str | None]:
    """Возраст отметки живости И ИМЯ ФАЙЛА, из которого он взят.

    Источник возвращается наружу намеренно: дефект прожил двое суток именно
    потому, что канал был не виден — экран говорил «технічна проблема», и по
    нему нельзя было понять, ЧТО он прочитал.

    `TAMAPI_HEARTBEAT`, если задан, остаётся ЕДИНСТВЕННЫМ источником: на нём
    стоит демо-стенд (`scripts/panels_demo.py`) и инстанс клиента со своим
    файлом. Догадка поверх явного указания увела бы стенд на чужую отметку —
    тот же класс, только зеркальный.

    Без него — берём САМУЮ СВЕЖУЮ из известных форм. Правило отличается от
    `ops_watchdog` («красим по самому старому») осознанно: там проба смотрит на
    ВСЮ ферму и обязана заметить любого упавшего, здесь экран показывает ОДНОГО
    клиента, и самая старая форма — это просто заброшенный файл.
    """
    now = time.time()
    env = os.getenv("TAMAPI_HEARTBEAT")
    if env:
        p = Path(env)
        try:
            return now - p.stat().st_mtime, p.name
        except OSError:
            return None, None
    best: tuple[float | None, str | None] = (None, None)
    for p in _beat_candidates():
        try:
            age = now - p.stat().st_mtime
        except OSError:
            continue
        if best[0] is None or age < best[0]:
            best = (age, p.name)
    return best


def _heartbeat_age() -> float | None:
    """Совместимость: возраст без источника."""
    return _heartbeat()[0]


def _cfg():
    """Конфиг клиента (язык, лимиты, owner_ref). Падение конфига не имеет права
    ронять экран — дашборд обязан показать статус даже при кривом yaml."""
    try:
        from chatter.config.loader import load_config
        return load_config(CLIENTS_DIR, _slug())
    except Exception:
        return None


# Что показать вместо имени, когда конфиг клиента не прочитан. Формулировка
# называет НАСТОЯЩУЮ причину — тот же приём, что у `cap_txt` ниже: пустое место
# в шапке читается клиентом как «так и задумано», а подстановка любого имени по
# умолчанию — это чужая персона на его экране.
NAME_UNREADABLE = "конфіг клієнта не прочитано"


def _persona_name() -> str:
    """Имя бота для ШАПКИ — единственное место экрана, где имя вообще звучит.

    Остальной копирайт написан без имени («бот»), и это не стилистика, а
    конструкция (спека 2026-08-20 §3, вариант Б). Украинский склоняет имя, а
    вывести форму по правилу нельзя: она зависит от рода и окончания и на живых
    именах ошибается. Подстановка одной формы во все места дала бы «Немає
    зв'язку з Ярина» — клиент прочтёт это как поломку интерфейса.

    Альтернатива (три формы рядом с `persona_name` в конфиге) отвергнута
    осознанно: она держится на том, что человек не забудет заполнить два
    необязательных поля при подключении КАЖДОГО клиента, а последствие пропуска
    увидит клиент, а не мы. Здесь неправильного состояния просто нет.

    Источник один — `persona_name` конфига. Отдельная env-переменная стала бы
    вторым числом на ту же вещь и разошлась бы ровно тогда, когда имя поменяют
    в конфиге.
    """
    cfg = _cfg()
    name = getattr(getattr(cfg, "settings", None), "persona_name", None)
    return (name or "").strip() or NAME_UNREADABLE


def _status() -> dict:
    """Три состояния, не два (спека §2.1): «зелёный» при взведённом kill_switch
    был бы прямой ложью — бот жив, но молчит всем."""
    from chatter.storage.db import Store
    age, beat_src = _heartbeat()
    killed = False
    try:
        # `with`, а не `Store(...)` плюс `del`: `del` снимает ОДНУ ссылку и
        # надеется на счётчик, а до него не доходит вовсе, если что-то бросит
        # выше. Процесс панели живёт неделями, а лампу зовут на каждый показ.
        with Store(_db_path()) as s:
            killed = (s.get_runtime_flag("kill_switch") or "0").strip() == "1"
    except Exception:
        pass
    # Точка красится по СМЫСЛУ: красный — только «зламано» (связи нет), янтарь —
    # «чекає вас» (пауза снимается вами), «на зв'язку» цвета не получает вовсе.
    if age is None or age > 90:
        return {"code": "down", "dot": "broken", "title": "Немає зв'язку",
                "sub": "технічна проблема, ми вже бачимо", "age": age,
                "beat_source": beat_src}
    if killed:
        return {"code": "paused", "dot": "wait", "title": "На паузі",
                "sub": "зупинено вами", "age": age, "beat_source": beat_src}
    return {"code": "live", "dot": "calm", "title": "На зв'язку",
            "sub": "відповідає", "age": age, "beat_source": beat_src}


def _answer(st: dict, att: list[dict]) -> tuple[str, str]:
    """Ответ экрана одной строкой: «мені зараз щось треба робити?».

    Порядок ответов — это и есть приоритет. Связи нет — про очередь говорить
    рано: цифры под ответом уже неживые, и звать человека разбирать их значит
    звать его не туда. Дальше долг, потом пауза, потом тишина.
    """
    fresh = [i for i in att if not i.get("stale")]
    if st["code"] == "down":
        return "Немає зв'язку з ботом", "broken"
    if fresh:
        return leads_waiting(len(fresh)), "wait"
    if st["code"] == "paused":
        return "Бот на паузі — не відповідає нікому", "wait"
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


def _farm_link_html(request: Request) -> str:
    """Ссылка на ферму — только если ферма есть В ЭТОМ приложении.

    Спека `docs/superpowers/specs/2026-08-20-farm-link-off-client-instance.md`,
    вариант Б. Инстанс клиента (`app.panel_client.build_app`) монтирует ровно
    два роутера, и `/panel/jarvis` в нём нет ПО ЗАМЫСЛУ: тот же ключ иначе
    открыл бы клиенту PID'ы, ветки и сроки ключей фермы. Ссылка при этом
    рисовалась всегда — и вела в 404. В `logs/panel_yarina.stdout.log` за 20.08
    две записи `GET /panel/jarvis ... 404`: клиент ткнул дважды.

    Слово «Ферма» уезжает вместе со ссылкой, и это не побочный эффект, а второй
    дефект той же строки: 404 читается как поломка, а слово читается ВЕРНО —
    оно рассказывает клиенту, что за его ботом стоит ферма других клиентов.

    Признак берётся у приложения, которое отдаёт страницу, а НЕ из переменной
    окружения: тупиком ссылку делает состав приложения, значит и спрашивать
    надо состав. Env был бы вторым числом на ту же вещь — забытая переменная
    однажды показала бы ссылку клиенту, ровно то, что чиним.
    """
    mounted = any(getattr(r, "path", "").startswith("/panel/jarvis")
                  for r in request.app.routes)
    return "\n <a href='/panel/jarvis'>Ферма →</a>" if mounted else ""


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
    # ИСТОЧНИК ОТМЕТКИ — в разметку, комментарием СНАРУЖИ карточки статуса:
    # внутри он разрывает пару </div></div>, по которой сторожа panels_hierarchy
    # находят статус-строку (поймано полным гейтом: 41-й красный).
    # Клиенту имя файла не нужно,
    # а вот «панель врёт, и непонятно откуда» стоило двух суток лжи на экране
    # Ольги: канал обязан быть видимым в том же артефакте, который врёт.
    beat_src_html = "<!-- heartbeat: %s -->" % esc(st.get("beat_source") or "джерела немає")

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
    # Пустота после двоеточия читается как «ноль» или как сломанная строка,
    # поэтому заглушка обязательна — и обязана называть НАСТОЯЩУЮ причину.
    # `daily_cap` имеет дефолт 500 в лоадере, значит None означает ровно одно:
    # конфиг клиента не прочитан (нет файла либо он битый). «Ліміт не заданий»
    # звучало как штатная настройка — спокойная ложь на экране КЛИЕНТА, тогда
    # как экран в этот момент не знает о нём вообще ничего.
    cap_txt = (f"добовий ліміт: {esc(pkg['daily_cap'])}"
               if pkg["daily_cap"] is not None else "конфіг клієнта не прочитано")
    farm_link = _farm_link_html(request)

    body = f"""
<h1 class='ans {tone}'>{esc(ans)}</h1>
<div class='sub'>{esc(_persona_name())} · TAMAPI · клієнт {esc(_slug())}
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
</div>{beat_src_html}

<h2>Навантаження · 7 днів</h2>
<div class='card'>{_load_html(sm)}</div>

<h2>Діалоги</h2>
<div class='card'>{_feed_html(feed)}</div>

<div style='margin-top:18px' class='row'>
 <a href='/panel/tamapi/dynamics'>Динаміка →</a>{farm_link}</div>

<div class='modal' id='pausebox' role='dialog' aria-modal='true'
     aria-labelledby='pausebox-title' tabindex='-1'
     onclick="closeOnBackdrop(event,'pausebox')"><div class='box'>
  <h3 id='pausebox-title'>Зупинити бота?</h3>
  <p>Бот перестане відповідати <b>ВСІМ</b> лідам, доки ви не увімкнете його назад.
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


# ------------------------------------------------- страница ОДНОГО диалога
#
# Спека 2026-08-28 §2.7: адрес диалога создаёт пара D. Ручка `POST
# /api/outgoing` живёт в дереве с 25.08 и до сих пор осталась без кнопки
# ровно потому, что кнопке негде было находиться: у дашборда четыре ручки и
# ни одной страницы разговора. Пульт — ПОТРЕБИТЕЛЬ этого адреса
# (`console.dialog_link`), а не его предусловие.

# Личность НАЖАТИЯ (§2.5). Рождается на СОСТАВЛЕНИЕ сообщения — то есть на
# рендер формы, а не на страницу и не на контакт: очистил поле, набрал заново
# — новый токен. Текст в ключ не входит намеренно: два одинаковых «Добрый
# день» — это два сообщения, и оба обязаны уйти.
#
# 🔴 СЛУЧАЙНЫЙ, А НЕ СЧЁТЧИК И НЕ ВРЕМЯ. Строка очереди НЕ УДАЛЯЕТСЯ НИКОГДА
# (§2.5 п.4), значит и токен живёт вечно: счётчик или время с секундной
# точностью через месяц молча «продублируют» чужое задание, и панель ответит
# «уже поставлено» на сообщение, которого владелец не отправлял.
_FORM_TOKEN_BYTES = 18

# Адрес страницы — ОДИН на дерево и лежит он в `chatter/core/console.py`,
# потому что оттуда же на него ссылается пульт. Здесь он не переписывается, а
# вычитается: два литерала «/d/» разъехались бы в день переименования, и
# ссылка владельца вела бы в 404 молча ([[jarvis-two-numbers-for-one-thing]]).
if not console_dialog_prefix.startswith(router.prefix + "/"):
    raise RuntimeError(
        "адрес диалога %r больше не лежит под префиксом роутера %r: ссылка "
        "пульта и страница разъехались"
        % (console_dialog_prefix, router.prefix))
_DIALOG_ROUTE = console_dialog_prefix[len(router.prefix):] + "{contact_id}"


def _form_token() -> str:
    import secrets
    return secrets.token_urlsafe(_FORM_TOKEN_BYTES)


# 🔴 ПОВТОР НАЗЫВАЕТСЯ СЛОВАМИ (§2.5 п.3). `duplicate: true` — это НОРМА
# (кнопку нажали дважды), но показать его как успех значит соврать, а как
# ошибку — напугать. Молчание здесь хуже обоих: это человек, жмущий третий
# раз.
#
# Успешная постановка ГАСИТ ТОКЕН В ФОРМЕ и рождает следующий (§2.5 п.2) —
# перезагрузкой страницы, а не правкой поля на месте: токен рождает СЕРВЕР,
# и второй его источник в браузере стал бы вторым числом на ту же вещь. Заодно
# лента показывает только что отправленное.
#
# Без JS форма остаётся рабочей: обычный POST уедет тем же телом, просто
# ответом будет JSON. То есть скрипт улучшает показ, а не держит отправку.
_DIALOG_JS = """
document.querySelector('form').addEventListener('submit', async function (e) {
  e.preventDefault();
  var note = document.getElementById('send-note');
  note.textContent = 'надсилаю…';
  try {
    var r = await fetch(e.target.action, {method: 'POST',
                                          body: new FormData(e.target)});
    var d = await r.json();
    if (!r.ok) { note.textContent = 'не поставлено: ' + (d.detail || r.status); return; }
    if (d.duplicate) { note.textContent = 'вже поставлено, другого не буде'; return; }
    location.reload();
  } catch (err) { note.textContent = 'не поставлено: ' + err; }
});
"""


def _dialog_line_html(m: dict) -> str:
    """Одна реплика ленты. `author` живёт РЯДОМ с ролью, а не вместо неё."""
    role = m.get("role") or ""
    author = m.get("author") or ""
    who = "Ви" if author == "human" else ("Лід" if role == "user" else "Бот")
    tone = "wait" if role == "user" else ""
    return ("<div class='row %s'><b>%s</b> <span class='sub'>%s</span>"
            "<div>%s</div></div>"
            % (tone, esc(who), esc(ago(m.get("ts"))), esc(m.get("text"))))


@router.get(_DIALOG_ROUTE, response_class=HTMLResponse)
async def dialog_screen(contact_id: str):
    """Лента ОДНОГО разговора и поле ввода под ней.

    🔴 FAIL-CLOSED ПО АДРЕСУ (§5 п.13). Чужой `contact_id` — чужой слуг либо
    контакт, которого в этой базе нет, — получает ОТКАЗ, а не пустую ленту.
    Пустая лента здесь хуже отказа вдвойне: она выглядит как «диалог пуст», то
    есть врёт молча и приглашает написать в него из формы. Цена ошибки —
    показ чужой переписки, и она та же, что в §3.1 спеки веба.

    Слуг сверяется с `TAMAPI_SLUG` этого инстанса, а не с «каким-нибудь
    известным»: ключ от клиентской панели у клиентки, и адресная строка — это
    ровно тот способ увидеть чужое, который не требует ни ключа соседа, ни
    ошибки в коде.
    """
    from chatter.core import outgoing as delivery
    from chatter.core.channel_ref import slug_of
    from chatter.core.contact_ref import ContactRefError
    from chatter.storage.db import Store

    # 🔴 РЕЕСТР ДОСТАВЩИКОВ НАСЕЛЯЕТ ТОТ, КТО УМЕЕТ СЛАТЬ, а панель — ДРУГОЙ
    # процесс: до этого импорта `deliverer_for` здесь пуст, и `can_send_now`
    # честно ответила бы «канал не обслуживается» про КАЖДЫЙ диалог, включая
    # телеграмный. Предупреждение, которое горит всегда, — это не сторож, а
    # фон ([[jarvis-loud-failure-next-to-a-soothing-lamp]]), и владелец
    # перестал бы читать его ровно к тому дню, когда писать правда нельзя.
    #
    # Список каналов лежит ЗДЕСЬ, а не в ядре: ядро не имеет права знать имена
    # каналов ни в каком виде (§2.1), а панель — знает, что именно она
    # разворачивает. Второй канал добавит сюда вторую строку, и это тот же
    # «один шов на канал», только со стороны потребителя.
    import chatter.telethon_run  # noqa: F401 — регистрирует доставщика Telegram

    def _closed() -> HTTPException:
        # Одна и та же формулировка на все три причины: «не ваш диалог»
        # НАМЕРЕННО не рассказывает, существует ли такой контакт у соседа.
        return HTTPException(404, "діалог не знайдено")

    try:
        slug = slug_of(contact_id)
    except ContactRefError:
        raise _closed() from None
    if slug != _slug():
        raise _closed()

    # `with`, а не голый `Store(...)`: панель живёт неделями, и открытие
    # страницы не имеет права оставлять за собой хэндл файла БД (DEV-48 §1.2).
    with Store(_db_path()) as store:
        if not store.has_contact(contact_id):
            raise _closed()
        lenta = store.history(contact_id, limit=200)

    cfg = _cfg()
    lang = cfg.settings.language if cfg else "uk"

    # ТА ЖЕ функция, которой спрашивает ядро доставки ПЕРЕД отправкой (§2.6).
    # Зовётся через модуль, а не импортированным именем: подмена `can_send_now`
    # обязана менять ОБА ответа — и предупреждение здесь, и слова отказа в
    # строке очереди. Иначе это две реализации одного вопроса, и меньшая
    # погасит большую молча.
    refusal = delivery.can_send_now(contact_id)
    if refusal is None:
        warn_html = ""
        disabled = ""
    else:
        # Fail-closed на доставке — это ПОЗДНО: человек уже набрал текст.
        # Поэтому причина называется ДО поля ввода и ТЕМИ ЖЕ словами.
        warn_html = ("<div class='card'><div class='k'>Написати не можна</div>"
                     "<div class='sub'>%s</div></div>" % esc(refusal.human))
        disabled = " disabled"

    token = _form_token()
    body = (
        "<div class='wrap'>"
        "<h1>Діалог %s</h1>"
        "<div class='sub'><a href='/panel/tamapi'>← до головного</a></div>"
        "%s"
        "<div class='card'>%s</div>"
        "<div class='card'>"
        "<form method='post' action='/api/outgoing'>"
        "<input type='hidden' name='contact_id' value='%s'>"
        "<input type='hidden' name='event_token' value='%s'>"
        "<textarea name='text' rows='3' style='width:100%%'></textarea>"
        "<button type='submit'%s>Надіслати</button>"
        "<div class='sub' id='send-note'></div>"
        "</form></div></div>"
        % (esc(contact_id), warn_html,
           "".join(_dialog_line_html(m) for m in lenta) or
           "<div class='sub'>поки порожньо</div>",
           esc(contact_id), esc(token), disabled))
    return HTMLResponse(page("TAMAPI — діалог", body, extra_js=_DIALOG_JS,
                             lang=lang))


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
    # ВСЯ ручка внутри `with`: `return` из блока проходит через `__exit__`, то
    # есть соединение закрывается и на РАННИХ возвратах. Их тут четыре, и
    # самый частый — `stop_all`, который не мутирует ничего, но базу уже
    # открыл. Без `with` каждое нажатие оставляло хэндл в процессе, живущем
    # неделями (DEV-48 §1.2).
    with Store(_db_path()) as store:
        now = time.time()

        # Глобальная заглушка требует ВТОРОГО осознанного действия, и проверка
        # эта СЕРВЕРНАЯ. Модалка в вебе защищает только от промаха пальцем по
        # экрану; одиночный POST мимо неё взводил флаг, от которого «бот молчит
        # на всех» (P15). Идиома «подтверждение последним токеном» — у пульта.
        if data == "stop_all":
            return JSONResponse({
                "confirm": True,
                "feedback": "Зупинити бота ВСІМ лідам? Підтвердіть ще раз.",
            })
        if data == "stop_all confirm":
            store.set_runtime_flag("kill_switch", "1", ts=now)
            store.add_event("kill_on", ts=now)
            return JSONResponse({"feedback": "Бота зупинено"})
        if data == "resume_all":
            # Симметрия: снятие паузы обязано работать из веба без Telegram —
            # иначе владелец заперт в TG (инцидент P15: kill_off только /start).
            store.set_runtime_flag("kill_switch", "0", ts=now)
            store.add_event("kill_off", ts=now)
            return JSONResponse({"feedback": "Бота увімкнено"})

        # event_token — личность события, сгенерированная браузером в момент
        # клика. Без неё оплата будет отвергнута: панель не имеет права писать
        # деньги, которые нельзя отличить от следующей такой же (сентинел `0`).
        res = route_callback(data, store=store, now=now, language=lang,
                             snooze_seconds=snooze, event_token=event_token)
        return JSONResponse({"feedback": res.answer})


@router.get("/api/summary")
async def api_summary(period: str = "week"):
    return JSONResponse(M.summary(_db_path(), now=time.time(), period=period))
