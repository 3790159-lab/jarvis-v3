# -*- coding: utf-8 -*-
"""Проба дрила восстановления: ТРИ состояния, а не два (DEV-46 §9.4, §7 п. 11).

Дрил живёт на машине с приватным ключом (§9.2), сторож — на хосте. Сторож не
может «запустить дрил и посмотреть»: он читает УЛИКУ — файл вердикта, который
доехал с той машины. Отсюда три разных положения дел и три разных действия:

    прошёл       — ничего;
    провалился   — бэкап негоден, чинить срочно;
    не гонялся   — не известно НИЧЕГО, поднять задачу; бэкап может быть цел.

Второе и третье лечатся ПРОТИВОПОЛОЖНО, а третье после §9.2 будет случаться
регулярно и по бытовой причине: ноутбук выключен, владелец в отъезде. Одна
лампа «дрил не зелёный» приучает читать красное как «опять ноутбук» — и
настоящий провал прочтут так же ([[jarvis-ask-bridge-auto-allow-suspect]],
[[jarvis-blocked-verdict-swallows-the-red]]).

Сторожа написаны ОТ СПЕКИ, кода реализации автор этих тестов не видел
([[jarvis-guards-not-by-the-plan-author]]).

Ни сети, ни телеграма, ни живых портов: `ops_watchdog` импортируется как
модуль, зовутся ровно две функции. `now` и `ran_at` задаются ЯВНО — сторож,
опирающийся на часы машины, врёт в конце года и в чужом часовом поясе.
"""
from __future__ import annotations

import importlib
import importlib.util
import json
import os
import re
import time
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "ops_watchdog_restore_drill",
    Path(__file__).resolve().parents[1] / "scripts" / "ops_watchdog.py")
ow = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(ow)

DAY = 86400.0
NOW = 1_786_000_000.0

# Величины §4.3 п. 4: число диалогов и сообщений, посчитанные заранее.
COUNTS_OK = {"dialogs": 12, "messages": 3480}
COUNTS_BAD = {"dialogs": 12, "messages": 3477}

# ЛИТЕРАЛ, а не путь к файлу: переменная держит base64 от 32 сырых байт X25519
# (§3.3, вариант B). Проба смотрит только на непустоту — `ops_watchdog`
# stdlib-only и разбирать ключ не обязан, — но подставлять надо то, что в
# переменной реально живёт: путь к файлу проходил бы по случайности.
PUBLIC_KEY_B64 = "Tm3ZxY4i+FtOXP6rIMecJDdEWnzJ+gGUh5VUAvsWY3E="


# ── доступ к модулю вердикта ───────────────────────────────────────────────

@pytest.fixture
def rdv():
    """Импорт ЛЕНИВЫЙ: пока модуля нет, краснеет каждый тест своим именем, а
    не один collect-error на весь файл."""
    return importlib.import_module("app.services.restore_drill_verdict")


@pytest.fixture
def make_live(tmp_path, monkeypatch):
    """Фабрика деревьев-хостов. Публичный ключ задан — без него пробы нет
    вовсе (см. `test_probe_is_not_registered_without_the_public_key`)."""
    monkeypatch.setenv("JARVIS_BACKUP_PUBLIC_KEY", PUBLIC_KEY_B64)

    def _make(name: str = "live") -> Path:
        tree = tmp_path / name
        (tree / "state" / "backup").mkdir(parents=True)
        return tree

    return _make


@pytest.fixture
def live(make_live):
    return make_live()


def _verdict_path(rdv, live) -> Path:
    return Path(live) / rdv.VERDICT_REL


def _write(rdv, live, *, ran_at, ok=True, expected=None, actual=None, detail="drill"):
    path = _verdict_path(rdv, live)
    path.parent.mkdir(parents=True, exist_ok=True)
    return rdv.write_verdict(
        path, ran_at=ran_at, ok=ok,
        expected=dict(COUNTS_OK if expected is None else expected),
        actual=dict(COUNTS_OK if actual is None else actual),
        detail=detail)


def _probe(live, *, now=NOW, **kw) -> dict:
    snap = ow._restore_drill_snapshot(live_tree=Path(live))
    assert snap is not None, (
        "снимок None при заданном JARVIS_BACKUP_PUBLIC_KEY — проба не зарегистрируется")
    return ow.probe_restore_drill(snap, now=now, **kw)


