# -*- coding: utf-8 -*-
"""/ig_gen bot wiring (control module). $0, mocks only.

``/ig_gen <тема>`` = полный цикл одной командой: генерация фото по теме
(money-gated) -> ig_media_prep + host_for_ig (R2) -> ig_caption (money-gated)
-> ТА ЖЕ превью-карточка/кнопки, что у /ig_post (igpost:publish/regen/cancel).
Ни одного реального платного вызова: генератор фото, скачивание, LLM и
Instagram API — везде мокнуты обёртки, как у /ig_post и /ig_caption.

Money flow: /ig_gen НЕ self-gating (в отличие от /ig_post) — фото генерируется
"с нуля" и стоит заметно дороже подписи, поэтому получает СВОЙ blanket
money-confirm тап на входе (тот же chokepoint, что /ig_caption, /menu_photo),
а публикация остаётся отдельным необратимым тапом на карточке.
"""
import importlib

from app.services.instagram_api import InstagramAPIError

mod = importlib.import_module("tools.jarvis_smart_telegram_control")
ADMIN = "237616472"
PENDING_KEY = "pending_ig_post"


# ---- registry / role ------------------------------------------------------


def test_ig_gen_is_paid_and_not_self_gating():
    from tools import intent_router as _ir
    assert _ir.is_paid("/ig_gen") is True
    assert "/ig_gen" not in mod._SELF_GATING_PAID


def test_ig_gen_admin_only():
    assert "/ig_gen" not in mod.FRIEND_ALLOWED_COMMANDS


def test_ig_gen_command_gated_by_money_gate_without_token(monkeypatch):
    fired = {"dispatch": 0, "confirm": 0}
    monkeypatch.setattr(mod, "_ig_gen_dispatch",
                        lambda cid, query, state: fired.__setitem__("dispatch", 1))
    monkeypatch.setattr(mod, "save_state", lambda s: None)
    monkeypatch.setattr(mod, "send_with_keyboard", lambda *a, **k: fired.__setitem__("confirm", 1))
    state = {}
    mod.handle_command(ADMIN, "/ig_gen", "тема", state)
    assert fired["dispatch"] == 0 and fired["confirm"] == 1


def test_ig_gen_command_dispatches_with_confirmed_token(monkeypatch):
    fired = {}
    monkeypatch.setattr(mod, "_ig_gen_dispatch",
                        lambda cid, query, state: fired.update(cid=cid, query=query))
    state = {"_paid_confirmed": "/ig_gen"}
    mod.handle_command(ADMIN, "/ig_gen", "тепла кава восени", state)
    assert fired == {"cid": ADMIN, "query": "тепла кава восени"}


# ---- dispatch (generate photo + prep + caption + preview card) ------------


def _patch_photo(monkeypatch, url="https://replicate/gen.jpg", local="C:/tmp/ig_gen_x.jpg"):
    monkeypatch.setattr(mod, "_ig_gen_generate_photo", lambda topic: url)
    monkeypatch.setattr(mod, "_ig_gen_generate_photo_persona", lambda pid, prompt, **kw: url)
    monkeypatch.setattr(mod, "_ig_gen_download_photo", lambda u: local)


def _patch_caption(monkeypatch, caption="Смачна кава ☕ #кава #ранок"):
    monkeypatch.setattr(mod, "_ig_caption_ask_llm", lambda s, m: caption)


def _patch_guard_spend_passthrough(monkeypatch):
    monkeypatch.setattr(mod, "guard_spend", lambda uid, uname, est, do: (do(), None))


def test_ig_gen_dispatch_requires_topic(monkeypatch):
    calls = {"photo": 0}
    monkeypatch.setattr(mod, "_ig_gen_generate_photo", lambda topic: calls.__setitem__("photo", 1) or "u")
    sent = {}
    monkeypatch.setattr(mod, "send", lambda cid, t, *a, **k: sent.setdefault("t", t))
    mod._ig_gen_dispatch(ADMIN, "   ", {})
    assert calls["photo"] == 0
    assert "тем" in sent["t"].lower()


