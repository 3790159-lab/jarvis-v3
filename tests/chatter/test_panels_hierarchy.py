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

def _make_client_dir(root, *, slug: str = "volska", daily_cap: int | None = None):
    """Минимальный клиент на диске. Нужен там, где проверяется, ЧТО экран берёт
    из конфига: иначе тест читает конфиг живого клиента и его выводы зависят от
    того, что сегодня лежит в `chatter/clients` (см. тесты про добовий ліміт)."""
    d = root / "clients" / slug
    d.mkdir(parents=True)
    (d / "persona.md").write_text("Ольга.", encoding="utf-8")
    (d / "knowledge.md").write_text("SMM — 900 $.", encoding="utf-8")
    (d / "playbook.md").write_text("Етапи воронки.", encoding="utf-8")
    limits = "" if daily_cap is None else f"limits: {{daily_cap: {daily_cap}}}\n"
    (d / "settings.yaml").write_text(
        f'model: claude-haiku-4-5\nlanguage: uk\nowner_id: "owner"\n'
        f'persona_name: "Ольга"\n{limits}', encoding="utf-8")
    return d.parent


def _client(db_path, monkeypatch, *, heartbeat: str | None = "fresh",
            package: str = "500", clients_dir=None, slug: str | None = None):
    """heartbeat: 'fresh' — бот на связи, None — связи нет (единственная авария).

    clients_dir/slug — откуда экран берёт конфиг клиента. По умолчанию НЕ
    трогаем: подавляющее большинство сторожей здесь про вёрстку и конфиг им
    безразличен."""
    monkeypatch.setenv("JARVIS_PANELS_KEY", KEY)
    monkeypatch.setenv("TAMAPI_DB", str(db_path))
    monkeypatch.setenv("TAMAPI_PACKAGE", package)
    if clients_dir is not None:
        monkeypatch.setenv("CHATTER_CLIENTS_DIR", str(clients_dir))
    if slug is not None:
        monkeypatch.setenv("TAMAPI_SLUG", slug)
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


def test_the_empty_state_markup_keeps_the_scale(tmp_path, monkeypatch):
    """Пустые состояния — любимое место кегля «на глаз»: до части A подпись
    «історія накопичується» несла собственный `font-size:13px` инлайном.

    Отдельным стендом, потому что ветка эта видна только когда сравнивать не с
    чем: на стенде с историей рисуются нормальные дельты, и мутация «пустая
    дельта протащила свой кегль» проходила мимо сторожа целиком.
    """
    p = tmp_path / "empty-scale.db"
    _seed(p, leads=1)                  # прошлого периода нет — подписи вместо цифр
    body = _visible(_page(_client(p, monkeypatch), "/panel/tamapi/dynamics"))
    assert "нема з чим порівняти" in body, "стенд не довёл пустую дельту до экрана"
    assert "історія накопичується" in body, "стенд не довёл пустую плитку до экрана"
    extra = _font_sizes(body) - SCALE
    assert not extra, f"кегли мимо шкалы в пустых состояниях: {sorted(extra)}"


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
    """Ферма подменена (стенд `_jarvis` ниже): прежняя версия сторожа смотрела
    в ЖИВУЮ ферму, и её вердикт зависел от того, поднят ли сейчас раннер на
    машине разработчика. Заодно словарь ответов расширился до лестницы из
    шести уровней (заход 1)."""
    body = _visible(_page(_jarvis(tmp_path, monkeypatch), "/panel/jarvis"))
    m = re.search(r"<h1 class='ans[^']*'>(.*?)</h1>", body, re.S)
    assert m, "панель Джарвиса начинается не с ответа"
    assert re.search(r"цела|Упало|Лиды|Не вижу|Сторож|Некому|Ключ|Автоматика",
                     m.group(1)), m.group(1)


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
    # Поимённо, а не «слово broken где-нибудь на странице»: ответ сверху при
    # обрыве и так красный, и одной подстроки хватало, чтобы сторож пережил
    # обесцвечивание САМОЙ точки статуса — то есть охранял он ответ, не статус.
    assert "<h1 class='ans broken'>" in body, "ответ об аварии не красный"
    assert "class='dot broken'" in body, "точка статуса не красная при обрыве"


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


