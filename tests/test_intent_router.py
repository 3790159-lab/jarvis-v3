# -*- coding: utf-8 -*-
"""IR-1 (intent-router) — offline keyword+fuzzy layer. $0, no network, pure.

Tests the dataset/corpus, the scorer/decision, and the money/role invariants of
tools/intent_router.py. Wiring into the bot lives in test_intent_router_wiring.py.
"""
from tools import intent_router as ir


# ── Task 1: corpus built from the menu registry ────────────────────────────
def test_corpus_covers_all_menu_commands():
    corpus = ir.build_corpus()
    assert len(corpus) == 110
    for cmd in ("/health", "/menu_photo", "/costs", "/git_status", "/my_stats"):
        assert cmd in corpus


def test_corpus_health_has_label_token():
    corpus = ir.build_corpus()
    # label "здоровье Jarvis" → normalized token "здоровье"
    assert "здоровье" in corpus["/health"].tokens


def test_corpus_labelless_command_uses_native_description_or_cmd():
    corpus = ir.build_corpus()
    # /costs has label=None in MENU but native description "Траты за сегодня"
    toks = corpus["/costs"].tokens
    assert "траты" in toks or "costs" in toks
