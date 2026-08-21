# -*- coding: utf-8 -*-
"""Сторожа DEV-46 §9.3 / §7 п. 10: срок хранения — свойство ПРЕФИКСА.

Написаны ОТ СПЕКИ, до реализации и без доступа к ней
([[jarvis-guards-not-by-the-plan-author]]).

Механика дефекта, ради которого файл существует. Сегодня
``rotate_old_backups`` берёт ВЕСЬ префикс ``backups/state/``, вырезает из ключа
сегмент даты и удаляет всё старше ``сегодня − 14 суток``; класс объекта в
решении не участвует — его в ключе попросту нет. Клиентскому набору владелец
назначил ГОД (§8 ответ 2). Общий префикс плюс один порог = на пятнадцатые сутки
годовой набор удалён целиком и МОЛЧА: удаление для этой функции не авария, а
работа.

Ни одного живого вызова R2 и ни одной сети: ``list_objects``/``delete_object``
подставные, ``today`` передаётся явно — на часы машины не опирается ни один
тест.
"""
from __future__ import annotations

import importlib.util
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from app.services import state_backup as sb

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "state_backup.py"

# Опорная дата снимка и «сегодня» из §7 п. 10 — ровно снимок + 15 суток.
SNAP = datetime(2026, 8, 21, tzinfo=timezone.utc)
TODAY = SNAP + timedelta(days=15)

# ЛИТЕРАЛЫ, а не sb.BACKUP_PREFIX/sb.CLIENT_PREFIX: тест, выражающий ожидание
# через саму константу, согласен с реализацией по определению
# ([[jarvis-literal-lists-not-introspection]]).
STATE = "backups/state"
CLIENT = "backups/client"

# Ключи ВНЕ обоих объявленных префиксов. `backups/media/...` — чужой набор;
# `backups/stateful/...` — сосед, которого утащит тот, кто листит
# `backups/state` БЕЗ завершающего слэша (в S3 префикс — обычная подстрока
# начала ключа, границы по `/` там нет).
FOREIGN_KEYS = (
    "backups/media/2020-01-01/x.bin",
    "backups/stateful/2020-01-01/x.bin",
    "backups/media/2027-09-05/fresh.bin",
)


def _backup_config():
    from app.services.r2_storage import R2Config
    return R2Config(
        account_id="acct", access_key_id="ak", secret_access_key="sk",
        bucket="jarvis-state-backups",
        endpoint="https://acct.r2.cloudflarestorage.com",
        public_base_url="",
    )


def _key(prefix: str, age_days: int, name: str, *, today: datetime = TODAY) -> str:
    """Ключ объекта возрастом РОВНО ``age_days`` суток относительно ``today``."""
    date_part = (today - timedelta(days=age_days)).strftime("%Y-%m-%d")
    return f"{prefix}/{date_part}/{name}"


class _FakeBucket:
    """Подставной бакет с настоящей S3-семантикой листинга по префиксу.

    Префикс матчится как подстрока начала ключа, без границы по ``/`` — ровно
    как у R2. Поэтому листинг ``backups/state`` (без слэша) вернёт и
    ``backups/stateful/...``, а листинг корня — вообще всё.
    """

    def __init__(self, keys):
        self.keys = list(keys)
        self.listed: list[str] = []
        self.deleted: list[str] = []

    def list_objects(self, prefix, client=None, config=None, **kw):
        self.listed.append(prefix)
        return [{"key": k, "size": 11} for k in self.keys if k.startswith(prefix)]

    def delete_object(self, key, client=None, config=None, **kw):
        self.deleted.append(key)
        if key in self.keys:
            self.keys.remove(key)

    def seams(self) -> dict:
        return {
            "list_objects": self.list_objects,
            "delete_object": self.delete_object,
            "client": MagicMock(),
            "config": _backup_config(),
        }


def _is_path_prefix(a: str, b: str) -> bool:
    """``a`` — ПУТЕВОЙ префикс ``b`` (``backups`` для ``backups/state``).

    ``backups/state`` путевым префиксом ``backups/stateful`` НЕ является:
    граница сегмента проверяется по ``/``, а не по подстроке.
    """
    return a == b or b.startswith(a.rstrip("/") + "/")


