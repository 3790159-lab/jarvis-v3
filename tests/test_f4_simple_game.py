"""Phase F.4: /simple_game command for HTML5 games."""
from __future__ import annotations

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def test_all_game_types_present():
    from app.services.game_generator import GAMES
    for expected in ["snake", "tictactoe", "memory", "2048"]:
        assert expected in GAMES, f"Game type '{expected}' missing"


def test_generate_snake_creates_html(tmp_path):
    from app.services.game_generator import generate_game
    path = generate_game("snake", str(tmp_path))
    assert os.path.exists(path)
    content = open(path, encoding="utf-8").read()
    assert "Snake" in content or "snake" in content.lower()
    assert "<!DOCTYPE html>" in content


def test_generate_tictactoe_creates_html(tmp_path):
    from app.services.game_generator import generate_game
    path = generate_game("tictactoe", str(tmp_path))
    content = open(path, encoding="utf-8").read()
    assert "Tic" in content or "tic" in content.lower()


def test_generate_memory_creates_html(tmp_path):
    from app.services.game_generator import generate_game
    path = generate_game("memory", str(tmp_path))
    content = open(path, encoding="utf-8").read()
    assert "Memory" in content or "memory" in content.lower()


def test_generate_2048_creates_html(tmp_path):
    from app.services.game_generator import generate_game
    path = generate_game("2048", str(tmp_path))
    content = open(path, encoding="utf-8").read()
    assert "2048" in content


def test_unknown_game_raises_value_error(tmp_path):
    from app.services.game_generator import generate_game
    try:
        generate_game("chess", str(tmp_path))
        assert False, "Should raise ValueError"
    except ValueError as e:
        assert "chess" in str(e).lower() or "Unknown" in str(e)


def test_cmd_simple_game_no_args_shows_usage():
    import tools.jarvis_smart_telegram_control as mod
    sent = []
    original_send = mod.send
    try:
        mod.send = lambda cid, text, **kw: sent.append(text)
        mod.cmd_simple_game("c1", "")
    finally:
        mod.send = original_send
    assert sent
    assert "snake" in sent[0].lower() or "Использование" in sent[0]


def test_cmd_simple_game_registered_in_handle_command():
    import tools.jarvis_smart_telegram_control as mod
    import inspect
    src = inspect.getsource(mod.handle_command)
    assert '"/simple_game"' in src or "'/simple_game'" in src
