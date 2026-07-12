# -*- coding: utf-8 -*-
"""/ig_post bot wiring (control module). $0, mocks only.

No real Anthropic call (caption LLM mocked), no real Graph API call (InstagramAPI
mocked), no real R2 upload (host wrapper mocked). Covers: paid+self-gating
registry, admin-only, preview-card flow with pending state, the money-safe
caption step (guard_spend), and the irreversible publish tap being fail-closed
(quota error => NOT published + honest message, pending kept).
"""
import importlib

from app.services.instagram_api import InstagramAPIError

mod = importlib.import_module("tools.jarvis_smart_telegram_control")
ADMIN = "237616472"
PENDING_KEY = "pending_ig_post"


# ---- registry / role ------------------------------------------------------


def test_ig_post_is_paid_and_self_gating():
    from tools import intent_router as _ir
    assert _ir.is_paid("/ig_post") is True
    assert "/ig_post" in mod._SELF_GATING_PAID


def test_ig_post_admin_only():
    assert "/ig_post" not in mod.FRIEND_ALLOWED_COMMANDS
    assert not any(p.startswith("igpost") for p in mod.FRIEND_ALLOWED_CALLBACK_PREFIXES)


def test_ig_post_command_dispatches(monkeypatch):
    fired = {}
    monkeypatch.setattr(mod, "_ig_post_dispatch",
                        lambda cid, query, state: fired.update(cid=cid, query=query))
    state = {}
    mod.handle_command(ADMIN, "/ig_post", "pic.jpg свежий кофе", state)
    assert fired == {"cid": ADMIN, "query": "pic.jpg свежий кофе"}


# ---- photo-with-caption routing (bug: /ig_post caption -> vision) ---------


def _photo_msg(caption):
    return {"photo": [{"file_id": "fid", "file_unique_id": "u"}], "caption": caption}


def test_photo_caption_ig_post_routes_to_dispatch(monkeypatch, tmp_path):
    """A photo captioned '/ig_post <topic>' must reach _ig_post_dispatch,
    NOT the vision analyzer."""
    img = tmp_path / "photo.jpg"
    img.write_bytes(b"\xff\xd8\xff")
    monkeypatch.setattr(mod, "_extract_file_from_msg",
                        lambda m: ("fid", "photo.jpg", "image/jpeg"))
    monkeypatch.setattr(mod, "_download_telegram_file", lambda fid, fn: str(img))
    fired = {}
    monkeypatch.setattr(mod, "_ig_post_dispatch",
                        lambda cid, query, state: fired.update(cid=cid, query=query))
    sent = []
    monkeypatch.setattr(mod, "send", lambda cid, t, *a, **k: sent.append(t))

    mod._handle_file_message(ADMIN, _photo_msg("/ig_post кофе с корицей"), {})

    assert fired.get("query") == "last кофе с корицей"
    # the just-uploaded photo becomes the ig_post source
    assert mod._LAST_IG_MEDIA.get(ADMIN) == str(img)
    # vision analyzer was never invoked
    assert not any("Анализирую изображение" in t for t in sent)


def test_photo_caption_ig_post_no_topic_asks_for_topic(monkeypatch, tmp_path):
    img = tmp_path / "photo.jpg"
    img.write_bytes(b"\xff\xd8\xff")
    monkeypatch.setattr(mod, "_extract_file_from_msg",
                        lambda m: ("fid", "photo.jpg", "image/jpeg"))
    monkeypatch.setattr(mod, "_download_telegram_file", lambda fid, fn: str(img))
    calls = {"dispatch": 0}
    monkeypatch.setattr(mod, "_ig_post_dispatch",
                        lambda *a, **k: calls.__setitem__("dispatch", 1))
    sent = []
    monkeypatch.setattr(mod, "send", lambda cid, t, *a, **k: sent.append(t))

    mod._handle_file_message(ADMIN, _photo_msg("/ig_post"), {})

    assert calls["dispatch"] == 0
    assert any("тем" in t.lower() for t in sent)          # prompts for a topic
    assert not any("Анализирую изображение" in t for t in sent)