def test_ig_gen_dispatch_photo_gate_block_stores_no_pending(monkeypatch):
    calls = {"photo": 0, "download": 0}
    monkeypatch.setattr(mod, "_ig_gen_generate_photo", lambda topic: calls.__setitem__("photo", 1) or "u")
    monkeypatch.setattr(mod, "_ig_gen_download_photo", lambda u: calls.__setitem__("download", 1) or "p")
    monkeypatch.setattr(mod, "guard_spend", lambda uid, uname, est, do: (None, "🚫 лимит исчерпан"))
    sent = {}
    monkeypatch.setattr(mod, "send", lambda cid, t, *a, **k: sent.setdefault("t", t))
    kb = []
    monkeypatch.setattr(mod, "send_with_keyboard", lambda *a, **k: kb.append(1))

    state = {}
    mod._ig_gen_dispatch(ADMIN, "тема", state)

    assert calls["photo"] == 0          # guard_spend blocked BEFORE _do() ran
    assert calls["download"] == 0
    assert PENDING_KEY not in state
    assert not kb
    assert "лимит" in sent["t"]


def test_ig_gen_dispatch_photo_gen_raises_honest_zero_dollar_message(monkeypatch):
    _patch_guard_spend_passthrough(monkeypatch)

    def _raising(topic):
        raise RuntimeError("Replicate rate limit достигнут")

    monkeypatch.setattr(mod, "_ig_gen_generate_photo", _raising)
    calls = {"download": 0}
    monkeypatch.setattr(mod, "_ig_gen_download_photo", lambda u: calls.__setitem__("download", 1) or "p")
    sent = {}
    monkeypatch.setattr(mod, "send", lambda cid, t, *a, **k: sent.setdefault("t", t))

    state = {}
    mod._ig_gen_dispatch(ADMIN, "тема", state)

    assert calls["download"] == 0
    assert PENDING_KEY not in state
    assert "$0" in sent["t"] or "недоступ" in sent["t"].lower()


def test_ig_gen_dispatch_download_failure_after_photo_paid_is_honest(monkeypatch):
    _patch_guard_spend_passthrough(monkeypatch)
    monkeypatch.setattr(mod, "_ig_gen_generate_photo", lambda topic: "https://replicate/gen.jpg")

    def _raising(url):
        raise RuntimeError("network unreachable")

    monkeypatch.setattr(mod, "_ig_gen_download_photo", _raising)
    calls = {"host": 0}
    monkeypatch.setattr(mod, "_ig_post_host_media", lambda p: calls.__setitem__("host", 1) or "u")
    sent = {}
    monkeypatch.setattr(mod, "send", lambda cid, t, *a, **k: sent.setdefault("t", t))

    state = {}
    mod._ig_gen_dispatch(ADMIN, "тема", state)

    assert calls["host"] == 0
    assert PENDING_KEY not in state
    assert "не удалось" in sent["t"].lower() or "сгенерир" in sent["t"].lower()


def test_ig_gen_dispatch_caption_gate_block_after_photo_stores_no_pending(monkeypatch):
    _patch_photo(monkeypatch)
    monkeypatch.setattr(mod, "_ig_post_host_media", lambda p: "https://pub/x.jpg")
    # guard_spend: first call (photo) passes через _do(), second (caption) blocks.
    calls = {"n": 0}

    def _guard(uid, uname, est, do):
        calls["n"] += 1
        if calls["n"] == 1:
            return do(), None
        return None, "🚫 лимит исчерпан"

    monkeypatch.setattr(mod, "guard_spend", _guard)
    sent = {}
    monkeypatch.setattr(mod, "send", lambda cid, t, *a, **k: sent.setdefault("t", t))
    kb = []
    monkeypatch.setattr(mod, "send_with_keyboard", lambda *a, **k: kb.append(1))

    state = {}
    mod._ig_gen_dispatch(ADMIN, "тема", state)

    assert PENDING_KEY not in state
    assert not kb
    assert "лимит" in sent["t"]


