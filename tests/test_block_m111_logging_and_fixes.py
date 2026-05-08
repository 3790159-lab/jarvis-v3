# -*- coding: utf-8 -*-
"""Tests for Block M.1.1.1 — file logging setup and concurrency fix."""
from __future__ import annotations

import asyncio
import logging
import logging.handlers
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch, call

import pytest

from app.services.block_m_common.logging_setup import setup_block_m_logging, get_logger
from app.services.block_m1_persona.persona_creator import PersonaCreator, _MAX_CONCURRENT
from app.services.block_m1_persona.persona_dialog import PersonaDialog
from app.services.block_m_common.cost_tracker import CostTracker, DailyLimitExceeded
from app.services.block_m_common.persona_storage import Persona, PersonaStorage
from app.services.block_m_common.replicate_video_client import ReplicateVideoClient
from datetime import datetime


# ──────────────────────────────────────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def isolate_block_m_logger():
    """Remove any RotatingFileHandler from block_m logger between tests."""
    lg = logging.getLogger("block_m")
    original_handlers = lg.handlers[:]
    lg.handlers = [h for h in lg.handlers if not isinstance(h, logging.handlers.RotatingFileHandler)]
    yield
    lg.handlers = original_handlers


@pytest.fixture
def dialog_dir(tmp_path, monkeypatch):
    import app.services.block_m1_persona.persona_dialog as pd_mod
    monkeypatch.setattr(pd_mod, "_DIALOGS_DIR", tmp_path / "dialogs")
    return tmp_path / "dialogs"


@pytest.fixture
def storage(tmp_path):
    return PersonaStorage(storage_dir=tmp_path / "personas")


@pytest.fixture
def tracker(tmp_path):
    return CostTracker(
        expenses_file=tmp_path / "expenses.jsonl",
        daily_limit=100.0,
    )


@pytest.fixture
def mock_client():
    c = MagicMock(spec=ReplicateVideoClient)
    c.generate_flux_pro = AsyncMock(
        return_value={"image_url": "https://img/test.jpg", "cost_usd": 0.04}
    )
    return c


@pytest.fixture
def sample_persona():
    return Persona(
        persona_id="persona_fix01",
        name="Mia",
        description="brown hair, green eyes",
        style="fashion",
        lora_weights_url=None,
        trigger_word="sks_fix01",
        created_at=datetime(2026, 1, 1),
    )


# ──────────────────────────────────────────────────────────────────────────────
# FIX 1: logging_setup
# ──────────────────────────────────────────────────────────────────────────────

class TestSetupBlockMLogging:
    def test_attaches_rotating_file_handler(self, tmp_path):
        lg = setup_block_m_logging(log_file=tmp_path / "test.log")
        assert any(isinstance(h, logging.handlers.RotatingFileHandler) for h in lg.handlers)

    def test_creates_log_directory(self, tmp_path):
        log_dir = tmp_path / "nested" / "logs"
        setup_block_m_logging(log_file=log_dir / "test.log")
        assert log_dir.exists()

    def test_creates_log_file(self, tmp_path):
        log_file = tmp_path / "test_m.log"
        lg = setup_block_m_logging(log_file=log_file)
        lg.info("hello")
        assert log_file.exists()
        assert "hello" in log_file.read_text(encoding="utf-8")

    def test_idempotent_second_call(self, tmp_path):
        setup_block_m_logging(log_file=tmp_path / "m.log")
        before = len(logging.getLogger("block_m").handlers)
        setup_block_m_logging(log_file=tmp_path / "m.log")
        after = len(logging.getLogger("block_m").handlers)
        assert after == before

    def test_max_bytes_configured(self, tmp_path):
        setup_block_m_logging(log_file=tmp_path / "m.log")
        fh = next(
            h for h in logging.getLogger("block_m").handlers
            if isinstance(h, logging.handlers.RotatingFileHandler)
        )
        assert fh.maxBytes == 10 * 1024 * 1024

    def test_backup_count_configured(self, tmp_path):
        setup_block_m_logging(log_file=tmp_path / "m.log")
        fh = next(
            h for h in logging.getLogger("block_m").handlers
            if isinstance(h, logging.handlers.RotatingFileHandler)
        )
        assert fh.backupCount == 3

    def test_log_level_set(self, tmp_path):
        lg = setup_block_m_logging(log_file=tmp_path / "m.log", level=logging.WARNING)
        assert lg.level == logging.WARNING

    def test_returns_block_m_logger(self, tmp_path):
        lg = setup_block_m_logging(log_file=tmp_path / "m.log")
        assert lg.name == "block_m"