# ---- dispatch (prepare media + caption + preview card) --------------------


def _patch_caption(monkeypatch, caption="Смачна кава ☕ #кава #ранок"):
    monkeypatch.setattr(mod, "guard_spend", lambda uid, uname, est, do: (do(), None))
    monkeypatch.setattr(mod, "_ig_caption_ask_llm", lambda s, m: caption)


def test_dispatch_requires_topic(monkeypatch):
    calls = {"host": 0}
    monkeypatch.setattr(mod, "_ig_post_host_media", lambda p: calls.__setitem__("host", 1) or "u")
    sent = {}
    monkeypatch.setattr(mod, "send", lambda cid, t, *a, **k: sent.setdefault("t", t))
    mod._ig_post_dispatch(ADMIN, "onlypath", {})
    assert calls["host"] == 0
    assert "тем" in sent["t"].lower()


def test_dispatch_bare_no_args_shows_usage_hint(monkeypatch):
    """Bare /ig_post as text (no photo, no args) must show the friendly usage
    hint, not a raw 'file not found'."""
    calls = {"host": 0}
    monkeypatch.setattr(mod, "_ig_post_host_media", lambda p: calls.__setitem__("host", 1) or "u")
    sent = {}
    monkeypatch.setattr(mod, "send", lambda cid, t, *a, **k: sent.setdefault("t", t))
    mod._ig_post_dispatch(ADMIN, "", {})
    assert calls["host"] == 0
    assert "📸" in sent["t"]
    assert "/ig_post" in sent["t"]
    assert "last" in sent["t"].lower()


def test_dispatch_bogus_source_shows_usage_hint_not_file_not_found(monkeypatch):
    """A first token that is neither an existing file nor 'last' (e.g. a
    stray word from natural-language phrasing) should show the same friendly
    usage hint instead of a raw 'Файл не найден' path dump."""
    calls = {"host": 0}
    monkeypatch.setattr(mod, "_ig_post_host_media", lambda p: calls.__setitem__("host", 1) or "u")
    sent = {}
    monkeypatch.setattr(mod, "send", lambda cid, t, *a, **k: sent.setdefault("t", t))
    mod._ig_post_dispatch(ADMIN, "первый пост про кофе", {})
    assert calls["host"] == 0
    assert "📸" in sent["t"]
    assert "Файл не найден" not in sent["t"]
    assert "первый" not in sent["t"]


def test_dispatch_happy_stores_pending_and_shows_card(monkeypatch, tmp_path):
    img = tmp_path / "pic.jpg"
    img.write_bytes(b"\xff\xd8\xff")  # not read — existence only
    _patch_caption(monkeypatch)
    monkeypatch.setattr(mod, "_ig_post_host_media", lambda p: "https://pub/x.jpg")
    monkeypatch.setattr(mod, "save_state", lambda s: None)
    monkeypatch.setattr(mod, "_send_photo_url", lambda *a, **k: None)
    kb_sent = []
    monkeypatch.setattr(mod, "send_with_keyboard",
                        lambda cid, t, kb, *a, **k: kb_sent.append((t, kb)))
    monkeypatch.setattr(mod, "send", lambda *a, **k: None)

    state = {}
    mod._ig_post_dispatch(ADMIN, f"{img} новий сезонний напій", state)

    p = state[PENDING_KEY]
    assert p["photo_url"] == "https://pub/x.jpg"
    assert "Смачна кава" in p["caption"]
    assert p["topic"] == "новий сезонний напій"
    assert len(kb_sent) == 1
    cbs = [btn.get("callback_data") for row in kb_sent[0][1] for btn in row]
    assert "igpost:publish" in cbs
    assert "igpost:regen" in cbs
    assert "igpost:cancel" in cbs
    assert "https://pub/x.jpg" in kb_sent[0][0]