# ── 1. Ведущий (§7 п. 10): «сегодня + 15 суток» по ОБОИМ префиксам ────────


def test_rotation_at_snapshot_plus_15_days_spares_the_entire_client_set():
    """Без него: на 15-е сутки годовой клиентский набор уезжает в удаление вместе со state — молча, потому что удаление для ротации не авария, а работа."""
    state_dead = [
        _key(STATE, 15, "users.json"),
        _key(STATE, 15, "manifest.json"),
        _key(STATE, 40, "users.json"),
        _key(STATE, 364, "users.json"),
    ]
    state_alive = [
        _key(STATE, 14, "users.json"),   # ровно порог — живёт (семантика `<`)
        _key(STATE, 1, "users.json"),
        _key(STATE, 0, "users.json"),
    ]
    client_alive = [
        _key(CLIENT, 15, "demo.db"),     # тот самый набор со снимка SNAP
        _key(CLIENT, 15, "requisites.yaml"),
        _key(CLIENT, 15, "manifest.json"),
        _key(CLIENT, 40, "yarina.db"),
        _key(CLIENT, 200, "demo.db"),
        _key(CLIENT, 0, "demo.db"),
    ]
    bucket = _FakeBucket(state_dead + state_alive + client_alive + list(FOREIGN_KEYS))

    sb.rotate_all_backups(today=TODAY, **bucket.seams())

    client_hit = [k for k in client_alive if k in bucket.deleted]
    assert not client_hit, (
        f"ротация удалила {len(client_hit)} объектов клиентского набора "
        f"(срок хранения 365 суток, возраст самого старого — 200): {client_hit}"
    )
    state_missed = [k for k in state_dead if k not in bucket.deleted]
    assert not state_missed, (
        f"state старше 14 суток НЕ удалён ({len(state_missed)} шт.): {state_missed}"
    )
    state_wrongly_hit = [k for k in state_alive if k in bucket.deleted]
    assert not state_wrongly_hit, (
        f"удалён свежий state ({len(state_wrongly_hit)} шт.): {state_wrongly_hit}"
    )


def test_rotate_all_backups_reports_deletions_per_prefix_including_the_empty_one():
    """Без него: префикс, у которого удалять было нечего, пропадает из отчёта, и «нечего удалять» становится неотличимо от «не гонялся»."""
    dead = _key(STATE, 40, "users.json")
    bucket = _FakeBucket([dead, _key(CLIENT, 15, "demo.db")])

    out = sb.rotate_all_backups(today=TODAY, **bucket.seams())

    assert isinstance(out, dict), (
        f"ожидался dict «префикс -> список удалённого», пришло {type(out)!r}"
    )
    assert set(out) == {STATE, CLIENT}, (
        f"ключи отчёта {sorted(out)} не совпадают с объявленными префиксами "
        f"{[STATE, CLIENT]} — префикс без удалений обязан присутствовать с пустым списком"
    )
    assert out[STATE] == [dead], (
        f"в отчёте по {STATE} ожидался ровно ['{dead}'], пришло {out[STATE]}"
    )
    assert out[CLIENT] == [], (
        f"по {CLIENT} удалять было нечего, а в отчёте {out[CLIENT]}"
    )


# ── 2. Годовая граница — с ОБЕИХ сторон ───────────────────────────────────


@pytest.mark.parametrize("age_days", [0, 200, 364, 365])
def test_client_object_within_the_year_survives(age_days):
    """Без него: клиентский объект младше года удаляют, и «год» подменяется числом из чужого набора."""
    key = _key(CLIENT, age_days, "demo.db")
    bucket = _FakeBucket([key, _key(STATE, 40, "users.json")])

    sb.rotate_all_backups(today=TODAY, **bucket.seams())

    assert key not in bucket.deleted, (
        f"удалён клиентский объект возрастом {age_days} суток при сроке хранения 365: {key}"
    )