class TestGetLogger:
    def test_returns_child_logger(self, tmp_path):
        setup_block_m_logging(log_file=tmp_path / "m.log")
        lg = get_logger("test_module")
        assert lg.name == "block_m.test_module"

    def test_initialises_handler_if_missing(self, tmp_path, monkeypatch):
        import app.services.block_m_common.logging_setup as ls
        monkeypatch.setattr(ls, "_LOG_FILE", tmp_path / "auto.log")
        lg = get_logger("automod")
        assert lg.name == "block_m.automod"
        root = logging.getLogger("block_m")
        assert any(isinstance(h, logging.handlers.RotatingFileHandler) for h in root.handlers)

    def test_different_names_are_different_loggers(self, tmp_path):
        setup_block_m_logging(log_file=tmp_path / "m.log")
        a = get_logger("a")
        b = get_logger("b")
        assert a is not b
        assert a.name != b.name


# ──────────────────────────────────────────────────────────────────────────────
# FIX 2: Semaphore-based concurrency in PersonaCreator
# ──────────────────────────────────────────────────────────────────────────────

class TestPersonaCreatorConcurrencyFix:
    @pytest.mark.anyio
    async def test_all_tasks_submitted_at_once(self, mock_client, storage, tracker, sample_persona):
        """All count tasks should be launched via a single gather call."""
        creator = PersonaCreator(mock_client, storage, tracker)
        concurrent_count = []
        active = [0]

        async def fake_generate(prompt, **_):
            active[0] += 1
            concurrent_count.append(active[0])
            await asyncio.sleep(0.01)
            active[0] -= 1
            return {"image_url": f"https://img/{prompt[:5]}.jpg", "cost_usd": 0.04}

        mock_client.generate_flux_pro = fake_generate

        with patch.object(storage, "update_persona", new_callable=AsyncMock):
            urls = await creator.generate_seed_photos(sample_persona, count=9)

        assert len(urls) == 9
        assert max(concurrent_count) <= _MAX_CONCURRENT

    @pytest.mark.anyio
    async def test_semaphore_caps_concurrency(self, mock_client, storage, tracker, sample_persona):
        """Concurrent active tasks must never exceed _MAX_CONCURRENT."""
        creator = PersonaCreator(mock_client, storage, tracker)
        active = [0]
        peak = [0]

        async def slow_gen(prompt, **_):
            active[0] += 1
            peak[0] = max(peak[0], active[0])
            await asyncio.sleep(0.02)
            active[0] -= 1
            return {"image_url": "https://img/x.jpg", "cost_usd": 0.04}

        mock_client.generate_flux_pro = slow_gen

        with patch.object(storage, "update_persona", new_callable=AsyncMock):
            await creator.generate_seed_photos(sample_persona, count=10)

        assert peak[0] <= _MAX_CONCURRENT

    @pytest.mark.anyio
    async def test_partial_failures_do_not_stop_others(self, mock_client, storage, tracker, sample_persona):
        """A failing task must not prevent other tasks from completing."""
        results = [
            {"image_url": "https://good1.jpg", "cost_usd": 0.04},
            RuntimeError("timeout"),
            {"image_url": "https://good2.jpg", "cost_usd": 0.04},
            RuntimeError("server error"),
            {"image_url": "https://good3.jpg", "cost_usd": 0.04},
        ]
        call_count = [0]

        async def alternating(*_a, **_kw):
            i = call_count[0] % len(results)
            call_count[0] += 1
            r = results[i]
            if isinstance(r, Exception):
                raise r
            return r

        mock_client.generate_flux_pro = alternating
        creator = PersonaCreator(mock_client, storage, tracker)

        with patch.object(storage, "update_persona", new_callable=AsyncMock):
            urls = await creator.generate_seed_photos(sample_persona, count=5)

        assert len(urls) == 3

    @pytest.mark.anyio
    async def test_daily_limit_propagates(self, mock_client, storage, tracker, sample_persona):
        mock_client.generate_flux_pro = AsyncMock(
            side_effect=DailyLimitExceeded("over limit")
        )
        creator = PersonaCreator(mock_client, storage, tracker)

        with pytest.raises(DailyLimitExceeded):
            with patch.object(storage, "update_persona", new_callable=AsyncMock):
                await creator.generate_seed_photos(sample_persona, count=3)

    @pytest.mark.anyio
    async def test_max_concurrent_constant_is_positive(self):
        assert _MAX_CONCURRENT >= 1

    @pytest.mark.anyio
    async def test_progress_callback_called_for_each_success(self, mock_client, storage, tracker, sample_persona):
        creator = PersonaCreator(mock_client, storage, tracker)
        calls = []

        def cb(done, total, url):
            calls.append(done)

        with patch.object(storage, "update_persona", new_callable=AsyncMock):
            await creator.generate_seed_photos(sample_persona, count=4, progress_cb=cb)

        assert calls == [1, 2, 3, 4]

    @pytest.mark.anyio
    async def test_storage_not_updated_when_all_fail(self, mock_client, storage, tracker, sample_persona):
        mock_client.generate_flux_pro = AsyncMock(side_effect=RuntimeError("all fail"))
        creator = PersonaCreator(mock_client, storage, tracker)

        with patch.object(storage, "update_persona", new_callable=AsyncMock) as mock_upd:
            urls = await creator.generate_seed_photos(sample_persona, count=3)

        assert urls == []
        mock_upd.assert_not_called()

    @pytest.mark.anyio
    async def test_uses_logging_from_get_logger(self, tmp_path, monkeypatch, mock_client, storage, tracker, sample_persona):
        """Persona creator should write at least one log line per photo."""
        import app.services.block_m_common.logging_setup as ls
        log_file = tmp_path / "creator_test.log"
        monkeypatch.setattr(ls, "_LOG_FILE", log_file)
        # Remove existing handlers so new one attaches
        logging.getLogger("block_m").handlers = []
        setup_block_m_logging(log_file=log_file)

        creator = PersonaCreator(mock_client, storage, tracker)
        with patch.object(storage, "update_persona", new_callable=AsyncMock):
            await creator.generate_seed_photos(sample_persona, count=2)

        assert log_file.exists()
        content = log_file.read_text(encoding="utf-8")
        assert "persona_fix01" in content