def test_dispatch_last_generation_resolves_from_store(monkeypatch, tmp_path):
    img = tmp_path / "gen.jpg"
    img.write_bytes(b"\xff\xd8\xff")
    mod._LAST_IG_MEDIA[ADMIN] = str(img)
    _patch_caption(monkeypatch)
    hosted = {}
    monkeypatch.setattr(mod, "_ig_post_host_media",
                        lambda p: hosted.setdefault("path", p) or "https://pub/last.jpg")
    monkeypatch.setattr(mod, "save_state", lambda s: None)
    monkeypatch.setattr(mod, "_send_photo_url", lambda *a, **k: None)
    monkeypatch.setattr(mod, "send_with_keyboard", lambda *a, **k: None)
    monkeypatch.setattr(mod, "send", lambda *a, **k: None)

    mod._ig_post_dispatch(ADMIN, "last кава дня", {})
    assert hosted["path"] == str(img)


def test_dispatch_last_without_history_is_honest(monkeypatch):
    mod._LAST_IG_MEDIA.pop(ADMIN, None)
    calls = {"host": 0}
    monkeypatch.setattr(mod, "_ig_post_host_media", lambda p: calls.__setitem__("host", 1) or "u")
    sent = {}
    monkeypatch.setattr(mod, "send", lambda cid, t, *a, **k: sent.setdefault("t", t))
    mod._ig_post_dispatch(ADMIN, "last тема", {})
    assert calls["host"] == 0
    assert "последн" in sent["t"].lower()


def test_dispatch_caption_gate_block_stores_no_pending(monkeypatch, tmp_path):
    img = tmp_path / "pic.jpg"
    img.write_bytes(b"\xff\xd8\xff")
    monkeypatch.setattr(mod, "_ig_post_host_media", lambda p: "https://pub/x.jpg")
    monkeypatch.setattr(mod, "guard_spend", lambda uid, uname, est, do: (None, "🚫 лимит исчерпан"))
    monkeypatch.setattr(mod, "save_state", lambda s: None)
    sent = {}
    monkeypatch.setattr(mod, "send", lambda cid, t, *a, **k: sent.setdefault("t", t))
    kb = []
    monkeypatch.setattr(mod, "send_with_keyboard", lambda *a, **k: kb.append(1))

    state = {}
    mod._ig_post_dispatch(ADMIN, f"{img} тема", state)
    assert PENDING_KEY not in state
    assert not kb
    assert "лимит" in sent["t"]


# ---- multi-account (@<account_key>) ----------------------------------------


def test_dispatch_account_arg_stripped_and_stored_in_pending(monkeypatch, tmp_path):
    img = tmp_path / "pic.jpg"
    img.write_bytes(b"\xff\xd8\xff")
    _patch_caption(monkeypatch)
    monkeypatch.setattr(mod, "_ig_post_host_media", lambda p: "https://pub/x.jpg")
    monkeypatch.setattr(mod, "save_state", lambda s: None)
    monkeypatch.setattr(mod, "_send_photo_url", lambda *a, **k: None)
    monkeypatch.setattr(mod, "send_with_keyboard", lambda *a, **k: None)
    monkeypatch.setattr(mod, "send", lambda *a, **k: None)

    state = {}
    mod._ig_post_dispatch(ADMIN, f"@vera_ai_ua {img} новий напій", state)

    p = state[PENDING_KEY]
    assert p["account_key"] == "vera_ai_ua"
    assert p["topic"] == "новий напій"


