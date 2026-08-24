"""Сверка регресса ПО ИМЕНАМ, а не по числу (DEV-72).

Повод: вердикт сравнивал только количество (`failed <= base_failed`). Такая
сверка близорука по построению — «82 ≤ 82» зелено даже когда упали ДРУГИЕ 82
теста, то есть настоящий регресс прячется в шуме ровно того размера, что и
известные падения. Плюс эталон с числом от 10.07 (33) против сегодняшних 82
заставлял гейт кричать «+49» на каждом прогоне.

Здесь три границы:
  1. имена вытаскиваются из хвоста pytest и переживают путь батч → агрегат;
  2. вердикт различает «те же» и «другие» при одинаковом ЧИСЛЕ падений;
  3. запись эталона не обедняет файл — иначе поимённый список исчез бы при
     первом же зелёном прогоне, а эталон продолжал бы выглядеть свежим.
"""
from __future__ import annotations

import json

from app.services.devtask.regress_batches import (
    aggregate_summaries, write_baseline)
from tools.jarvis_observe import failed_names, regress_verdict

_TAIL = """\
FAILED tests/test_a.py::test_one - AssertionError: 1 != 2
FAILED tests/test_b.py::test_two - TypeError: bad
ERROR tests/test_c.py::test_three - fixture 'x' not found
ERROR: usage: pytest [options]
2 failed, 10 passed, 1 error in 3.21s
"""


# ── 1. имена ────────────────────────────────────────────────────────────────

def test_imena_beryotsya_i_iz_FAILED_i_iz_ERROR():
    assert failed_names(_TAIL) == [
        "tests/test_a.py::test_one",
        "tests/test_b.py::test_two",
        "tests/test_c.py::test_three",
    ]


def test_sluzhebnaya_stroka_ERROR_usage_ne_schitaetsya_testom():
    """`ERROR: usage` — это не тест. «Не тест» в эталоне падений хуже, чем
    его отсутствие: по нему потом сверяют."""
    assert "usage:" not in " ".join(failed_names(_TAIL))
    assert failed_names("ERROR: usage: pytest [options]") == []


def test_prichina_otrezaetsya_a_imya_ostayotsya():
    """Причина меняется от прогона к прогону (адреса, тайминги), имя — нет."""
    a = failed_names("FAILED tests/t.py::test_x - AssertionError: 0x7f9a != 0x1234")
    b = failed_names("FAILED tests/t.py::test_x - AssertionError: 0xdead != 0xbeef")
    assert a == b == ["tests/t.py::test_x"]


# ── 2. вердикт ──────────────────────────────────────────────────────────────

_BASE = {"failed": 2, "passed": 10, "errors": 1,
         "known_failures": ["tests/test_a.py::test_one", "tests/test_b.py::test_two"]}


def test_te_zhe_padeniya_zelyonoe():
    v = regress_verdict({"failed": 2, "passed": 10, "errors": 1,
                         "failed_names": list(_BASE["known_failures"])}, _BASE)
    assert v.startswith("✅")
    assert "те же" in v


def test_TO_ZHE_CHISLO_no_DRUGIE_testy_KRASNOE():
    """Главный случай, ради которого всё затевалось: количество совпадает."""
    v = regress_verdict({"failed": 2, "passed": 10, "errors": 1,
                         "failed_names": ["tests/test_a.py::test_one",
                                          "tests/test_NEW.py::test_zzz"]}, _BASE)
    assert v.startswith("⚠️"), "подмена состава при том же числе обязана краснеть"
    assert "tests/test_NEW.py::test_zzz" in v, "новое падение обязано быть НАЗВАНО"


def test_pochinennye_ne_krasnyat_no_nazvany():
    v = regress_verdict({"failed": 1, "passed": 11, "errors": 1,
                         "failed_names": ["tests/test_a.py::test_one"]}, _BASE)
    assert v.startswith("✅")
    assert "починились" in v


def test_bez_imyon_otkat_k_chislu_NAZVAN_vsluh():
    """Молчаливый откат к близорукому режиму выглядел бы как полная проверка."""
    v = regress_verdict({"failed": 2, "passed": 10, "errors": 1}, _BASE)
    assert "ТОЛЬКО ПО ЧИСЛУ" in v
    v2 = regress_verdict({"failed": 2, "passed": 10, "errors": 1,
                          "failed_names": []}, {"failed": 2})
    assert "ТОЛЬКО ПО ЧИСЛУ" in v2


def test_staroe_povedenie_ne_slomano():
    """Вызовы без имён с обеих сторон обязаны отвечать как раньше."""
    assert "✅" in regress_verdict({"failed": 130, "passed": 3353, "errors": 4},
                                  {"failed": 130})
    assert "⚠️" in regress_verdict({"failed": 134, "passed": 3349, "errors": 4},
                                   {"failed": 130})
    assert "baseline не задан" in regress_verdict({"failed": 1}, None)


# ── 3. имена переживают батчи и запись ──────────────────────────────────────

def test_agregat_obyedinyaet_imena_batchey():
    agg = aggregate_summaries([
        {"failed": 1, "passed": 5, "errors": 0, "failed_names": ["t/a.py::x"]},
        {"failed": 1, "passed": 4, "errors": 1, "failed_names": ["t/b.py::y"]},
    ])
    assert agg["failed"] == 2 and agg["passed"] == 9 and agg["errors"] == 1
    assert agg["failed_names"] == ["t/a.py::x", "t/b.py::y"]


def test_bez_imyon_klyucha_NET_a_ne_pustoi_spisok():
    """«Имён не собирали» и «падений по именам нет» — разные вещи, и вердикт
    ведёт себя по-разному. Пустой список тут был бы враньём."""
    agg = aggregate_summaries([{"failed": 1, "passed": 5, "errors": 0}])
    assert "failed_names" not in agg


def test_zapis_etalona_NE_zatirayet_chuzhie_klyuchi(tmp_path):
    """Эталон, обедняющий сам себя при зелёном прогоне, выглядит свежим —
    и потому опаснее отсутствующего."""
    p = tmp_path / "regress_baseline.json"
    p.write_text(json.dumps({
        "failed": 82, "passed": 10268, "errors": 15,
        "known_failures": ["t/old.py::x"],
        "taken_at": "2026-08-25", "composition": "worktree без конфигов",
    }, ensure_ascii=False), encoding="utf-8")

    write_baseline(p, {"failed": 80, "passed": 10270, "errors": 15,
                       "failed_names": ["t/new.py::y"]})

    got = json.loads(p.read_text(encoding="utf-8"))
    assert got["failed"] == 80
    assert got["known_failures"] == ["t/new.py::y"]
    assert got["taken_at"] == "2026-08-25", "метаданные эталона обязаны выжить"
    assert got["composition"] == "worktree без конфигов"


def test_zapis_bez_imyon_ne_ubivaet_uzhe_lezhashchiy_spisok(tmp_path):
    p = tmp_path / "b.json"
    p.write_text(json.dumps({"failed": 5, "known_failures": ["t/a.py::x"]}),
                 encoding="utf-8")
    write_baseline(p, {"failed": 4, "passed": 1, "errors": 0})
    got = json.loads(p.read_text(encoding="utf-8"))
    assert got["known_failures"] == ["t/a.py::x"]
    assert got["failed"] == 4