def test_ig_gen_dispatch_happy_stores_pending_and_shows_card(monkeypatch):
    _patch_photo(monkeypatch, url="https://replicate/gen.jpg", local="C:/tmp/ig_gen_x.jpg")
    _patch_caption(monkeypatch)
    _patch_guard_spend_passthrough(monkeypatch)
    monkeypatch.setattr(mod, "_ig_post_host_media", lambda p: "https://pub/x.jpg")
    monkeypatch.setattr(mod, "save_state", lambda s: None)
    monkeypatch.setattr(mod, "_send_photo_url", lambda *a, **k: None)
    kb_sent = []
    monkeypatch.setattr(mod, "send_with_keyboard",
                        lambda cid, t, kb, *a, **k: kb_sent.append((t, kb)))
    monkeypatch.setattr(mod, "send", lambda *a, **k: None)

    state = {}
    mod._ig_gen_dispatch(ADMIN, "новий сезонний напій", state)

    p = state[PENDING_KEY]
    assert p["photo_url"] == "https://pub/x.jpg"
    assert "Смачна кава" in p["caption"]
    assert p["topic"] == "новий сезонний напій"
    assert p["source"] == "C:/tmp/ig_gen_x.jpg"
    assert len(kb_sent) == 1
    cbs = [btn.get("callback_data") for row in kb_sent[0][1] for btn in row]
    assert "igpost:publish" in cbs
    assert "igpost:regen" in cbs
    assert "igpost:cancel" in cbs
    assert "https://pub/x.jpg" in kb_sent[0][0]


# ---- clients/<name>/brand.md wiring ---------------------------------------


def test_ig_gen_dispatch_client_arg_merges_brand_into_photo_prompt_and_caption(monkeypatch):
    # vera_ai_ua/brand.md has persona_media.persona_id configured -> nopersona
    # forces the old generic-photo path (see persona routing tests below) so
    # this test can keep exercising visual_style merging in isolation.
    photo_prompts = []
    monkeypatch.setattr(mod, "_ig_gen_generate_photo",
                        lambda prompt: photo_prompts.append(prompt) or "https://replicate/gen.jpg")
    monkeypatch.setattr(mod, "_ig_gen_download_photo", lambda u: "C:/tmp/ig_gen_x.jpg")
    _patch_guard_spend_passthrough(monkeypatch)
    monkeypatch.setattr(mod, "_ig_post_host_media", lambda p: "https://pub/x.jpg")
    monkeypatch.setattr(mod, "save_state", lambda s: None)
    monkeypatch.setattr(mod, "_send_photo_url", lambda *a, **k: None)
    kb_sent = []
    monkeypatch.setattr(mod, "send_with_keyboard",
                        lambda cid, t, kb, *a, **k: kb_sent.append((t, kb)))
    monkeypatch.setattr(mod, "send", lambda *a, **k: None)
    captured = {}

    def _fake_llm(system, messages):
        captured["messages"] = messages
        return "Привіт! #ші #автоматизація"

    monkeypatch.setattr(mod, "_ig_caption_ask_llm", _fake_llm)

    state = {}
    mod._ig_gen_dispatch(ADMIN, "client=vera_ai_ua nopersona новий пост про ші", state)

    assert photo_prompts == ["новий пост про ші, генеративна естетика ШІ-аватара Віри: "
                             "яскраві акценти, чисті кадри, сучасний технологічний стиль "
                             "без кітчу і без фотореалістичних людей"]
    assert "молодий" in captured["messages"][0]["content"]
    assert state[PENDING_KEY]["topic"] == "новий пост про ші"


def _patch_brand(monkeypatch, brand):
    monkeypatch.setattr("app.services.brand_config.load_brand_config", lambda client: brand)