def test_an_unreadable_client_config_says_so_instead_of_an_empty_value(tmp_path, monkeypatch):
    """Строка «добовий ліміт:» оставалась висеть с пустотой после двоеточия.

    И заглушка обязана называть НАСТОЯЩУЮ причину. `daily_cap` имеет дефолт 500
    в лоадере, поэтому None означает ровно одно: конфиг клиента не прочитан —
    его нет или он битый. «Ліміт не заданий» читалось как спокойная штатная
    настройка, тогда как экран в этот момент не знает о клиенте ничего.

    ⚠️ Каталог клиентов задаём ЯВНО (пустой). Прежняя редакция полагалась на
    то, что конфига volska нет на стенде, — то есть кодировала СОСТОЯНИЕ диска,
    а не инвариант: на машине с живым клиентом тест краснел, в worktree без
    него — зеленел. Тот же класс, что 22 ложных падения без requisites.yaml."""
    p = tmp_path / "cap.db"
    _seed(p, leads=1)
    (tmp_path / "clients").mkdir()
    c = _client(p, monkeypatch, clients_dir=tmp_path / "clients", slug="volska")
    body = _visible(_page(c))
    assert "конфіг клієнта не прочитано" in body
    assert "не заданий" not in body, "спокойная формулировка вернулась"
    assert "добовий ліміт: </span>" not in body


def test_a_known_daily_cap_is_shown_with_its_number(tmp_path, monkeypatch):
    """Парный сторож: конфиг ЕСТЬ — на экране число из него, а не заглушка.
    Без этой половины тревога проходила бы и в случае, когда экран разучился
    читать конфиг вовсе."""
    p = tmp_path / "cap2.db"
    _seed(p, leads=1)
    clients = _make_client_dir(tmp_path, slug="volska", daily_cap=137)
    body = _visible(_page(_client(p, monkeypatch, clients_dir=clients, slug="volska")))
    assert "добовий ліміт: 137" in body
    assert "не прочитано" not in body


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


# ══════════════════ D. Панель Джарвиса: ответ по лестнице ═══════════════════
#
# Заход 1 (спека 2026-08-14-jarvis-panel-answer-view.md). Ферма подменяется
# целиком: сторожа обязаны судить о ВЁРСТКЕ и ПРИОРИТЕТЕ, а не о том, что
# сегодня запущено на машине разработчика. Прежний сторож
# `test_jarvis_panel_answers_before_it_lists` смотрел в живую ферму, и его
# вердикт зависел от того, поднят ли сейчас раннер.

def _row(key, state, detail="", **extra):
    from app.services.jarvis_farm import Row
    return Row(key, key, state, detail, extra)


def _jarvis(tmp_path, monkeypatch, *, procs=None, guards=None, external=None,
            tasks=None, keys=None, arcs=None, slow_fresh=True):
    """Клиент панели Джарвиса с ПОЛНОСТЬЮ подменённой фермой.

    slow_fresh=False — медленный кэш протух: задачи и ключи не прочитаны, и
    экран обязан сказать это словами, а не показать пустоту."""
    import app.services.jarvis_farm as F

    c = _client(tmp_path / "jarvis.db", monkeypatch)
    fast = {
        "collected_at": time.time(),
        "external": external or _row("ext", "bad", "НЕ настроен"),
        "processes": procs if procs is not None else [
            _row("backend", "ok", "PID 1"), _row("bot", "ok", "PID 2"),
            _row("chatter", "ok", "PID 3")],
        "guardians": guards if guards is not None else [
            _row("backend_guardian", "ok"), _row("bot_guardian", "ok"),
            _row("chatter_guardian", "ok"), _row("ops_watchdog", "ok")],
    }
    slow = None if not slow_fresh else {
        "collected_at": time.time(),
        "tasks": tasks if tasks is not None else [_row("JarvisBotGuardian", "ok", "Running")],
        "keys": keys if keys is not None else [
            {"name": "ANTHROPIC_API_KEY", "purpose": "brain", "expires": None,
             "auto": None, "note": "", "days_left": None, "state": "ok"}],
        "arcs": arcs if arcs is not None else [],
        "events": [],
    }
    monkeypatch.setattr(F, "snapshot_fast", lambda: fast)
    monkeypatch.setattr(F, "slow_cached", lambda: slow)
    return c


