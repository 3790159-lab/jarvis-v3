"""Журнал событий на панели: чтение, инвариант живости, схлопывание, вид.

Спека: docs/superpowers/specs/2026-08-14-jarvis-panel-event-journal.md, §4.

Панель — ЧИТАТЕЛЬ. Писателя сторожит `tests/test_panel_event_journal.py`, и
пересечение между файлами намеренное: формат журнала продублирован в
`scripts/ops_watchdog.py` и в `app/services/jarvis_farm.py` (сторож stdlib-only
и импортировать из `app/` не имеет права). Разъедутся — журнал станет пустым
МОЛЧА, поэтому обе стороны накрыты порознь.

Невидимые символы (U+2028, U+FEFF) записаны ЭКРАНИРОВАННО: сырой литерал не
переживает ни копирование, ни ревью — его не видно, и тест молча становится
тестом про обычный пробел.
"""
from __future__ import annotations

import json
import os
import time

from app.services import jarvis_farm as F

DAY = 86400.0
LINE_SEP = "\u2028"      # `splitlines()` считает его концом строки, `split("\n")` — нет
BOM = "\ufeff"


def _journal(tmp_path, records, *, beat_age: float | None = 5.0, raw: str | None = None):
    state = tmp_path / "state"
    state.mkdir(parents=True, exist_ok=True)
    j = state / "panel_events.jsonl"
    body = raw if raw is not None else "".join(
        json.dumps(r, ensure_ascii=False) + "\n" for r in records)
    with open(j, "w", encoding="utf-8", newline="") as fh:
        fh.write(body)
    beat = state / "panel_events.heartbeat"
    if beat_age is not None:
        beat.write_text("x", encoding="ascii")
        stamp = time.time() - beat_age
        os.utime(beat, (stamp, stamp))
    return j


def _rec(ts, check="chatter_runner", kind="down", detail="процесс не найден"):
    return {"ts": ts, "check": check, "kind": kind, "reason": "r", "detail": detail}


# ── инвариант живости писателя (§4.4) ──────────────────────────────────────


def test_a_fresh_marker_with_an_empty_journal_means_real_silence(tmp_path, monkeypatch):
    """Утверждение, а не пустота: переходов действительно не было."""
    monkeypatch.setattr(F, "ROOT", tmp_path)
    _journal(tmp_path, [], beat_age=5.0)
    records, note = F.journal()
    assert records == []
    assert note == "", note


def test_a_stale_marker_means_the_writer_is_silent_not_the_farm(tmp_path, monkeypatch):
    """Молчание сломанного писателя не имеет права читаться как тишина здоровой
    фермы — ровно тот класс, что «ещё не прочитаны» у медленного кэша."""
    monkeypatch.setattr(F, "ROOT", tmp_path)
    _journal(tmp_path, [_rec(time.time() - 60)], beat_age=10_000.0)
    records, note = F.journal()
    assert note, "панель молчит о молчащем писателе"
    assert "не пишет" in note or "молчит" in note, note
    assert records == [], "показан список, которому нельзя верить"


def test_a_missing_marker_is_treated_as_stale(tmp_path, monkeypatch):
    monkeypatch.setattr(F, "ROOT", tmp_path)
    _journal(tmp_path, [], beat_age=None)
    _records, note = F.journal()
    assert note, "отсутствующий маркер принят за свежий"


def test_a_marker_from_the_future_is_not_read_as_fresh(tmp_path, monkeypatch):
    """Часы уезжают вперёд (степ NTP — тот же сдвиг, который `detect_reboot`
    уже сторожит отдельным допуском). Возраст становится ОТРИЦАТЕЛЬНЫМ, и
    наивное `age > порог` объявляет мёртвого писателя живым молча и НАВСЕГДА,
    пока часы не догонят маркер."""
    monkeypatch.setattr(F, "ROOT", tmp_path)
    _journal(tmp_path, [], beat_age=-10_000.0)
    _records, note = F.journal()
    assert note, "маркер из будущего принят за свежий"


# ── чтение файла, который дописывает другой процесс (§6, ловушка 4) ─────────


def test_a_corrupt_last_line_does_not_break_the_read(tmp_path, monkeypatch):
    """Гонка чтения и дозаписи: последняя строка может быть без `\\n`."""
    monkeypatch.setattr(F, "ROOT", tmp_path)
    j = _journal(tmp_path, [_rec(time.time() - 60)], beat_age=5.0)
    with open(j, "a", encoding="utf-8", newline="") as f:
        f.write('{"ts": 1.0, "kind": "do')
    records, note = F.journal()
    assert len(records) == 1
    assert note == ""