def _patch_common(monkeypatch):
    _patch_guard_spend_passthrough(monkeypatch)
    monkeypatch.setattr(mod, "_ig_gen_download_photo", lambda u: "C:/tmp/ig_gen_x.jpg")
    _patch_caption(monkeypatch)
    monkeypatch.setattr(mod, "_ig_post_host_media", lambda p: "https://pub/x.jpg")
    monkeypatch.setattr(mod, "save_state", lambda s: None)
    monkeypatch.setattr(mod, "_send_photo_url", lambda *a, **k: None)
    monkeypatch.setattr(mod, "send_with_keyboard", lambda *a, **k: None)
    monkeypatch.setattr(mod, "send", lambda *a, **k: None)


def test_ig_gen_dispatch_persona_id_routes_to_persona_engine_with_lora(monkeypatch):
    """С persona_id в brand.md (secция persona_media) вызов уходит в
    persona-движок с LoRA (_ig_gen_generate_photo_persona), а не в генерик
    FLUX (_ig_gen_generate_photo)."""
    _patch_brand(monkeypatch, {
        "persona_media": {"persona_id": "persona_af2f54ee",
                          "style": "реалізм, тепле світло, сучасний контекст"},
    })
    _patch_common(monkeypatch)
    persona_calls = {}
    monkeypatch.setattr(
        mod, "_ig_gen_generate_photo_persona",
        lambda pid, prompt, **kw: persona_calls.update(pid=pid, prompt=prompt) or "https://lora/gen.jpg",
    )
    old_calls = {"n": 0}
    monkeypatch.setattr(
        mod, "_ig_gen_generate_photo",
        lambda prompt: old_calls.__setitem__("n", old_calls["n"] + 1) or "u",
    )

    state = {}
    mod._ig_gen_dispatch(ADMIN, "client=vera_ai_ua перше знайомство: хто така Віра", state)

    assert persona_calls["pid"] == "persona_af2f54ee"
    # Короткі фото-якорі (photo, natural light) + промпт-правило мейку; БЕЗ
    # інлайн-негативів — FLUX-dev не парсить заперечення й "no anime/…" у
    # позитивному промпті ПРИЗИВАЄ його (live-доказано 2026-07-12).
    assert persona_calls["prompt"] == (
        "перше знайомство: хто така Віра, реалізм, тепле світло, сучасний контекст, "
        "photo, natural light, light natural makeup, natural lips"
    )
    assert old_calls["n"] == 0
    assert state[PENDING_KEY]["photo_url"] == "https://pub/x.jpg"


def test_ig_gen_generate_photo_persona_forwards_combat_params(monkeypatch):
    """``_ig_gen_generate_photo_persona`` має прокидати бойові параметри
    (scale/guidance/aspect) у ``PhotoGenerator.generate_photo``."""
    captured = {}

    class _FakeGen:
        def __init__(self, *a, **k):
            pass

        async def generate_photo(self, pid, prompt, **kw):
            captured.update(pid=pid, prompt=prompt, **kw)
            return {"image_url": "https://x/y.jpg"}

    monkeypatch.setattr(
        "app.services.block_m1_persona.photo_generator.PhotoGenerator", _FakeGen)
    monkeypatch.setattr(
        "app.services.block_m_common.persona_storage.PersonaStorage", lambda *a, **k: object())
    monkeypatch.setattr(
        "app.services.block_m_common.replicate_video_client.ReplicateVideoClient",
        lambda *a, **k: object())
    monkeypatch.setattr(
        "app.services.block_m_common.cost_tracker.CostTracker", lambda *a, **k: object())

    url = mod._ig_gen_generate_photo_persona(
        "persona_x", "scene", lora_scale=1.1, guidance=4.0, aspect_ratio="3:4")

    assert url == "https://x/y.jpg"
    assert captured["lora_scale"] == 1.1
    assert captured["guidance"] == 4.0
    assert captured["aspect_ratio"] == "3:4"