def _ans(body: str) -> str:
    m = re.search(r"<h1 class='ans[^']*'>(.*?)</h1>", body, re.S)
    assert m, "ответа сверху нет"
    return m.group(1)


def _tone(body: str) -> str:
    return re.search(r"<h1 class='ans ([^']*)'>", body).group(1)


def _second(body: str) -> str:
    """Вторая строка — сразу под ответом, до служебной подписи."""
    m = re.search(r"</h1>\s*<div class='sub second'>(.*?)</div>", body, re.S)
    assert m, "второй строки под ответом нет"
    return m.group(1)


def _state_block(body: str) -> str:
    """Свёрнутое состояние. Всё, что внутри, на первом экране не видно."""
    m = re.search(r"<details class='state'>(.*?)</details>", body, re.S)
    return m.group(1) if m else ""


def _first_screen(body: str) -> str:
    """То, что видно без разворота: страница минус свёрнутое состояние."""
    vis = _visible(body)
    return re.sub(r"<details class='state'>.*?</details>", "", vis, flags=re.S)


# ── лестница: каждый уровень перебивает нижние ──────────────────────────────

def test_a_blind_collector_outranks_everything(tmp_path, monkeypatch):
    """L1. psutil лёг — ферма собрана наполовину, и говорить о ней рано.
    Ровно та же идиома, что «немає зв'язку перебиває чергу» на клиентской."""
    c = _jarvis(tmp_path, monkeypatch,
                procs=[_row("procs", "warn", "psutil недоступен")],
                guards=[_row("chatter_guardian", "bad", "не работает")])
    body = _page(c, "/panel/jarvis")
    assert "Не вижу ферму" in _ans(body), _ans(body)
    assert _tone(body) == "broken"


def test_a_dead_runner_outranks_a_lying_guardian(tmp_path, monkeypatch):
    """L2 > L4. Упавший раннер chatter — это лиды без ответа, то есть деньги;
    расхождение сторожей ждёт своей очереди."""
    c = _jarvis(tmp_path, monkeypatch,
                procs=[_row("backend", "ok"), _row("bot", "ok"),
                       _row("chatter", "bad", "процесс не найден")],
                guards=[_row("chatter_guardian", "warn", "heartbeat протух")])
    assert "Лиды без ответа" in _ans(_page(c, "/panel/jarvis"))


def test_a_fallen_process_reads_differently_when_a_guardian_is_alive(tmp_path, monkeypatch):
    """L3, две половины одного уровня. 14.08 в 00:45 раннер упал и поднялся сам
    за 61 с — это НЕ то же событие, что падение без живого гардиана, и ответ
    обязан различать их тоном, а не только словом."""
    lifted = _jarvis(tmp_path, monkeypatch,
                     procs=[_row("bot", "bad", "процесс не найден")],
                     guards=[_row("bot_guardian", "ok", "PID 9")])
    b1 = _page(lifted, "/panel/jarvis")
    assert "поднимется" in _ans(b1), _ans(b1)
    assert _tone(b1) == "wait", "самоподнимающееся падение — не авария"

    orphan = _jarvis(tmp_path, monkeypatch,
                     procs=[_row("bot", "bad", "процесс не найден")],
                     guards=[_row("bot_guardian", "bad", "не работает")])
    b2 = _page(orphan, "/panel/jarvis")
    assert "сам не поднимется" in _ans(b2), _ans(b2)
    assert _tone(b2) == "broken"


def test_the_second_line_names_the_guardian_and_the_eta(tmp_path, monkeypatch):
    c = _jarvis(tmp_path, monkeypatch,
                procs=[_row("chatter", "bad", "процесс не найден")],
                guards=[_row("chatter_guardian", "ok", "PID 7")])
    s = _second(_page(c, "/panel/jarvis"))
    assert "chatter_guardian" in s and "90" in s, s


def test_the_second_line_says_when_nobody_will_lift_it(tmp_path, monkeypatch):
    c = _jarvis(tmp_path, monkeypatch,
                procs=[_row("chatter", "bad", "процесс не найден")],
                guards=[_row("chatter_guardian", "bad", "не работает")])
    s = _second(_page(c, "/panel/jarvis"))
    assert "не поднимется" in s and "chatter_guardian" in s, s