# Заглушки боевого цикла: ни сети, ни портов — `probe_all` зовёт их сам.
def _http_ok(path):
    return 200


def _disk_free(path):
    return (100 * 2 ** 30, 0, 50 * 2 ** 30)


_NEG = re.compile(r"-\d+(?:[.,]\d+)?")


def _negative_numbers(text) -> list:
    """Отрицательные числа отдельными токенами. Дата «2026-08-25» — один
    токен, начинающийся с цифры, и под шаблон не попадает."""
    out = []
    for tok in str(text).replace("\n", " ").split():
        tok = tok.strip("«»\"'()[]{}<>:;,.!?")
        if _NEG.fullmatch(tok):
            out.append(tok)
    return out


# ── 1. ВЕДУЩИЙ: три состояния различимы ────────────────────────────────────

def test_three_states_are_distinguishable(rdv, make_live):
    """Без него «дрил нашёл расхождение» и «дрил не гонялся» приезжают одной
    лампой, и настоящий провал читают как «опять ноутбук выключен»."""
    fresh_ok = make_live("ok")
    _write(rdv, fresh_ok, ran_at=NOW - 1 * DAY, ok=True)

    fresh_bad = make_live("bad")
    _write(rdv, fresh_bad, ran_at=NOW - 1 * DAY, ok=False,
           actual=COUNTS_BAD, detail="restore failed")

    stale_ok = make_live("stale")
    _write(rdv, stale_ok, ran_at=NOW - 30 * DAY, ok=True)

    got = {
        "прошёл": _probe(fresh_ok),
        "провалился": _probe(fresh_bad),
        "просрочен": _probe(stale_ok),
    }
    reasons = {name: r.get("reason") for name, r in got.items()}

    assert len(set(reasons.values())) == 3, (
        "три положения дел склеены в одну лампу: %r" % (reasons,))
    assert reasons == {"прошёл": "drill_ok",
                       "провалился": "drill_failed",
                       "просрочен": "drill_stale"}, reasons

    greens = [name for name, r in got.items() if r.get("ok") is True]
    assert greens == ["прошёл"], (
        "зелёных не ровно одно: %r (reason=%r)" % (greens, reasons))


# ── 2. «вердикта нет» ≠ «вердикт красный» ──────────────────────────────────

def test_missing_verdict_is_never_not_failed_and_not_stale(rdv, make_live):
    """Без него отсутствие улики читается как найденный дефект (чинить бэкап)
    вместо «задача не заведена» — и наоборот, пропущенный запуск лечат заводом
    задачи, которая уже заведена."""
    empty = make_live("empty")
    assert not (Path(empty) / rdv.VERDICT_REL).exists(), "предпосылка теста сломана"
    never = _probe(empty)

    failed = make_live("failed")
    _write(rdv, failed, ran_at=NOW - 1 * DAY, ok=False,
           actual=COUNTS_BAD, detail="restore failed")
    failed_r = _probe(failed)

    stale = make_live("stale")
    _write(rdv, stale, ran_at=NOW - 30 * DAY, ok=True)
    stale_r = _probe(stale)

    assert never.get("ok") is False, (
        "вердикта нет, а лампа зелёная: reason=%r" % (never.get("reason"),))
    assert never.get("reason") == "drill_never", never
    assert never["reason"] != failed_r["reason"], (
        "«улики нет» приехало как «дрил провалился»: %r" % (never["reason"],))
    assert never["reason"] != stale_r["reason"], (
        "«задача не заведена» и «задача пропустила запуск» — одна лампа: %r"
        % (never["reason"],))


# ── 3. возраст по ran_at, а НЕ по mtime ────────────────────────────────────

def test_age_comes_from_ran_at_not_file_mtime(rdv, live):
    """Без него переезд файла вердикта с ноутбука на хост обнуляет возраст:
    mtime свежий, а проверка была 40 суток назад — и лампа зелёная."""
    _write(rdv, live, ran_at=NOW - 40 * DAY, ok=True)
    os.utime(_verdict_path(rdv, live), (NOW, NOW))   # «файл только что доехал»

    got = _probe(live)
    assert got.get("reason") == "drill_stale", (
        "возраст взят по mtime файла (NOW), а не по ran_at (NOW-40 сут): %r" % (got,))
    assert got.get("ok") is False, got


