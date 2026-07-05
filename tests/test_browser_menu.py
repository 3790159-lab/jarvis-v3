# -*- coding: utf-8 -*-
"""🌐 Браузер menu category — admin-only, static registration. $0, no network."""
from tools import jarvis_menu as m


def _cats(role):
    _, kb = m.render_root(role)
    return [b["text"] for row in kb for b in row
            if "menu:cat:" in b.get("callback_data", "")]


def test_browser_category_admin_only_11_admin_5_friend():
    admin = _cats("admin")
    assert any("🌐" in c for c in admin)          # admin sees 🌐 Браузер
    assert len(admin) == 11                        # was 10, now +🌐
    friend = _cats("friend")
    assert not any("🌐" in c for c in friend)      # hidden from friend (all items friend=False)
    assert len(friend) == 5                        # unchanged


def test_browse_commands_native_admin_only():
    admin = [c for c, _ in m.NATIVE_ADMIN_COMMANDS]
    friend = [c for c, _ in m.NATIVE_FRIEND_COMMANDS]
    assert "browse_check" in admin and "browse_check" not in friend


def test_browse_items_are_admin_only_in_lookup():
    # friend asking a browse command via menu lookup gets nothing (tooth #3)
    assert m.lookup_item("/browse_check", "friend") is None
    assert m.lookup_item("/browse_check", "admin") is not None
