# -*- coding: utf-8 -*-
"""Unit tests for the swap-batch router tools.

These tools wrap the legacy ``/swapbatch_*`` flow. The legacy dispatcher and
set-quality handler are *injected* (``dispatch_fn`` / ``set_quality_fn``) so the
tools are exercised without importing the 5k-line bot module or touching
Telegram. Each test drives the real handler and asserts the command/args it
forwards plus the confirmation :class:`ToolResult` it returns.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.unified.llm_router.tool_registry import ToolContext
from app.services.unified.llm_router.tools.swap_batch import build_swap_batch_tools


def _ctx(chat_id: str = "222") -> ToolContext:
    return ToolContext(user_id=222, username="vasya", chat_id=chat_id)


def _by_name(tools):
    return {t.name: t for t in tools}


def test_swap_batch_tool_set_names_and_schemas():
    tools = build_swap_batch_tools()
    names = {t.name for t in tools}
    assert names == {
        "swap_batch_start_source",
        "swap_batch_start_targets",
        "swap_batch_run_swap",
        "cancel_current_batch",
        "swap_batch_run_animation",
        "swap_batch_set_quality",
    }
    # Every tool renders into a well-formed Anthropic tool block.
    for t in tools:
        block = t.to_anthropic()
        assert block["name"] == t.name
        assert block["input_schema"]["type"] == "object"
        assert t.description


def test_dispatch_tools_forward_correct_command():
    calls: list = []
    tools = _by_name(build_swap_batch_tools(dispatch_fn=lambda chat, cmd: calls.append((chat, cmd))))

    expected = {
        "swap_batch_start_source": ("source", "Жду исходные фото (источник лиц)."),
        "swap_batch_start_targets": ("batch", "Жду целевые фото для подстановки."),
        "swap_batch_run_swap": ("go", "Запускаю пакетный face-swap."),
        "cancel_current_batch": ("cancel", "Отменяю текущий пакет."),
    }
    for name, (command, confirmation) in expected.items():
        calls.clear()
        result = asyncio.run(tools[name].handler({}, _ctx()))
        assert calls == [(222, command)]
        assert not result.is_error
        assert result.text == confirmation


def test_dispatch_tool_without_backend_fails_gracefully():
    tools = _by_name(build_swap_batch_tools(dispatch_fn=None))
    result = asyncio.run(tools["swap_batch_run_swap"].handler({}, _ctx()))
    assert result.is_error
    assert "не подключён" in result.error.lower()


def test_dispatch_tool_with_bad_chat_id_fails_gracefully():
    tools = _by_name(build_swap_batch_tools(dispatch_fn=lambda chat, cmd: None))
    result = asyncio.run(tools["swap_batch_run_swap"].handler({}, _ctx(chat_id="not-an-int")))
    assert result.is_error
    assert "chat_id" in result.error


def test_dispatch_tool_surfaces_backend_exception():
    def boom(chat, cmd):
        raise RuntimeError("legacy exploded")

    tools = _by_name(build_swap_batch_tools(dispatch_fn=boom))
    result = asyncio.run(tools["swap_batch_run_swap"].handler({}, _ctx()))
    assert result.is_error
    assert "go" in result.error  # command name is included in the error


def test_dispatch_tool_awaits_async_backend():
    calls: list = []

    async def async_dispatch(chat, cmd):
        calls.append((chat, cmd))

    tools = _by_name(build_swap_batch_tools(dispatch_fn=async_dispatch))
    result = asyncio.run(tools["swap_batch_start_source"].handler({}, _ctx()))
    assert calls == [(222, "source")]
    assert not result.is_error


def test_animation_tool_maps_modes_to_commands():
    calls: list = []
    tools = _by_name(build_swap_batch_tools(dispatch_fn=lambda chat, cmd: calls.append(cmd)))
    anim = tools["swap_batch_run_animation"]

    for mode, command in (("yes", "animate_yes"), ("custom", "animate_custom"), ("no", "animate_no")):
        calls.clear()
        result = asyncio.run(anim.handler({"mode": mode}, _ctx()))
        assert calls == [command]
        assert not result.is_error
        assert mode in result.text


def test_animation_tool_defaults_to_yes():
    calls: list = []
    tools = _by_name(build_swap_batch_tools(dispatch_fn=lambda chat, cmd: calls.append(cmd)))
    result = asyncio.run(tools["swap_batch_run_animation"].handler({}, _ctx()))
    assert calls == ["animate_yes"]
    assert not result.is_error


def test_animation_tool_rejects_unknown_mode():
    tools = _by_name(build_swap_batch_tools(dispatch_fn=lambda chat, cmd: None))
    result = asyncio.run(tools["swap_batch_run_animation"].handler({"mode": "sideways"}, _ctx()))
    assert result.is_error
    assert "sideways" in result.error


def test_set_quality_tool_forwards_duration_and_fps():
    calls: list = []
    tools = _by_name(
        build_swap_batch_tools(set_quality_fn=lambda chat, args: calls.append((chat, args)))
    )
    result = asyncio.run(
        tools["swap_batch_set_quality"].handler({"duration_sec": 5, "fps": 24}, _ctx())
    )
    assert calls == [(222, "5 24")]
    assert not result.is_error
    assert "5 24" in result.text


def test_set_quality_tool_without_backend_fails_gracefully():
    tools = _by_name(build_swap_batch_tools(set_quality_fn=None))
    result = asyncio.run(tools["swap_batch_set_quality"].handler({"fps": 24}, _ctx()))
    assert result.is_error
    assert "недоступна" in result.error.lower()