def test_fresh_ran_at_with_ancient_mtime_stays_green(rdv, live):
    """Зеркало предыдущего: без него старый mtime у свежего вердикта красит
    лампу зря, и владелец приучается гасить настоящее красное как ложное."""
    _write(rdv, live, ran_at=NOW - 1 * DAY, ok=True)
    os.utime(_verdict_path(rdv, live), (NOW - 400 * DAY, NOW - 400 * DAY))

    got = _probe(live)
    assert got.get("reason") == "drill_ok", (
        "возраст взят по mtime файла (NOW-400 сут), а не по ran_at (NOW-1 сут): %r"
        % (got,))
    assert got.get("ok") is True, got


# ── 4. граница строгая, с ОБЕИХ сторон ─────────────────────────────────────

def test_exactly_at_the_threshold_is_still_green(rdv, live):
    """Без него сторож загорается ровно на границе ритма и приучает к тому,
    что он слегка врёт (тот же довод, что у BUNDLE_MAX_LAG_DAYS)."""
    _write(rdv, live, ran_at=NOW - 10.0 * DAY, ok=True)
    got = _probe(live, now=NOW)
    assert got.get("reason") == "drill_ok", (
        "ровно 10.0 суток — уже красное, граница не строгая: %r" % (got,))
    assert got.get("ok") is True, got


def test_a_minute_past_the_threshold_is_stale(rdv, live):
    """Без него «строгая граница» превращается в «>=» наоборот — порог не
    срабатывает вовсе, и просроченный дрил остаётся зелёным навсегда."""
    _write(rdv, live, ran_at=NOW - (10.0 * DAY + 60.0), ok=True)
    got = _probe(live, now=NOW)
    assert got.get("reason") == "drill_stale", (
        "10 суток и 1 минута — всё ещё зелёное, порог не работает: %r" % (got,))
    assert got.get("ok") is False, got


def test_threshold_is_a_parameter_not_a_magic_number(rdv, live):
    """Без него порог нельзя ни подвинуть, ни проверить — и сторож на сторожа
    придётся писать через подмену системного времени."""
    _write(rdv, live, ran_at=NOW - 12 * DAY, ok=True)
    assert _probe(live, max_lag_days=30.0).get("reason") == "drill_ok"
    assert _probe(live, max_lag_days=5.0).get("reason") == "drill_stale"


def test_threshold_and_path_are_named_once(rdv):
    """Без него путь вердикта живёт двумя литералами: писатель кладёт файл в
    одно место, сторож смотрит в другое, и лампа вечно «не гонялся»
    ([[jarvis-two-numbers-for-one-thing]])."""
    assert ow.DRILL_MAX_LAG_DAYS == 10.0, ow.DRILL_MAX_LAG_DAYS
    assert ow.DRILL_FUTURE_TOLERANCE_S == 300.0, ow.DRILL_FUTURE_TOLERANCE_S
    assert ow.RESTORE_DRILL_REL == rdv.VERDICT_REL == "state/backup/restore_drill.json", (
        ow.RESTORE_DRILL_REL, rdv.VERDICT_REL)


# ── 5. ничего не зелёное по умолчанию ──────────────────────────────────────

BROKEN = {
    "не JSON": "{ это не json",
    "нет поля ok": json.dumps(
        {"ran_at": NOW, "expected": COUNTS_OK, "actual": COUNTS_OK, "detail": "x"}),
    "нет поля ran_at": json.dumps(
        {"ok": True, "expected": COUNTS_OK, "actual": COUNTS_OK, "detail": "x"}),
    "ran_at строкой": json.dumps(
        {"ran_at": str(NOW), "ok": True, "expected": COUNTS_OK,
         "actual": COUNTS_OK, "detail": "x"}),
    "ok строкой \"true\"": json.dumps(
        {"ran_at": NOW, "ok": "true", "expected": COUNTS_OK,
         "actual": COUNTS_OK, "detail": "x"}),
    # Обязательны ВСЕ ПЯТЬ полей (вердикт координатора 22.08): без
    # `expected`/`actual` алерт §7 п. 6 нечем наполнить, без `detail` — нечего
    # показать человеку.
    "нет поля expected": json.dumps(
        {"ran_at": NOW, "ok": True, "actual": COUNTS_OK, "detail": "x"}),
    "нет поля actual": json.dumps(
        {"ran_at": NOW, "ok": True, "expected": COUNTS_OK, "detail": "x"}),
    "нет поля detail": json.dumps(
        {"ran_at": NOW, "ok": True, "expected": COUNTS_OK, "actual": COUNTS_OK}),
}


