"""Tests for Phase H3.1: Photo Studio Foundation."""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest


# ──────────────────────────────────────────────────────────────
# replicate_models
# ──────────────────────────────────────────────────────────────

from app.services.replicate_models import (
    REPLICATE_MODELS,
    get_cheapest_for_task,
    get_model,
    get_model_cost,
    get_models_for_task,
    list_model_names,
)


class TestReplicateModels:
    def test_catalog_not_empty(self):
        assert len(REPLICATE_MODELS) >= 8

    def test_get_model_known(self):
        m = get_model("flux_pro")
        assert m is not None
        assert "url" in m
        assert "cost" in m
        assert "best_for" in m

    def test_get_model_unknown_returns_none(self):
        assert get_model("nonexistent_model_xyz") is None

    def test_flux_pro_cost(self):
        assert get_model("flux_pro")["cost"] == 0.04

    def test_flux_pro_ultra_cost(self):
        assert get_model("flux_pro_ultra")["cost"] == 0.06

    def test_face_swap_basic_cost(self):
        assert get_model("face_swap_basic")["cost"] == 0.005

    def test_gfpgan_cost(self):
        assert get_model("gfpgan")["cost"] == 0.002

    def test_list_model_names(self):
        names = list_model_names()
        assert "flux_pro" in names
        assert "flux_pro_ultra" in names
        assert "face_swap_basic" in names
        assert "gfpgan" in names
        assert "img2img" in names

    def test_get_models_for_task_people(self):
        models = get_models_for_task("people")
        names = [m["name"] for m in models]
        assert "flux_pro_ultra" in names

    def test_get_models_for_task_face_polish(self):
        models = get_models_for_task("face_polish")
        names = [m["name"] for m in models]
        assert "gfpgan" in names

    def test_get_models_for_unknown_task(self):
        assert get_models_for_task("totally_unknown_task_xyz") == []

    def test_get_cheapest_for_task_quick_swap(self):
        m = get_cheapest_for_task("quick_swap")
        assert m is not None
        assert m["name"] == "face_swap_basic"

    def test_get_cheapest_for_unknown_task(self):
        assert get_cheapest_for_task("no_such_task") is None

    def test_get_model_cost_known(self):
        assert get_model_cost("flux_pro") == 0.04

    def test_get_model_cost_unknown(self):
        assert get_model_cost("no_such") == 0.0

    def test_all_models_have_url(self):
        for name, cfg in REPLICATE_MODELS.items():
            assert "url" in cfg, f"{name} missing url"

    def test_all_models_have_cost(self):
        for name, cfg in REPLICATE_MODELS.items():
            assert "cost" in cfg, f"{name} missing cost"
            assert cfg["cost"] >= 0, f"{name} cost < 0"

    def test_lora_training_model_present(self):
        m = get_model("flux_lora_training")
        assert m is not None
        assert m["cost"] == 10.0


# ──────────────────────────────────────────────────────────────
# image_library
# ──────────────────────────────────────────────────────────────

from app.services.image_library import (
    count_images_for_user,
    delete_image,
    get_image,
    list_images,
    list_images_by_mode,
    save_image_metadata,
    total_cost_for_user,
)


@pytest.fixture()
def tmp_library(tmp_path, monkeypatch):
    """Redirect library path to a temp dir for each test."""
    import app.services.image_library as lib_mod
    lib_path = tmp_path / "index.jsonl"
    monkeypatch.setattr(lib_mod, "_LIBRARY_PATH", lib_path)
    return lib_path


class TestImageLibrary:
    def test_save_returns_id(self, tmp_library):
        iid = save_image_metadata(
            url="https://example.com/img.jpg",
            prompt="test prompt",
            mode="flux_pro",
            cost=0.04,
            user_id="user1",
        )
        assert iid.startswith("img_")

    def test_save_creates_file(self, tmp_library):
        save_image_metadata("https://x.com/a.jpg", "p", "flux_pro", 0.04, "u1")
        assert tmp_library.exists()

    def test_list_images_empty_when_no_file(self, tmp_library):
        assert list_images("u1") == []

    def test_list_images_returns_user_entries(self, tmp_library):
        save_image_metadata("https://x.com/1.jpg", "p1", "m", 0.04, "alice")
        save_image_metadata("https://x.com/2.jpg", "p2", "m", 0.04, "bob")
        results = list_images("alice")
        assert len(results) == 1
        assert results[0]["user_id"] == "alice"

    def test_list_images_limit(self, tmp_library):
        for i in range(10):
            save_image_metadata(f"https://x.com/{i}.jpg", f"p{i}", "m", 0.04, "alice")
        results = list_images("alice", limit=3)
        assert len(results) == 3

    def test_list_images_newest_first(self, tmp_library):
        iid1 = save_image_metadata("https://x.com/1.jpg", "p1", "m", 0.04, "alice")
        iid2 = save_image_metadata("https://x.com/2.jpg", "p2", "m", 0.04, "alice")
        results = list_images("alice")
        assert results[0]["id"] == iid2  # newest first

    def test_get_image_found(self, tmp_library):
        iid = save_image_metadata("https://x.com/a.jpg", "p", "m", 0.04, "alice")
        img = get_image(iid)
        assert img is not None
        assert img["id"] == iid

    def test_get_image_not_found(self, tmp_library):
        assert get_image("img_nonexistent") is None

    def test_get_image_no_file(self, tmp_library):
        assert get_image("img_xxx") is None

    def test_total_cost_for_user(self, tmp_library):
        save_image_metadata("https://x.com/1.jpg", "p", "m", 0.04, "alice")
        save_image_metadata("https://x.com/2.jpg", "p", "m", 0.06, "alice")
        save_image_metadata("https://x.com/3.jpg", "p", "m", 0.10, "bob")
        total = total_cost_for_user("alice")
        assert total == pytest.approx(0.10, abs=0.001)

    def test_total_cost_zero_when_no_file(self, tmp_library):
        assert total_cost_for_user("alice") == 0.0

    def test_list_images_by_mode(self, tmp_library):
        save_image_metadata("https://x.com/1.jpg", "p", "restaurant", 0.04, "alice")
        save_image_metadata("https://x.com/2.jpg", "p", "party", 0.06, "alice")
        results = list_images_by_mode("alice", "restaurant")
        assert len(results) == 1
        assert results[0]["mode"] == "restaurant"

    def test_delete_image(self, tmp_library):
        iid = save_image_metadata("https://x.com/a.jpg", "p", "m", 0.04, "alice")
        assert delete_image(iid) is True
        assert get_image(iid) is None

    def test_delete_nonexistent_returns_false(self, tmp_library):
        assert delete_image("img_no_such") is False

    def test_count_images_for_user(self, tmp_library):
        for _ in range(5):
            save_image_metadata("https://x.com/a.jpg", "p", "m", 0.04, "alice")
        save_image_metadata("https://x.com/b.jpg", "p", "m", 0.04, "bob")
        assert count_images_for_user("alice") == 5
        assert count_images_for_user("bob") == 1

    def test_metadata_field_saved(self, tmp_library):
        iid = save_image_metadata(
            "https://x.com/a.jpg", "p", "m", 0.04, "alice",
            metadata={"style": "rustic", "dish": "бруно"}
        )
        img = get_image(iid)
        assert img["metadata"]["style"] == "rustic"