def test_a_line_separator_inside_detail_does_not_split_the_record(tmp_path, monkeypatch):
    """`json.dumps(ensure_ascii=False)` пишет U+2028/U+2029/U+0085 СЫРЫМИ, а
    `splitlines()` считает их концом строки: одна запись читалась бы как две
    битых, и событие терялось бы молча. Писатель уже делит ровно по `"\\n"` —
    читатель обязан быть не слабее, иначе граница §2.2 держится на одной
    стороне."""
    monkeypatch.setattr(F, "ROOT", tmp_path)
    now = time.time()
    _journal(tmp_path, [_rec(now - 60, detail="строка" + LINE_SEP + "вторая")],
             beat_age=5.0)
    records, note = F.journal(now=now)
    assert note == ""
    assert len(records) == 1, records
    assert LINE_SEP in records[0]["detail"], records


def test_a_bom_does_not_eat_the_oldest_record(tmp_path, monkeypatch):
    """Файл живёт 30 суток и переживает открытие человеком: Блокнот и
    PowerShell `>` ставят U+FEFF в начало. Ценой была бы ПЕРВАЯ запись файла —
    самая старая, то есть ровно та, ради которой в журнал и заглядывают."""
    monkeypatch.setattr(F, "ROOT", tmp_path)
    now = time.time()
    _journal(tmp_path, [], beat_age=5.0,
             raw=BOM + json.dumps(_rec(now - 60), ensure_ascii=False) + "\n")
    records, _note = F.journal(now=now)
    assert len(records) == 1, records


def test_junk_that_is_not_an_object_is_not_a_record(tmp_path, monkeypatch):
    """`42` — законный json и НЕ запись. Без проверки `.get` на числе бросит
    AttributeError и уронит весь первый экран фермы."""
    monkeypatch.setattr(F, "ROOT", tmp_path)
    now = time.time()
    _journal(tmp_path, [], beat_age=5.0,
             raw='42\n["тоже не запись"]\n'
                 + json.dumps(_rec(now - 60), ensure_ascii=False) + "\n")
    records, _note = F.journal(now=now)
    assert len(records) == 1, records


# ── время записи: щит, а не голый float() (§6, ловушка 1) ──────────────────


def test_unreadable_time_does_not_take_down_the_whole_page(tmp_path, monkeypatch):
    """У писателя падение прячется за живым heartbeat; здесь цена выше — ляжет
    ВСЯ страница фермы, потому что журнал читается в БЫСТРОЙ части снапшота.

    Названы порознь все формы, которые голый `float(rec["ts"] or 0.0)` не
    переживает: строка (`ValueError`), огромное целое (`OverflowError`),
    `±inf` (проходят молча и ломают и окно, и сортировку), `True` (становится
    временем 1.0) и отсутствующее поле.
    """
    monkeypatch.setattr(F, "ROOT", tmp_path)
    now = time.time()
    bad = [
        json.dumps({"ts": "вчера", "check": "x", "kind": "down", "reason": "r",
                    "detail": "d"}, ensure_ascii=False),
        '{"ts": 1' + "0" * 400 + ', "check": "x", "kind": "down", "reason": "r", "detail": "d"}',
        '{"ts": 1e400, "check": "x", "kind": "down", "reason": "r", "detail": "d"}',
        '{"ts": -1e400, "check": "x", "kind": "down", "reason": "r", "detail": "d"}',
        json.dumps({"ts": True, "check": "x", "kind": "down", "reason": "r",
                    "detail": "d"}, ensure_ascii=False),
        json.dumps({"check": "x", "kind": "down", "reason": "r", "detail": "d"},
                   ensure_ascii=False),
    ]
    raw = "\n".join(bad + [json.dumps(_rec(now - 60, detail="единственная настоящая"),
                                      ensure_ascii=False)]) + "\n"
    _journal(tmp_path, [], beat_age=5.0, raw=raw)
    records, note = F.journal(now=now)
    assert note == "", note
    assert [r["detail"] for r in records] == ["единственная настоящая"], records


# ── окно экрана (§4.1) ─────────────────────────────────────────────────────