@pytest.mark.parametrize("label", sorted(BROKEN))
def test_broken_verdict_is_unreadable_never_green(rdv, live, label):
    """Без него полуобрезанный или чужой файл на месте вердикта читается как
    «дрил прошёл»: строка "true" истинна, а `.get("ok")` у мусора — None."""
    _verdict_path(rdv, live).write_text(BROKEN[label], encoding="utf-8")
    got = _probe(live)
    assert got.get("ok") is False, (
        "битый вердикт (%s) прочитан как зелёный: %r" % (label, got))
    assert got.get("reason") == "unreadable", (label, got)


@pytest.mark.parametrize("label", sorted(BROKEN))
def test_read_verdict_raises_on_broken_input(rdv, live, label):
    """Без него мусор доезжает до пробы «нормальным словарём», и разбираться
    приходится по TypeError в середине цикла, а не по VerdictError на входе."""
    path = _verdict_path(rdv, live)
    path.write_text(BROKEN[label], encoding="utf-8")
    with pytest.raises(rdv.VerdictError):
        rdv.read_verdict(path)


def test_read_verdict_raises_when_the_file_is_absent(rdv, live):
    """Без него «файла нет» приезжает голым FileNotFoundError и ловится теми
    же руками, что и любая другая ошибка ввода-вывода."""
    with pytest.raises(rdv.VerdictError):
        rdv.read_verdict(_verdict_path(rdv, live))


# ── 6. вердикт «из будущего»: КРАСНОЕ, возраст недоказуем ─────────────────

def test_verdict_from_the_future_is_unreadable_not_green(rdv, make_live):
    """Без него часы, ушедшие вперёд, держат `drill_ok` НАВСЕГДА: зажатый в
    ноль возраст никогда не перевалит порог, даже если дрил не гонялся
    месяцами. Странность в `detail` этого не лечит — на зелёное не смотрят."""
    near = make_live("near")
    _write(rdv, near, ran_at=NOW + 1 * DAY, ok=True)
    far = make_live("far")
    _write(rdv, far, ran_at=NOW + 30 * DAY, ok=True)

    got = _probe(near, now=NOW)
    assert got.get("reason") == "unreadable", (
        "вердикт из будущего принят за нормальный: %r" % (got,))
    assert got.get("ok") is False, got

    negatives = _negative_numbers(got.get("detail"))
    assert not negatives, (
        "возраст вердикта отрицательный (%r): %r" % (negatives, got.get("detail")))

    # «Насколько ушла вперёд» — величина, а не факт: одинаковый текст на
    # опережении в сутки и в месяц означает, что размер нигде не назван.
    far_got = _probe(far, now=NOW)
    assert far_got.get("reason") == "unreadable", far_got
    assert got.get("detail") != far_got.get("detail"), (
        "опережение на 1 сутки и на 30 суток описаны слово в слово — на сколько "
        "отметка ушла вперёд, в алерте не сказано: %r" % (got.get("detail"),))


def test_future_inside_the_ntp_tolerance_stays_normal(rdv, make_live):
    """Без него сторож краснеет на здоровом джиттере NTP — и это ровно тот
    сторож, который приучает не смотреть на красное."""
    jitter = make_live("jitter")
    _write(rdv, jitter, ran_at=NOW + 60.0, ok=True)
    got = _probe(jitter, now=NOW)
    assert got.get("reason") == "drill_ok", (
        "минута расхождения часов покрасила лампу: %r" % (got,))
    assert got.get("ok") is True, got

    edge = make_live("edge")
    _write(rdv, edge, ran_at=NOW + 300.0, ok=True)   # РОВНО допуск — ещё в пределах
    edge_got = _probe(edge, now=NOW)
    assert edge_got.get("reason") == "drill_ok", (
        "ровно на допуске (300 с) уже красное — граница не строгая: %r" % (edge_got,))


def test_future_verdict_with_ok_false_is_also_unreadable(rdv, live):
    """Без него разъехавшиеся часы отдают провал как разобранный факт: при
    сбитой отметке непонятно, о КАКОМ прогоне речь, и «чинить бэкап срочно»
    приезжает без права на этот вывод."""
    _write(rdv, live, ran_at=NOW + 3 * DAY, ok=False,
           actual=COUNTS_BAD, detail="restore failed")
    got = _probe(live, now=NOW)
    assert got.get("reason") == "unreadable", (
        "провал из будущего разобран как drill_failed: %r" % (got,))
    assert got.get("ok") is False, got


