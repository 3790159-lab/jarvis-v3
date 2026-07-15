"""H1 security fix — /telegram/webhook must verify the Telegram secret token.

Audit 2026-07-15 finding H1: the webhook accepted any JSON and appended it to
``state/webhook_queue.jsonl`` with no ``X-Telegram-Bot-Api-Secret-Token`` check,
letting anyone forge admin updates. Fix: reject (403) every request whose secret
header does not match ``TELEGRAM_WEBHOOK_SECRET`` — and reject everything, fail
closed, when the secret is unset — writing nothing to the queue.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

SECRET = "s3cr3t-webhook-token"
UPDATE = {"update_id": 1, "message": {"text": "hi", "chat": {"id": "1"}}}


@pytest.fixture(scope="module")
def app_client():
    from app.main import app
    return TestClient(app, raise_server_exceptions=False)


def _queue_path(cwd: Path) -> Path:
    return cwd / "state" / "webhook_queue.jsonl"


def test_no_secret_configured_rejects_all(app_client, tmp_path, monkeypatch):
    monkeypatch.delenv("TELEGRAM_WEBHOOK_SECRET", raising=False)
    monkeypatch.chdir(tmp_path)
    r = app_client.post("/telegram/webhook", json=UPDATE)
    assert r.status_code == 403
    assert not _queue_path(tmp_path).exists()


def test_missing_secret_header_rejected(app_client, tmp_path, monkeypatch):
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", SECRET)
    monkeypatch.chdir(tmp_path)
    r = app_client.post("/telegram/webhook", json=UPDATE)
    assert r.status_code == 403
    assert not _queue_path(tmp_path).exists()


def test_wrong_secret_header_rejected(app_client, tmp_path, monkeypatch):
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", SECRET)
    monkeypatch.chdir(tmp_path)
    r = app_client.post(
        "/telegram/webhook",
        headers={"X-Telegram-Bot-Api-Secret-Token": "wrong"},
        json=UPDATE,
    )
    assert r.status_code == 403
    assert not _queue_path(tmp_path).exists()


def test_correct_secret_header_accepted_and_queued(app_client, tmp_path, monkeypatch):
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", SECRET)
    monkeypatch.chdir(tmp_path)
    r = app_client.post(
        "/telegram/webhook",
        headers={"X-Telegram-Bot-Api-Secret-Token": SECRET},
        json=UPDATE,
    )
    assert r.status_code == 200
    assert r.json().get("ok") is True
    q = _queue_path(tmp_path)
    assert q.exists()
    assert "update_id" in q.read_text(encoding="utf-8")