def test_ig_gen_dispatch_forwards_combat_params_from_brand(monkeypatch):
    """Бойові параметри Вери живуть у ``brand.md`` ``persona_media``
    (lora_scale/guidance/aspect_ratio) і мають доходити до персона-движка."""
    _patch_brand(monkeypatch, {
        "persona_media": {"persona_id": "persona_68fb76b2", "style": "S",
                          "lora_scale": 1.1, "guidance": 4.0, "aspect_ratio": "3:4"},
    })
    _patch_common(monkeypatch)
    captured = {}
    monkeypatch.setattr(
        mod, "_ig_gen_generate_photo_persona",
        lambda pid, prompt, **kw: captured.update(pid=pid, **kw) or "https://lora/gen.jpg")

    state = {}
    mod._ig_gen_dispatch(ADMIN, "client=vera_ai_ua тема", state)

    assert captured["pid"] == "persona_68fb76b2"
    assert captured["lora_scale"] == 1.1
    assert captured["guidance"] == 4.0
    assert captured["aspect_ratio"] == "3:4"


def test_ig_gen_photo_prompt_persona_adds_short_anchors_without_style():
    """Без ``persona_media.style`` короткі фото-якорі все одно додаються."""
    prompt = mod._ig_gen_photo_prompt_persona("тема", {"persona_id": "p1"})
    assert prompt == "тема, photo, natural light, light natural makeup, natural lips"


def test_ig_gen_photo_prompt_persona_includes_makeup_rule():
    """Промпт-правило (боєве): LoRA вивчила ТЯЖКИЙ мейк датасету дефолтом →
    у кожен персона-промпт додаємо 'light natural makeup, natural lips'
    (позитивний якір, не негатив)."""
    prompt = mod._ig_gen_photo_prompt_persona("тема", {"persona_id": "p1"})
    assert "light natural makeup" in prompt
    assert "natural lips" in prompt


def test_ig_gen_photo_prompt_persona_has_no_inline_negatives_or_pores():
    """Інлайн-негативи ("no <term>") та буквальне "pores" видалені —
    вони давали зворотний ефект (аніме/підпис, пор-точки)."""
    prompt = mod._ig_gen_photo_prompt_persona("тема", {"style": "реалізм"})
    assert "no " not in prompt
    assert "pores" not in prompt
    assert "anime" not in prompt


def test_ig_gen_dispatch_nopersona_forces_old_path_even_with_persona_id(monkeypatch):
    """Явный оверрайд ``nopersona`` — генерит без персоны (напр. рубрика
    «Пост дня» с чистой эстетикой), даже если у клиента настроена персона."""
    _patch_brand(monkeypatch, {
        "persona_media": {"persona_id": "persona_af2f54ee", "style": "реалізм"},
        "visual_style": "чисті кадри",
    })
    _patch_common(monkeypatch)
    persona_calls = {"n": 0}
    monkeypatch.setattr(
        mod, "_ig_gen_generate_photo_persona",
        lambda pid, prompt: persona_calls.__setitem__("n", persona_calls["n"] + 1) or "u",
    )
    old_prompts = []
    monkeypatch.setattr(
        mod, "_ig_gen_generate_photo",
        lambda prompt: old_prompts.append(prompt) or "https://replicate/gen.jpg",
    )

    state = {}
    mod._ig_gen_dispatch(ADMIN, "client=vera_ai_ua nopersona пост дня", state)

    assert persona_calls["n"] == 0
    assert old_prompts == ["пост дня, чисті кадри"]
    assert state[PENDING_KEY]["topic"] == "пост дня"