@pytest.mark.parametrize("age_days", [366, 400, 730])
def test_client_object_older_than_the_year_is_deleted(age_days):
    """Без него: клиентский набор не чистится вовсе — «год» читается как «навсегда», и бакет растёт без границы."""
    key = _key(CLIENT, age_days, "demo.db")
    bucket = _FakeBucket([key, _key(CLIENT, 10, "demo.db")])

    sb.rotate_all_backups(today=TODAY, **bucket.seams())

    assert key in bucket.deleted, (
        f"клиентский объект возрастом {age_days} суток пережил порог 365: {key}; "
        f"фактически удалено: {bucket.deleted}"
    )


# ── 3. Fail-closed на негодном списке ─────────────────────────────────────


BAD_RETENTIONS = {
    "повтор префикса": ((STATE, 14), (STATE, 14)),
    "два срока у одного префикса": ((STATE, 14), (STATE, 365)),
    "нулевой срок": ((STATE, 0), (CLIENT, 365)),
    "отрицательный срок": ((STATE, 14), (CLIENT, -1)),
    "вложенный префикс (родитель первым)": (("backups", 14), (STATE, 365)),
    "вложенный префикс (потомок первым)": (("backups/state/daily", 14), (STATE, 365)),
}


@pytest.mark.parametrize("bad", list(BAD_RETENTIONS.values()), ids=list(BAD_RETENTIONS))
def test_validate_retention_rejects_unusable_list(bad):
    """Без него: негодный список пролезает молча, и порог применяется не к тому префиксу."""
    with pytest.raises(sb.RetentionError):
        sb.validate_retention(bad)


@pytest.mark.parametrize("bad", list(BAD_RETENTIONS.values()), ids=list(BAD_RETENTIONS))
def test_rotate_all_backups_is_fail_closed_on_unusable_retention(bad):
    """Без него: «упало» без «ничего не удалено» — то есть часть объектов уехала ДО того, как список признали негодным."""
    bucket = _FakeBucket([
        _key(STATE, 40, "users.json"),
        _key(CLIENT, 400, "demo.db"),
        _key(CLIENT, 15, "demo.db"),
    ])

    with pytest.raises(sb.RetentionError):
        sb.rotate_all_backups(retention=bad, today=TODAY, **bucket.seams())

    assert bucket.deleted == [], (
        f"на негодном списке ротация успела удалить {len(bucket.deleted)} объектов: "
        f"{bucket.deleted} — это не fail-closed"
    )


def test_validate_retention_accepts_the_shipped_list():
    """Без него: сторожа выше зелены и на реализации, которая ругается вообще на всё, включая боевой список."""
    sb.validate_retention()
    sb.validate_retention(sb.RETENTION)


# ── 4. Корень бакета не листится ──────────────────────────────────────────


def test_every_listing_is_scoped_to_a_declared_prefix_with_trailing_slash():
    """Без него: «взяли весь бакет одним листингом и разобрали в памяти» — чужие ключи попадают в решение об удалении, а платит за это соседний набор."""
    bucket = _FakeBucket([
        _key(STATE, 40, "users.json"),
        _key(CLIENT, 400, "demo.db"),
        *FOREIGN_KEYS,
    ])

    sb.rotate_all_backups(today=TODAY, **bucket.seams())

    expected = {f"{p}/" for p, _ in sb.RETENTION}
    stray = [p for p in bucket.listed if p not in expected]
    assert not stray, (
        f"листинг ушёл мимо объявленных префиксов: {stray} "
        f"(допустимы только {sorted(expected)}; пустая строка = корень бакета)"
    )
    assert set(bucket.listed) == expected, (
        f"отлистаны {sorted(set(bucket.listed))}, а объявлено {sorted(expected)} — "
        f"каждый префикс обязан листиться отдельно"
    )


# ── 5. Чужой ключ не удаляется НИ ПРИ КАКОМ возрасте ──────────────────────


def test_keys_outside_declared_prefixes_are_never_deleted():
    """Без него: ротация вычищает соседний набор в том же бакете — `backups/media/...` и `backups/stateful/...` не принадлежат ни одному объявленному префиксу."""
    bucket = _FakeBucket([
        _key(STATE, 40, "users.json"),
        _key(CLIENT, 400, "demo.db"),
        *FOREIGN_KEYS,
        "backups/2019-01-01/loose.bin",
        "manifest.json",
    ])

    sb.rotate_all_backups(today=TODAY, **bucket.seams())

    outside = [
        k for k in bucket.deleted
        if not any(k.startswith(f"{p}/") for p, _ in sb.RETENTION)
    ]
    assert not outside, (
        f"удалено {len(outside)} ключей вне объявленных префиксов: {outside}"
    )


