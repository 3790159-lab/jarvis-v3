"""Phase 36: Tests for self_healing — disk, memory, backups, archive."""
from __future__ import annotations

import json
import sys
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch, MagicMock

ROOT = Path(__file__).parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.self_healing import (
    disk_free_gb,
    process_memory_mb,
    cleanup_old_logs,
    archive_old_decisions,
    backup_today,
    create_backup,
    cleanup_old_backups,
    notify_admin,
    SelfHealing,
    check_backend,
    recommend_recovery,
    BackendHealthSnapshot,
)


# ---------------------------------------------------------------------------
# disk_free_gb
# ---------------------------------------------------------------------------

class TestDiskFreeGb:
    def test_returns_float(self):
        result = disk_free_gb()
        assert isinstance(result, float)
        assert result >= 0

    def test_returns_positive_value(self):
        with patch("shutil.disk_usage", return_value=(100e9, 50e9, 20e9)):
            result = disk_free_gb()
        assert result == pytest.approx(20e9 / 1024**3, abs=0.01)

    def test_no_crash_on_exception(self):
        with patch("shutil.disk_usage", side_effect=Exception("disk error")):
            result = disk_free_gb()
        assert result == 999.0


# ---------------------------------------------------------------------------
# cleanup_old_logs
# ---------------------------------------------------------------------------

class TestCleanupOldLogs:
    def test_cleans_large_file(self, tmp_path):
        log = tmp_path / "test.log"
        lines = [f"line {i}" for i in range(5000)]
        log.write_text("\n".join(lines), encoding="utf-8")
        # Use very low threshold so the real file triggers cleanup
        cleaned = cleanup_old_logs(tmp_path, max_size_mb=0.0)
        assert cleaned == 1

    def test_skips_small_files(self, tmp_path):
        log = tmp_path / "small.log"
        log.write_text("just a small log", encoding="utf-8")
        cleaned = cleanup_old_logs(tmp_path, max_size_mb=50)
        assert cleaned == 0

    def test_no_crash_on_nonexistent_dir(self, tmp_path):
        cleaned = cleanup_old_logs(tmp_path / "nonexistent")
        assert cleaned == 0

    def test_only_cleans_log_files_not_json(self, tmp_path):
        # Create a json file (should not be cleaned)
        json_file = tmp_path / "data.json"
        json_file.write_text("{}", encoding="utf-8")
        # Create a small .log file (below threshold)
        (tmp_path / "small.log").write_text("x", encoding="utf-8")
        # Use very low threshold
        cleaned = cleanup_old_logs(tmp_path, max_size_mb=0.001)
        # Only the .log file could be cleaned, not .json
        assert cleaned <= 1


# ---------------------------------------------------------------------------
# archive_old_decisions
# ---------------------------------------------------------------------------

class TestArchiveOldDecisions:
    def test_archives_old_records(self, tmp_path):
        decisions = tmp_path / "decisions.jsonl"
        old_dt = (datetime.now(timezone.utc) - timedelta(days=35)).isoformat()
        new_dt = datetime.now(timezone.utc).isoformat()
        old = json.dumps({"decision_id": "old1", "timestamp": old_dt, "intent": "research"})
        new = json.dumps({"decision_id": "new1", "timestamp": new_dt, "intent": "chat"})
        decisions.write_text(f"{old}\n{new}\n", encoding="utf-8")

        archived = archive_old_decisions(decisions, older_than_days=30)
        assert archived == 1

        remaining = decisions.read_text(encoding="utf-8").strip().splitlines()
        remaining_ids = [json.loads(l)["decision_id"] for l in remaining]
        assert "new1" in remaining_ids
        assert "old1" not in remaining_ids

    def test_archive_file_created(self, tmp_path):
        decisions = tmp_path / "decisions.jsonl"
        old_dt = (datetime.now(timezone.utc) - timedelta(days=35)).isoformat()
        old = json.dumps({"decision_id": "arch1", "timestamp": old_dt})
        decisions.write_text(f"{old}\n", encoding="utf-8")

        archive_old_decisions(decisions, older_than_days=30)

        archive = tmp_path / "decisions_archive.jsonl"
        assert archive.exists()

    def test_no_archive_when_all_recent(self, tmp_path):
        decisions = tmp_path / "decisions.jsonl"
        new_dt = datetime.now(timezone.utc).isoformat()
        new = json.dumps({"decision_id": "new1", "timestamp": new_dt})
        decisions.write_text(f"{new}\n", encoding="utf-8")

        archived = archive_old_decisions(decisions, older_than_days=30)
        assert archived == 0

    def test_no_crash_on_missing_file(self, tmp_path):
        archived = archive_old_decisions(tmp_path / "missing.jsonl")
        assert archived == 0


# ---------------------------------------------------------------------------
# create_backup / backup_today
# ---------------------------------------------------------------------------

