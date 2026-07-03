# -*- coding: utf-8 -*-
"""Unified menu — pure declarative registry + role filter + render + lookup.

$0, no network, no import of the main control module. Isolation teeth:
  Tooth #1 (render-by-role): admin-only items never reach a friend keyboard.
  Tooth #3 (role-checked lookup): friend request for an admin cmd -> None.
"""
import tools.jarvis_menu as m


# ── Task 1: registry + role filter ────────────────────────────────────────
def test_registry_has_expected_categories():
    ids = [c.cat_id for c in m.MENU]
    for expected in ("video", "persona", "me", "photo", "apps", "agents", "stats", "system"):
        assert expected in ids


def test_friend_sees_only_allowed_categories():
    friend_cats = [c.cat_id for c in m.MENU if m._visible(c.items, "friend")]
    # friend sees the same categories as admin EXCEPT system/agents/apps.
    assert set(friend_cats) == {"video", "persona", "me", "photo", "stats"}


def test_admin_only_items_never_visible_to_friend():
    # Tooth #1: not a single admin-only item leaks into a friend view.
    for cat in m.MENU:
        for it in m._visible(cat.items, "friend"):
            assert it.friend is True


def test_stats_category_friend_subset_is_only_my_stats():
    stats = next(c for c in m.MENU if c.cat_id == "stats")
    friend_items = [it.cmd for it in m._visible(stats.items, "friend")]
    assert friend_items == ["/my_stats"]
