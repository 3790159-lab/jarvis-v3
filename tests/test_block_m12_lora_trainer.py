# -*- coding: utf-8 -*-
"""Tests for Block M.1.2 — LoRA training pipeline."""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.block_m_common.cost_tracker import CostTracker, DailyLimitExceeded
from app.services.block_m_common.persona_storage import Persona, PersonaStorage
from app.services.block_m_common.replicate_video_client import ReplicateVideoClient
from app.services.block_m_common.video_queue import (
    STATUS_DONE,
    STATUS_FAILED,
    STATUS_PENDING,
    STATUS_RUNNING,
    VideoJob,
    VideoQueue,
)
from app.services.block_m1_persona.lora_trainer import LoRATrainer, MIN_PHOTOS, COST_ESTIMATE_USD


# ──────────────────────────────────────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def reset_queue():
    VideoQueue.reset()
    yield
    VideoQueue.reset()


@pytest.fixture
def storage(tmp_path):
    return PersonaStorage(storage_dir=tmp_path / "personas")


@pytest.fixture
def tracker(tmp_path):
    return CostTracker(expenses_file=tmp_path / "expenses.jsonl", daily_limit=100.0)


@pytest.fixture
def queue(tmp_path):
    return VideoQueue(queue_file=tmp_path / "vq.json")


@pytest.fixture
def mock_client():
    client = MagicMock(spec=ReplicateVideoClient)
    client.train_flux_lora = AsyncMock(
        return_value={"weights_url": "https://weights.com/lora.safetensors", "cost_usd": 2.00}
    )
    return client


@pytest.fixture
def state_file(tmp_path):
    return tmp_path / "lora_jobs.json"


@pytest.fixture
def trainer(mock_client, storage, tracker, queue, state_file):
    return LoRATrainer(mock_client, storage, tracker, queue, state_file=state_file)


@pytest.fixture
async def persona_with_photos(storage):
    """A persona in storage with exactly MIN_PHOTOS seed photos."""
    p = await storage.create_persona("Sofia", "25, blue eyes, blonde", "fashion")
    await storage.update_persona(
        p.persona_id,
        seed_photos=[f"https://img.com/{i}.jpg" for i in range(MIN_PHOTOS)],
    )
    return await storage.get_persona(p.persona_id)


# ──────────────────────────────────────────────────────────────────────────────
# 1. start_training validates persona exists
# ──────────────────────────────────────────────────────────────────────────────

@pytest.mark.anyio
async def test_start_training_validates_persona_exists(trainer):
    with pytest.raises(ValueError, match="not found"):
        await trainer.start_training("persona_doesnotexist")


# ──────────────────────────────────────────────────────────────────────────────
# 2. start_training requires minimum photos
# ──────────────────────────────────────────────────────────────────────────────

@pytest.mark.anyio
async def test_start_training_requires_minimum_photos(trainer, storage):
    p = await storage.create_persona("Ana", "desc", "style")
    await storage.update_persona(p.persona_id, seed_photos=["url1", "url2", "url3"])

    with pytest.raises(ValueError, match="minimum"):
        with patch("threading.Thread"):
            await trainer.start_training(p.persona_id)


# ──────────────────────────────────────────────────────────────────────────────
# 3. start_training uses persona seed_photos by default
# ──────────────────────────────────────────────────────────────────────────────

@pytest.mark.anyio
async def test_start_training_uses_persona_seed_photos_by_default(
    trainer, storage, persona_with_photos
):
    with patch("threading.Thread"):
        job_id = await trainer.start_training(persona_with_photos.persona_id)

    job = await trainer._queue.get_status(job_id)
    assert job is not None
    assert job.params["photo_count"] == MIN_PHOTOS


# ──────────────────────────────────────────────────────────────────────────────
# 4. start_training logs cost to tracker (via _run_training)
# ──────────────────────────────────────────────────────────────────────────────

@pytest.mark.anyio
async def test_start_training_logs_cost_to_tracker(
    trainer, storage, tracker, queue, persona_with_photos
):
    job_id = f"lora_{uuid.uuid4().hex[:8]}"
    await queue.submit(VideoJob(
        job_id=job_id, job_type="lora_training",
        persona_id=persona_with_photos.persona_id, params={},
        status=STATUS_PENDING, user_chat_id=0, created_at=datetime.utcnow(),
    ))
    await trainer._run_training(
        job_id, persona_with_photos, persona_with_photos.seed_photos, 1000, 0, None
    )
    total = await tracker.get_today_total()
    assert total == pytest.approx(2.00)


