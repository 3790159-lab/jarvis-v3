# -*- coding: utf-8 -*-
"""Б2: самопроверка бэкапа — доказать, что объект ДОЕХАЛ, а не что заливка
вернула управление.

Урок, который здесь закрепляется (тот же, что в слоте обязательств
2026-07-24): логировать ОБА конца. «Посчитано» ≠ «доехало». До этой правки
успехом считался put_object, вернувший управление без исключения; таск с
таким критерием бежал бы зелёным и рапортовал «Загружено: 12 файлов», даже
если объекты не появились в бакете (не тот бакет, ретенция, политика
токена на запись без чтения). Ровно этого владелец и потребовал не
допустить: «таск, который успешно бежит, а бэкап молча никуда не идёт —
хуже отсутствия».

Проверка идёт ДРУГИМ вызовом API (list_objects_v2), а не тем же
put_object: это и есть второй конец. Сеть здесь не трогается — листинг
инжектируется.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from app.services import state_backup as sb
from app.services.r2_storage import R2Config, R2Error


def _config() -> R2Config:
    return R2Config(
        account_id="acct", access_key_id="ak", secret_access_key="sk",
        bucket="jarvis-state-backups",
        endpoint="https://acct.r2.cloudflarestorage.com", public_base_url="",
    )


def _tiny_state(tmp_path: Path) -> Path:
    root = tmp_path / "state"
    root.mkdir()
    (root / "users.json").write_text('{"1": {}}', encoding="utf-8")
    (root / "cost_tracking.json").write_text("{}", encoding="utf-8")
    return root


def _now():
    return datetime(2026, 8, 6, 3, 30, tzinfo=timezone.utc)


def _listing(*keys_with_sizes) -> list[dict]:
    return [{"key": k, "size": s, "last_modified": None} for k, s in keys_with_sizes]


# ── verify_uploaded: сам контракт ────────────────────────────────────────


def test_verify_uploaded_passes_when_every_object_is_listed_with_size():
    result = sb.BackupResult(
        date="2026-08-06", uploaded=["users.json", "cost_tracking.json"],
        manifest_key="backups/state/2026-08-06/manifest.json")

    def fake_list(prefix, *, client=None, config=None):
        return _listing(
            ("backups/state/2026-08-06/users.json", 9),
            ("backups/state/2026-08-06/cost_tracking.json", 2),
            ("backups/state/2026-08-06/manifest.json", 300),
        )

    problems = sb.verify_uploaded(result, list_objects=fake_list, config=_config())

    assert problems == []
    assert set(result.verified) == {
        "users.json", "cost_tracking.json", "manifest.json"}


def test_verify_uploaded_flags_object_missing_from_the_listing():
    """Главный сценарий Б2: заливка «прошла», объекта в бакете нет."""
    result = sb.BackupResult(
        date="2026-08-06", uploaded=["users.json", "cost_tracking.json"],
        manifest_key="backups/state/2026-08-06/manifest.json")

    def fake_list(prefix, *, client=None, config=None):
        return _listing(
            ("backups/state/2026-08-06/users.json", 9),
            ("backups/state/2026-08-06/manifest.json", 300),
        )

    problems = sb.verify_uploaded(result, list_objects=fake_list, config=_config())

    assert [p["rel_path"] for p in problems] == ["cost_tracking.json"]
    assert "листинг" in problems[0]["error"].lower()
    assert "cost_tracking.json" not in result.verified


def test_verify_uploaded_flags_zero_byte_object():
    """Объект есть, но пустой — «доехало» ложно: восстановиться нечем."""
    result = sb.BackupResult(
        date="2026-08-06", uploaded=["users.json"],
        manifest_key="backups/state/2026-08-06/manifest.json")

    def fake_list(prefix, *, client=None, config=None):
        return _listing(
            ("backups/state/2026-08-06/users.json", 0),
            ("backups/state/2026-08-06/manifest.json", 300),
        )

    problems = sb.verify_uploaded(result, list_objects=fake_list, config=_config())

    assert [p["rel_path"] for p in problems] == ["users.json"]
    assert "0" in problems[0]["error"] or "нулев" in problems[0]["error"].lower()


def test_verify_uploaded_flags_missing_manifest():
    """Без манифеста бэкап неверифицируем при восстановлении."""
    result = sb.BackupResult(
        date="2026-08-06", uploaded=["users.json"],
        manifest_key="backups/state/2026-08-06/manifest.json")

    def fake_list(prefix, *, client=None, config=None):
        return _listing(("backups/state/2026-08-06/users.json", 9))

    problems = sb.verify_uploaded(result, list_objects=fake_list, config=_config())

    assert [p["rel_path"] for p in problems] == ["manifest.json"]


def test_verify_uploaded_reports_listing_failure_instead_of_swallowing_it():
    """DEV-18: непроверяемый бэкап — это КРАСНОЕ, а не «ну и ладно».
    Если проверка не смогла отработать, мы не знаем ничего — и обязаны
    сказать это вслух, иначе вернулись к тому же молчаливому зелёному."""
    result = sb.BackupResult(
        date="2026-08-06", uploaded=["users.json"],
        manifest_key="backups/state/2026-08-06/manifest.json")

    def boom_list(prefix, *, client=None, config=None):
        raise R2Error("R2 list_objects failed: AccessDenied")

    problems = sb.verify_uploaded(result, list_objects=boom_list, config=_config())

    assert len(problems) == 1
    assert "AccessDenied" in problems[0]["error"]
    assert result.verified == []


def test_verify_uploaded_looks_only_at_todays_prefix():
    """Вчерашний объект того же имени не должен закрывать сегодняшний."""
    result = sb.BackupResult(
        date="2026-08-06", uploaded=["users.json"],
        manifest_key="backups/state/2026-08-06/manifest.json")
    seen = {}

    def fake_list(prefix, *, client=None, config=None):
        seen["prefix"] = prefix
        return _listing(
            ("backups/state/2026-08-05/users.json", 9),
            ("backups/state/2026-08-06/manifest.json", 300),
        )

    problems = sb.verify_uploaded(result, list_objects=fake_list, config=_config())

    assert seen["prefix"] == "backups/state/2026-08-06/"
    assert [p["rel_path"] for p in problems] == ["users.json"]


# ── run_backup: проверка встроена в прогон, а не опциональна ─────────────


def test_run_backup_is_not_ok_when_objects_never_landed(tmp_path):
    """Сквозной сценарий: upload_file «успешен», бакет пуст -> rc=1."""
    root = _tiny_state(tmp_path)
    uploaded_keys: list[str] = []

    def fake_upload(path, key=None, *, client=None, config=None):
        uploaded_keys.append(key)
        return f"https://example/{key}"

    def empty_list(prefix, *, client=None, config=None):
        return []

    result = sb.run_backup(root, now=_now(), upload_file=fake_upload,
                           list_objects=empty_list, config=_config())

    assert len(uploaded_keys) == 3  # 2 файла + манифест
    assert result.ok is False, "заливка без доказательства не может быть ok"
    assert len(result.failed) == 3
    assert result.verified == []


def test_run_backup_is_ok_when_listing_confirms_every_object(tmp_path):
    root = _tiny_state(tmp_path)

    def fake_upload(path, key=None, *, client=None, config=None):
        return f"https://example/{key}"

    def full_list(prefix, *, client=None, config=None):
        return _listing(
            ("backups/state/2026-08-06/users.json", 9),
            ("backups/state/2026-08-06/cost_tracking.json", 2),
            ("backups/state/2026-08-06/manifest.json", 300),
        )

    result = sb.run_backup(root, now=_now(), upload_file=fake_upload,
                           list_objects=full_list, config=_config())

    assert result.ok is True
    assert sorted(result.verified) == [
        "cost_tracking.json", "manifest.json", "users.json"]


def test_run_backup_does_not_verify_files_that_failed_to_upload(tmp_path):
    """Упавшая заливка уже посчитана провалом — не удваиваем её в отчёте."""
    root = _tiny_state(tmp_path)

    def half_broken_upload(path, key=None, *, client=None, config=None):
        if key.endswith("cost_tracking.json"):
            raise R2Error("boom")
        return f"https://example/{key}"

    def partial_list(prefix, *, client=None, config=None):
        return _listing(
            ("backups/state/2026-08-06/users.json", 9),
            ("backups/state/2026-08-06/manifest.json", 300),
        )

    result = sb.run_backup(root, now=_now(), upload_file=half_broken_upload,
                           list_objects=partial_list, config=_config())

    failed_paths = [f["rel_path"] for f in result.failed]
    assert failed_paths == ["cost_tracking.json"]
    assert result.ok is False


# ── отчёт оператору показывает ОБА конца ─────────────────────────────────


def test_format_backup_result_reports_verified_count():
    """В TG должно быть видно не «загружено», а «подтверждено листингом»."""
    result = sb.BackupResult(
        date="2026-08-06", uploaded=["users.json"], total_bytes=9,
        manifest_key="backups/state/2026-08-06/manifest.json",
        verified=["users.json", "manifest.json"])

    text = sb.format_backup_result(result)

    assert "Подтверждено листингом: 2" in text


def test_format_backup_result_shouts_when_nothing_was_verified():
    """Молчаливый зелёный запрещён: ноль подтверждений — это авария."""
    result = sb.BackupResult(
        date="2026-08-06", uploaded=["users.json"],
        failed=[{"rel_path": "users.json", "error": "нет в листинге бакета"}])

    text = sb.format_backup_result(result)

    assert "🚨" in text
    assert "нет в листинге бакета" in text
