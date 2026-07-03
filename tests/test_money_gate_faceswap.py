"""Зубы money-гейта faceswap/enhance/me_into: платный вызов ТОЛЬКО через guard_spend.

Точки траты: enhance_face (шаг enhance_upload), face_swap_basic (шаг meinto_target
и callback fs:exec). Мокаем load_conv/clear_conv, чтобы задать шаг без диска.
"""
import tools.photo_studio_telegram as ps


def _conv(monkeypatch, conv):
    monkeypatch.setattr(ps, "load_conv", lambda cid: conv)
    monkeypatch.setattr(ps, "clear_conv", lambda cid: None)


# ── enhance (GFPGAN) ──────────────────────────────────────────────────────────

def test_enhance_over_limit_never_calls_enhance_face(monkeypatch):
    _conv(monkeypatch, {"step": "enhance_upload", "data": {}})
    calls = {"n": 0}
    monkeypatch.setattr(ps, "guard_spend", lambda uid, un, est, do: (None, "лимит"))
    monkeypatch.setattr("app.services.face_swap.enhance_face",
                        lambda *a, **k: calls.__setitem__("n", calls["n"] + 1) or "url")
    sent = []
    ps.handle_faceswap_photo_step("42", "http://photo", lambda cid, t, **k: sent.append(t), lambda *a, **k: None)
    assert calls["n"] == 0
    assert any("🚫" in s or "лимит" in s.lower() for s in sent)


def test_enhance_success_routes_through_guard(monkeypatch):
    _conv(monkeypatch, {"step": "enhance_upload", "data": {}})
    seen = {}
    monkeypatch.setattr(ps, "guard_spend", lambda uid, un, est, do: seen.update(est=est) or (do(), None))
    monkeypatch.setattr("app.services.face_swap.enhance_face", lambda *a, **k: "http://enh")
    photos = []
    ps.handle_faceswap_photo_step("42", "http://photo", lambda *a, **k: None, lambda cid, url, **k: photos.append(url))
    assert seen["est"] > 0 and photos == ["http://enh"]


# ── faceswap swap (callback fs:exec) ──────────────────────────────────────────

def test_faceswap_over_limit_never_swaps(monkeypatch):
    _conv(monkeypatch, {"data": {"source_url": "s", "target_url": "t"}})
    calls = {"n": 0}
    monkeypatch.setattr(ps, "guard_spend", lambda uid, un, est, do: (None, "лимит"))
    monkeypatch.setattr("app.services.face_swap.face_swap_basic",
                        lambda *a, **k: calls.__setitem__("n", calls["n"] + 1) or "url")
    sent = []
    ps.handle_faceswap_callback("42", "fs:exec:basic", lambda cid, t, **k: sent.append(t), lambda *a, **k: None)
    assert calls["n"] == 0
    assert any("🚫" in s or "лимит" in s.lower() for s in sent)


def test_faceswap_success_routes_through_guard(monkeypatch):
    _conv(monkeypatch, {"data": {"source_url": "s", "target_url": "t"}})
    seen = {}
    monkeypatch.setattr(ps, "guard_spend", lambda uid, un, est, do: seen.update(est=est) or (do(), None))
    monkeypatch.setattr("app.services.face_swap.face_swap_basic", lambda *a, **k: "http://swap")
    photos = []
    ps.handle_faceswap_callback("42", "fs:exec:basic", lambda *a, **k: None, lambda cid, url, **k: photos.append(url))
    assert seen["est"] > 0 and photos == ["http://swap"]


# ── me_into (face_swap_basic на сохранённом лице) ─────────────────────────────

def test_me_into_over_limit_never_swaps(monkeypatch, tmp_path):
    _conv(monkeypatch, {"step": "meinto_target", "data": {}})
    # положить сохранённое лицо, чтобы флоу дошёл до гейта
    face = ps._ROOT / "state" / "my_face_url.txt"
    face.parent.mkdir(parents=True, exist_ok=True)
    existed = face.exists()
    backup = face.read_text(encoding="utf-8") if existed else None
    face.write_text("http://myface", encoding="utf-8")
    try:
        calls = {"n": 0}
        monkeypatch.setattr(ps, "guard_spend", lambda uid, un, est, do: (None, "лимит"))
        monkeypatch.setattr("app.services.face_swap.face_swap_basic",
                            lambda *a, **k: calls.__setitem__("n", calls["n"] + 1) or "url")
        sent = []
        ps.handle_faceswap_photo_step("42", "http://target", lambda cid, t, **k: sent.append(t), lambda *a, **k: None)
        assert calls["n"] == 0
        assert any("🚫" in s or "лимит" in s.lower() for s in sent)
    finally:
        if backup is not None:
            face.write_text(backup, encoding="utf-8")
        elif face.exists():
            face.unlink()