def test_records_older_than_the_window_are_not_shown(tmp_path, monkeypatch):
    """72 часа — это окно ЭКРАНА, а не хранения: в файле записи живут 30 суток."""
    monkeypatch.setattr(F, "ROOT", tmp_path)
    now = time.time()
    _journal(tmp_path, [_rec(now - 5 * DAY), _rec(now - 1 * DAY)], beat_age=5.0)
    records, _note = F.journal(now=now)
    assert len(records) == 1


def test_records_are_returned_oldest_first_whatever_the_file_order(tmp_path, monkeypatch):
    """Файл дозаписывается конкурентно, гарантии порядка нет ниоткуда — тот же
    урок, который стоил потолку обрезки правки «резать по времени, а не по
    позиции в файле»."""
    monkeypatch.setattr(F, "ROOT", tmp_path)
    now = time.time()
    _journal(tmp_path,
             [_rec(now - 60, detail="вторая"), _rec(now - 600, detail="первая")],
             beat_age=5.0)
    records, _note = F.journal(now=now)
    assert [r["detail"] for r in records] == ["первая", "вторая"]


def test_a_missing_journal_with_a_fresh_marker_is_real_silence(tmp_path, monkeypatch):
    """Файла нет, пока не случилось первого перехода. Это не поломка, и на
    живой ферме это НОРМА: сегодня журнала на диске ещё нет вовсе."""
    monkeypatch.setattr(F, "ROOT", tmp_path)
    (tmp_path / "state").mkdir(parents=True, exist_ok=True)
    (tmp_path / "state" / "panel_events.heartbeat").write_text("x", encoding="ascii")
    records, note = F.journal()
    assert records == [] and note == ""


# ── схлопывание «подавлено → исход» (§4.6) ─────────────────────────────────


def test_suppressed_then_down_is_one_line_with_the_confirmation_delay():
    now = 1_000_000.0
    recs = [_rec(now - 600, kind="suppressed"), _rec(now - 300, kind="down")]
    rows = F.collapse_suppressed(recs)
    assert len(rows) == 1, rows
    assert rows[0]["kind"] == "suppressed"
    assert rows[0]["outcome"] == "down"
    assert rows[0]["ts"] == now - 600, "время взято от подтверждения, а не от падения"
    assert rows[0]["after_s"] == 300.0


def test_suppressed_then_recovered_says_it_rose_by_itself():
    now = 1_000_000.0
    recs = [_rec(now - 600, kind="suppressed"), _rec(now - 300, kind="recovered")]
    rows = F.collapse_suppressed(recs)
    assert len(rows) == 1 and rows[0]["outcome"] == "recovered"


def test_a_suppressed_fall_with_no_outcome_yet_says_so():
    rows = F.collapse_suppressed([_rec(1.0, kind="suppressed")])
    assert len(rows) == 1 and rows[0]["outcome"] is None


def test_other_checks_are_not_swallowed_by_the_collapse():
    """Парный сторож: схлопывание по ОДНОЙ пробе. Схлопнуть соседнюю значит
    спрятать чужой инцидент."""
    now = 1_000_000.0
    recs = [_rec(now - 600, check="backend", kind="suppressed"),
            _rec(now - 500, check="bot_heartbeat", kind="down"),
            _rec(now - 300, check="backend", kind="down")]
    rows = F.collapse_suppressed(recs)
    assert len(rows) == 2, rows
    assert {r["check"] for r in rows} == {"backend", "bot_heartbeat"}


def test_only_the_first_outcome_is_taken_not_any_later_one():
    """Между подавлением и исходом может стоять СВОЙ же переход другого вида.
    Схватить исход через него значит склеить два разных инцидента в один и
    соврать о задержке подтверждения."""
    now = 1_000_000.0
    recs = [_rec(now - 900, kind="suppressed"),
            _rec(now - 600, kind="suppressed"),
            _rec(now - 300, kind="down")]
    rows = F.collapse_suppressed(recs)
    assert [r["kind"] for r in rows] == ["suppressed", "suppressed"], rows
    assert rows[0]["outcome"] is None, "исход приписан ЧУЖОМУ подавлению"
    assert rows[1]["outcome"] == "down"


def test_the_incoming_records_are_not_mutated():
    """Список приезжает из снапшота и переживает вызов: панель рисует его же.
    Правка на месте оставила бы в снапшоте запись с полем, которого нет в
    формате §2.2, — и следующий читатель нашёл бы шестое поле."""
    now = 1_000_000.0
    recs = [_rec(now - 600, kind="suppressed"), _rec(now - 300, kind="down")]
    before = [dict(r) for r in recs]
    F.collapse_suppressed(recs)
    assert recs == before, recs