# ── 6. Литеральность и согласованность RETENTION — в ОБЕ стороны ──────────


def test_retention_constants_are_the_literals_the_owner_answered():
    """Без него: год превращается в любое другое число молча — ответ владельца §8 п. 2 теряется в коде."""
    assert sb.BACKUP_PREFIX == "backups/state", f"BACKUP_PREFIX = {sb.BACKUP_PREFIX!r}"
    assert sb.CLIENT_PREFIX == "backups/client", f"CLIENT_PREFIX = {sb.CLIENT_PREFIX!r}"
    assert sb.KEEP_DAYS == 14, f"KEEP_DAYS = {sb.KEEP_DAYS!r}"
    assert sb.CLIENT_KEEP_DAYS == 365, f"CLIENT_KEEP_DAYS = {sb.CLIENT_KEEP_DAYS!r}"
    assert sb.CLIENT_KEEP_DAYS > sb.KEEP_DAYS, (
        f"срок клиентского набора {sb.CLIENT_KEEP_DAYS} не больше срока state "
        f"{sb.KEEP_DAYS} — ответ владельца «год» потерян"
    )


def test_retention_list_matches_the_declared_pairs_in_both_directions():
    """Без него: константа объявлена, а в список ротации не попала — префикс не чистится вовсе либо чистится чужим сроком."""
    pairs = [tuple(x) for x in sb.RETENTION]
    assert sorted(pairs) == sorted([("backups/state", 14), ("backups/client", 365)]), (
        f"RETENTION = {pairs}, а объявлено ровно "
        f"[('backups/state', 14), ('backups/client', 365)]"
    )
    for name, prefix in (("BACKUP_PREFIX", sb.BACKUP_PREFIX),
                         ("CLIENT_PREFIX", sb.CLIENT_PREFIX)):
        assert any(p == prefix for p, _ in pairs), (
            f"{name} = {prefix!r} отсутствует в RETENTION"
        )
    for prefix, days in pairs:
        expected = 14 if prefix == "backups/state" else 365
        assert days == expected, (
            f"у префикса {prefix!r} срок {days}, ожидался {expected}"
        )


def test_retention_prefixes_are_unique_unnested_and_single_valued():
    """Без него: два срока у одного префикса или префикс, вложенный в другой, — и какой порог применится, решает порядок обхода."""
    pairs = [tuple(x) for x in sb.RETENTION]
    prefixes = [p for p, _ in pairs]
    dupes = sorted({p for p in prefixes if prefixes.count(p) > 1})
    assert not dupes, f"префикс встречается в RETENTION больше одного раза: {dupes}"
    for i, a in enumerate(prefixes):
        for j, b in enumerate(prefixes):
            if i != j:
                assert not _is_path_prefix(a, b), (
                    f"префикс {a!r} — путевой префикс {b!r}: один набор лежит внутри другого"
                )
    for prefix, days in pairs:
        assert isinstance(days, int) and days > 0, (
            f"срок хранения у {prefix!r} = {days!r}: не положительное целое"
        )


def test_rotate_all_backups_defaults_to_the_shipped_retention():
    """Без него: боевой вызов без аргументов ходит по своему, зашитому внутри списку, а RETENTION остаётся декорацией."""
    import inspect
    default = inspect.signature(sb.rotate_all_backups).parameters["retention"].default
    assert default == sb.RETENTION, (
        f"умолчание retention = {default!r}, а RETENTION = {sb.RETENTION!r}"
    )


# ── 6b. Одиночная ротация знает СВОЙ префикс — и в листинге, и в дате ─────


