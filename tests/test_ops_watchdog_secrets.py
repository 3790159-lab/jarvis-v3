# -*- coding: utf-8 -*-
"""Девятая проверка ops_watchdog: копия секретов (.jrvbak) не отстала.

Бандл — ЕДИНСТВЕННЫЙ путь восстановления после смерти диска или профиля:
DPAPI привязан к учётке+машине, и без бандла сессии всех клиентов теряются,
каждый логинится заново (ONBOARDING_MANUAL §12). Экспорт остаётся ручным —
пароль вводит владелец, — поэтому сторож не делает копию, а напоминает, что
она отстала.

Сравнивается не «.env против бандла», а весь МАТЕРИАЛ, который в бандл реально
едет (`collect_secrets`): `.env`/`.env.enc`, `<slug>.session.enc`, `entropy.bin`.
Иначе пропущенный экспорт после логина НОВОГО клиента остался бы невидимым —
а это ровно тот случай, который мануал называет незакрытым онбордингом.

Только чтение: проверка смотрит mtime и ничего не трогает.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "ops_watchdog_secrets", Path(__file__).resolve().parents[1] / "scripts" / "ops_watchdog.py")
ow = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(ow)

DAY = 86400.0
NOW = 1_786_000_000.0


def _snap(material=(( ".env", NOW - 2 * DAY),), bundle=("jarvis-secrets-2026-08-05.jrvbak", NOW - 3 * DAY),
          searched=("C:/Users/Admin/OneDrive/jarvis-recovery",), error=None):
    return {
        "material": [{"name": n, "mtime": m} for n, m in material],
        "bundle": None if bundle is None else {"name": bundle[0], "mtime": bundle[1]},
        "searched": list(searched),
        "error": error,
    }


# ── зелёное ────────────────────────────────────────────────────────────────

def test_bundle_newer_than_material_is_ok():
    got = ow.probe_secrets_bundle(_snap())
    assert got["ok"] is True


def test_lag_under_the_threshold_is_ok():
    got = ow.probe_secrets_bundle(
        _snap(material=((".env", NOW),), bundle=("b.jrvbak", NOW - 6 * DAY)))
    assert got["ok"] is True
    assert "6" in got["detail"], "в зелёном тексте не видно, сколько уже отставание"


def test_exactly_at_the_threshold_is_still_ok():
    """Граница названа явно: красное СТРОГО больше порога. Иначе сторож
    загорается ровно на седьмые сутки, когда владелец ещё в графике."""
    got = ow.probe_secrets_bundle(
        _snap(material=((".env", NOW),), bundle=("b.jrvbak", NOW - 7 * DAY)))
    assert got["ok"] is True


def test_bundle_newer_than_material_never_shows_negative_lag():
    got = ow.probe_secrets_bundle(
        _snap(material=((".env", NOW - 9 * DAY),), bundle=("b.jrvbak", NOW)))
    assert got["ok"] is True
    assert "-" not in got["detail"], got["detail"]


# ── красное: бандл отстал ──────────────────────────────────────────────────

def test_stale_bundle_is_down():
    got = ow.probe_secrets_bundle(
        _snap(material=((".env", NOW),), bundle=("b.jrvbak", NOW - 8 * DAY)))
    assert got["ok"] is False
    assert "8" in got["detail"]


def test_alert_names_which_material_outran_the_bundle():
    """«Что-то новее бандла» бесполезно: владельцу нужно знать, ЧТО именно
    поменялось — .env или новая сессия клиента."""
    got = ow.probe_secrets_bundle(_snap(
        material=((".env", NOW - 20 * DAY), ("volska.session.enc", NOW)),
        bundle=("b.jrvbak", NOW - 9 * DAY)))
    assert got["ok"] is False
    assert "volska.session.enc" in got["detail"]
    assert "b.jrvbak" in got["detail"], "не названо, какой бандл считать устаревшим"


def test_new_client_session_alone_triggers_the_check():
    """Пропущенный экспорт после логина нового клиента — незакрытый онбординг.
    Если смотреть только на .env, этот случай невидим."""
    got = ow.probe_secrets_bundle(_snap(
        material=((".env", NOW - 40 * DAY), ("newclient.session.enc", NOW),
                  ("entropy.bin", NOW - 40 * DAY)),
        bundle=("b.jrvbak", NOW - 30 * DAY)))
    assert got["ok"] is False
    assert "newclient.session.enc" in got["detail"]


# ── красное: бандла нет вовсе ──────────────────────────────────────────────

def test_missing_bundle_is_down_and_says_where_it_looked():
    got = ow.probe_secrets_bundle(_snap(bundle=None))
    assert got["ok"] is False
    assert "jarvis-recovery" in got["detail"], "не сказано, где искали"


def test_no_material_is_down_not_silently_ok():
    """Пустой материал — это не «нечего бэкапить», а «мы ничего не увидели».
    Зелёное здесь означало бы слепую зону вокруг единственного пути
    восстановления."""
    got = ow.probe_secrets_bundle(_snap(material=()))
    assert got["ok"] is False


def test_both_causes_land_in_one_alert():
    """Тот же принцип, что у worktree-чека: узнав одну причину из двух,
    починишь её и решишь, что закрыл вопрос."""
    got = ow.probe_secrets_bundle(_snap(material=(), bundle=None))
    assert got["ok"] is False
    d = got["detail"]
    assert "бандл" in d.lower() and "материал" in d.lower(), d


def test_scan_error_is_down_not_green():
    got = ow.probe_secrets_bundle(_snap(error="PermissionError: OneDrive"))
    assert got["ok"] is False
    assert "PermissionError" in got["detail"]


# ── форма и настройки ──────────────────────────────────────────────────────

def test_threshold_is_a_parameter_not_a_magic_number():
    stale = _snap(material=((".env", NOW),), bundle=("b.jrvbak", NOW - 3 * DAY))
    assert ow.probe_secrets_bundle(stale, max_lag_days=2)["ok"] is False
    assert ow.probe_secrets_bundle(stale, max_lag_days=30)["ok"] is True


def test_default_threshold_is_seven_days():
    assert ow.BUNDLE_MAX_LAG_DAYS == 7.0


def test_alert_stays_readable_in_telegram():
    got = ow.probe_secrets_bundle(_snap(
        material=[(f"c{i}.session.enc", NOW) for i in range(30)],
        bundle=("b.jrvbak", NOW - 40 * DAY)))
    assert len(got["detail"]) < 400, "алерт разросся в простыню"


def test_bundle_location_is_the_offsite_mirror():
    """Бандл лежит ВНЕ машины (мануал). Смотрим на локальное зеркало OneDrive:
    файл там — свидетельство, что экспорт был, а синхронизация унесла копию
    за пределы диска."""
    assert ow.BUNDLE_GLOB == "*.jrvbak"
    assert any("jarvis-recovery" in str(d) for d in ow.BUNDLE_DIRS), ow.BUNDLE_DIRS


# ── только чтение ──────────────────────────────────────────────────────────

def test_snapshot_touches_nothing(tmp_path):
    """Сторож не имеет права трогать секреты: ни создать, ни продлить mtime.
    Иначе он сам испортит то, что сторожит — и заодно навсегда обнулит
    отставание, которое должен был показывать.

    Файлам ЗАРАНЕЕ проставляется старое время. Без этого тест был слеп:
    системные часы Windows тикают раз в ~15 мс, только что созданный файл и
    его `touch()` попадают в один тик, mtime не меняется и мутация
    «сторож пишет» проходила зелёной (поймано DEV-26)."""
    import os
    live = tmp_path / "tree"
    (live / ".secrets").mkdir(parents=True)
    (live / ".env").write_text("X=1", encoding="utf-8")
    (live / ".secrets" / "entropy.bin").write_bytes(b"0" * 32)
    (live / ".secrets" / "volska.session.enc").write_bytes(b"s")
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "b.jrvbak").write_bytes(b"blob")

    old = NOW - 100 * DAY
    for f in tmp_path.rglob("*"):
        if f.is_file():
            os.utime(f, (old, old))

    def fingerprint():
        return sorted((str(p.relative_to(tmp_path)), p.stat().st_mtime_ns, p.stat().st_size)
                      for p in tmp_path.rglob("*") if p.is_file())

    before = fingerprint()
    ow._secrets_bundle_snapshot(live_tree=live, bundle_dirs=(vault,))
    assert fingerprint() == before, "снимок изменил файлы"


def test_snapshot_reads_the_real_shapes(tmp_path):
    live = tmp_path / "tree"
    (live / ".secrets").mkdir(parents=True)
    (live / ".env").write_text("X=1", encoding="utf-8")
    (live / ".secrets" / "volska.session.enc").write_bytes(b"s")
    (live / ".secrets" / "entropy.bin").write_bytes(b"e")
    (live / ".secrets" / "demo.db").write_bytes(b"db")     # НЕ материал бандла
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "old.jrvbak").write_bytes(b"1")
    (vault / "new.jrvbak").write_bytes(b"2")

    snap = ow._secrets_bundle_snapshot(live_tree=live, bundle_dirs=(vault,))
    names = {m["name"] for m in snap["material"]}
    assert ".env" in names and "volska.session.enc" in names and "entropy.bin" in names
    assert "demo.db" not in names, "БД попала в материал бандла — её там нет"
    assert snap["bundle"]["name"] in {"old.jrvbak", "new.jrvbak"}
    assert snap["error"] is None


def test_plaintext_env_counts_even_when_the_enc_version_exists(tmp_path):
    """Живой прогон 2026-08-10: .env от 08.08, .env.enc от 23.07, бандл от
    05.08. Версия «предпочесть .enc, иначе .env» рапортовала отставание
    0.0 суток — при том, что рабочий .env ушёл вперёд бандла на двое суток.

    `collect_secrets` предпочитает .enc, потому что его и экспортирует; но
    проверка не о том, что УЕДЕТ в бандл, а о том, что уже ИЗМЕНИЛОСЬ."""
    import os
    live = tmp_path / "tree"
    (live / ".secrets").mkdir(parents=True)
    (live / ".env").write_text("X=1", encoding="utf-8")
    (live / ".env.enc").write_bytes(b"old")
    os.utime(live / ".env", (NOW, NOW))
    os.utime(live / ".env.enc", (NOW - 40 * DAY, NOW - 40 * DAY))
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "b.jrvbak").write_bytes(b"1")
    os.utime(vault / "b.jrvbak", (NOW - 20 * DAY, NOW - 20 * DAY))

    snap = ow._secrets_bundle_snapshot(live_tree=live, bundle_dirs=(vault,))
    names = {m["name"] for m in snap["material"]}
    assert ".env" in names and ".env.enc" in names, names

    got = ow.probe_secrets_bundle(snap)
    assert got["ok"] is False, "плейнтекстовый .env ушёл вперёд бандла, а сторож молчит"
    assert ".env" in got["detail"]


def test_snapshot_picks_the_newest_bundle(tmp_path):
    live = tmp_path / "tree"
    (live / ".secrets").mkdir(parents=True)
    (live / ".env").write_text("X=1", encoding="utf-8")
    vault = tmp_path / "vault"
    vault.mkdir()
    import os
    (vault / "old.jrvbak").write_bytes(b"1")
    os.utime(vault / "old.jrvbak", (NOW - 50 * DAY, NOW - 50 * DAY))
    (vault / "new.jrvbak").write_bytes(b"2")
    os.utime(vault / "new.jrvbak", (NOW, NOW))

    snap = ow._secrets_bundle_snapshot(live_tree=live, bundle_dirs=(vault,))
    assert snap["bundle"]["name"] == "new.jrvbak", "взят не самый свежий бандл"


def test_snapshot_without_any_vault_reports_no_bundle_not_a_crash(tmp_path):
    live = tmp_path / "tree"
    (live / ".secrets").mkdir(parents=True)
    (live / ".env").write_text("X=1", encoding="utf-8")
    snap = ow._secrets_bundle_snapshot(live_tree=live, bundle_dirs=(tmp_path / "nope",))
    assert snap["bundle"] is None and snap["error"] is None
    assert ow.probe_secrets_bundle(snap)["ok"] is False


# ── подключение к боевому циклу ────────────────────────────────────────────

def test_probe_all_includes_the_check_when_snapshot_supplied():
    probes = ow.probe_all(lambda p: 200, lambda p: (100 * 2**30, 0, 50 * 2**30),
                          secrets_snapshot=_snap())
    assert probes["secrets_bundle"]["ok"] is True


def test_probe_all_without_snapshot_keeps_old_behaviour():
    probes = ow.probe_all(lambda p: 200, lambda p: (100 * 2**30, 0, 50 * 2**30))
    assert "secrets_bundle" not in probes


def test_check_goes_through_the_existing_debounce():
    down = {"secrets_bundle": {"ok": False, "detail": "бандл отстал"}}
    alerts, st = ow.evaluate({}, down)
    assert alerts == []
    alerts, st = ow.evaluate(st, down)
    assert len(alerts) == 1 and "DOWN" in alerts[0]
    alerts, st = ow.evaluate(st, {"secrets_bundle": {"ok": True, "detail": "ok"}})
    assert len(alerts) == 1 and "Восстановлено" in alerts[0]


def test_check_has_a_human_label():
    assert "secrets_bundle" in ow.LABELS
    assert "jrvbak" in ow.LABELS["secrets_bundle"].lower()


def test_it_is_the_ninth_check():
    probes = ow.probe_all(lambda p: 200, lambda p: (100 * 2**30, 0, 50 * 2**30),
                          chatter_snapshot={"processes": [], "runner_beat_age": None,
                                            "guardian_beat_age": None,
                                            "guardian_lock_pid": None, "root": "x"},
                          worktree_snapshot={"branch": "phase-4.0-unified-jarvis",
                                             "dirty": [], "error": None},
                          secrets_snapshot=_snap())
    assert len(probes) == 9, sorted(probes)