# ──────────────────────────────────────────────────────────────────────────────
# 5. start_training creates a VideoJob in the queue
# ──────────────────────────────────────────────────────────────────────────────

@pytest.mark.anyio
async def test_start_training_creates_videojob(trainer, storage, persona_with_photos):
    with patch("threading.Thread"):
        job_id = await trainer.start_training(persona_with_photos.persona_id)

    assert job_id.startswith("lora_")
    job = await trainer._queue.get_status(job_id)
    assert job is not None
    assert job.job_type == "lora_training"
    assert job.persona_id == persona_with_photos.persona_id
    assert job.status == STATUS_PENDING


# ──────────────────────────────────────────────────────────────────────────────
# 6. start_training respects daily limit
# ──────────────────────────────────────────────────────────────────────────────

@pytest.mark.anyio
async def test_start_training_respects_daily_limit(mock_client, storage, queue, state_file, persona_with_photos):
    exhausted_tracker = CostTracker(
        expenses_file=storage._file.parent / "exp.jsonl",
        daily_limit=1.0,
    )
    await exhausted_tracker.log_expense("fill", 1.0, None)

    t = LoRATrainer(mock_client, storage, exhausted_tracker, queue, state_file=state_file)
    with pytest.raises(DailyLimitExceeded):
        with patch("threading.Thread"):
            await t.start_training(persona_with_photos.persona_id)


# ──────────────────────────────────────────────────────────────────────────────
# 7. check_status returns pending for a new job
# ──────────────────────────────────────────────────────────────────────────────

@pytest.mark.anyio
async def test_check_status_returns_pending_for_new_job(trainer, storage, persona_with_photos):
    with patch("threading.Thread"):
        job_id = await trainer.start_training(persona_with_photos.persona_id)

    status = await trainer.check_status(persona_with_photos.persona_id)
    assert status is not None
    assert status["status"] == "pending"
    assert status["progress_pct"] == 0
    assert status["weights_url"] is None


# ──────────────────────────────────────────────────────────────────────────────
# 8. check_status returns done with weights_url
# ──────────────────────────────────────────────────────────────────────────────

@pytest.mark.anyio
async def test_check_status_returns_done_with_weights_url(
    trainer, storage, queue, state_file, persona_with_photos
):
    job_id = f"lora_{uuid.uuid4().hex[:8]}"
    await queue.submit(VideoJob(
        job_id=job_id, job_type="lora_training",
        persona_id=persona_with_photos.persona_id, params={},
        status=STATUS_PENDING, user_chat_id=0, created_at=datetime.utcnow(),
    ))
    await queue._mark_done(job_id, {"weights_url": "https://weights/lora.safetensors"})

    jobs = {persona_with_photos.persona_id: job_id}
    trainer._save_jobs(jobs)

    await storage.set_lora_weights(
        persona_with_photos.persona_id, "https://weights/lora.safetensors", "sks_test"
    )

    status = await trainer.check_status(persona_with_photos.persona_id)
    assert status["status"] == "done"
    assert status["progress_pct"] == 100
    assert status["weights_url"] == "https://weights/lora.safetensors"


# ──────────────────────────────────────────────────────────────────────────────
# 9. check_status returns failed with error
# ──────────────────────────────────────────────────────────────────────────────

@pytest.mark.anyio
async def test_check_status_returns_failed_with_error(
    trainer, storage, queue, state_file, persona_with_photos
):
    job_id = f"lora_{uuid.uuid4().hex[:8]}"
    await queue.submit(VideoJob(
        job_id=job_id, job_type="lora_training",
        persona_id=persona_with_photos.persona_id, params={},
        status=STATUS_PENDING, user_chat_id=0, created_at=datetime.utcnow(),
    ))
    await queue._mark_failed(job_id, "GPU out of memory")

    trainer._save_jobs({persona_with_photos.persona_id: job_id})

    status = await trainer.check_status(persona_with_photos.persona_id)
    assert status["status"] == "failed"
    assert status["error"] == "GPU out of memory"
    assert status["weights_url"] is None


