# -*- coding: utf-8 -*-
"""Router money-gate — Task 1: registry carries paid/est_usd per tool."""
from app.services.unified.llm_router.tool_registry import ToolRegistry
from app.services.unified.llm_router.tools import register_default_tools


def test_paid_tools_flagged_with_cost():
    reg = register_default_tools(ToolRegistry())
    gi = reg.get("generate_image")
    assert gi is not None
    assert gi.paid is True
    assert gi.est_usd > 0

    stats = reg.get("get_user_stats")
    assert stats is not None
    assert stats.paid is False          # read-only, free
    assert stats.est_usd == 0.0


def test_free_setup_tools_not_paid():
    reg = register_default_tools(ToolRegistry())
    for free_name in ("swap_batch_start_source", "swap_batch_start_targets",
                      "swap_batch_set_quality", "cancel_current_batch"):
        t = reg.get(free_name)
        assert t is not None, free_name
        assert t.paid is False, free_name