def test_an_always_true_condition_never_becomes_the_answer(tmp_path, monkeypatch):
    """Внешний сторож не настроен со дня рождения панели. Ответ, который не
    меняется, перестаёт быть ответом — это ровно тот вечно-красный, из-за
    которого красный теряет смысл. Строка остаётся, ответ — нет."""
    c = _jarvis(tmp_path, monkeypatch,
                external=_row("ext", "bad", "НЕ настроен — панель не видит смерти машины"))
    body = _page(c, "/panel/jarvis")
    assert "Ферма цела" in _ans(body), _ans(body)
    assert "НЕ настроен" in body, "строка о слепоте панели пропала совсем"


def test_a_key_reaches_the_answer_only_under_seven_days(tmp_path, monkeypatch):
    """Порог решением владельца: 7 дней — в ОТВЕТ, 30 — в аномалии."""
    def key(days):
        return [{"name": "Instagram", "purpose": "IG", "expires": "2026-09-09",
                 "auto": None, "note": "", "days_left": days, "state": "warn"}]

    soon = _page(_jarvis(tmp_path, monkeypatch, keys=key(6)), "/panel/jarvis")
    assert "Instagram" in _ans(soon), _ans(soon)

    later = _page(_jarvis(tmp_path, monkeypatch, keys=key(20)), "/panel/jarvis")
    assert "Ферма цела" in _ans(later), _ans(later)
    assert "Instagram" in _first_screen(later), "ключ на 20 дней пропал и из аномалий"


def test_a_stale_slow_cache_skips_the_level_and_says_so(tmp_path, monkeypatch):
    """Медленное не прочитано — уровень пропускается, но МОЛЧАТЬ нельзя:
    иначе «Ферма ціла» тихо означает «про задачи и ключи не знаю»."""
    body = _page(_jarvis(tmp_path, monkeypatch, slow_fresh=False), "/panel/jarvis")
    assert "Ферма цела" in _ans(body)
    assert "ещё не прочитаны" in _first_screen(body), "экран молчит о том, чего не знает"


def test_the_calm_answer_lists_what_was_checked(tmp_path, monkeypatch):
    """Заход 1: перечень проверенного. Дата последнего падения честнее, но без
    журнала мы её не знаем, и печатать её значит соврать (заход 2)."""
    s = _second(_page(_jarvis(tmp_path, monkeypatch), "/panel/jarvis"))
    assert "3 процесса" in s and "4 гардиана" in s, s


# ── аномалия против состояния ───────────────────────────────────────────────

def test_healthy_rows_stay_off_the_first_screen(tmp_path, monkeypatch):
    """Двадцать зелёных строк выделяют ровно ничего. «heartbeat 12 с тому»
    меняется на каждый запрос, а смысл не меняется ни разу."""
    body = _page(_jarvis(tmp_path, monkeypatch,
                         guards=[_row("bot_guardian", "ok", "PID 9 · heartbeat 12 с назад")]),
                 "/panel/jarvis")
    assert "heartbeat 12 с назад" in _state_block(body), "состояние не свёрнуто"
    assert "heartbeat 12 с назад" not in _first_screen(body)


def test_an_anomaly_is_never_hidden_in_the_state_block(tmp_path, monkeypatch):
    body = _page(_jarvis(tmp_path, monkeypatch,
                         procs=[_row("bot", "bad", "процесс не найден")],
                         guards=[_row("bot_guardian", "bad", "не работает")]),
                 "/panel/jarvis")
    assert "процесс не найден" in _first_screen(body)
    assert "процесс не найден" not in _state_block(body)


def test_a_retired_task_is_not_an_anomaly(tmp_path, monkeypatch):
    """Снайпер отставлен НАМЕРЕННО. Панель, красящая это жёлтым, ежедневно
    требует чинить нечинимое, и её перестают читать."""
    body = _page(_jarvis(tmp_path, monkeypatch,
                         tasks=[_row("JarvisSniperDetached", "off", "RunPod-пивот")]),
                 "/panel/jarvis")
    assert "JarvisSniperDetached" in _state_block(body)
    assert "Ферма цела" in _ans(body)


