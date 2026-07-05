# -*- coding: utf-8 -*-
"""Browser session artifact store + rotation. $0, filesystem only. See §5bis."""
from app.services.browser import session_store as ss


def test_open_session_creates_dir_and_writes_artifacts(tmp_path):
    p = ss.open_session("20260705_1", base_dir=tmp_path)
    assert p.exists()
    ss.write_artifact(p, "dom/page.html", "<html>hi</html>")
    ss.write_artifact(p, "screenshots/1.png", b"\x89PNG")
    assert (p / "dom" / "page.html").read_text(encoding="utf-8") == "<html>hi</html>"
    assert (p / "screenshots" / "1.png").read_bytes() == b"\x89PNG"


def test_rotate_keeps_newest_removes_oldest(tmp_path):
    for sid in ["s1", "s2", "s3", "s4"]:
        ss.open_session(sid, base_dir=tmp_path)
    removed = ss.rotate(base_dir=tmp_path, keep=2)
    remaining = ss.list_sessions(base_dir=tmp_path)
    assert set(remaining) == {"s3", "s4"}          # 2 newest by sortable id
    assert set(removed) == {"s1", "s2"}


def test_rotate_is_best_effort_on_missing_base(tmp_path):
    # a non-existent base must not raise (cleanup never blocks a run)
    assert ss.rotate(base_dir=tmp_path / "nope", keep=5) == []