def test_rotate_old_backups_honours_the_given_prefix_end_to_end():
    """Без него: длина префикса зашита под `backups/state`, и у `backups/client` (на символ длиннее) дата вырезается из ключа мимо — ротация тихо не удаляет НИЧЕГО."""
    dead = _key(CLIENT, 400, "demo.db")
    alive = _key(CLIENT, 300, "demo.db")
    untouched = _key(STATE, 40, "users.json")
    bucket = _FakeBucket([dead, alive, untouched])

    out = sb.rotate_old_backups(
        prefix=CLIENT, keep_days=365, today=TODAY, **bucket.seams())

    assert bucket.listed == [f"{CLIENT}/"], (
        f"листинг шёл по {bucket.listed}, ожидался ровно ['{CLIENT}/']"
    )
    assert out == [dead], f"удалённым ожидался ровно ['{dead}'], пришло {out}"
    assert bucket.deleted == [dead], f"фактически удалено {bucket.deleted}"
    assert untouched not in bucket.deleted, f"задет чужой префикс: {untouched}"


# ── 7. Скрипт называет ОБА префикса ───────────────────────────────────────


def _load_script():
    spec = importlib.util.spec_from_file_location("state_backup_script_retention", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["state_backup_script_retention"] = mod
    spec.loader.exec_module(mod)
    return mod


def _run_main(monkeypatch, rotate):
    """Прогон ``scripts/state_backup.py::main`` со всеми швами подставными.

    ``rotate_old_backups`` и ``load_backup_config`` заглушены НАМЕРЕННО: у
    боевого ``rotate_old_backups`` умолчания швов связаны с настоящим
    ``r2_storage`` в момент определения функции, и вызов её из скрипта без
    аргументов уходит в живой R2 прямо под pytest.
    """
    mod = _load_script()
    result = sb.BackupResult(date="2026-08-21", uploaded=["users.json"], total_bytes=100)

    def _no_network(*a, **k):
        raise AssertionError(
            "скрипт зовёт ротацию мимо rotate_all_backups — живой вызов R2 под pytest")

    monkeypatch.setattr(sb, "run_backup", lambda root: result)
    monkeypatch.setattr(sb, "load_backup_config", _backup_config)
    monkeypatch.setattr(sb, "rotate_old_backups", _no_network)
    monkeypatch.setattr(sb, "rotate_all_backups", rotate)
    captured: dict = {}
    monkeypatch.setattr(
        mod, "send_telegram", lambda text: captured.setdefault("text", text) or True)
    rc = mod.main([])
    return rc, captured.get("text", "")


def test_script_names_every_prefix_with_its_keep_days(monkeypatch):
    """Без него: в отчёте одно число «удалено N объектов», и по какому набору прошлись 14 суток, а по какому 365, из него не узнать."""
    deleted = {STATE: [_key(STATE, 40, "users.json")], CLIENT: []}
    rc, text = _run_main(monkeypatch, lambda *a, **k: deleted)

    assert rc == 0, f"прогон вернул {rc} при успешном бэкапе и успешной ротации"
    for prefix, days in sb.RETENTION:
        assert re.search(re.escape(prefix) + r"[^\n]{0,40}?" + str(days), text), (
            f"в отчёте нет пары «{prefix} + {days}д» (называется КАЖДЫЙ префикс, "
            f"в том числе тот, где удалять было нечего). Отчёт:\n{text}"
        )


def test_script_distinguishes_rotation_that_never_ran_from_nothing_to_delete(monkeypatch):
    """Без него: упавшая ротация и ротация без работы дают ОДИН И ТОТ ЖЕ отчёт, и «бэкап не чистится» читается как «всё в порядке»."""
    empty = {p: [] for p, _ in sb.RETENTION}
    rc_empty, text_empty = _run_main(monkeypatch, lambda *a, **k: empty)

    def _boom(*a, **k):
        raise RuntimeError("list_objects failed")

    rc_boom, text_boom = _run_main(monkeypatch, _boom)

    assert rc_empty == 0, f"«нечего удалять» уронило прогон: rc={rc_empty}"
    assert rc_boom == 0, f"падение ротации уронило весь прогон: rc={rc_boom}"
    assert text_boom != text_empty, (
        "«ротация не гонялась» и «нечего удалять» дали одинаковый отчёт:\n" + text_empty
    )