# ──────────────────────────────────────────────────────────────
# photo_studio
# ──────────────────────────────────────────────────────────────

from app.services.photo_studio import PhotoStudio


@pytest.fixture()
def studio(tmp_path, monkeypatch):
    import app.services.image_library as lib_mod
    monkeypatch.setattr(lib_mod, "_LIBRARY_PATH", tmp_path / "index.jsonl")
    monkeypatch.setenv("REPLICATE_API_KEY", "test_key_123")
    return PhotoStudio(user_id="daniil")


@pytest.fixture()
def studio_no_key(tmp_path, monkeypatch):
    import app.services.image_library as lib_mod
    monkeypatch.setattr(lib_mod, "_LIBRARY_PATH", tmp_path / "index.jsonl")
    monkeypatch.delenv("REPLICATE_API_KEY", raising=False)
    return PhotoStudio(user_id="daniil")


class TestPhotoStudio:
    def test_is_configured_with_key(self, studio):
        assert studio.is_configured() is True

    def test_is_configured_without_key(self, studio_no_key):
        assert studio_no_key.is_configured() is False

    def test_estimate_cost_flux_pro(self, studio):
        assert studio.estimate_cost("flux_pro") == 0.04

    def test_estimate_cost_unknown(self, studio):
        assert studio.estimate_cost("no_such_model") == 0.0

    def test_estimate_pipeline_cost_restaurant(self, studio):
        cost = studio.estimate_pipeline_cost("restaurant")
        assert cost == pytest.approx(0.04, abs=0.001)

    def test_estimate_pipeline_cost_party(self, studio):
        cost = studio.estimate_pipeline_cost("party")
        assert cost == pytest.approx(0.06, abs=0.001)

    def test_estimate_pipeline_cost_face_swap_polished(self, studio):
        # face_swap_basic + gfpgan
        cost = studio.estimate_pipeline_cost("face_swap_polished")
        assert cost == pytest.approx(0.005 + 0.002, abs=0.001)

    def test_get_total_cost_empty(self, studio):
        assert studio.get_total_cost() == 0.0

    def test_save_and_retrieve_result(self, studio):
        iid = studio.save_result(
            url="https://x.com/photo.jpg",
            prompt="борщ на тарелке",
            mode="restaurant",
            cost=0.04,
        )
        history = studio.get_history()
        assert len(history) == 1
        assert history[0]["id"] == iid

    def test_get_history_empty(self, studio):
        assert studio.get_history() == []

    def test_available_models_contains_keys(self, studio):
        names = studio.available_models()
        assert "flux_pro" in names
        assert "gfpgan" in names

    def test_model_info_known(self, studio):
        info = studio.model_info("flux_pro")
        assert info is not None
        assert info["cost"] == 0.04

    def test_model_info_unknown(self, studio):
        assert studio.model_info("nope") is None

    def test_models_for_task(self, studio):
        models = studio.models_for_task("face_polish")
        assert any(m["name"] == "gfpgan" for m in models)

    def test_pipeline_summary_keys(self, studio):
        summary = studio.pipeline_summary()
        for key in ["restaurant", "party", "face_swap", "enhance", "general"]:
            assert key in summary

    def test_pipeline_summary_configured_flag(self, studio):
        summary = studio.pipeline_summary()
        assert summary["restaurant"]["configured"] is True

    def test_total_cost_accumulates(self, studio):
        studio.save_result("https://x.com/1.jpg", "p1", "flux_pro", 0.04)
        studio.save_result("https://x.com/2.jpg", "p2", "flux_pro_ultra", 0.06)
        assert studio.get_total_cost() == pytest.approx(0.10, abs=0.001)
