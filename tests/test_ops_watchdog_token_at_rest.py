"""DEV-74: проба «учётные данные не лежат в данных».

Почему проба, а не тест суиты. Сторож жил в `tests/` и смотрел на `state/`
относительно себя. В worktree мерж-гейта `state/` гитигнорен и отсутствует —
тест скипался, то есть молчал ПО ПОСТРОЕНИЮ ровно там, где его и гоняли.
Скип выглядит как «всё в порядке»: это лампа, а не сторож
([[jarvis-loud-failure-next-to-a-soothing-lamp]]).

Проба смотрит на LIVE_TREE и видит боевой диск. Здесь проверяется её
поведение — на подсунутом дереве, а не на живом: тест, зависящий от состояния
рабочей машины, был бы вторым способом соврать.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def ow():
    spec = importlib.util.spec_from_file_location(
        "ops_watchdog_under_test", ROOT / "scripts" / "ops_watchdog.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


TOKEN_LINE = b'https://api.telegram.org/file/bot7819877837:AAHTiE0Cciq/photos/a.jpg'


# ── снимок ──────────────────────────────────────────────────────────────────
def test_snapshot_finds_the_token_and_names_the_file(ow, tmp_path):
    (tmp_path / "state").mkdir()
    bad = tmp_path / "state" / "conv.json"
    bad.write_bytes(TOKEN_LINE)
    snap = ow._token_at_rest_snapshot(live_tree=tmp_path)
    assert snap["offenders"] == ["state\\conv.json".replace("\\", "/")] or \
           snap["offenders"] == ["state\\conv.json"], snap["offenders"]
    assert snap["error"] is None


def test_snapshot_is_clean_when_nothing_leaks(ow, tmp_path):
    (tmp_path / "state").mkdir()
    (tmp_path / "state" / "ok.json").write_bytes(b'{"file_id": "ABC123"}')
    snap = ow._token_at_rest_snapshot(live_tree=tmp_path)
    assert snap["offenders"] == []
    assert snap["scanned"] == 1


def test_snapshot_skips_connect_where_secrets_are_legal(ow, tmp_path):
    """`state/connect/` держит бандл и сессии — там секреты по построению."""
    d = tmp_path / "state" / "connect"
    d.mkdir(parents=True)
    (d / "bundle.txt").write_bytes(TOKEN_LINE)
    snap = ow._token_at_rest_snapshot(live_tree=tmp_path)
    assert snap["offenders"] == []


def test_snapshot_counts_oversize_instead_of_hiding_it(ow, tmp_path):
    """Пропущенный по размеру файл СЧИТАЕТСЯ — молча суженный охват читается
    как «всё чисто»."""
    (tmp_path / "state").mkdir()
    big = tmp_path / "state" / "huge.log"
    big.write_bytes(b"x" * (ow.TOKEN_SCAN_MAX_FILE_BYTES + 1))
    snap = ow._token_at_rest_snapshot(live_tree=tmp_path)
    assert snap["oversize"] == 1
    assert snap["unreadable"] == 0, "занятых файлов не было, а счётчик сработал"
    assert snap["scanned"] == 0


def test_oversize_and_unreadable_are_counted_apart(ow, tmp_path, monkeypatch):
    """🔴 Живой прогон 25.08: проба сказала «по размеру пропущено 1», хотя
    файлов свыше порога не было ни одного — пропущен был ЗАНЯТЫЙ файл.

    Одно число на две причины врёт про причину, а чинят их по-разному:
    большой файл — это порог, занятый — это гонка с другим процессом.
    """
    (tmp_path / "state").mkdir()
    (tmp_path / "state" / "locked.json").write_bytes(b"{}")
    real = Path.read_bytes

    def boom(self, *a, **k):
        if self.name == "locked.json":
            raise OSError("файл занят другим процессом")
        return real(self, *a, **k)

    monkeypatch.setattr(Path, "read_bytes", boom)
    snap = ow._token_at_rest_snapshot(live_tree=tmp_path)
    assert snap["unreadable"] == 1
    assert snap["oversize"] == 0, "занятый файл посчитан как «слишком большой»"


def test_probe_names_unreadable_separately_from_oversize(ow):
    r = ow.probe_token_at_rest(
        {"offenders": [], "scanned": 5, "oversize": 2, "unreadable": 3,
         "error": None})
    assert "по размеру пропущено 2" in r["detail"]
    assert "не прочиталось 3" in r["detail"]


def test_snapshot_is_none_when_there_is_no_live_state(ow, tmp_path):
    """Нет живого state/ → пробы в цикле НЕТ ВОВСЕ.

    Не зелёная и не красная: watchdog не имеет права слать DOWN о том, чего
    он не мерил, — и не имеет права рапортовать чистоту, не посмотрев.
    """
    assert ow._token_at_rest_snapshot(live_tree=tmp_path) is None


# ── проба ───────────────────────────────────────────────────────────────────
def test_probe_red_names_the_files(ow):
    r = ow.probe_token_at_rest(
        {"offenders": ["state/conversations/1.json", "state/me_personas/1.json"],
         "scanned": 10, "oversize": 0, "error": None})
    assert not r["ok"]
    assert r["reason"] == "token_at_rest"
    assert "state/conversations/1.json" in r["detail"]
    assert "state/me_personas/1.json" in r["detail"]


def test_probe_never_prints_the_secret_itself(ow):
    """Алерт уезжает в Telegram. Сторож, печатающий охраняемое, был бы вторым
    каналом утечки."""
    r = ow.probe_token_at_rest(
        {"offenders": ["state/x.json"], "scanned": 1, "oversize": 0, "error": None})
    assert "bot7819877837" not in r["detail"]
    assert ":AAH" not in r["detail"]


def test_probe_green_still_says_what_was_not_looked_at(ow):
    """Хвост про охват одинаков на зелёном и красном — иначе зелёное выглядит
    полнее, чем оно есть."""
    r = ow.probe_token_at_rest(
        {"offenders": [], "scanned": 1105, "oversize": 3, "error": None})
    assert r["ok"]
    assert "1105" in r["detail"]
    assert "connect" in r["detail"] and "browser_profile" in r["detail"]
    assert "3" in r["detail"], "пропущенное по размеру не названо"


def test_probe_unreadable_is_red_not_green(ow):
    r = ow.probe_token_at_rest(
        {"offenders": [], "scanned": 0, "oversize": 0, "error": "PermissionError: x"})
    assert not r["ok"] and r["reason"] == "unreadable"


def test_probe_refuses_a_missing_snapshot(ow):
    assert not ow.probe_token_at_rest(None)["ok"]


# ── подключение в цикл ──────────────────────────────────────────────────────
def test_probe_is_registered_in_probe_all(ow):
    probes = ow.probe_all(
        lambda path: None, lambda path: (1, 1, 10 * 1024 ** 3),
        token_at_rest_snapshot={"offenders": [], "scanned": 5, "oversize": 0,
                                "error": None})
    assert "token_at_rest" in probes and probes["token_at_rest"]["ok"]


def test_no_snapshot_means_no_probe_at_all(ow):
    """Снимка нет → пробы в цикле нет. Не зелёная: см. соседние семейства."""
    probes = ow.probe_all(lambda path: None, lambda path: (1, 1, 10 * 1024 ** 3))
    assert "token_at_rest" not in probes
