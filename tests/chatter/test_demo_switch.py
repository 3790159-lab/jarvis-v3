from __future__ import annotations
from pathlib import Path
from chatter.config.loader import load_config
from chatter.core.disclosure import HONESTY_MARKERS
from chatter.core.llm import FakeLLM
from chatter.demo_switch import DIALOGUE, collect_replies, render_columns

CLIENTS = Path(__file__).resolve().parents[2] / "chatter" / "clients"


def test_dialogue_has_four_turns_and_a_bot_question():
    assert len(DIALOGUE) == 4
    assert "bot" in DIALOGUE[-1].lower()


def test_collect_replies_demo_ru_honesty_turn():
    cfg = load_config(CLIENTS, "demo")
    llm = FakeLLM(scripted=["Привет!", "Консультация и фотосессия.", "От 5000 руб."])
    replies = collect_replies(cfg, llm, DIALOGUE)
    assert len(replies) == 4
    assert all(r.strip() for r in replies)
    assert HONESTY_MARKERS["ru"] in replies[-1]


def test_collect_replies_demo2_en_honesty_turn():
    cfg = load_config(CLIENTS, "demo2")
    llm = FakeLLM(scripted=["Hi.", "I automate workflows.", "From $3000 per workflow."])
    replies = collect_replies(cfg, llm, DIALOGUE)
    assert len(replies) == 4
    assert all(r.strip() for r in replies)
    assert HONESTY_MARKERS["en"] in replies[-1]


def test_render_columns_contains_labels_and_separator():
    rows = [("Hi there", "Привет!", "Hi.")]
    out = render_columns("Аня (ru)", "Dmitry (en)", rows)
    assert "Аня (ru)" in out
    assert "Dmitry (en)" in out
    assert " │ " in out
    assert "Hi there" in out
    assert "Привет!" in out
    assert "Hi." in out
