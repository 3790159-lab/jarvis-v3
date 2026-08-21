# -*- coding: utf-8 -*-
"""Tests for the DEV-16 daily state/ backup to Cloudflare R2.

Money-safe: R2 is free within limits (like app.services.r2_storage media
uploads), but no real network/boto3 client is ever constructed here — every
client/upload/list/delete/download seam is injected or monkeypatched.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from app.services import state_backup as sb
from app.services.r2_storage import R2ConfigError, R2Error


# ── helpers ───────────────────────────────────────────────────────────────


def _state_tree(tmp_path: Path) -> Path:
    root = tmp_path / "state"
    (root / "personas").mkdir(parents=True)
    (root / "personas" / "persona_001").mkdir()
    (root / "jarvis_brain").mkdir()
    (root / "dev_tasks").mkdir()
    (root / "dev_tasks" / "abc123").mkdir()
    (root / "expenses").mkdir()

    (root / "users.json").write_text('{"1": {}}', encoding="utf-8")
    (root / "cost_tracking.json").write_text("{}", encoding="utf-8")
    (root / "daily_metrics.json").write_text("{}", encoding="utf-8")
    (root / "regress_baseline.json").write_text("{}", encoding="utf-8")
    (root / "personas" / "personas.json").write_text("[]", encoding="utf-8")
    (root / "personas" / "expenses.jsonl").write_text("", encoding="utf-8")
    (root / "personas" / "persona_001" / "arcface_centroid.npy").write_bytes(b"\x00" * 16)
    (root / "expenses" / "persona_video.jsonl").write_text("", encoding="utf-8")
    (root / "jarvis_brain" / "action_queue_v6_4.json").write_text("{}", encoding="utf-8")
    (root / "dev_tasks" / "abc123.json").write_text("{}", encoding="utf-8")
    (root / "dev_tasks" / "log.jsonl").write_text("", encoding="utf-8")
    (root / "dev_tasks" / "abc123" / "report.md").write_text("# report", encoding="utf-8")

    # Secrets/credentials — must NEVER be picked up by the allowlist.
    (root / "ig_accounts.json").write_text('{"tok": "secret"}', encoding="utf-8")
    (root / "api_keys.json").write_text('{"key": "secret"}', encoding="utf-8")
    (root / "google_oauth_token.json").write_text('{"token": "secret"}', encoding="utf-8")
    # Non-critical noise — must not be picked up either.
    (root / "some_scratch_file.tmp").write_text("noise", encoding="utf-8")

    return root


def _listing_from_puts(client):
    """Листинг, отвечающий тем, что клиент реально принял на запись.

    Тесты ниже — про ЗАЛИВКУ. Листинг в них чужая механика, и подменять её надо
    ЯВНО: `run_backup` с коммита 19679bdf дозванивается до `verify_uploaded`, а
    тому умолчанием приходит настоящий `r2_storage.list_objects`. Настоящий
    пагинатор с mock-клиентом уходил в бесконечную петлю и съедал хост —
    спека 2026-08-21-r2-paginator-page-cap, сторожа в tests/test_r2_storage.py.

    Ключи берём из записанных вызовов `put_object`, а не из ожиданий теста:
    так подмена отвечает на вопрос «что доехало» тем же, что «что отправили», и
    не начинает молча соглашаться с проверкой, которую должна кормить.
    """
    recorded = client   # снаружи: у `_list` свой параметр `client` от вызова

    def _list(prefix, client=None, config=None):
        return [{"key": call.kwargs["Key"], "size": 1}
                for call in recorded.put_object.call_args_list
                if call.kwargs.get("Key", "").startswith(prefix)]

    return _list


def _backup_config():
    from app.services.r2_storage import R2Config
    return R2Config(
        account_id="acct", access_key_id="ak", secret_access_key="sk",
        bucket="jarvis-state-backups", endpoint="https://acct.r2.cloudflarestorage.com",
        public_base_url="",
    )


# ── discover_backup_files ────────────────────────────────────────────────


def test_discover_backup_files_finds_all_critical_categories(tmp_path):
    root = _state_tree(tmp_path)
    files = sb.discover_backup_files(root)
    rels = {p.relative_to(root).as_posix() for p in files}

    assert "users.json" in rels
    assert "cost_tracking.json" in rels
    assert "personas/personas.json" in rels
    assert "personas/expenses.jsonl" in rels
    assert "personas/persona_001/arcface_centroid.npy" in rels
    assert "expenses/persona_video.jsonl" in rels
    assert "jarvis_brain/action_queue_v6_4.json" in rels
    assert "dev_tasks/abc123.json" in rels
    assert "dev_tasks/log.jsonl" in rels
    assert "dev_tasks/abc123/report.md" in rels
    assert "regress_baseline.json" in rels
    assert "daily_metrics.json" in rels


def test_discover_backup_files_excludes_secrets_and_noise(tmp_path):
    root = _state_tree(tmp_path)
    files = sb.discover_backup_files(root)
    rels = {p.relative_to(root).as_posix() for p in files}

    assert "ig_accounts.json" not in rels
    assert "api_keys.json" not in rels
    assert "google_oauth_token.json" not in rels
    assert "some_scratch_file.tmp" not in rels
    assert not any(r.endswith(".env") for r in rels)


def test_discover_backup_files_missing_category_is_skipped_not_error(tmp_path):
    root = tmp_path / "state"
    root.mkdir()
    (root / "users.json").write_text("{}", encoding="utf-8")
    files = sb.discover_backup_files(root)
    assert [p.name for p in files] == ["users.json"]


def test_discover_backup_files_no_duplicates(tmp_path):
    root = _state_tree(tmp_path)
    files = sb.discover_backup_files(root)
    assert len(files) == len(set(files))


# ── sha256_file / build_manifest ─────────────────────────────────────────


def test_sha256_file_matches_known_hash(tmp_path):
    p = tmp_path / "f.txt"
    p.write_bytes(b"hello world")
    import hashlib
    expected = hashlib.sha256(b"hello world").hexdigest()
    assert sb.sha256_file(p) == expected


def test_build_manifest_shape(tmp_path):
    root = _state_tree(tmp_path)
    files = sb.discover_backup_files(root)
    manifest = sb.build_manifest(files, root, "2026-07-15T00:00:00+00:00")

    assert manifest["count"] == len(files)
    assert manifest["generated_at"] == "2026-07-15T00:00:00+00:00"
    assert manifest["total_bytes"] > 0
    rel_paths = {f["rel_path"] for f in manifest["files"]}
    assert "users.json" in rel_paths
    entry = next(f for f in manifest["files"] if f["rel_path"] == "users.json")
    assert entry["sha256"] == sb.sha256_file(root / "users.json")
    assert entry["size"] == (root / "users.json").stat().st_size


# ── run_backup ────────────────────────────────────────────────────────────


def test_run_backup_uploads_every_discovered_file_and_manifest(tmp_path):
    root = _state_tree(tmp_path)
    client = MagicMock()
    client.put_object = MagicMock(return_value={})
    now = datetime(2026, 7, 15, 3, 0, tzinfo=timezone.utc)

    result = sb.run_backup(root, now=now, client=client, config=_backup_config(),
                           list_objects=_listing_from_puts(client))

    assert result.date == "2026-07-15"
    assert result.failed == []
    assert result.ok is True
    n_files = len(sb.discover_backup_files(root))
    assert len(result.uploaded) == n_files
    # +1 for the manifest.json upload itself
    assert client.put_object.call_count == n_files + 1
    assert result.manifest_key == "backups/state/2026-07-15/manifest.json"

    keys = {c.kwargs["Key"] for c in client.put_object.call_args_list}
    assert "backups/state/2026-07-15/users.json" in keys
    assert "backups/state/2026-07-15/manifest.json" in keys
    assert "backups/state/2026-07-15/personas/persona_001/arcface_centroid.npy" in keys


def test_run_backup_per_file_failure_is_isolated(tmp_path):
    root = _state_tree(tmp_path)
    client = MagicMock()

    def _put(Bucket, Key, Body, ContentType):  # noqa: N803 - matches boto3 kwarg names
        if Key.endswith("cost_tracking.json"):
            raise RuntimeError("boom")
        return {}

    client.put_object = MagicMock(side_effect=_put)
    now = datetime(2026, 7, 15, tzinfo=timezone.utc)

    result = sb.run_backup(root, now=now, client=client, config=_backup_config(),
                           list_objects=_listing_from_puts(client))

    assert result.ok is False
    assert len(result.failed) == 1
    assert result.failed[0]["rel_path"] == "cost_tracking.json"
    # every other file still uploaded despite the one failure
    assert "users.json" in result.uploaded
    assert "cost_tracking.json" not in result.uploaded


def test_run_backup_manifest_excludes_failed_files(tmp_path):
    root = _state_tree(tmp_path)
    client = MagicMock()
    captured_manifest_body = {}

    def _put(Bucket, Key, Body, ContentType):  # noqa: N803
        if Key.endswith("cost_tracking.json"):
            raise RuntimeError("boom")
        if Key.endswith("manifest.json"):
            captured_manifest_body["text"] = Body.read().decode("utf-8")
        return {}

    client.put_object = MagicMock(side_effect=_put)
    now = datetime(2026, 7, 15, tzinfo=timezone.utc)

    sb.run_backup(root, now=now, client=client, config=_backup_config(),
                  list_objects=_listing_from_puts(client))

    import json
    manifest = json.loads(captured_manifest_body["text"])
    rel_paths = {f["rel_path"] for f in manifest["files"]}
    assert "cost_tracking.json" not in rel_paths
    assert "users.json" in rel_paths


def test_run_backup_empty_state_dir_uploads_only_manifest(tmp_path):
    root = tmp_path / "state"
    root.mkdir()
    client = MagicMock()
    client.put_object = MagicMock(return_value={})
    now = datetime(2026, 7, 15, tzinfo=timezone.utc)

    result = sb.run_backup(root, now=now, client=client, config=_backup_config(),
                           list_objects=_listing_from_puts(client))

    assert result.uploaded == []
    assert result.ok is True
    assert client.put_object.call_count == 1  # manifest only


# ── list_backup_dates / rotate_old_backups ───────────────────────────────


def test_list_backup_dates_extracts_unique_sorted_dates():
    def _fake_list(prefix, client=None, config=None):
        return [
            {"key": "backups/state/2026-07-01/users.json"},
            {"key": "backups/state/2026-07-01/manifest.json"},
            {"key": "backups/state/2026-07-03/users.json"},
            {"key": "backups/state/2026-06-30/users.json"},
        ]

    dates = sb.list_backup_dates(list_objects=_fake_list, client=MagicMock(), config=_backup_config())
    assert dates == ["2026-06-30", "2026-07-01", "2026-07-03"]


def test_list_backup_dates_empty_bucket():
    dates = sb.list_backup_dates(list_objects=lambda *a, **k: [], client=MagicMock(), config=_backup_config())
    assert dates == []


def test_rotate_old_backups_deletes_only_older_than_keep_days():
    objs = [
        {"key": "backups/state/2026-06-01/users.json"},   # 44 days old -> delete
        {"key": "backups/state/2026-07-01/users.json"},   # 14 days old -> keep (== cutoff boundary)
        {"key": "backups/state/2026-07-10/users.json"},   # 5 days old -> keep
    ]
    deleted = []

    def _fake_list(prefix, client=None, config=None):
        return objs

    def _fake_delete(key, client=None, config=None):
        deleted.append(key)

    today = datetime(2026, 7, 15, tzinfo=timezone.utc)
    out = sb.rotate_old_backups(
        keep_days=14, today=today,
        list_objects=_fake_list, delete_object=_fake_delete,
        client=MagicMock(), config=_backup_config(),
    )

    assert out == ["backups/state/2026-06-01/users.json"]
    assert deleted == out


def test_rotate_old_backups_nothing_to_delete():
    def _fake_list(prefix, client=None, config=None):
        return [{"key": "backups/state/2026-07-14/users.json"}]

    def _fake_delete(key, client=None, config=None):
        raise AssertionError("must not delete a recent backup")

    today = datetime(2026, 7, 15, tzinfo=timezone.utc)
    out = sb.rotate_old_backups(
        keep_days=14, today=today,
        list_objects=_fake_list, delete_object=_fake_delete,
        client=MagicMock(), config=_backup_config(),
    )
    assert out == []


# ── restore_file / verify_restored_file ──────────────────────────────────


def test_restore_file_builds_dated_key_and_downloads(tmp_path):
    calls = {}

    def _fake_download(key, dest, client=None, config=None):
        calls["key"] = key
        calls["dest"] = dest
        Path(dest).parent.mkdir(parents=True, exist_ok=True)
        Path(dest).write_text("restored", encoding="utf-8")
        return Path(dest)

    dest = tmp_path / "restored" / "users.json"
    out = sb.restore_file(
        "users.json", dest, date="2026-07-15",
        download_file=_fake_download, client=MagicMock(), config=_backup_config(),
    )

    assert calls["key"] == "backups/state/2026-07-15/users.json"
    assert out == dest
    assert dest.read_text(encoding="utf-8") == "restored"


def test_verify_restored_file_matches():
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "f.bin"
        p.write_bytes(b"payload")
        assert sb.verify_restored_file(p, sb.sha256_file(p)) is True


def test_verify_restored_file_mismatch_detected(tmp_path):
    p = tmp_path / "f.bin"
    p.write_bytes(b"payload")
    assert sb.verify_restored_file(p, "0" * 64) is False


# ── formatting (pure text helpers for /backup_now, /backup_status) ───────


def test_format_backup_result_ok():
    result = sb.BackupResult(date="2026-07-15", uploaded=["users.json", "cost_tracking.json"],
                              manifest_key="backups/state/2026-07-15/manifest.json", total_bytes=2048)
    text = sb.format_backup_result(result)
    assert "2026-07-15" in text
    assert "2" in text
    assert "manifest" in text.lower()


def test_format_backup_result_with_failures_flags_them():
    result = sb.BackupResult(date="2026-07-15", uploaded=["users.json"],
                              failed=[{"rel_path": "cost_tracking.json", "error": "boom"}])
    text = sb.format_backup_result(result)
    assert "cost_tracking.json" in text
    assert "boom" in text
    assert "⚠️" in text or "ошиб" in text.lower()


def test_format_backup_status_empty():
    text = sb.format_backup_status([])
    assert "не найдено" in text.lower() or "нет" in text.lower()


def test_format_backup_status_reports_range_and_policy():
    text = sb.format_backup_status(["2026-07-01", "2026-07-14", "2026-07-15"], keep_days=14)
    assert "2026-07-15" in text  # latest
    assert "2026-07-01" in text  # oldest
    assert "14" in text


# ── config loader (separate private bucket, no public URL needed) ────────


def test_load_backup_config_raises_on_missing_env(monkeypatch):
    for var in ("R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY",
                "R2_ENDPOINT", "R2_BACKUP_BUCKET"):
        monkeypatch.delenv(var, raising=False)
    with pytest.raises(R2ConfigError):
        sb.load_backup_config()


def test_load_backup_config_reads_env(monkeypatch):
    monkeypatch.setenv("R2_ACCOUNT_ID", "acct")
    monkeypatch.setenv("R2_ACCESS_KEY_ID", "ak")
    monkeypatch.setenv("R2_SECRET_ACCESS_KEY", "sk")
    monkeypatch.setenv("R2_ENDPOINT", "https://acct.r2.cloudflarestorage.com")
    monkeypatch.setenv("R2_BACKUP_BUCKET", "jarvis-state-backups")

    cfg = sb.load_backup_config()
    assert cfg.bucket == "jarvis-state-backups"
    assert cfg.public_base_url == ""


def test_run_backup_without_injected_config_raises_configerror(tmp_path, monkeypatch):
    for var in ("R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY",
                "R2_ENDPOINT", "R2_BACKUP_BUCKET"):
        monkeypatch.delenv(var, raising=False)
    root = _state_tree(tmp_path)
    with pytest.raises(R2ConfigError):
        sb.run_backup(root, client=MagicMock())