def test_dispatch_no_account_arg_pending_has_none(monkeypatch, tmp_path):
    img = tmp_path / "pic.jpg"
    img.write_bytes(b"\xff\xd8\xff")
    _patch_caption(monkeypatch)
    monkeypatch.setattr(mod, "_ig_post_host_media", lambda p: "https://pub/x.jpg")
    monkeypatch.setattr(mod, "save_state", lambda s: None)
    monkeypatch.setattr(mod, "_send_photo_url", lambda *a, **k: None)
    monkeypatch.setattr(mod, "send_with_keyboard", lambda *a, **k: None)
    monkeypatch.setattr(mod, "send", lambda *a, **k: None)

    state = {}
    mod._ig_post_dispatch(ADMIN, f"{img} тема", state)

    assert state[PENDING_KEY]["account_key"] is None


def test_publish_tap_uses_account_key_from_pending(monkeypatch):
    seen = {}

    class _FakeIG:
        def __init__(self, *a, account_key=None, **k):
            seen["account_key"] = account_key

        def publish_photo(self, url, caption):
            return {"id": "media_1", "permalink": "https://www.instagram.com/p/AAA/"}

    monkeypatch.setattr("app.services.instagram_api.InstagramAPI", _FakeIG)
    monkeypatch.setattr(mod, "save_state", lambda s: None)
    monkeypatch.setattr(mod, "answer_callback_query", lambda *a, **k: None)
    monkeypatch.setattr(mod, "edit_message_with_keyboard", lambda *a, **k: None)
    monkeypatch.setattr(mod, "send", lambda *a, **k: None)

    state = {PENDING_KEY: {"photo_url": "https://pub/x.jpg", "caption": "c",
                           "topic": "t", "source": "s", "account_key": "vera_ai_ua"}}
    mod.handle_callback_query(_cq("igpost:publish"), state)

    assert seen["account_key"] == "vera_ai_ua"


def test_publish_tap_pending_without_account_key_defaults_to_none(monkeypatch):
    """Old-shape pending dicts (no account_key, e.g. persisted before this
    feature) must not crash — default account, same as before."""
    seen = {}

    class _FakeIG:
        def __init__(self, *a, account_key=None, **k):
            seen["account_key"] = account_key

        def publish_photo(self, url, caption):
            return {"id": "media_1", "permalink": None}

    monkeypatch.setattr("app.services.instagram_api.InstagramAPI", _FakeIG)
    monkeypatch.setattr(mod, "save_state", lambda s: None)
    monkeypatch.setattr(mod, "answer_callback_query", lambda *a, **k: None)
    monkeypatch.setattr(mod, "edit_message_with_keyboard", lambda *a, **k: None)
    monkeypatch.setattr(mod, "send", lambda *a, **k: None)

    state = {PENDING_KEY: {"photo_url": "u", "caption": "c", "topic": "t", "source": "s"}}
    mod.handle_callback_query(_cq("igpost:publish"), state)

    assert seen["account_key"] is None


# ---- publish tap (irreversible, fail-closed) ------------------------------


def _cq(data):
    return {
        "id": "cq1", "data": data,
        "message": {"chat": {"id": int(ADMIN)}, "message_id": 55},
        "from": {"id": int(ADMIN)},
    }


def test_publish_tap_publishes_and_sends_permalink(monkeypatch):
    published = []

    class _FakeIG:
        def __init__(self, *a, **k):
            pass

        def publish_photo(self, url, caption):
            published.append((url, caption))
            return {"id": "media_1", "permalink": "https://www.instagram.com/p/AAA/",
                    "container_id": "c1"}

    monkeypatch.setattr("app.services.instagram_api.InstagramAPI", _FakeIG)
    monkeypatch.setattr(mod, "save_state", lambda s: None)
    monkeypatch.setattr(mod, "answer_callback_query", lambda *a, **k: None)
    monkeypatch.setattr(mod, "edit_message_with_keyboard", lambda *a, **k: None)
    sent = []
    monkeypatch.setattr(mod, "send", lambda cid, t, *a, **k: sent.append(t))

    state = {PENDING_KEY: {"photo_url": "https://pub/x.jpg", "caption": "Смачно",
                           "topic": "кава", "source": "pic.jpg"}}
    mod.handle_callback_query(_cq("igpost:publish"), state)

    assert published == [("https://pub/x.jpg", "Смачно")]
    assert any("instagram.com/p/AAA" in t for t in sent)
    assert PENDING_KEY not in state          # cleared on success


