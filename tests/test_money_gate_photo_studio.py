"""Зубы money-гейта Photo Studio: платный вызов ТОЛЬКО через guard_spend.

Приём: мокаем ps.guard_spend так, что при отказе do_spend НЕ вызывается →
если хендлер зовёт платный сервис в обход guard, спай сервиса это ловит.
"""
import tools.photo_studio_telegram as ps


# ── T2 restaurant_mode ────────────────────────────────────────────────────────

def test_menu_photo_over_limit_never_generates(monkeypatch):
    gen = {"n": 0}
    monkeypatch.setattr(ps, "guard_spend", lambda uid, un, est, do: (None, "Дневной лимит $1.00 исчерпан"))
    monkeypatch.setattr("app.services.restaurant_mode.generate_dish_photo",
                        lambda *a, **k: gen.__setitem__("n", gen["n"] + 1) or "http://x")
    sent = []
    ps.handle_menu_photo("42", "паста", lambda cid, t, **k: sent.append(t), lambda *a, **k: None)
    assert gen["n"] == 0                                   # ЗУБ 1: генерации не было
    assert any("🚫" in s or "лимит" in s.lower() for s in sent)


def test_menu_photo_success_routes_through_guard(monkeypatch):
    seen = {}
    monkeypatch.setattr(ps, "guard_spend",
                        lambda uid, un, est, do: seen.update(uid=uid, un=un, est=est) or (do(), None))
    monkeypatch.setattr("app.services.restaurant_mode.generate_dish_photo", lambda *a, **k: "http://img")
    photos = []
    ps.handle_menu_photo("42", "паста", lambda *a, **k: None, lambda cid, url, **k: photos.append(url))
    assert seen["uid"] == "42" and seen["un"] is None and seen["est"] > 0
    assert photos == ["http://img"]


def test_social_post_over_limit_never_generates(monkeypatch):
    gen = {"n": 0}
    monkeypatch.setattr(ps, "guard_spend", lambda uid, un, est, do: (None, "лимит"))
    monkeypatch.setattr("app.services.restaurant_mode.generate_social_post",
                        lambda *a, **k: gen.__setitem__("n", gen["n"] + 1) or {"url": "x"})
    sent = []
    ps.handle_social_post("42", "тирамису", lambda cid, t, **k: sent.append(t), lambda *a, **k: None)
    assert gen["n"] == 0
    assert any("🚫" in s or "лимит" in s.lower() for s in sent)


def test_menu_book_over_limit_never_generates(monkeypatch):
    gen = {"n": 0}
    monkeypatch.setattr(ps, "guard_spend", lambda uid, un, est, do: (None, "лимит"))
    monkeypatch.setattr("app.services.restaurant_mode.generate_menu_series",
                        lambda *a, **k: gen.__setitem__("n", gen["n"] + 1) or [])
    sent = []
    ps.handle_menu_book("42", "борщ, стейк", lambda cid, t, **k: sent.append(t), lambda *a, **k: None)
    assert gen["n"] == 0
    assert any("🚫" in s or "лимит" in s.lower() for s in sent)


def test_menu_book_est_scales_with_dish_count(monkeypatch):
    seen = {}
    monkeypatch.setattr(ps, "guard_spend",
                        lambda uid, un, est, do: seen.update(est=est) or (do(), None))
    monkeypatch.setattr("app.services.restaurant_mode.generate_menu_series",
                        lambda dishes, *a, **k: [{"url": "u", "dish": d} for d in dishes])
    ps.handle_menu_book("42", "борщ, стейк, суп", lambda *a, **k: None, lambda *a, **k: None)
    per = float(__import__("os").getenv("PHOTO_MENU_BOOK_USD", "0.04"))
    assert abs(seen["est"] - per * 3) < 1e-9              # оценка = per × N блюд


# ── T3 party_mode ─────────────────────────────────────────────────────────────

def test_party_promo_over_limit_never_generates(monkeypatch):
    gen = {"n": 0}
    monkeypatch.setattr(ps, "guard_spend", lambda uid, un, est, do: (None, "лимит"))
    monkeypatch.setattr("app.services.party_mode.generate_party_promo",
                        lambda *a, **k: gen.__setitem__("n", gen["n"] + 1) or {"poster_url": "x"})
    sent = []
    ps.handle_party_promo("42", "halloween", lambda cid, t, **k: sent.append(t), lambda *a, **k: None)
    assert gen["n"] == 0
    assert any("🚫" in s or "лимит" in s.lower() for s in sent)


def test_party_promo_success_routes_through_guard(monkeypatch):
    seen = {}
    monkeypatch.setattr(ps, "guard_spend",
                        lambda uid, un, est, do: seen.update(est=est) or (do(), None))
    monkeypatch.setattr("app.services.party_mode.generate_party_promo",
                        lambda *a, **k: {"poster_url": "http://p", "promo_text": "yo"})
    photos = []
    ps.handle_party_promo("42", "halloween", lambda *a, **k: None, lambda cid, url, **k: photos.append(url))
    assert seen["est"] > 0 and photos == ["http://p"]


def test_invite_card_over_limit_never_generates(monkeypatch):
    gen = {"n": 0}
    monkeypatch.setattr(ps, "guard_spend", lambda uid, un, est, do: (None, "лимит"))
    monkeypatch.setattr("app.services.party_mode.generate_invite_card",
                        lambda *a, **k: gen.__setitem__("n", gen["n"] + 1) or {"card_url": "x"})
    sent = []
    ps.handle_invite_card("42", 'Иван "ДР" "5 мая"', lambda cid, t, **k: sent.append(t), lambda *a, **k: None)
    assert gen["n"] == 0
    assert any("🚫" in s or "лимит" in s.lower() for s in sent)


def test_event_photo_over_limit_never_generates(monkeypatch):
    gen = {"n": 0}
    monkeypatch.setattr(ps, "guard_spend", lambda uid, un, est, do: (None, "лимит"))
    monkeypatch.setattr("app.services.party_mode.generate_event_photo",
                        lambda *a, **k: gen.__setitem__("n", gen["n"] + 1) or {"url": "x"})
    sent = []
    ps.handle_event_photo("42", "банкетный зал", lambda cid, t, **k: sent.append(t), lambda *a, **k: None)
    assert gen["n"] == 0
    assert any("🚫" in s or "лимит" in s.lower() for s in sent)
