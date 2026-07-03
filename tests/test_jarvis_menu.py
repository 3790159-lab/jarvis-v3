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


# ── Task 2: render root + category ────────────────────────────────────────
def test_render_root_lists_only_role_categories():
    text, kb = m.render_root("friend")
    datas = [b["callback_data"] for row in kb for b in row]
    assert "menu:cat:video" in datas
    assert "menu:cat:system" not in datas       # admin-only скрыта
    assert all(d.startswith("menu:cat:") for d in datas)


def test_render_category_friend_hides_admin_items():
    res = m.render_category("persona", "friend")
    assert res is not None
    _text, kb = res
    datas = [b["callback_data"] for row in kb for b in row]
    assert "menu:x:persona_photo" in datas       # friend-пункт есть
    assert "menu:x:train_lora" not in datas      # admin-пункт скрыт (tooth #1)
    assert kb[-1][0]["callback_data"] == "menu:root"   # кнопка «⬅️ Назад»


def test_render_category_denied_for_role_returns_none():
    assert m.render_category("system", "friend") is None


def test_render_category_unknown_returns_none():
    assert m.render_category("nope", "admin") is None


def test_button_text_respects_label_presence():
    _t, kb = m.render_category("video", "admin")
    labels = [b["text"] for row in kb for b in row if b["callback_data"].startswith("menu:x:")]
    assert any(" — " in x for x in labels)        # Видео: подписи есть
    _t2, kb2 = m.render_category("system", "admin")
    sys_labels = [b["text"] for row in kb2 for b in row if b["callback_data"].startswith("menu:x:")]
    assert all(" — " not in x for x in sys_labels)  # Система: голые команды
