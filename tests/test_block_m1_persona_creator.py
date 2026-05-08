# -*- coding: utf-8 -*-
"""Tests for Block M.1.1 — persona creator dialog and FLUX seed generation."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.block_m1_persona.prompt_builder import PersonaPromptBuilder
from app.services.block_m1_persona.persona_creator import PersonaCreator
from app.services.block_m1_persona.persona_dialog import (
    PersonaDialog,
    STATUS_COLLECTING,
    STATUS_CONFIRMING,
    STATUS_GENERATING,
    STATUS_WAITING_SELECTION,
    STATUS_DONE,
    STATUS_CANCELLED,
)
from app.services.block_m_common.cost_tracker import CostTracker, DailyLimitExceeded
from app.services.block_m_common.persona_storage import Persona, PersonaStorage
from app.services.block_m_common.replicate_video_client import ReplicateVideoClient

# ──────────────────────────────────────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def dialog_dir(tmp_path, monkeypatch):
    """Redirect PersonaDialog storage to a temp directory."""
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
    client = MagicMock(spec=ReplicateVideoClient)
    client.generate_flux_pro = AsyncMock(return_value={"image_url": "https://img/test.jpg", "cost_usd": 0.04})
    return client


@pytest.fixture
def sample_persona(tmp_path):
    from datetime import datetime
    return Persona(
        persona_id="persona_test01",
        name="Sofia",
        description="young woman, 25, blue eyes, blonde hair",
        style="fashion",
        lora_weights_url=None,
        trigger_word="sks_persona_test01",
        created_at=datetime(2026, 1, 1),
    )


# ──────────────────────────────────────────────────────────────────────────────
# PersonaPromptBuilder
# ──────────────────────────────────────────────────────────────────────────────

class TestPersonaPromptBuilder:
    def test_default_count(self):
        prompts = PersonaPromptBuilder.build_seed_prompts("blue eyes woman", "fashion")
        assert len(prompts) == 20

    def test_custom_count(self):
        prompts = PersonaPromptBuilder.build_seed_prompts("brunette", "lifestyle", count=5)
        assert len(prompts) == 5

    def test_count_larger_than_combinations(self):
        prompts = PersonaPromptBuilder.build_seed_prompts("red hair", "beauty", count=30)
        assert len(prompts) == 30

    def test_all_unique(self):
        prompts = PersonaPromptBuilder.build_seed_prompts("blonde", "business", count=20)
        assert len(set(prompts)) == 20

    def test_description_in_prompts(self):
        desc = "green eyes, curly hair"
        prompts = PersonaPromptBuilder.build_seed_prompts(desc, "fashion", count=3)
        assert all(desc in p for p in prompts)

    def test_style_in_prompts(self):
        prompts = PersonaPromptBuilder.build_seed_prompts("tall woman", "lifestyle", count=3)
        assert any("lifestyle" in p for p in prompts)

    def test_quality_suffix_present(self):
        prompts = PersonaPromptBuilder.build_seed_prompts("dark hair", "fashion", count=5)
        assert all("photorealistic" in p for p in prompts)

    def test_no_nsfw_keywords(self):
        bad_words = {"nude", "naked", "explicit", "nsfw", "sex"}
        prompts = PersonaPromptBuilder.build_seed_prompts("woman", "fashion", count=20)
        for p in prompts:
            assert not any(bw in p.lower() for bw in bad_words)

    def test_count_one(self):
        prompts = PersonaPromptBuilder.build_seed_prompts("woman", "beauty", count=1)
        assert len(prompts) == 1
        assert isinstance(prompts[0], str)
        assert len(prompts[0]) > 20


# ──────────────────────────────────────────────────────────────────────────────
# PersonaCreator
# ──────────────────────────────────────────────────────────────────────────────

class TestPersonaCreator:
    @pytest.mark.anyio
    async def test_generate_returns_urls(self, mock_client, storage, tracker, sample_persona, tmp_path):
        storage._file = tmp_path / "personas.json"
        storage._dir.mkdir(parents=True, exist_ok=True)
        creator = PersonaCreator(mock_client, storage, tracker)

        with patch.object(storage, "update_persona", new_callable=AsyncMock):
            urls = await creator.generate_seed_photos(sample_persona, count=5)

        assert len(urls) == 5
        assert all(u == "https://img/test.jpg" for u in urls)

    @pytest.mark.anyio
    async def test_batches_of_five(self, mock_client, storage, tracker, sample_persona):
        creator = PersonaCreator(mock_client, storage, tracker)
        with patch.object(storage, "update_persona", new_callable=AsyncMock):
            await creator.generate_seed_photos(sample_persona, count=10)
        # gather called twice (batch 0-4, 5-9) → 10 calls total
        assert mock_client.generate_flux_pro.call_count == 10

    @pytest.mark.anyio
    async def test_progress_callback_called(self, mock_client, storage, tracker, sample_persona):
        creator = PersonaCreator(mock_client, storage, tracker)
        calls: list[tuple] = []

        def cb(done, total, url):
            calls.append((done, total, url))

        with patch.object(storage, "update_persona", new_callable=AsyncMock):
            await creator.generate_seed_photos(sample_persona, count=3, progress_cb=cb)

        assert len(calls) == 3
        assert calls[0] == (1, 3, "https://img/test.jpg")

    @pytest.mark.anyio
    async def test_partial_failure_continues(self, mock_client, storage, tracker, sample_persona):
        side_effects = [
            {"image_url": "https://img/1.jpg", "cost_usd": 0.04},
            RuntimeError("network error"),
            {"image_url": "https://img/3.jpg", "cost_usd": 0.04},
        ]
        mock_client.generate_flux_pro = AsyncMock(side_effect=side_effects)
        creator = PersonaCreator(mock_client, storage, tracker)

        with patch.object(storage, "update_persona", new_callable=AsyncMock):
            urls = await creator.generate_seed_photos(sample_persona, count=3)

        assert len(urls) == 2
        assert "https://img/1.jpg" in urls
        assert "https://img/3.jpg" in urls

    @pytest.mark.anyio
    async def test_daily_limit_exceeded_propagates(self, mock_client, storage, tracker, sample_persona):
        mock_client.generate_flux_pro = AsyncMock(
            side_effect=DailyLimitExceeded("limit reached")
        )
        creator = PersonaCreator(mock_client, storage, tracker)

        with pytest.raises(DailyLimitExceeded):
            with patch.object(storage, "update_persona", new_callable=AsyncMock):
                await creator.generate_seed_photos(sample_persona, count=3)

    @pytest.mark.anyio
    async def test_updates_persona_storage(self, mock_client, storage, tracker, sample_persona):
        creator = PersonaCreator(mock_client, storage, tracker)

        with patch.object(storage, "update_persona", new_callable=AsyncMock) as mock_update:
            await creator.generate_seed_photos(sample_persona, count=3)

        mock_update.assert_called_once()
        kwargs = mock_update.call_args
        assert "seed_photos" in kwargs.kwargs or "seed_photos" in str(kwargs)

    @pytest.mark.anyio
    async def test_cost_logged_per_image(self, mock_client, storage, tracker, sample_persona):
        creator = PersonaCreator(mock_client, storage, tracker)

        with patch.object(storage, "update_persona", new_callable=AsyncMock):
            await creator.generate_seed_photos(sample_persona, count=3)

        total = await tracker.get_today_total()
        assert abs(total - 3 * 0.04) < 0.001

    @pytest.mark.anyio
    async def test_no_urls_skips_storage_update(self, mock_client, storage, tracker, sample_persona):
        mock_client.generate_flux_pro = AsyncMock(side_effect=RuntimeError("all failed"))
        creator = PersonaCreator(mock_client, storage, tracker)

        with patch.object(storage, "update_persona", new_callable=AsyncMock) as mock_update:
            urls = await creator.generate_seed_photos(sample_persona, count=3)

        assert urls == []
        mock_update.assert_not_called()

    @pytest.mark.anyio
    async def test_sequential_concurrency(self, storage, tracker, sample_persona):
        """_MAX_CONCURRENT=1 enforces strictly sequential FLUX calls."""
        active = [0]
        active_at_call = []

        async def recording_flux_pro(prompt):
            active[0] += 1
            active_at_call.append(active[0])
            active[0] -= 1
            return {"image_url": "https://img/test.jpg", "cost_usd": 0.04}

        mock_client = MagicMock(spec=ReplicateVideoClient)
        mock_client.generate_flux_pro = recording_flux_pro
        creator = PersonaCreator(mock_client, storage, tracker)

        with patch("asyncio.sleep", new_callable=AsyncMock), \
             patch.object(storage, "update_persona", new_callable=AsyncMock):
            urls = await creator.generate_seed_photos(sample_persona, count=5)

        assert len(urls) == 5
        # active count at the moment each call was made must always be exactly 1
        assert active_at_call == [1, 1, 1, 1, 1]


# ──────────────────────────────────────────────────────────────────────────────
# PersonaDialog
# ──────────────────────────────────────────────────────────────────────────────

class TestPersonaDialog:
    def test_initial_state(self, dialog_dir):
        d = PersonaDialog("user1", "chat1")
        assert d.status == STATUS_COLLECTING
        assert d.step == 0
        assert d.name == ""
        assert d.description == ""
        assert d.style == ""
        assert d.persona_id is None
        assert d.photo_urls == []

    def test_persisted_on_creation(self, dialog_dir):
        d = PersonaDialog("user2", "chat2")
        path = dialog_dir / "user2.json"
        assert path.exists()
        data = json.loads(path.read_text())
        assert data["status"] == STATUS_COLLECTING

    def test_advance_name(self, dialog_dir):
        d = PersonaDialog("user3", "chat3")
        done, next_q = d.advance("Sofia")
        assert not done
        assert "Шаг 2/3" in next_q
        assert d.name == "Sofia"
        assert d.step == 1

    def test_advance_description(self, dialog_dir):
        d = PersonaDialog("user4", "chat4")
        d.advance("Sofia")
        done, next_q = d.advance("25 years old, blue eyes")
        assert not done
        assert "Шаг 3/3" in next_q
        assert d.description == "25 years old, blue eyes"

    def test_advance_style_reaches_confirming(self, dialog_dir):
        d = PersonaDialog("user5", "chat5")
        d.advance("Sofia")
        d.advance("25 years old, blue eyes")
        done, next_q = d.advance("fashion")
        assert done
        assert d.status == STATUS_CONFIRMING
        assert "Проверьте данные" in next_q
        assert "Sofia" in next_q

    def test_get_current_question_collecting(self, dialog_dir):
        d = PersonaDialog("user6", "chat6")
        assert "Шаг 1/3" in d.get_current_question()

    def test_get_current_question_confirming(self, dialog_dir):
        d = PersonaDialog("user7", "chat7")
        d.advance("Anna")
        d.advance("30, brown hair")
        d.advance("lifestyle")
        q = d.get_current_question()
        assert "Anna" in q
        assert "да / нет" in q

    def test_confirm_sets_generating(self, dialog_dir):
        d = PersonaDialog("user8", "chat8")
        d.advance("Maya")
        d.advance("dark hair")
        d.advance("beauty")
        d.confirm()
        assert d.status == STATUS_GENERATING

    def test_cancel_sets_cancelled(self, dialog_dir):
        d = PersonaDialog("user9", "chat9")
        d.cancel()
        assert d.status == STATUS_CANCELLED
        assert not d.is_active()

    def test_set_persona_id(self, dialog_dir):
        d = PersonaDialog("user10", "chat10")
        d.set_persona_id("persona_abc123")
        assert d.persona_id == "persona_abc123"
        data = json.loads((dialog_dir / "user10.json").read_text())
        assert data["persona_id"] == "persona_abc123"

    def test_set_generation_complete(self, dialog_dir):
        d = PersonaDialog("user11", "chat11")
        urls = ["https://img/1.jpg", "https://img/2.jpg"]
        d.set_generation_complete(urls)
        assert d.status == STATUS_WAITING_SELECTION
        assert d.photo_urls == urls

    def test_select_photo_valid(self, dialog_dir):
        d = PersonaDialog("user12", "chat12")
        d.set_generation_complete(["https://a.jpg", "https://b.jpg"])
        url = d.select_photo(2)
        assert url == "https://b.jpg"
        assert d.status == STATUS_DONE

    def test_select_photo_invalid_index(self, dialog_dir):
        d = PersonaDialog("user13", "chat13")
        d.set_generation_complete(["https://a.jpg"])
        assert d.select_photo(0) is None
        assert d.select_photo(2) is None
        assert d.status != STATUS_DONE

    def test_is_active_terminal_states(self, dialog_dir):
        d = PersonaDialog("user14", "chat14")
        assert d.is_active()
        d.cancel()
        assert not d.is_active()

    def test_find_active_returns_session(self, dialog_dir):
        d = PersonaDialog("user15", "chat15")
        d.advance("Lena")
        found = PersonaDialog.find_active("user15")
        assert found is not None
        assert found.name == "Lena"

    def test_find_active_no_session(self, dialog_dir):
        found = PersonaDialog.find_active("nonexistent_user")
        assert found is None

    def test_find_active_returns_none_for_done(self, dialog_dir):
        d = PersonaDialog("user16", "chat16")
        d.set_generation_complete(["https://x.jpg"])
        d.select_photo(1)
        assert PersonaDialog.find_active("user16") is None

    def test_find_active_returns_none_for_cancelled(self, dialog_dir):
        d = PersonaDialog("user17", "chat17")
        d.cancel()
        assert PersonaDialog.find_active("user17") is None

    def test_reload_from_disk(self, dialog_dir):
        d = PersonaDialog("user18", "chat18")
        d.advance("Vera")
        sid = d.session_id
        d2 = PersonaDialog("user18", "chat18", session_id=sid)
        assert d2.name == "Vera"
        assert d2.step == 1

    def test_advance_in_non_collecting_state_is_noop(self, dialog_dir):
        d = PersonaDialog("user19", "chat19")
        d.cancel()
        done, q = d.advance("anything")
        assert done
        assert q == ""

    def test_select_photo_negative_index(self, dialog_dir):
        d = PersonaDialog("user20", "chat20")
        d.set_generation_complete(["https://a.jpg", "https://b.jpg"])
        assert d.select_photo(-1) is None


# ──────────────────────────────────────────────────────────────────────────────
# persona_handler (unit with injected mocks)
# ──────────────────────────────────────────────────────────────────────────────

class TestPersonaHandler:
    @pytest.fixture(autouse=True)
    def setup_handler(self, dialog_dir):
        import app.handlers.persona_handler as h
        self._send_calls: list = []
        self._photo_calls: list = []

        def mock_send(chat_id, text, reply_markup=None):
            self._send_calls.append((chat_id, text))

        def mock_send_photo(chat_id, url, caption=""):
            self._photo_calls.append((chat_id, url, caption))

        h.init_bot(mock_send, mock_send_photo)
        self._handler = h
        yield
        # reset injected callables
        h._send = None
        h._send_photo = None

    def test_handle_create_persona_sends_first_question(self, dialog_dir):
        self._handler.handle_create_persona(1001)
        assert any("Шаг 1/3" in t for _, t in self._send_calls)

    def test_handle_create_persona_existing_session_warns(self, dialog_dir):
        self._handler.handle_create_persona(1002)
        self._send_calls.clear()
        self._handler.handle_create_persona(1002)
        assert any("уже есть активная" in t for _, t in self._send_calls)

    def test_handle_cancel_no_session(self, dialog_dir):
        self._handler.handle_cancel_persona(9999)
        assert any("Нет активной" in t for _, t in self._send_calls)

    def test_handle_cancel_cancels_session(self, dialog_dir):
        self._handler.handle_create_persona(2001)
        self._send_calls.clear()
        self._handler.handle_cancel_persona(2001)
        assert any("отменено" in t for _, t in self._send_calls)
        assert PersonaDialog.find_active("2001") is None

    def test_handle_answer_no_session_returns_false(self, dialog_dir):
        result = self._handler.handle_persona_answer(8888, "hello")
        assert result is False

    def test_handle_answer_collecting_advances(self, dialog_dir):
        self._handler.handle_create_persona(3001)
        result = self._handler.handle_persona_answer(3001, "Sofia")
        assert result is True
        dialog = PersonaDialog.find_active("3001")
        assert dialog.name == "Sofia"

    def test_handle_answer_confirming_cancel(self, dialog_dir):
        self._handler.handle_create_persona(4001)
        self._handler.handle_persona_answer(4001, "Maya")
        self._handler.handle_persona_answer(4001, "25, brown hair")
        self._handler.handle_persona_answer(4001, "fashion")
        # Now in CONFIRMING — say no
        self._send_calls.clear()
        result = self._handler.handle_persona_answer(4001, "нет")
        assert result is True
        assert PersonaDialog.find_active("4001") is None

    def test_handle_answer_waiting_selection_invalid(self, dialog_dir):
        d = PersonaDialog("5001", "5001")
        d.advance("X")
        d.advance("Y")
        d.advance("Z")
        d.confirm()
        d.set_generation_complete(["https://a.jpg", "https://b.jpg"])

        result = self._handler.handle_persona_answer(5001, "abc")
        assert result is True
        assert any("Введите номер" in t for _, t in self._send_calls)

    def test_handle_answer_waiting_selection_out_of_range(self, dialog_dir):
        d = PersonaDialog("5002", "5002")
        d.advance("X")
        d.advance("Y")
        d.advance("Z")
        d.confirm()
        d.set_generation_complete(["https://a.jpg"])

        result = self._handler.handle_persona_answer(5002, "99")
        assert result is True
        assert any("Неверный номер" in t for _, t in self._send_calls)

    def test_handle_answer_waiting_selection_valid(self, dialog_dir):
        d = PersonaDialog("5003", "5003")
        d.advance("Mia")
        d.advance("tall, red hair")
        d.advance("beauty")
        d.confirm()
        d.set_persona_id("persona_mia01")
        d.set_generation_complete(["https://photo1.jpg", "https://photo2.jpg"])

        result = self._handler.handle_persona_answer(5003, "1")
        assert result is True
        assert any("photo1" in url for _, url, _ in self._photo_calls)
        assert PersonaDialog.find_active("5003") is None

    def test_handle_answer_generating_state(self, dialog_dir):
        d = PersonaDialog("6001", "6001")
        d.advance("Q")
        d.advance("W")
        d.advance("E")
        d.confirm()

        result = self._handler.handle_persona_answer(6001, "anything")
        assert result is True
        assert any("Идёт генерация" in t for _, t in self._send_calls)