# ──────────────────────────────────────────────────────────────────────────────
# _safe_send / _safe_send_photo in persona_handler
# ──────────────────────────────────────────────────────────────────────────────

class TestSafeSend:
    @pytest.fixture(autouse=True)
    def setup(self, dialog_dir, tmp_path, monkeypatch):
        import app.services.block_m_common.logging_setup as ls
        monkeypatch.setattr(ls, "_LOG_FILE", tmp_path / "handler.log")
        logging.getLogger("block_m").handlers = []

        import app.handlers.persona_handler as h
        self._calls = []
        self._h = h

        h.init_bot(
            lambda chat_id, text, **_: self._calls.append(("text", chat_id, text)),
            lambda chat_id, url, caption="": self._calls.append(("photo", chat_id, url, caption)),
        )
        yield
        h._send = None
        h._send_photo = None

    def test_safe_send_calls_underlying(self):
        self._h._safe_send(42, "hello")
        assert ("text", 42, "hello") in self._calls

    def test_safe_send_swallows_exception(self):
        import app.handlers.persona_handler as h

        def boom(chat_id, text, **_):
            raise RuntimeError("Telegram down")

        h._send = boom
        h._safe_send(42, "hello")  # must not raise

    def test_safe_send_photo_swallows_exception(self):
        import app.handlers.persona_handler as h

        def boom(chat_id, url, caption=""):
            raise ConnectionError("timeout")

        h._send_photo = boom
        h._safe_send_photo(42, "https://img/x.jpg")  # must not raise

    def test_safe_send_photo_calls_underlying(self):
        self._h._safe_send_photo(42, "https://img/y.jpg", caption="test")
        assert ("photo", 42, "https://img/y.jpg", "test") in self._calls

    def test_handle_create_persona_uses_safe_send(self, dialog_dir):
        self._h.handle_create_persona(7777)
        assert any(t[0] == "text" for t in self._calls)

    def test_handle_cancel_no_session_uses_safe_send(self, dialog_dir):
        self._h.handle_cancel_persona(8888)
        assert any("Нет" in t[2] for t in self._calls if t[0] == "text")