# ──────────────────────────────────────────────────────────────────────────────
# 10. check_status for unknown persona returns None
# ──────────────────────────────────────────────────────────────────────────────

@pytest.mark.anyio
async def test_check_status_for_unknown_persona_returns_none(trainer):
    result = await trainer.check_status("persona_ghost_xyz")
    assert result is None


# ──────────────────────────────────────────────────────────────────────────────
# 11. list_trained excludes pending / in-progress jobs
# ──────────────────────────────────────────────────────────────────────────────

@pytest.mark.anyio
async def test_list_trained_excludes_pending_jobs(trainer, storage, persona_with_photos):
    with patch("threading.Thread"):
        await trainer.start_training(persona_with_photos.persona_id)

    trained = await trainer.list_trained()
    assert len(trained) == 0


# ──────────────────────────────────────────────────────────────────────────────
# 12. list_trained includes completed personas
# ──────────────────────────────────────────────────────────────────────────────

@pytest.mark.anyio
async def test_list_trained_includes_completed(trainer, storage, persona_with_photos):
    await storage.set_lora_weights(
        persona_with_photos.persona_id,
        "https://weights/lora.safetensors",
        "sks_test",
    )
    trained = await trainer.list_trained()
    assert len(trained) == 1
    assert trained[0]["persona_id"] == persona_with_photos.persona_id
    assert trained[0]["weights_url"] == "https://weights/lora.safetensors"


# ──────────────────────────────────────────────────────────────────────────────
# 13. cancel_training marks job as failed (cancelled)
# ──────────────────────────────────────────────────────────────────────────────

@pytest.mark.anyio
async def test_cancel_training_marks_job_cancelled(trainer, storage, queue, persona_with_photos):
    with patch("threading.Thread"):
        job_id = await trainer.start_training(persona_with_photos.persona_id)

    cancelled = await trainer.cancel_training(persona_with_photos.persona_id)
    assert cancelled is True

    job = await queue.get_status(job_id)
    assert job.status == STATUS_FAILED
    assert "Cancelled" in (job.error or "")


# ──────────────────────────────────────────────────────────────────────────────
# 14. cancel_training returns False for a completed job
# ──────────────────────────────────────────────────────────────────────────────

@pytest.mark.anyio
async def test_cancel_training_returns_false_for_done_job(
    trainer, storage, queue, state_file, persona_with_photos
):
    job_id = f"lora_{uuid.uuid4().hex[:8]}"
    await queue.submit(VideoJob(
        job_id=job_id, job_type="lora_training",
        persona_id=persona_with_photos.persona_id, params={},
        status=STATUS_PENDING, user_chat_id=0, created_at=datetime.utcnow(),
    ))
    await queue._mark_done(job_id, {"weights_url": "https://w/lora.safetensors"})
    trainer._save_jobs({persona_with_photos.persona_id: job_id})

    result = await trainer.cancel_training(persona_with_photos.persona_id)
    assert result is False


# ──────────────────────────────────────────────────────────────────────────────
# 15. _run_training calls set_lora_weights on success
# ──────────────────────────────────────────────────────────────────────────────

@pytest.mark.anyio
async def test_training_completion_calls_set_lora_weights(
    trainer, storage, queue, persona_with_photos
):
    job_id = f"lora_{uuid.uuid4().hex[:8]}"
    await queue.submit(VideoJob(
        job_id=job_id, job_type="lora_training",
        persona_id=persona_with_photos.persona_id, params={},
        status=STATUS_PENDING, user_chat_id=0, created_at=datetime.utcnow(),
    ))

    with patch.object(storage, "set_lora_weights", new_callable=AsyncMock) as mock_set:
        await trainer._run_training(
            job_id, persona_with_photos, persona_with_photos.seed_photos, 1000, 0, None
        )

    mock_set.assert_called_once()
    call_args = mock_set.call_args
    assert call_args.args[0] == persona_with_photos.persona_id
    assert "safetensors" in call_args.args[1]

    job = await queue.get_status(job_id)
    assert job.status == STATUS_DONE


# ──────────────────────────────────────────────────────────────────────────────
# 16. _run_training marks job failed on error
# ──────────────────────────────────────────────────────────────────────────────

