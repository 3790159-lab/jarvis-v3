"""H2 security fix — /api/jarvis/files/parse must require auth + confine paths.

Audit 2026-07-15 finding H3 (user task H2): unauthenticated arbitrary file read
(``{"path": "C:/jarvis/.env"}`` leaked secrets). Fix: require an API key and
resolve every requested path inside an allow-list of project directories,
rejecting absolute paths, ``..`` traversal, dotfiles and symlink escapes.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

KEY = "test-internal-key-h2"


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """Mini app with only the file-tools router, an allow-list rooted at tmp."""
    monkeypatch.setenv("JARVIS_INTERNAL_API_KEY", KEY)
    # Clear any real admin/public keys so tests are deterministic.
    monkeypatch.delenv("JARVIS_ADMIN_KEY", raising=False)

    # Allow-list points at a temp "incoming" dir with one sample file.
    incoming = tmp_path / "incoming_files"
    incoming.mkdir()
    (incoming / "note.txt").write_text("hello world", encoding="utf-8")
    # A secret that lives OUTSIDE the allow-list, mimicking C:/jarvis/.env
    secret = tmp_path / ".env"
    secret.write_text("BOT_TOKEN=supersecret", encoding="utf-8")

    monkeypatch.setenv("JARVIS_FILES_ALLOWED_ROOTS", str(incoming))

    import importlib
    import app.routers.jarvis_file_tools_router as mod
    importlib.reload(mod)

    app = FastAPI()
    app.include_router(mod.router)
    return TestClient(app), incoming, secret


def test_parse_without_key_is_401(client):
    c, incoming, _ = client
    r = c.post("/api/jarvis/files/parse", json={"path": "note.txt"})
    assert r.status_code == 401


def test_parse_absolute_path_to_env_is_403(client):
    """The headline attack: read C:/jarvis/.env by absolute path -> 403."""
    c, incoming, secret = client
    r = c.post(
        "/api/jarvis/files/parse",
        headers={"X-API-Key": KEY},
        json={"path": str(secret)},
    )
    assert r.status_code == 403
    assert "supersecret" not in r.text


def test_parse_traversal_escape_is_403(client):
    c, incoming, secret = client
    r = c.post(
        "/api/jarvis/files/parse",
        headers={"X-API-Key": KEY},
        json={"path": "../.env"},
    )
    assert r.status_code == 403
    assert "supersecret" not in r.text


def test_parse_dotfile_is_403(client):
    c, incoming, _ = client
    (incoming / ".secret").write_text("nope", encoding="utf-8")
    r = c.post(
        "/api/jarvis/files/parse",
        headers={"X-API-Key": KEY},
        json={"path": ".secret"},
    )
    assert r.status_code == 403


def test_parse_symlink_escape_is_403(client):
    """A symlink inside the allow-list pointing at the outside secret -> 403."""
    c, incoming, secret = client
    link = incoming / "innocent.txt"
    try:
        link.symlink_to(secret)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not permitted on this host")
    r = c.post(
        "/api/jarvis/files/parse",
        headers={"X-API-Key": KEY},
        json={"path": "innocent.txt"},
    )
    assert r.status_code == 403
    assert "supersecret" not in r.text


def test_parse_allowed_file_succeeds(client):
    c, incoming, _ = client
    r = c.post(
        "/api/jarvis/files/parse",
        headers={"X-API-Key": KEY},
        json={"path": "note.txt"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body.get("ok") is True
    assert "hello world" in body.get("text", "")
