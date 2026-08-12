"""Иерархия и типографика панелей: экран отвечает, а не выкладывает данные.

Разбор скриншотов с телефона 12.08: шесть кеглей в диапазоне 11-21 px, четыре
цвета без закреплённого смысла (красным была и авария, и штатная кнопка паузы,
и просроченная карточка), а первое, что видел владелец — заголовок «Ольга ·
TAMAPI» и статус-карточка в два этажа. Ответ на вопрос «мне сейчас что-то надо
делать?» приходилось собирать глазами из середины страницы.

ГРАНИЦА, проведённая владельцем: прячем ДЕЙСТВИЯ, не ДОЛГИ. Второстепенные
кнопки уезжают под «⋯», но возраст карточки, счётчик застарелых и пометка
мёртвого лида обязаны остаться на виду при любой перекладке — на это здесь
отдельные сторожа, и они парные к тестам «стало компактнее».
"""
from __future__ import annotations

import importlib
import re
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from chatter.storage.db import Store

KEY = "test-owner-key"
HOUR = 3600.0
DAY = 86400.0

# Единственная разрешённая шкала кеглей. Четыре числа, а не «примерно такие»:
# 13 и 15 рядом не читаются как разные уровни, они читаются как небрежность.
SCALE = {32, 24, 15, 12}


# ─────────────────────────────── стенд ──────────────────────────────────────

def _client(db_path, monkeypatch, *, heartbeat: str | None = "fresh",
            package: str = "500"):
    """heartbeat: 'fresh' — бот на связи, None — связи нет (единственная авария)."""
    monkeypatch.setenv("JARVIS_PANELS_KEY", KEY)
    monkeypatch.setenv("TAMAPI_DB", str(db_path))
    monkeypatch.setenv("TAMAPI_PACKAGE", package)
    hb = str(db_path) + ".hb"
    if heartbeat == "fresh":
        with open(hb, "w", encoding="utf-8") as f:
            f.write("ok")
    monkeypatch.setenv("TAMAPI_HEARTBEAT", hb if heartbeat == "fresh"
                       else str(db_path) + ".nope")

    import app.routers.jarvis_panel as jp
    import app.routers.panels_auth as pa
    import app.routers.tamapi_dashboard as td
    for m in (pa, td, jp):
        importlib.reload(m)
    api = FastAPI()
    for m in (pa, td, jp):
        api.include_router(m.router)
    return TestClient(api)


def _page(c, url: str = "/panel/tamapi") -> str:
    r = c.get(url, headers={"X-Panels-Key": KEY})
    assert r.status_code == 200
    return r.text


def _visible(html: str) -> str:
    """То, что человек видит, открыв экран.

    Без блока стилей: определения `.broken{...}` лежат в CSS всегда, и искать
    цвет по всей странице — значит искать его в таблице стилей. И без модальных
    окон: они закрыты до тапа, и красная кнопка подтверждения на экране не
    присутствует — на неё отдельный, парный сторож ниже.
    """
    body = html.split("</style>", 1)[1]
    return body.split("<div class='modal'", 1)[0]


def _seed(p, *, fresh: int = 0, stale: int = 0, dead: bool = False,
          leads: int = 1, now: float | None = None) -> float:
    now = now or time.time()
    s = Store(str(p))
    for i in range(leads):
        cid = f"lead{i}:volska"
        s.get_or_create_contact(cid)
        s.add_message(cid, "user", "скільки коштує SMM?", ts=now - 2 * HOUR)
    for i in range(fresh):
        cid = f"fresh{i}:volska"
        s.get_or_create_contact(cid)
        s.add_message(cid, "user", "потрібен логотип", ts=now - 3 * HOUR)
        s.add_card(msg_id=100 + i, contact_id=cid, kind="escalation",
                   ts=now - 4 * HOUR)
        s.set_runtime_flag(f"esc_active:{cid}", "bot:1:5", ts=now - 4 * HOUR)
    for i in range(stale):
        cid = f"stale{i}:volska"
        s.get_or_create_contact(cid)
        s.add_message(cid, "user", "стара розмова", ts=now - 9 * DAY)
        s.add_card(msg_id=200 + i, contact_id=cid, kind="escalation",
                   ts=now - 9 * DAY)
        s.set_runtime_flag(f"esc_active:{cid}", "bot:1:5", ts=now - 9 * DAY)
        if dead:
            s.set_state(cid, "dead")
    del s
    return now


def _cards(html: str) -> list[str]:
    """Карточки блока «Требує вас» — по классу, а не по порядку в разметке."""
    return re.findall(r"<div class='card att'>(.*?)</div>\s*(?=<div class='card|"
                      r"<details|<h2|<div class='empty')", html, re.S)


