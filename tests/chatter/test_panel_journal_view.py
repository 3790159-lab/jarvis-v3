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