def test_ig_gen_dispatch_client_without_persona_id_uses_old_path(monkeypatch):
    """Клиент с brand.md, но БЕЗ persona_media/persona_id -> старое поведение
    (генерик FLUX), persona-движок не трогается."""
    _patch_brand(monkeypatch, {"visual_style": "чисті кадри"})
    _patch_common(monkeypatch)
    persona_calls = {"n": 0}
    monkeypatch.setattr(
        mod, "_ig_gen_generate_photo_persona",
        lambda pid, prompt: persona_calls.__setitem__("n", persona_calls["n"] + 1) or "u",
    )
    old_prompts = []
    monkeypatch.setattr(
        mod, "_ig_gen_generate_photo",
        lambda prompt: old_prompts.append(prompt) or "https://replicate/gen.jpg",
    )

    state = {}
    mod._ig_gen_dispatch(ADMIN, "client=some_client тема", state)

    assert persona_calls["n"] == 0
    assert old_prompts == ["тема, чисті кадри"]


def test_ig_gen_dispatch_without_client_config_is_unchanged(monkeypatch):
    monkeypatch.delenv("IG_CLIENT", raising=False)
    photo_prompts = []
    monkeypatch.setattr(mod, "_ig_gen_generate_photo",
                        lambda prompt: photo_prompts.append(prompt) or "https://replicate/gen.jpg")
    monkeypatch.setattr(mod, "_ig_gen_download_photo", lambda u: "C:/tmp/ig_gen_x.jpg")
    _patch_caption(monkeypatch)
    _patch_guard_spend_passthrough(monkeypatch)
    monkeypatch.setattr(mod, "_ig_post_host_media", lambda p: "https://pub/x.jpg")
    monkeypatch.setattr(mod, "save_state", lambda s: None)
    monkeypatch.setattr(mod, "_send_photo_url", lambda *a, **k: None)
    monkeypatch.setattr(mod, "send_with_keyboard", lambda *a, **k: None)
    monkeypatch.setattr(mod, "send", lambda *a, **k: None)

    state = {}
    mod._ig_gen_dispatch(ADMIN, "новий сезонний напій", state)

    assert photo_prompts == ["новий сезонний напій"]  # no brand -> prompt untouched


# ---- multi-account (@<account_key> / client=<name> brand mapping) ---------


def test_ig_gen_dispatch_explicit_account_arg_stored_in_pending(monkeypatch):
    _patch_photo(monkeypatch)
    _patch_caption(monkeypatch)
    _patch_guard_spend_passthrough(monkeypatch)
    monkeypatch.setattr(mod, "_ig_post_host_media", lambda p: "https://pub/x.jpg")
    monkeypatch.setattr(mod, "save_state", lambda s: None)
    monkeypatch.setattr(mod, "_send_photo_url", lambda *a, **k: None)
    monkeypatch.setattr(mod, "send_with_keyboard", lambda *a, **k: None)
    monkeypatch.setattr(mod, "send", lambda *a, **k: None)

    state = {}
    mod._ig_gen_dispatch(ADMIN, "@vera_ai_ua тема поста", state)

    assert state[PENDING_KEY]["account_key"] == "vera_ai_ua"
    assert state[PENDING_KEY]["topic"] == "тема поста"


def test_ig_gen_dispatch_client_arg_maps_to_account_key(monkeypatch):
    """No explicit @account_key -> client=vera_ai_ua's brand.md maps to
    account_key 'vera_ai_ua' (dir-name convention, no account_key field set
    in this fixture brand.md)."""
    _patch_photo(monkeypatch)
    _patch_caption(monkeypatch)
    _patch_guard_spend_passthrough(monkeypatch)
    monkeypatch.setattr(mod, "_ig_post_host_media", lambda p: "https://pub/x.jpg")
    monkeypatch.setattr(mod, "save_state", lambda s: None)
    monkeypatch.setattr(mod, "_send_photo_url", lambda *a, **k: None)
    monkeypatch.setattr(mod, "send_with_keyboard", lambda *a, **k: None)
    monkeypatch.setattr(mod, "send", lambda *a, **k: None)

    state = {}
    mod._ig_gen_dispatch(ADMIN, "client=vera_ai_ua тема поста", state)

    assert state[PENDING_KEY]["account_key"] == "vera_ai_ua"