# ── 7. detail на провале называет ОБА числа (§7 п. 6) ──────────────────────

def test_failed_verdict_names_both_numbers(rdv, live):
    """Без него алерт говорит «restore failed», и по нему нельзя отличить
    «потеряли три сообщения» от «база пустая» — а действия разные."""
    _write(rdv, live, ran_at=NOW - 1 * DAY, ok=False,
           expected=COUNTS_OK, actual=COUNTS_BAD, detail="restore failed")

    got = _probe(live)
    assert got.get("reason") == "drill_failed", got
    assert got.get("ok") is False, got
    detail = str(got.get("detail"))
    assert "3480" in detail, "в алерте нет ОЖИДАВШЕГОСЯ числа сообщений: %r" % detail
    assert "3477" in detail, "в алерте нет НАЙДЕННОГО числа сообщений: %r" % detail


# ── 8. условие регистрации — в ОБЕ стороны ─────────────────────────────────

def test_probe_is_not_registered_without_the_public_key(rdv, live, monkeypatch):
    """Без него сторож горит красным по построению на машине без публичного
    ключа, где клиентский набор не едет вовсе, — и его гасят навсегда."""
    _write(rdv, live, ran_at=NOW - 1 * DAY, ok=True)   # вердикт ЕСТЬ и он зелёный
    monkeypatch.delenv("JARVIS_BACKUP_PUBLIC_KEY", raising=False)

    assert ow._restore_drill_snapshot(live_tree=Path(live)) is None, (
        "снимок собрался без JARVIS_BACKUP_PUBLIC_KEY — проба зарегистрируется там, "
        "где бэкапа клиентских данных нет вовсе")


def test_with_the_key_and_no_verdict_the_lamp_is_red(rdv, live):
    """Вторая половина предыдущего. Без неё «нет ключа — нет пробы» проверено,
    а «есть ключ — лампа горит» нет: пробу можно выключить переменной
    окружения, и молчание сойдёт за зелёное."""
    assert not _verdict_path(rdv, live).exists(), "предпосылка теста сломана"

    snap = ow._restore_drill_snapshot(live_tree=Path(live))
    assert snap is not None, (
        "ключ задан, а снимка нет — проба не зарегистрируется и молчание "
        "прочтут как «всё в порядке»")

    got = ow.probe_restore_drill(snap, now=NOW)
    assert got.get("reason") == "drill_never", got
    assert got.get("ok") is False, got


# ── 9. круговорот вердикта ─────────────────────────────────────────────────

def test_verdict_round_trip_keeps_types(rdv, live):
    """Без него `ran_at` возвращается строкой, а `ok` — строкой "false", и
    вычитание времени падает, а `if ok:` истинно ровно на провале."""
    ran_at = NOW + 0.25          # дробное: int здесь не пройдёт незамеченным
    expected = {"dialogs": 12, "messages": 3480, "last_message_at": NOW - 900.0}
    actual = {"dialogs": 12, "messages": 3477, "last_message_at": NOW - 900.0}

    returned = rdv.write_verdict(_verdict_path(rdv, live), ran_at=ran_at, ok=False,
                                 expected=expected, actual=actual,
                                 detail="ЖИВОЙ дрил 22.08")
    assert Path(returned) == _verdict_path(rdv, live), returned

    got = rdv.read_verdict(_verdict_path(rdv, live))
    assert type(got["ran_at"]) is float, (
        "ran_at вернулся %s: %r" % (type(got["ran_at"]).__name__, got["ran_at"]))
    assert got["ran_at"] == ran_at, (got["ran_at"], ran_at)
    assert got["ok"] is False, (
        "ok вернулся %s %r, а не bool" % (type(got["ok"]).__name__, got["ok"]))
    assert got["expected"] == expected, got["expected"]
    assert got["actual"] == actual, got["actual"]
    assert got["detail"] == "ЖИВОЙ дрил 22.08", got["detail"]

    rdv.write_verdict(_verdict_path(rdv, live), ran_at=ran_at, ok=True,
                      expected=expected, actual=expected, detail="зелёный")
    again = rdv.read_verdict(_verdict_path(rdv, live))
    assert again["ok"] is True, (
        "ok=True вернулся %s %r" % (type(again["ok"]).__name__, again["ok"]))
    assert again["detail"] == "зелёный", again["detail"]