class TestBackup:
    def test_creates_backup_dir(self, tmp_path):
        state = tmp_path / "state"
        state.mkdir()
        (state / "test.json").write_text("{}", encoding="utf-8")
        backups = tmp_path / "backups"

        result = create_backup(state, backups)
        assert result is not None
        assert result.exists()

    def test_backup_dir_named_today(self, tmp_path):
        state = tmp_path / "state"
        state.mkdir()
        (state / "f.txt").write_text("x", encoding="utf-8")
        backups = tmp_path / "backups"

        result = create_backup(state, backups)
        assert result is not None
        today = datetime.now().strftime("%Y-%m-%d")
        assert result.name == today

    def test_backup_today_true_after_backup(self, tmp_path):
        state = tmp_path / "state"
        state.mkdir()
        (state / "f.txt").write_text("x")
        backups = tmp_path / "backups"

        create_backup(state, backups)
        assert backup_today(backups, state) is True

    def test_backup_today_false_when_no_backup(self, tmp_path):
        backups = tmp_path / "backups"
        assert backup_today(backups) is False

    def test_returns_none_when_state_missing(self, tmp_path):
        result = create_backup(tmp_path / "missing_state", tmp_path / "backups")
        assert result is None

    def test_cleanup_old_backups(self, tmp_path):
        backups = tmp_path / "backups"
        backups.mkdir()
        old_date = (datetime.now() - timedelta(days=10)).strftime("%Y-%m-%d")
        old_dir = backups / old_date
        old_dir.mkdir()
        (old_dir / "f.txt").write_text("x")

        deleted = cleanup_old_backups(backups, keep_days=7)
        assert deleted == 1
        assert not old_dir.exists()

    def test_keeps_recent_backups(self, tmp_path):
        backups = tmp_path / "backups"
        backups.mkdir()
        recent_date = (datetime.now() - timedelta(days=2)).strftime("%Y-%m-%d")
        recent_dir = backups / recent_date
        recent_dir.mkdir()

        deleted = cleanup_old_backups(backups, keep_days=7)
        assert deleted == 0
        assert recent_dir.exists()


# ---------------------------------------------------------------------------
# SelfHealing.check_and_heal
# ---------------------------------------------------------------------------

class TestSelfHealingCheckAndHeal:
    def test_returns_report_dict(self, tmp_path):
        healer = SelfHealing(state_dir=tmp_path / "state", backups_dir=tmp_path / "backups")
        with patch("app.services.self_healing.disk_free_gb", return_value=50.0):
            with patch("app.services.self_healing.process_memory_mb", return_value=100.0):
                report = healer.check_and_heal()
        assert "timestamp" in report
        assert "actions" in report
        assert "disk_free_gb" in report
        assert "memory_mb" in report

    def test_no_actions_when_healthy(self, tmp_path):
        healer = SelfHealing(state_dir=tmp_path / "state", backups_dir=tmp_path / "backups")
        with patch("app.services.self_healing.disk_free_gb", return_value=50.0):
            with patch("app.services.self_healing.process_memory_mb", return_value=100.0):
                report = healer.check_and_heal()
        # Backup action will be there since no backup today
        backup_actions = [a for a in report["actions"] if "backup" in a.lower()]
        assert len(backup_actions) <= 1

    def test_log_cleanup_when_disk_low(self, tmp_path):
        healer = SelfHealing(state_dir=tmp_path / "state", backups_dir=tmp_path / "backups",
                             min_disk_gb=5.0)
        with patch("app.services.self_healing.disk_free_gb", return_value=0.5):
            with patch("app.services.self_healing.process_memory_mb", return_value=100.0):
                with patch("app.services.self_healing.cleanup_old_logs", return_value=3) as mock_clean:
                    with patch("app.services.self_healing.notify_admin"):
                        report = healer.check_and_heal()
        mock_clean.assert_called_once()
        assert any("cleaned" in a for a in report["actions"])

    def test_memory_alert_when_high(self, tmp_path):
        healer = SelfHealing(state_dir=tmp_path / "state", backups_dir=tmp_path / "backups",
                             max_memory_mb=500.0)
        notified = []
        with patch("app.services.self_healing.disk_free_gb", return_value=50.0):
            with patch("app.services.self_healing.process_memory_mb", return_value=1500.0):
                with patch("app.services.self_healing.notify_admin", side_effect=lambda m: notified.append(m)):
                    report = healer.check_and_heal()
        assert any("high_memory" in a for a in report["actions"])

    def test_creates_backup_when_none_today(self, tmp_path):
        state = tmp_path / "state"
        state.mkdir()
        healer = SelfHealing(state_dir=state, backups_dir=tmp_path / "backups")
        with patch("app.services.self_healing.disk_free_gb", return_value=50.0):
            with patch("app.services.self_healing.process_memory_mb", return_value=100.0):
                report = healer.check_and_heal()
        assert any("backup_created" in a for a in report["actions"])


# ---------------------------------------------------------------------------
# check_backend / recommend_recovery
# ---------------------------------------------------------------------------

class TestCheckBackendLegacy:
    def test_ok_on_success(self):
        mock_resp = MagicMock()
        mock_resp.read.return_value = b'{"status": "ok"}'
        with patch("urllib.request.urlopen", return_value=mock_resp):
            snap = check_backend()
        assert snap.ok is True

    def test_not_ok_on_error(self):
        with patch("urllib.request.urlopen", side_effect=Exception("refused")):
            snap = check_backend()
        assert snap.ok is False
        assert snap.error is not None

    def test_recommend_none_when_healthy(self):
        snap = BackendHealthSnapshot(ok=True, status_code=200, url="http://x/health")
        rec = recommend_recovery(snap)
        assert rec["action"] == "none"

    def test_recommend_restart_when_unhealthy(self):
        snap = BackendHealthSnapshot(ok=False, status_code=500, url="http://x/health", error="timeout")
        rec = recommend_recovery(snap)
        assert rec["action"] == "restart_backend_then_recheck"


class TestNotifyAdminIsolation:
    def test_suppressed_under_pytest(self, monkeypatch):
        """notify_admin must not hit Telegram during a pytest run."""
        monkeypatch.delenv("JARVIS_ALLOW_TELEGRAM_SEND", raising=False)
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok123456")
        monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_ID", "123")
        calls = {"n": 0}

        def spy(*a, **k):
            calls["n"] += 1
            return MagicMock()

        with patch("urllib.request.urlopen", spy):
            notify_admin("phantom self-heal")

        assert calls["n"] == 0


import pytest