def test_publish_tap_quota_error_fail_closed(monkeypatch):
    class _FakeIG:
        def __init__(self, *a, **k):
            pass

        def publish_photo(self, url, caption):
            raise InstagramAPIError("The media posting limit has been reached.",
                                    code=9, subcode=2207042)

    monkeypatch.setattr("app.services.instagram_api.InstagramAPI", _FakeIG)
    monkeypatch.setattr(mod, "save_state", lambda s: None)
    monkeypatch.setattr(mod, "answer_callback_query", lambda *a, **k: None)
    monkeypatch.setattr(mod, "edit_message_with_keyboard", lambda *a, **k: None)
    sent = []
    monkeypatch.setattr(mod, "send", lambda cid, t, *a, **k: sent.append(t))

    state = {PENDING_KEY: {"photo_url": "https://pub/x.jpg", "caption": "c",
                           "topic": "t", "source": "s"}}
    mod.handle_callback_query(_cq("igpost:publish"), state)

    joined = " ".join(sent).lower()
    assert "лимит" in joined or "25" in joined
    assert not any("instagram.com/p/" in t for t in sent)   # no fake permalink
    assert PENDING_KEY in state              # kept for a retry (NOT published)


def test_publish_tap_no_pending_is_safe(monkeypatch):
    calls = {"api": 0}

    class _FakeIG:
        def __init__(self, *a, **k):
            calls["api"] += 1

        def publish_photo(self, *a, **k):
            calls["api"] += 1
            return {"id": "x", "permalink": None}

    monkeypatch.setattr("app.services.instagram_api.InstagramAPI", _FakeIG)
    monkeypatch.setattr(mod, "answer_callback_query", lambda *a, **k: None)
    monkeypatch.setattr(mod, "save_state", lambda s: None)
    monkeypatch.setattr(mod, "send", lambda *a, **k: None)

    mod.handle_callback_query(_cq("igpost:publish"), {})
    assert calls["api"] == 0                  # never touched Graph API


def test_regen_tap_updates_caption(monkeypatch):
    _patch_caption(monkeypatch, caption="Оновлена підпис 🌿 #нове")
    monkeypatch.setattr(mod, "save_state", lambda s: None)
    monkeypatch.setattr(mod, "answer_callback_query", lambda *a, **k: None)
    edited = []
    monkeypatch.setattr(mod, "edit_message_with_keyboard",
                        lambda cid, mid, t, kb, *a, **k: edited.append((t, kb)))
    monkeypatch.setattr(mod, "send", lambda *a, **k: None)

    state = {PENDING_KEY: {"photo_url": "https://pub/x.jpg", "caption": "старая",
                           "topic": "кава", "source": "s"}}
    mod.handle_callback_query(_cq("igpost:regen"), state)

    assert state[PENDING_KEY]["caption"] == "Оновлена підпис 🌿 #нове"
    assert len(edited) == 1
    assert "Оновлена підпис" in edited[0][0]


def test_cancel_tap_clears_pending(monkeypatch):
    monkeypatch.setattr(mod, "save_state", lambda s: None)
    monkeypatch.setattr(mod, "answer_callback_query", lambda *a, **k: None)
    monkeypatch.setattr(mod, "edit_message_with_keyboard", lambda *a, **k: None)
    state = {PENDING_KEY: {"photo_url": "u", "caption": "c", "topic": "t", "source": "s"}}
    mod.handle_callback_query(_cq("igpost:cancel"), state)
    assert PENDING_KEY not in state


def test_remember_last_media_stores_path():
    mod._LAST_IG_MEDIA.pop("999", None)
    mod._remember_last_media("999", "C:/gen/out.jpg")
    assert mod._LAST_IG_MEDIA["999"] == "C:/gen/out.jpg"