def test_a_dirty_worktree_is_an_anomaly_and_its_age_is_not(tmp_path, monkeypatch):
    """Грязное дерево слепит гейт (1669 циклов сторожа). Возраст ветки растёт
    сам собой — это не событие."""
    body = _page(_jarvis(tmp_path, monkeypatch, arcs=[
        {"branch": "arc/dirty", "path": "C:/wt/dirty", "dirty": True,
         "merged": False, "age_days": 1.0},
        {"branch": "arc/old", "path": "C:/wt/old", "dirty": False,
         "merged": False, "age_days": 99.0}]), "/panel/jarvis")
    assert "arc/dirty" in _first_screen(body)
    assert "arc/old" not in _first_screen(body)
    assert "arc/old" in _state_block(body)


# ── быстрое и медленное ─────────────────────────────────────────────────────

def test_the_panel_answers_while_git_and_powershell_hang(tmp_path, monkeypatch):
    """Медленные источники изолированы: сегодня исключение в `arcs()` или
    зависший PowerShell уронили бы страницу целиком вместе с ответом."""
    import app.services.jarvis_farm as F

    def boom(*a, **k):
        raise RuntimeError("git висит")

    c = _client(tmp_path / "slow.db", monkeypatch)
    monkeypatch.setattr(F, "arcs", boom)
    monkeypatch.setattr(F, "scheduled_tasks", boom)
    monkeypatch.setattr(F, "events", boom)
    monkeypatch.setattr(F, "_slow_cache", None, raising=False)
    body = _page(c, "/panel/jarvis")
    assert re.search(r"цела|Упало|Лиды|Не вижу|Сторож|Ключ|Некому", _ans(body)), _ans(body)


def test_the_slow_note_replaces_the_server_one_instead_of_standing_next_to_it(
        tmp_path, monkeypatch):
    """Живой скриншот 14.08: «ще не зчитанізадачі, арки, ключі: 0 с тому».

    Строка склеена и сказана дважды противоположным образом, потому что
    серверная подпись и подпись после догрузки жили в РАЗНЫХ узлах: JS писал в
    пустой соседний span, а «ще не зчитані» оставалось на месте. Серверный HTML
    при этом выглядел безупречно — беда появлялась только в браузере, поэтому
    сторож сверяет АДРЕС узла: куда JS кладёт `j.note` и где стоит серверная
    подпись, обязан быть один и тот же элемент."""
    import app.routers.jarvis_panel as jp

    body = _page(_jarvis(tmp_path, monkeypatch, slow_fresh=False), "/panel/jarvis")
    html, js = body.split("<script>", 1)
    m = re.search(r"getElementById\('(\w+)'\)\.textContent\s*=\s*j\.note", js)
    assert m, "не видно, в какой узел JS кладёт подпись возраста"
    holder = re.search(rf"id='{m.group(1)}'[^>]*>\s*{re.escape(jp._SLOW_PREFIX)}", html)
    assert holder, (f"JS пишет в #{m.group(1)}, а серверная подпись стоит в другом "
                    f"узле — на экране они встанут рядом, а не заменят друг друга")
    assert html.count(jp._SLOW_PREFIX) == 1, "подпись контекста напечатана дважды"


def test_the_two_ages_are_printed_separately(tmp_path, monkeypatch):
    """Один общий возраст соврёт ровно тогда, когда встанет медленный сборщик."""
    body = _page(_jarvis(tmp_path, monkeypatch), "/panel/jarvis")
    assert "ферма собрана" in body and "задачи, арки, ключи" in body, body[:400]


def test_the_slow_route_is_owner_guarded(tmp_path, monkeypatch):
    c = _jarvis(tmp_path, monkeypatch)
    assert c.get("/panel/jarvis/slow").status_code in (401, 403)
    assert c.get("/panel/jarvis/slow", headers={"X-Panels-Key": KEY}).status_code == 200


# ── место под заход 2 и раскладка ───────────────────────────────────────────

def test_no_empty_slot_pretends_there_were_no_events(tmp_path, monkeypatch):
    """Слот «поки тебе не було» — заход 2. Пустая рамка читается как «ничего не
    случилось», а мы этого не знаем."""
    body = _page(_jarvis(tmp_path, monkeypatch), "/panel/jarvis")
    assert "поки тебе не було" not in body.lower()