@pytest.mark.anyio
async def test_training_failure_marks_job_failed(
    trainer, storage, queue, mock_client, persona_with_photos
):
    mock_client.train_flux_lora = AsyncMock(side_effect=RuntimeError("GPU timeout"))
    job_id = f"lora_{uuid.uuid4().hex[:8]}"
    await queue.submit(VideoJob(
        job_id=job_id, job_type="lora_training",
        persona_id=persona_with_photos.persona_id, params={},
        status=STATUS_PENDING, user_chat_id=0, created_at=datetime.utcnow(),
    ))

    await trainer._run_training(
        job_id, persona_with_photos, persona_with_photos.seed_photos, 1000, 0, None
    )

    job = await queue.get_status(job_id)
    assert job.status == STATUS_FAILED
    assert "GPU timeout" in job.error


# ──────────────────────────────────────────────────────────────────────────────
# 17. Concurrent training for different personas is supported
# ──────────────────────────────────────────────────────────────────────────────

@pytest.mark.anyio
async def test_concurrent_training_different_personas(trainer, storage):
    personas = []
    for i in range(2):
        p = await storage.create_persona(f"P{i}", f"desc{i}", "style")
        await storage.update_persona(
            p.persona_id,
            seed_photos=[f"https://img.com/{i}_{j}.jpg" for j in range(MIN_PHOTOS)],
        )
        personas.append(await storage.get_persona(p.persona_id))

    with patch("threading.Thread"):
        job_id_0 = await trainer.start_training(personas[0].persona_id)
        job_id_1 = await trainer.start_training(personas[1].persona_id)

    assert job_id_0 != job_id_1

    job0 = await trainer._queue.get_status(job_id_0)
    job1 = await trainer._queue.get_status(job_id_1)
    assert job0 is not None and job1 is not None
    assert job0.persona_id != job1.persona_id


# ──────────────────────────────────────────────────────────────────────────────
# 18. Handler: handle_train_lora parses persona_id and sends confirmation
# ──────────────────────────────────────────────────────────────────────────────

def test_handler_train_lora_parses_persona_id(tmp_path, monkeypatch):
    import app.handlers.persona_handler as h
    import app.services.block_m1_persona.lora_trainer as lt_mod

    sent = []
    h.init_bot(lambda cid, txt, **kw: sent.append(txt), lambda *a, **kw: None)

    persona = MagicMock()
    persona.name = "Sofia"
    persona.seed_photos = [f"url{i}" for i in range(MIN_PHOTOS)]
    persona.lora_weights_url = None

    with patch.object(h, "_run_async", return_value=persona):
        h.handle_train_lora(1001, "persona_abc123")

    assert 1001 in h._lora_pending_confirm
    assert h._lora_pending_confirm[1001] == "persona_abc123"
    full = " ".join(sent)
    assert "да" in full.lower() or "нет" in full.lower()

    # cleanup
    h._lora_pending_confirm.pop(1001, None)


# ──────────────────────────────────────────────────────────────────────────────
# 19. Handler: handle_train_lora rejects unknown persona
# ──────────────────────────────────────────────────────────────────────────────

def test_handler_train_lora_rejects_unknown_persona():
    import app.handlers.persona_handler as h

    sent = []
    h.init_bot(lambda cid, txt, **kw: sent.append(txt), lambda *a, **kw: None)

    with patch.object(h, "_run_async", return_value=None):
        h.handle_train_lora(1002, "persona_notexist")

    assert any("не найдена" in t for t in sent)
    assert 1002 not in h._lora_pending_confirm


# ──────────────────────────────────────────────────────────────────────────────
# 20. Handler: handle_lora_status displays progress
# ──────────────────────────────────────────────────────────────────────────────

def test_handler_lora_status_displays_progress(monkeypatch):
    import app.handlers.persona_handler as h

    sent = []
    h.init_bot(lambda cid, txt, **kw: sent.append(txt), lambda *a, **kw: None)

    fake_status = {"status": "training", "progress_pct": 50, "weights_url": None, "error": None}

    with patch.object(h, "_run_async", return_value=fake_status):
        h.handle_lora_status(1003, "persona_xyz")

    full = " ".join(sent)
    assert "50" in full or "процессе" in full.lower() or "training" in full.lower()