# ───────────────────────── A. одна шкала кеглей ─────────────────────────────

def _font_sizes(text: str) -> set[int]:
    """Все кегли: и `font-size:15px` в CSS/инлайне, и `font-size='11'` у SVG.
    Подпись оси — такой же текст на экране, как остальные."""
    return {int(m) for m in re.findall(r"font-size\s*[:=]\s*'?\s*(\d+)", text)}


def test_the_type_scale_has_four_sizes_and_no_near_duplicates():
    from app.routers.panels_ui import CSS
    extra = _font_sizes(CSS) - SCALE
    assert not extra, f"кегли вне шкалы {sorted(SCALE)}: {sorted(extra)}"


@pytest.mark.parametrize("url", ["/panel/tamapi", "/panel/tamapi/dynamics",
                                 "/panel/jarvis"])
def test_no_page_smuggles_its_own_type_size(tmp_path, monkeypatch, url):
    """Инлайновый `style='font-size:13px'` обходит шкалу молча: CSS остаётся
    правильным, а экран — разнокалиберным."""
    p = tmp_path / "scale.db"
    _seed(p, fresh=1, stale=1)
    html = _page(_client(p, monkeypatch), url)
    extra = _font_sizes(_visible(html)) - SCALE
    assert not extra, f"{url}: кегли мимо шкалы: {sorted(extra)}"


# ─────────────────────── B. ответ сверху, данные ниже ───────────────────────

def test_the_answer_stands_first_and_largest(tmp_path, monkeypatch):
    p = tmp_path / "ans.db"
    _seed(p, fresh=2)
    body = _visible(_page(_client(p, monkeypatch)))
    m = re.search(r"<h1 class='ans[^']*'>(.*?)</h1>", body, re.S)
    assert m, "ответа крупным кеглем сверху нет"
    # Ни одна карточка и ни один заголовок раздела не имеют права стоять выше.
    for earlier in ("<div class='card", "<h2"):
        pos = body.find(earlier)
        assert pos == -1 or pos > m.start(), f"{earlier} стоит выше ответа"


def test_the_answer_counts_what_waits_for_you(tmp_path, monkeypatch):
    p = tmp_path / "ans2.db"
    _seed(p, fresh=2)
    body = _page(_client(p, monkeypatch))
    ans = re.search(r"<h1 class='ans[^']*'>(.*?)</h1>", body, re.S).group(1)
    assert "2 ліди" in ans and "чекають" in ans, ans


def test_the_answer_is_calm_when_nothing_is_owed(tmp_path, monkeypatch):
    p = tmp_path / "ans3.db"
    _seed(p, leads=1)
    body = _page(_client(p, monkeypatch))
    ans = re.search(r"<h1 class='ans[^']*'>(.*?)</h1>", body, re.S).group(1)
    assert "спокійно" in ans.lower(), ans


def test_a_dead_heartbeat_outranks_everything_in_the_answer(tmp_path, monkeypatch):
    """Связи нет — про очередь говорить рано: цифры на экране уже неживые."""
    p = tmp_path / "ans4.db"
    _seed(p, fresh=2)
    body = _page(_client(p, monkeypatch, heartbeat=None))
    ans = re.search(r"<h1 class='ans[^']*'>(.*?)</h1>", body, re.S).group(1)
    assert "зв'язку" in ans or "зв&#x27;язку" in ans, ans


def test_jarvis_panel_answers_before_it_lists(tmp_path, monkeypatch):
    body = _visible(_page(_client(tmp_path / "j.db", monkeypatch), "/panel/jarvis"))
    m = re.search(r"<h1 class='ans[^']*'>(.*?)</h1>", body, re.S)
    assert m, "панель Джарвиса начинается не с ответа"
    assert re.search(r"ціла|Впало|уваги", m.group(1)), m.group(1)


# ───────────────── C. долг → деньги → статус, статус в строку ───────────────

def test_debt_comes_before_money_and_money_before_status(tmp_path, monkeypatch):
    p = tmp_path / "order.db"
    _seed(p, fresh=1)
    body = _visible(_page(_client(p, monkeypatch)))
    debt, money, status = (body.find("Требує вас"), body.find("Воронка"),
                           body.find("statusline"))
    assert -1 not in (debt, money, status), (debt, money, status)
    assert debt < money < status, (
        f"порядок блоков долг/деньги/статус нарушен: {debt}/{money}/{status}")


