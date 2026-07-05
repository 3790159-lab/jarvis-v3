# -*- coding: utf-8 -*-
"""/start = приветствие + инлайн-меню (a real, routed command, not in registry).
$0, no network. Reuses the /menu render path (jarvis_menu.render_root)."""
import importlib

mod = importlib.import_module("tools.jarvis_smart_telegram_control")


def test_start_greets_and_shows_inline_menu(monkeypatch):
    cap = {}
    monkeypatch.setattr(mod, "send_with_keyboard",
                        lambda cid, text, kb, *a, **k: cap.update(text=text, kb=kb))
    monkeypatch.setattr(mod, "send", lambda *a, **k: cap.setdefault("plain", a))
    mod.handle_command("123", "/start", "", {})
    assert cap.get("kb"), "/start must show the inline menu keyboard"
    assert any(w in cap.get("text", "") for w in ("Привет", "👋")), "greeting expected"


def test_help_still_plain_text(monkeypatch):
    # /help / /smart_help keep the plain HELP_TEXT (not the menu keyboard)
    sent = []
    monkeypatch.setattr(mod, "send", lambda cid, t, *a, **k: sent.append(t))
    monkeypatch.setattr(mod, "send_with_keyboard",
                        lambda *a, **k: sent.append("KEYBOARD"))
    mod.handle_command("123", "/help", "", {})
    assert sent and "KEYBOARD" not in sent
