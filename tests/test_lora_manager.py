"""Tests for Phase H3.5: LoRA Foundation."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.services.lora_manager import (
    LORA_TRAINING_COST,
    MIN_TRAINING_IMAGES,
    check_lora_status,
    delete_lora,
    generate_with_lora,
    get_lora,
    list_loras,
    start_lora_training,
    update_lora_status,
)

_PATCH_DB = "app.services.lora_manager._LORAS_PATH"
_FAKE_URLS = [f"https://example.com/img{i}.jpg" for i in range(15)]


@pytest.fixture()
def tmp_lora_path(tmp_path, monkeypatch):
    import app.services.lora_manager as lm
    lora_path = tmp_path / "loras" / "index.json"
    monkeypatch.setattr(lm, "_LORAS_PATH", lora_path)
    return lora_path


def _urlopen_ok(data: dict):
    m = MagicMock()
    m.read.return_value = json.dumps(data).encode()
    m.__enter__ = lambda s: s
    m.__exit__ = MagicMock(return_value=False)
    return m


class TestListLoras:
    def test_empty_when_no_file(self, tmp_lora_path):
        result = list_loras("user1")
        assert result == []

    def test_returns_only_user_entries(self, tmp_lora_path):
        tmp_lora_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_lora_path.write_text(json.dumps({
            "loras": [
                {"user_id": "alice", "name": "mymodel", "status": "succeeded"},
                {"user_id": "bob", "name": "bobmodel", "status": "training"},
            ]
        }), encoding="utf-8")
        result = list_loras("alice")
        assert len(result) == 1
        assert result[0]["name"] == "mymodel"

    def test_returns_empty_for_unknown_user(self, tmp_lora_path):
        tmp_lora_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_lora_path.write_text(json.dumps({"loras": []}), encoding="utf-8")
        assert list_loras("nobody") == []


class TestGetLora:
    def test_returns_lora_by_name(self, tmp_lora_path):
        tmp_lora_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_lora_path.write_text(json.dumps({
            "loras": [{"user_id": "alice", "name": "mymodel", "status": "succeeded"}]
        }), encoding="utf-8")
        lora = get_lora("alice", "mymodel")
        assert lora is not None
        assert lora["name"] == "mymodel"

    def test_returns_none_for_missing(self, tmp_lora_path):
        assert get_lora("alice", "nomodel") is None


class TestStartLoraTraining:
    def test_raises_on_too_few_images(self, tmp_lora_path, monkeypatch):
        monkeypatch.setenv("REPLICATE_API_KEY", "test_key")
        with pytest.raises(ValueError, match="at least"):
            start_lora_training("user1", "test", ["https://x.com/img.jpg"] * 5)

    def test_raises_without_api_key(self, tmp_lora_path, monkeypatch):
        monkeypatch.delenv("REPLICATE_API_KEY", raising=False)
        with pytest.raises(ValueError, match="REPLICATE_API_KEY"):
            start_lora_training("user1", "test", _FAKE_URLS)

    def test_saves_entry_to_db(self, tmp_lora_path, monkeypatch):
        monkeypatch.setenv("REPLICATE_API_KEY", "test_key")
        resp = _urlopen_ok({"id": "train_abc"})
        monkeypatch.setattr("urllib.request.urlopen", lambda req, timeout=None: resp)

        start_lora_training("user1", "MyLora", _FAKE_URLS, trigger_word="TRIGGER")
        loras = list_loras("user1")
        assert len(loras) == 1
        assert loras[0]["name"] == "MyLora"
        assert loras[0]["trigger_word"] == "TRIGGER"

    def test_returns_training_id(self, tmp_lora_path, monkeypatch):
        monkeypatch.setenv("REPLICATE_API_KEY", "test_key")
        resp = _urlopen_ok({"id": "train_xyz"})
        monkeypatch.setattr("urllib.request.urlopen", lambda req, timeout=None: resp)

        result = start_lora_training("user1", "MyLora", _FAKE_URLS)
        assert result["training_id"] == "train_xyz"

    def test_returns_cost(self, tmp_lora_path, monkeypatch):
        monkeypatch.setenv("REPLICATE_API_KEY", "test_key")
        resp = _urlopen_ok({"id": "train_xyz"})
        monkeypatch.setattr("urllib.request.urlopen", lambda req, timeout=None: resp)

        result = start_lora_training("user1", "MyLora", _FAKE_URLS)
        assert result["cost"] == LORA_TRAINING_COST

    def test_returns_estimated_time(self, tmp_lora_path, monkeypatch):
        monkeypatch.setenv("REPLICATE_API_KEY", "test_key")
        resp = _urlopen_ok({"id": "train_xyz"})
        monkeypatch.setattr("urllib.request.urlopen", lambda req, timeout=None: resp)

        result = start_lora_training("user1", "MyLora", _FAKE_URLS)
        assert result["estimated_time_minutes"] == 30

    def test_replaces_existing_entry_with_same_name(self, tmp_lora_path, monkeypatch):
        monkeypatch.setenv("REPLICATE_API_KEY", "test_key")
        resp1 = _urlopen_ok({"id": "train_1"})
        resp2 = _urlopen_ok({"id": "train_2"})
        calls = [resp1, resp2]
        idx = [0]

        def fake_urlopen(req, timeout=None):
            r = calls[idx[0] % len(calls)]
            idx[0] += 1
            return r

        monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
        start_lora_training("user1", "MyLora", _FAKE_URLS)
        start_lora_training("user1", "MyLora", _FAKE_URLS)
        assert len(list_loras("user1")) == 1


class TestUpdateLoraStatus:
    def test_updates_status(self, tmp_lora_path):
        tmp_lora_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_lora_path.write_text(json.dumps({
            "loras": [{"user_id": "alice", "name": "m", "status": "training"}]
        }), encoding="utf-8")
        update_lora_status("alice", "m", "succeeded")
        lora = get_lora("alice", "m")
        assert lora["status"] == "succeeded"


class TestDeleteLora:
    def test_deletes_existing(self, tmp_lora_path):
        tmp_lora_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_lora_path.write_text(json.dumps({
            "loras": [{"user_id": "alice", "name": "m", "status": "succeeded"}]
        }), encoding="utf-8")
        assert delete_lora("alice", "m") is True
        assert get_lora("alice", "m") is None

    def test_returns_false_for_missing(self, tmp_lora_path):
        assert delete_lora("alice", "no_such") is False


class TestCheckLoraStatus:
    def test_returns_pending_for_pending(self, monkeypatch):
        monkeypatch.setenv("REPLICATE_API_KEY", "test_key")
        result = check_lora_status("pending")
        assert result == "pending"

    def test_returns_error_for_error(self, monkeypatch):
        monkeypatch.setenv("REPLICATE_API_KEY", "test_key")
        result = check_lora_status("error")
        assert result == "error"

    def test_queries_api_for_real_id(self, monkeypatch):
        monkeypatch.setenv("REPLICATE_API_KEY", "test_key")
        resp = _urlopen_ok({"status": "succeeded"})
        monkeypatch.setattr("urllib.request.urlopen", lambda req, timeout=None: resp)
        result = check_lora_status("train_real_123")
        assert result == "succeeded"


class TestGenerateWithLora:
    def test_raises_if_lora_not_found(self, tmp_lora_path):
        with pytest.raises(ValueError, match="not found"):
            generate_with_lora("nonexistent", "some prompt", "user1")

    def test_raises_if_lora_not_ready(self, tmp_lora_path):
        tmp_lora_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_lora_path.write_text(json.dumps({
            "loras": [{"user_id": "alice", "name": "m", "status": "training",
                       "trigger_word": "X", "destination": "alice/m"}]
        }), encoding="utf-8")
        with pytest.raises(ValueError, match="not ready"):
            generate_with_lora("m", "test prompt", "alice")

    def test_generates_image_when_ready(self, tmp_lora_path, monkeypatch):
        tmp_lora_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_lora_path.write_text(json.dumps({
            "loras": [{"user_id": "alice", "name": "m", "status": "succeeded",
                       "trigger_word": "ALICE", "destination": "alice/m"}]
        }), encoding="utf-8")
        monkeypatch.setenv("REPLICATE_API_KEY", "test_key")

        submit_resp = _urlopen_ok({"id": "pred_gen_001"})
        poll_resp = _urlopen_ok({"status": "succeeded", "output": ["https://cdn.r.com/out.jpg"]})
        responses = [submit_resp, poll_resp]
        idx = [0]

        def fake_urlopen(req, timeout=None):
            r = responses[idx[0]]
            idx[0] += 1
            return r

        monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
        monkeypatch.setattr("time.sleep", lambda _: None)

        result = generate_with_lora("m", "bodybuilder on stage", "alice")
        assert result == "https://cdn.r.com/out.jpg"