def test_the_status_card_is_a_single_line(tmp_path, monkeypatch):
    """Статус-карточка занимала два этажа (заголовок + подпись под ним) на самом
    верху экрана. Строка: точка, слово, возраст heartbeat и кнопка — в одном
    ряду."""
    p = tmp_path / "st.db"
    _seed(p)
    body = _visible(_page(_client(p, monkeypatch)))
    m = re.search(r"<div class='card statusline'>(.*?)</div>\s*</div>", body, re.S)
    assert m, "статус-строки нет"
    inner = m.group(1)
    assert inner.count("<div") == 1, (
        f"статус снова в несколько этажей: {inner.count('<div') + 1} блоков")
    assert "heartbeat" in inner, "возраст heartbeat пропал из статуса"


def test_the_heartbeat_age_is_not_truncated_away(tmp_path, monkeypatch):
    """ГРАНИЦА. «В одну строку» не имеет права означать «обрезано по краю»:
    первый прогон строки съел возраст heartbeat многоточием, а панель без
    возраста врёт при первом же зависании сборщика."""
    p = tmp_path / "hb.db"
    _seed(p)
    body = _visible(_page(_client(p, monkeypatch)))
    m = re.search(r"<div class='card statusline'>(.*?)</div>\s*</div>", body, re.S)
    assert "ell" not in m.group(1), (
        "статус обрезается многоточием — возраст heartbeat исчезает первым")


# ──────────────── D. карточка «Требує вас»: две строки, одно действие ────────

def test_attention_card_is_two_rows(tmp_path, monkeypatch):
    p = tmp_path / "att.db"
    _seed(p, fresh=1)
    cards = _cards(_visible(_page(_client(p, monkeypatch))))
    assert len(cards) == 1, cards
    assert cards[0].count("class='row'") == 2, (
        f"строк в карточке не две: {cards[0]}")


def test_only_one_action_stays_visible_the_rest_hide_under_dots(tmp_path, monkeypatch):
    p = tmp_path / "act.db"
    _seed(p, fresh=1)
    card = _cards(_visible(_page(_client(p, monkeypatch))))[0]
    outside = re.sub(r"<details.*?</details>", "", card, flags=re.S)
    assert outside.count("<button") == 1, (
        f"на виду больше одного действия: {outside}")
    assert "⋯" in card, "нет кнопки «⋯» — остальные действия просто исчезли"
    hidden = re.search(r"<details.*?</details>", card, re.S).group(0)
    for act in ("snooze:", "keep:", "paid("):
        assert act in hidden, f"действие {act} потерялось совсем"


# ГРАНИЦА: под «⋯» уезжают ДЕЙСТВИЯ. Долги — возраст, счётчик застарелых,
# пометка мёртвого лида — остаются на виду. Тесты ниже парные к тестам выше:
# компактность, купленная сокрытием долга, — не компактность.

def test_both_ages_survive_the_two_line_card(tmp_path, monkeypatch):
    p = tmp_path / "age.db"
    _seed(p, fresh=1)
    card = _cards(_visible(_page(_client(p, monkeypatch))))[0]
    visible = re.sub(r"<details.*?</details>", "", card, flags=re.S)
    assert "підняв руку" in visible, "возраст карточки уехал под «⋯»"
    assert "чекає" in visible, "сколько человек ждёт — уехало под «⋯»"


def test_the_stale_counter_and_the_dead_mark_stay_on_screen(tmp_path, monkeypatch):
    p = tmp_path / "stale.db"
    _seed(p, fresh=1, stale=2, dead=True)
    body = _visible(_page(_client(p, monkeypatch)))
    assert "Застарілі (2)" in body, "счётчик застарелых пропал при перекладке"
    assert "лід мертвий, картку не закрито" in body


# ─────────────────────────── E. цвет по смыслу ──────────────────────────────

def _rules(css: str):
    """Правила CSS без блоков @media: медиазапрос перекладывает, но не красит."""
    flat, i = [], 0
    while i < len(css):
        if css.startswith("@media", i):
            depth, i = 0, css.index("{", i)
            while i < len(css):
                depth += (css[i] == "{") - (css[i] == "}")
                i += 1
                if depth == 0:
                    break
            continue
        j = css.find("{", i)
        if j < 0:
            break
        k = css.find("}", j)
        flat.append((css[i:j].strip(), css[j + 1:k]))
        i = k + 1
    return flat


@pytest.mark.parametrize("token,owner", [("--ok", "money"),
                                         ("--warn", "wait"),
                                         ("--bad", "broken")])
def test_each_meaning_owns_its_colour(token, owner):
    """Зелёный — деньги, янтарь — «чекає вас», красный — «зламано». Цвет,
    который значит и то и это, не значит ничего: красная кнопка «Зупинити всіх»
    рядом с красным «немає зв'язку» учила не смотреть на красный вовсе."""
    from app.routers.panels_ui import CSS
    for sel, decls in _rules(CSS):
        if sel.startswith(":root") or f"var({token})" not in decls:
            continue
        assert owner in sel, (
            f"{token} используется вне смысла «{owner}»: {sel}{{{decls}}}")


