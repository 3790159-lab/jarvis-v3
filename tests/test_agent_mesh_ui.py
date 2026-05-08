"""Phase 20: Agent Mesh UI tests."""
from __future__ import annotations

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


# ---------------------------------------------------------------------------
# /mesh command — on/off/debug/history/default
# ---------------------------------------------------------------------------

def test_mesh_on_sets_state(monkeypatch):
    import tools.jarvis_smart_telegram_control as mod
    sent = []
    monkeypatch.setattr(mod, "send", lambda cid, txt, **kw: sent.append(txt))
    monkeypatch.setattr(mod, "save_state", lambda s: None)
    state = mod.default_state()
    mod.handle_command("123", "/mesh", "on", state)
    assert state.get("mesh_enabled") is True
    assert any("включён" in s for s in sent)


def test_mesh_off_sets_state(monkeypatch):
    import tools.jarvis_smart_telegram_control as mod
    sent = []
    monkeypatch.setattr(mod, "send", lambda cid, txt, **kw: sent.append(txt))
    monkeypatch.setattr(mod, "save_state", lambda s: None)
    state = mod.default_state()
    mod.handle_command("123", "/mesh", "off", state)
    assert state.get("mesh_enabled") is False
    assert any("выключен" in s for s in sent)


def test_mesh_debug_no_history(monkeypatch):
    import tools.jarvis_smart_telegram_control as mod
    sent = []
    monkeypatch.setattr(mod, "send", lambda cid, txt, **kw: sent.append(txt))
    monkeypatch.setattr(mod, "save_state", lambda s: None)
    state = mod.default_state()
    mod.handle_command("123", "/mesh", "debug", state)
    assert len(sent) == 1
    assert "нет" in sent[0].lower() or "план" in sent[0].lower()


def test_mesh_debug_with_plan(monkeypatch):
    import tools.jarvis_smart_telegram_control as mod
    sent = []
    monkeypatch.setattr(mod, "send", lambda cid, txt, **kw: sent.append(txt))
    monkeypatch.setattr(mod, "save_state", lambda s: None)
    state = mod.default_state()
    state["last_mesh_plan"] = "📋 План (2 шага):\n  [1] internet_research"
    mod.handle_command("123", "/mesh", "debug", state)
    assert any("2" in s or "internet" in s for s in sent)


def test_mesh_history_empty(monkeypatch):
    import tools.jarvis_smart_telegram_control as mod
    sent = []
    monkeypatch.setattr(mod, "send", lambda cid, txt, **kw: sent.append(txt))
    monkeypatch.setattr(mod, "save_state", lambda s: None)
    state = mod.default_state()
    mod.handle_command("123", "/mesh", "history", state)
    assert any("пуст" in s for s in sent)


def test_mesh_history_with_entries(monkeypatch):
    import tools.jarvis_smart_telegram_control as mod
    sent = []
    monkeypatch.setattr(mod, "send", lambda cid, txt, **kw: sent.append(txt))
    monkeypatch.setattr(mod, "save_state", lambda s: None)
    state = mod.default_state()
    state["mesh_history"] = [
        {"query": "найди топ AI и сделай таблицу", "agents": "internet_research, smart_table"},
        {"query": "сравни облачные сервисы", "agents": "perplexity_researcher"},
    ]
    mod.handle_command("123", "/mesh", "history", state)
    assert any("найди топ" in s or "сравни" in s for s in sent)


def test_mesh_default_shows_control_panel(monkeypatch):
    import tools.jarvis_smart_telegram_control as mod
    sent = []
    # /mesh now uses send_with_keyboard for the interactive panel
    monkeypatch.setattr(mod, "send_with_keyboard", lambda cid, txt, kb: sent.append(txt))
    monkeypatch.setattr(mod, "send", lambda cid, txt, **kw: sent.append(txt))
    monkeypatch.setattr(mod, "save_state", lambda s: None)
    state = mod.default_state()
    mod.handle_command("123", "/mesh", "", state)
    # Panel text contains "MESH" or "Router"
    assert any("MESH" in s or "Router" in s or "mesh" in s.lower() for s in sent)


# ---------------------------------------------------------------------------
# /agents command
# ---------------------------------------------------------------------------

def test_agents_command_returns_status(monkeypatch):
    import tools.jarvis_smart_telegram_control as mod
    sent = []
    monkeypatch.setattr(mod, "send", lambda cid, txt, **kw: sent.append(txt))
    state = mod.default_state()
    mod.handle_command("123", "/agents", "", state)
    assert len(sent) >= 1
    assert any("агент" in s.lower() or "статус" in s.lower() for s in sent)


# ---------------------------------------------------------------------------
# /help includes mesh section
# ---------------------------------------------------------------------------

def test_help_mentions_mesh(monkeypatch):
    import tools.jarvis_smart_telegram_control as mod
    sent = []
    monkeypatch.setattr(mod, "send", lambda cid, txt, **kw: sent.append(txt))
    state = mod.default_state()
    mod.handle_command("123", "/help", "", state)
    assert any("mesh" in s.lower() or "Mesh" in s for s in sent)


def test_help_mentions_agents(monkeypatch):
    import tools.jarvis_smart_telegram_control as mod
    sent = []
    monkeypatch.setattr(mod, "send", lambda cid, txt, **kw: sent.append(txt))
    state = mod.default_state()
    mod.handle_command("123", "/help", "", state)
    assert any("agents" in s.lower() or "агент" in s.lower() for s in sent)


# ---------------------------------------------------------------------------
# /status includes mesh stats
# ---------------------------------------------------------------------------

def test_status_includes_mesh_info(monkeypatch):
    import tools.jarvis_smart_telegram_control as mod
    sent = []
    monkeypatch.setattr(mod, "send", lambda cid, txt, **kw: sent.append(txt))

    # Mock provider_health to avoid real API calls
    fake_providers = {"anthropic": True, "openai": False, "ollama": False}
    monkeypatch.setattr(
        "app.services.provider_health.check_all_providers",
        lambda use_cache=True: fake_providers,
    )
    monkeypatch.setattr(
        "app.services.provider_health.get_healthy_provider",
        lambda use_cache=True: "anthropic",
    )
    state = mod.default_state()
    state["mesh_history"] = [{"query": "test", "agents": "internet_research"}]
    state["mesh_enabled"] = True
    mod.handle_command("123", "/status", "", state)
    combined = " ".join(sent)
    assert "Router" in combined or "mesh" in combined.lower() or "Smart" in combined