def test_ig_gen_dispatch_no_account_or_client_pending_account_key_none(monkeypatch):
    monkeypatch.delenv("IG_CLIENT", raising=False)
    _patch_photo(monkeypatch)
    _patch_caption(monkeypatch)
    _patch_guard_spend_passthrough(monkeypatch)
    monkeypatch.setattr(mod, "_ig_post_host_media", lambda p: "https://pub/x.jpg")
    monkeypatch.setattr(mod, "save_state", lambda s: None)
    monkeypatch.setattr(mod, "_send_photo_url", lambda *a, **k: None)
    monkeypatch.setattr(mod, "send_with_keyboard", lambda *a, **k: None)
    monkeypatch.setattr(mod, "send", lambda *a, **k: None)

    state = {}
    mod._ig_gen_dispatch(ADMIN, "тема поста", state)

    assert state[PENDING_KEY]["account_key"] is None


def test_ig_gen_dispatch_explicit_account_wins_over_client_mapping(monkeypatch):
    _patch_photo(monkeypatch)
    _patch_caption(monkeypatch)
    _patch_guard_spend_passthrough(monkeypatch)
    monkeypatch.setattr(mod, "_ig_post_host_media", lambda p: "https://pub/x.jpg")
    monkeypatch.setattr(mod, "save_state", lambda s: None)
    monkeypatch.setattr(mod, "_send_photo_url", lambda *a, **k: None)
    monkeypatch.setattr(mod, "send_with_keyboard", lambda *a, **k: None)
    monkeypatch.setattr(mod, "send", lambda *a, **k: None)

    state = {}
    mod._ig_gen_dispatch(ADMIN, "@other_account client=vera_ai_ua тема", state)

    assert state[PENDING_KEY]["account_key"] == "other_account"


# ---- reuse of the existing ig_post preview/publish flow --------------------


def _cq(data):
    return {
        "id": "cq1", "data": data,
        "message": {"chat": {"id": int(ADMIN)}, "message_id": 55},
        "from": {"id": int(ADMIN)},
    }


def test_ig_gen_pending_card_publishes_through_shared_igpost_callback(monkeypatch):
    """The preview card /ig_gen shows uses the SAME igpost: callback namespace as
    /ig_post — no new publish/regen/cancel code needed, just shared pending state."""
    published = []

    class _FakeIG:
        def __init__(self, *a, **k):
            pass

        def publish_photo(self, url, caption):
            published.append((url, caption))
            return {"id": "media_1", "permalink": "https://www.instagram.com/p/BBB/"}

    monkeypatch.setattr("app.services.instagram_api.InstagramAPI", _FakeIG)
    monkeypatch.setattr(mod, "save_state", lambda s: None)
    monkeypatch.setattr(mod, "answer_callback_query", lambda *a, **k: None)
    monkeypatch.setattr(mod, "edit_message_with_keyboard", lambda *a, **k: None)
    sent = []
    monkeypatch.setattr(mod, "send", lambda cid, t, *a, **k: sent.append(t))

    state = {PENDING_KEY: {"photo_url": "https://pub/gen.jpg", "caption": "Смачно",
                           "topic": "кава", "source": "C:/tmp/ig_gen_x.jpg"}}
    mod.handle_callback_query(_cq("igpost:publish"), state)

    assert published == [("https://pub/gen.jpg", "Смачно")]
    assert any("instagram.com/p/BBB" in t for t in sent)
    assert PENDING_KEY not in state


def test_ig_gen_pending_card_publish_fail_closed_on_quota(monkeypatch):
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

    state = {PENDING_KEY: {"photo_url": "https://pub/gen.jpg", "caption": "c",
                           "topic": "кава", "source": "C:/tmp/ig_gen_x.jpg"}}
    mod.handle_callback_query(_cq("igpost:publish"), state)

    joined = " ".join(sent).lower()
    assert "лимит" in joined or "25" in joined
    assert not any("instagram.com/p/" in t for t in sent)
    assert PENDING_KEY in state              # kept for a retry (NOT published)