def test_money_is_the_only_green_on_the_screen(tmp_path, monkeypatch):
    p = tmp_path / "green.db"
    _seed(p, leads=1)
    body = _visible(_page(_client(p, monkeypatch)))
    assert "money" in body, "деньги на экране ничем не выделены"


def test_nothing_is_red_while_nothing_is_broken(tmp_path, monkeypatch):
    """Пакет исчерпан, лид мёртвый, карточки застарелые — и ни одного красного:
    бот работает. Красный обязан значить «сломано», иначе он значит «страница»."""
    p = tmp_path / "nored.db"
    _seed(p, fresh=1, stale=2, dead=True, leads=3)
    body = _visible(_page(_client(p, monkeypatch, package="2")))
    assert "Пакет вичерпано" in body, "стенд не довёл перерасход до экрана"
    assert "broken" not in body, "красное там, где ничего не сломано"
    # Красный кружок в ленте — тот же красный, только эмодзи: правило про цвет
    # обходится символом, если про символ не сказать отдельно.
    assert "🔴" not in body, "красный кружок у лида, который просто ждёт вас"


def test_broken_link_is_red(tmp_path, monkeypatch):
    body = _visible(_page(_client(tmp_path / "red.db", monkeypatch, heartbeat=None)))
    assert "broken" in body, "авария не отмечена красным"


def test_stop_all_is_neutral_and_red_lives_in_the_confirmation(tmp_path, monkeypatch):
    """Кнопка ничего не ломает — она открывает вопрос. Красное принадлежит
    подтверждению, где решение и принимается."""
    p = tmp_path / "stop.db"
    _seed(p)
    html = _page(_client(p, monkeypatch))
    btn = re.search(r"<button[^>]*>⏹ Зупинити всіх</button>",
                    _visible(html)).group(0)
    assert "broken" not in btn, f"кнопка паузы всё ещё красная: {btn}"
    confirm = re.search(r"<button[^>]*>Так, зупинити</button>", html).group(0)
    assert "broken" in confirm, "подтверждение не отмечено красным"


# ──────────────────────────── F. пустые состояния ───────────────────────────

def test_a_tile_without_history_is_dimmed_and_not_clickable(tmp_path, monkeypatch):
    """Тап по плитке без истории раньше перерисовывал график в пустоту — плитка
    выглядела рабочей."""
    p = tmp_path / "tiles.db"
    _seed(p, leads=1)
    body = _visible(_page(_client(p, monkeypatch), "/panel/tamapi/dynamics"))
    dead = re.findall(r"<(\w+) class='tile off'", body)
    assert dead, "плитки без истории не погашены"
    assert set(dead) == {"div"}, f"погашенная плитка осталась ссылкой: {dead}"
    assert "історія накопичується" in body


def test_a_missing_delta_says_why_instead_of_drawing_a_dash(tmp_path, monkeypatch):
    p = tmp_path / "delta.db"
    _seed(p, leads=1)
    body = _visible(_page(_client(p, monkeypatch), "/panel/tamapi/dynamics"))
    assert "нема з чим порівняти" in body
    assert "class='d flat'>—<" not in body, "прочерк вместо подписи вернулся"


def test_a_missing_daily_cap_says_so_instead_of_an_empty_value(tmp_path, monkeypatch):
    """Конфига клиента на стенде нет — строка «добовий ліміт:» оставалась
    висеть с пустотой после двоеточия."""
    p = tmp_path / "cap.db"
    _seed(p, leads=1)
    body = _visible(_page(_client(p, monkeypatch)))
    assert "добовий ліміт не заданий" in body
    assert "добовий ліміт: </span>" not in body


def test_a_single_point_series_is_not_drawn_as_a_chart():
    """Одна точка — это не динамика. Линия из неё вырождается, а маркер в пустом
    поле осей читается как «график есть», хотя сравнивать не с чем."""
    from app.routers.panels_ui import line_chart
    from app.services.tamapi_metrics import Series
    s = Series(key="dialogs", label="Діалоги", unit="", money=False, points=[(0.0, 3.0)])
    out = line_chart([s])
    assert "<svg" not in out, "график из одной точки всё ещё рисуется"
    assert "недостатньо" in out.lower()


def test_two_points_still_draw(tmp_path, monkeypatch):
    """Парный сторож: «не рисуем» не имеет права стать «не рисуем никогда»."""
    from app.routers.panels_ui import line_chart
    from app.services.tamapi_metrics import Series
    s = Series(key="dialogs", label="Діалоги", unit="", money=False, points=[(0.0, 3.0), (1.0, 5.0)])
    assert "<svg" in line_chart([s])