def test_a_record_with_unreadable_time_is_never_taken_as_an_outcome():
    """`after_s` считается ВЫЧИТАНИЕМ, и голый `float(...)` здесь роняет
    страницу так же, как в `journal()`.

    Но щита мало: запись, время которой прочитать нельзя, НЕ УПОРЯДОЧИВАЕТСЯ —
    «следующей за подавлением» она не является ни в каком смысле. Назвать её
    исходом значит угадать, а не прочитать, и владелец увидел бы подтверждение
    падения, которого, возможно, не было. Две честные строки лучше одной
    выдуманной.

    Через `journal()` такая запись до сюда не доходит (там она отсеивается) —
    сторож стоит на прямом вызове, потому что функция публичная.
    """
    now = 1_000_000.0
    bad = _rec(now - 300, kind="down")
    bad["ts"] = "вчера"
    rows = F.collapse_suppressed([_rec(now - 600, kind="suppressed"), bad])
    assert len(rows) == 2, rows
    assert all(r.get("after_s") is None for r in rows), rows
    assert all(r.get("outcome") is None for r in rows), rows


# ── блок «Что изменилось» на экране (§4.1-§4.3) ────────────────────────────

import importlib  # noqa: E402  — стенд ниже перезагружает роутеры
import re  # noqa: E402

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

KEY = "test-owner-key"


def _panel_client(tmp_path, monkeypatch, records, *, beat_age=5.0, backend_since=None):
    """Стенд первого экрана с ПОДМЕНЁННЫМ быстрым снапшотом.

    Журнал кладётся в снапшот тем же вызовом `F.journal()`, что и в проде: если
    ключа в `snapshot_fast()` не окажется, разметка получит пустышку и сторожа
    покраснеют — а не молча покажут пустой блок.
    """
    monkeypatch.setenv("JARVIS_PANELS_KEY", KEY)
    monkeypatch.setenv("TAMAPI_DB", str(tmp_path / "x.db"))
    monkeypatch.setattr(F, "ROOT", tmp_path)
    _journal(tmp_path, records, beat_age=beat_age)

    import app.routers.jarvis_panel as jp
    import app.routers.panels_auth as pa
    for m in (pa, jp):
        importlib.reload(m)
    monkeypatch.setattr(F, "ROOT", tmp_path)

    now = time.time()
    fast = {
        "collected_at": now,
        "external": F.Row("ext", "Внешний сторож", "bad", "НЕ настроен"),
        "processes": [F.Row("backend", "backend :8010", "ok", "PID 1",
                            {"since": backend_since or (now - 7200)})],
        "guardians": [F.Row("ops_watchdog", "ops_watchdog", "ok", "PID 2")],
        "journal": F.journal(now=now),
    }
    monkeypatch.setattr(F, "snapshot_fast", lambda *a, **k: fast)
    monkeypatch.setattr(F, "slow_cached", lambda *a, **k: None)
    api = FastAPI()
    for m in (pa, jp):
        api.include_router(m.router)
    return TestClient(api)


def _page(c):
    r = c.get("/panel/jarvis", headers={"X-Panels-Key": KEY})
    assert r.status_code == 200, r.text[:400]
    return r.text


def test_todays_events_are_open_and_older_days_are_counted(tmp_path, monkeypatch):
    """Три дня событий зальют первый экран так же, как его залили одиннадцать
    грязных деревьев 14.08. Сегодня раскрыто, вчера и позавчера — счётчиком."""
    now = time.time()
    recs = [_rec(now - 3600, detail="упал сегодня")]
    recs += [_rec(now - 2 * DAY - i, detail=f"позавчера {i}") for i in range(4)]
    body = _page(_panel_client(tmp_path, monkeypatch, recs))
    assert "упал сегодня" in body
    grp = re.search(r"<details class='grp'><summary>(.*?)</summary>", body, re.S)
    assert grp, "старые сутки не свёрнуты в счётчик"
    assert "4" in grp.group(1), grp.group(1)


