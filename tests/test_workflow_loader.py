# -*- coding: utf-8 -*-
"""Tests for the ComfyUI workflow loader."""
from __future__ import annotations

import pytest

from app.services.block_m2_video.runpod.workflows.loader import load_workflow


def test_load_minimal_test_workflow():
    workflow = load_workflow("_minimal_test")

    assert isinstance(workflow, dict)
    assert "3" in workflow  # KSampler
    assert workflow["3"]["class_type"] == "KSampler"
    assert "5" in workflow  # EmptyLatentImage
    assert workflow["5"]["class_type"] == "EmptyLatentImage"
    assert "8" in workflow  # VAEDecode
    assert workflow["8"]["class_type"] == "VAEDecode"
    assert "9" in workflow  # SaveImage
    assert workflow["9"]["class_type"] == "SaveImage"


def test_load_applies_overrides():
    workflow = load_workflow(
        "_minimal_test",
        overrides={
            "6.inputs.text": "a brand new prompt",
            "3.inputs.seed": 1234,
            "5.inputs.width": 768,
        },
    )

    assert workflow["6"]["inputs"]["text"] == "a brand new prompt"
    assert workflow["3"]["inputs"]["seed"] == 1234
    assert workflow["5"]["inputs"]["width"] == 768


def test_load_does_not_mutate_template_on_subsequent_calls():
    a = load_workflow(
        "_minimal_test", overrides={"6.inputs.text": "first"}
    )
    b = load_workflow("_minimal_test")  # no overrides

    assert a["6"]["inputs"]["text"] == "first"
    assert b["6"]["inputs"]["text"] == "a placeholder prompt"


def test_load_unknown_workflow_raises():
    with pytest.raises(FileNotFoundError):
        load_workflow("nonexistent_workflow_xyz")


def test_load_invalid_override_path_raises():
    with pytest.raises(ValueError):
        load_workflow(
            "_minimal_test",
            overrides={"3.inputs.does_not_exist.text": "x"},
        )


def test_load_with_list_index_override():
    workflow = load_workflow(
        "_minimal_test",
        overrides={"3.inputs.model.0": "999"},
    )

    assert workflow["3"]["inputs"]["model"][0] == "999"