def test_overwrite_leaves_no_temp_stumps(rdv, live):
    """Без него запись поверх идёт «открыть-обрезать-писать»: падение посреди
    неё оставляет обрубок, который проба прочтёт как unreadable навсегда, — а
    брошенные `.tmp` копят наружу содержимое вердикта рядом с бэкапом."""
    name = Path(rdv.VERDICT_REL).name
    folder = _verdict_path(rdv, live).parent

    for i, ok in enumerate((True, False, True)):
        _write(rdv, live, ran_at=NOW - i * DAY, ok=ok,
               actual=COUNTS_OK if ok else COUNTS_BAD, detail="round %d" % i)
        leftovers = sorted(p.name for p in folder.iterdir())
        assert leftovers == [name], (
            "после записи №%d в каталоге вердикта лишние файлы: %r" % (i, leftovers))

    assert rdv.read_verdict(_verdict_path(rdv, live))["detail"] == "round 2"


def test_write_verdict_creates_the_missing_folders(rdv, tmp_path):
    """Без него первый прогон дрила на чистой машине падает на отсутствующем
    `state/backup/`, улика не появляется ни разу, и лампа вечно показывает
    «не гонялся» — правду по форме и молчание по причине."""
    path = tmp_path / "bare" / "state" / "backup" / "restore_drill.json"
    assert not path.parent.exists(), "предпосылка теста сломана"

    rdv.write_verdict(path, ran_at=NOW, ok=True, expected=COUNTS_OK,
                      actual=COUNTS_OK, detail="первый прогон")
    assert rdv.read_verdict(path)["detail"] == "первый прогон"


# ── 10. регистрация в БОЕВОМ цикле, в обе стороны ─────────────────────────

def test_probe_all_registers_the_check_and_computes_the_same_verdict(rdv, live):
    """Без него проба существует, но в цикл не попадает — или попадает и
    считает не то, что даёт прямой вызов на том же снимке.

    Здесь `now` не подставить: `probe_all` его не принимает. Поэтому вердикт
    кладётся свежим ОТНОСИТЕЛЬНО настоящих часов и далеко от порога (сутки при
    десяти), чтобы округление возраста не плавало между двумя вызовами."""
    _write(rdv, live, ran_at=time.time() - 1 * DAY, ok=True)
    snap = ow._restore_drill_snapshot(live_tree=Path(live))
    assert snap is not None, "предпосылка теста сломана"

    probes = ow.probe_all(_http_ok, _disk_free, restore_drill_snapshot=snap)
    assert "restore_drill" in probes, (
        "проба не зарегистрирована в цикле: %r" % (sorted(probes),))
    assert probes["restore_drill"] == ow.probe_restore_drill(snap), (
        "цикл считает не то же, что прямой вызов на том же снимке: %r против %r"
        % (probes["restore_drill"], ow.probe_restore_drill(snap)))
    # Состав остальных проб — здесь, а не только в половине без ключа: роняет
    # его именно РЕГИСТРАЦИЯ, и там, где её нет, такая мутация невидима.
    assert "backend" in probes and "disk" in probes, (
        "регистрация пробы уронила состав остальных: %r" % (sorted(probes),))


def test_probe_all_drops_the_check_without_the_key_and_keeps_the_rest(rdv, live,
                                                                     monkeypatch):
    """Без него отсутствие ключа либо тащит в цикл красную по построению лампу,
    либо, наоборот, роняет состав ОСТАЛЬНЫХ проб — и watchdog замолкает про
    бэкенд и диск ровно на той машине, где бэкапа клиентских данных нет."""
    _write(rdv, live, ran_at=time.time() - 1 * DAY, ok=True)
    monkeypatch.delenv("JARVIS_BACKUP_PUBLIC_KEY", raising=False)

    snap = ow._restore_drill_snapshot(live_tree=Path(live))
    assert snap is None, "предпосылка теста сломана"

    probes = ow.probe_all(_http_ok, _disk_free, restore_drill_snapshot=snap)
    assert "restore_drill" not in probes, (
        "проба зарегистрирована без публичного ключа: %r" % (sorted(probes),))
    # Число проб НЕ зашиваем: состав меняют другие авторы этой же арки.
    assert "backend" in probes and "disk" in probes, (
        "регистрация одной пробы уронила состав остальных: %r" % (sorted(probes),))
