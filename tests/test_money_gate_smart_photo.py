"""Зубы money-гейта /smart_photo и /pro_food (T8 Арки 1).

Обе синхронно тратят через generate_images_replicate (FLUX) без гейта.
"""
import importlib

ctl = importlib.import_module("tools.jarvis_smart_telegram_control")


def test_smart_photo_over_limit_never_generates(monkeypatch):
    gen = {"n": 0}
    monkeypatch.setattr(ctl, "guard_spend", lambda uid, un, est, do: (None, "лимит"), raising=False)
    monkeypatch.setattr("app.services.replicate_image_gen.generate_images_replicate",
                        lambda *a, **k: gen.__setitem__("n", gen["n"] + 1) or ["http://x"])
    sent = []
    monkeypatch.setattr(ctl, "send", lambda cid, t, **k: sent.append(t))
    ctl.cmd_smart_photo("42", "паста с морепродуктами")
    assert gen["n"] == 0
    assert any("🚫" in s or "лимит" in s.lower() for s in sent)


def test_smart_photo_success_routes_through_guard(monkeypatch):
    seen = {}
    monkeypatch.setattr(ctl, "guard_spend",
                        lambda uid, un, est, do: seen.update(est=est) or (do(), None), raising=False)
    monkeypatch.setattr("app.services.smart_prompts.smart_enhance",
                        lambda q: {"category": "food", "enhanced": "pro " + q, "from_cache": False})
    monkeypatch.setattr("app.services.replicate_image_gen.generate_images_replicate",
                        lambda *a, **k: ["http://img"])
    monkeypatch.setattr(ctl, "send", lambda *a, **k: None)
    photos = []
    monkeypatch.setattr(ctl, "_send_photo_url", lambda cid, url, *a, **k: photos.append(url))
    ctl.cmd_smart_photo("42", "паста")
    assert seen["est"] > 0 and photos == ["http://img"]


def test_pro_food_over_limit_never_generates(monkeypatch):
    gen = {"n": 0}
    monkeypatch.setattr(ctl, "guard_spend", lambda uid, un, est, do: (None, "лимит"), raising=False)
    monkeypatch.setattr("app.services.replicate_image_gen.generate_images_replicate",
                        lambda *a, **k: gen.__setitem__("n", gen["n"] + 1) or ["http://x"])
    sent = []
    monkeypatch.setattr(ctl, "send", lambda cid, t, **k: sent.append(t))
    ctl.cmd_pro_food("42", "борщ")
    assert gen["n"] == 0
    assert any("🚫" in s or "лимит" in s.lower() for s in sent)


def test_pro_food_success_routes_through_guard(monkeypatch):
    seen = {}
    monkeypatch.setattr(ctl, "guard_spend",
                        lambda uid, un, est, do: seen.update(est=est) or (do(), None), raising=False)
    monkeypatch.setattr("app.services.smart_prompts.enhance_food_prompt", lambda q: "food " + q)
    monkeypatch.setattr("app.services.replicate_image_gen.generate_images_replicate",
                        lambda *a, **k: ["http://food"])
    monkeypatch.setattr(ctl, "send", lambda *a, **k: None)
    photos = []
    monkeypatch.setattr(ctl, "_send_photo_url", lambda cid, url, *a, **k: photos.append(url))
    ctl.cmd_pro_food("42", "борщ")
    assert seen["est"] > 0 and photos == ["http://food"]