def test_the_second_column_appears_by_content_not_by_device():
    """Порог выводится из КОНТЕНТА: минимальная комфортная колонка ×2 + gap.
    Медиазапрос под конкретную модель телефона врёт на любой другой.

    `min(340px,100%)` — не украшение: minmax(340px,·) не сжимается ниже своего
    минимума, и на внешнем экране Fold (344 px) страница уезжала вбок на 32 px
    (поймано `panels_mobile_check --width 344`)."""
    from app.routers.panels_ui import CSS
    m = re.search(r"\.two\{[^}]*minmax\(min\((\d+)px,\s*100%\)", CSS, re.S)
    assert m, "второй столбец не выводится из ширины колонки (или не умеет сжиматься)"
    assert 320 <= int(m.group(1)) <= 360, m.group(1)


def test_the_state_unfolds_exactly_where_the_second_column_appears():
    """Порог один по смыслу и записан в ДВУХ местах: `.two` в CSS и matchMedia
    в _JS панели. Разъедутся — состояние развернётся там, где второй колонки ещё
    нет (или наоборот), и ни один тест этого не заметит: код не падает, экран
    просто ведёт себя не так, как объяснено в комментарии рядом.

    Считаем из ширин, а не сверяем два числа: 707 px на развёрнутом Fold ловятся
    только если обе стороны выведены из одного и того же минимума колонки."""
    from app.routers.panels_ui import CSS
    import app.routers.jarvis_panel as jp

    col = int(re.search(r"\.two\{[^}]*minmax\(min\((\d+)px", CSS, re.S).group(1))
    gap = int(re.search(r"\.two\{[^}]*gap:(\d+)px", CSS, re.S).group(1))
    pad = int(re.search(r"body\{[^}]*padding:(\d+)px", CSS, re.S).group(1))
    js = int(re.search(r"min-width:(\d+)px", jp._JS).group(1))
    want = col * 2 + gap + 2 * pad
    assert js == want, f"колонка {col}px → порог {want}px, а в JS стоит {js}px"
    assert want <= 707, (
        f"на развёрнутом Fold (707 px) второго столбца снова нет: порог {want}")


def test_the_viewport_width_is_printed_for_the_next_layout_pass(tmp_path, monkeypatch):
    body = _page(_jarvis(tmp_path, monkeypatch), "/panel/jarvis")
    assert "innerWidth" in body and "ширина экрана" in body


def test_a_bulk_anomaly_is_grouped_with_a_counter(tmp_path, monkeypatch):
    """Sentry-правило: не 1000 ошибок, а 5 проблем со счётчиками.

    Живой скриншот 14.08: 11 грязных worktree из 17 заняли ВЕСЬ первый экран и
    вытеснили с него ответ. Грязное дерево остаётся аномалией (оно слепит
    мерж-гейт), но одиннадцать одинаковых аномалий — это ОДНА проблема со
    счётчиком, а не одиннадцать проблем."""
    arcs = [{"branch": f"arc/d{i}", "path": f"C:/wt/d{i}", "dirty": True,
             "merged": False, "age_days": 3.0} for i in range(11)]
    body = _page(_jarvis(tmp_path, monkeypatch, arcs=arcs), "/panel/jarvis")
    first = _first_screen(body)
    m = re.search(r"<details class='grp'><summary>(.*?)</summary>", first, re.S)
    assert m, "одиннадцать одинаковых аномалий выложены списком, а не свёрнуты"
    assert "11" in m.group(1), m.group(1)
    # Свёрнуто — но не спрятано: имена веток остаются в разметке под сводкой.
    assert "arc/d7" in first


def test_a_couple_of_anomalies_are_not_hidden_behind_a_counter(tmp_path, monkeypatch):
    """Парный сторож. Свёртка по счётчику не имеет права проглатывать две
    строки: тогда на первом экране не остаётся НИЧЕГО, кроме числа, и панель
    снова требует лишнего тапа там, где всё помещалось."""
    arcs = [{"branch": "arc/one", "path": "C:/wt/one", "dirty": True,
             "merged": False, "age_days": 3.0},
            {"branch": "arc/two", "path": "C:/wt/two", "dirty": True,
             "merged": False, "age_days": 4.0}]
    first = _first_screen(_page(_jarvis(tmp_path, monkeypatch, arcs=arcs), "/panel/jarvis"))
    assert "<details class='grp'>" not in first, "две аномалии свёрнуты без нужды"
    assert "arc/one" in first and "arc/two" in first