def test_the_restart_divider_stands_at_the_backend_start(tmp_path, monkeypatch):
    """Якорь объективный: не «пока тебя не было», а «здесь перезапустился
    бэкенд». Момент уже лежит в снапшоте — нового состояния не заводим."""
    now = time.time()
    body = _page(_panel_client(tmp_path, monkeypatch,
                               [_rec(now - 3600, detail="до рестарта"),
                                _rec(now - 600, detail="после рестарта")],
                               backend_since=now - 1800))
    assert "перезапустился" in body
    assert body.index("после рестарта") < body.index("перезапустился") < body.index("до рестарта")


def test_a_silent_writer_replaces_the_list_with_words(tmp_path, monkeypatch):
    body = _page(_panel_client(tmp_path, monkeypatch, [_rec(time.time() - 60)],
                               beat_age=10_000.0))
    assert "верить нельзя" in body
    assert "процесс не найден" not in body, "показан список, которому нельзя верить"


def test_a_confirmed_suppressed_fall_reads_as_one_line(tmp_path, monkeypatch):
    now = time.time()
    body = _page(_panel_client(
        tmp_path, monkeypatch,
        [_rec(now - 900, kind="suppressed"), _rec(now - 600, kind="down")]))
    assert body.count("подавлено в загрузочном окне") == 1
    assert "подтверждено через 5 мин" in body


def test_an_empty_journal_says_it_out_loud(tmp_path, monkeypatch):
    """Пустая рамка читается как «ничего не случилось», а это УТВЕРЖДЕНИЕ —
    его надо произнести, иначе оно неотличимо от «не знаю» (та же граница, что
    у «медленные данные ещё не прочитаны»)."""
    body = _page(_panel_client(tmp_path, monkeypatch, []))
    assert "Что изменилось" in body
    assert "Переходов не было" in body


def test_a_detail_with_markup_cannot_reach_the_page_raw(tmp_path, monkeypatch):
    """`detail` приезжает из файла, который переживает 30 суток и чужие руки.
    Экранирование — не паранойя: панель открыта по ключу, но текст в неё
    кладёт сторож, а не человек."""
    now = time.time()
    body = _page(_panel_client(tmp_path, monkeypatch,
                               [_rec(now - 60, detail="<script>alert(1)</script>")]))
    assert "<script>alert(1)</script>" not in body
    assert "&lt;script&gt;" in body


def test_a_late_night_event_belongs_to_yesterday_not_to_today():
    """Сутки КАЛЕНДАРНЫЕ, а не 24-часовые куски. Разница видна ровно в тот час,
    когда панель открывают чаще всего: событие в 23:00, прочитанное в 01:00,
    двухчасовое по возрасту — и уехало бы в «Сегодня», хотя случилось ВЧЕРА.
    Владелец, ищущий «что было ночью», не нашёл бы его там, где искал."""
    import datetime

    import app.routers.jarvis_panel as jp

    night = datetime.datetime(2026, 8, 14, 23, 0).timestamp()
    early = datetime.datetime(2026, 8, 15, 1, 0).timestamp()
    assert jp._day_index(night, early) == 1, "ночное событие уехало в «Сегодня»"

    morning = datetime.datetime(2026, 8, 15, 2, 0).timestamp()
    late = datetime.datetime(2026, 8, 15, 23, 0).timestamp()
    assert jp._day_index(morning, late) == 0, "сегодняшнее событие уехало во «Вчера»"


def test_an_unreadable_time_does_not_take_down_the_grouping():
    """Парный сторож к щиту в `journal()`: разделитель рестарта панель кладёт
    САМА, и `since` приезжает из снапшота, а не из отфильтрованного журнала."""
    import app.routers.jarvis_panel as jp

    assert jp._day_index("вчера", time.time()) == 2
    assert jp._day_index(None, time.time()) == 2
    assert jp._day_index(10 ** 30, time.time()) == 2


def test_the_restart_divider_is_not_a_button_label(tmp_path, monkeypatch):
    """Парный сторож к read-only (`test_panels_web.py`): подпись «рестарт» в
    ячейке читается как подпись КНОПКИ, а панель фазы 0 не имеет права
    выглядеть управляемой. Разделитель — строка во всю ширину, а не вид записи.
    """
    now = time.time()
    body = _page(_panel_client(tmp_path, monkeypatch, [_rec(now - 3600)],
                               backend_since=now - 1800))
    assert ">рестарт" not in body.lower(), "разделитель выглядит подписью кнопки"
    assert "── здесь перезапустился бэкенд" in body, body[:200]
