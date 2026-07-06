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


# ── Task 2: scorer + decision (resolve, admin role) ────────────────────────
def _cmds(res):
    return [c.cmd for c in res.candidates]


def test_resolve_routes_bot_health_phrase():
    res = ir.resolve("глянь что с ботом", "admin")
    assert res.decision == "route"
    assert res.candidates[0].cmd == "/health"


def test_resolve_routes_spending_phrase():
    res = ir.resolve("что я потратил сегодня", "admin")
    assert res.decision == "route"
    assert res.candidates[0].cmd in ("/costs", "/my_stats")


def test_resolve_routes_menu_photo_phrase():
    res = ir.resolve("сделай фото блюда для меню", "admin")
    assert res.decision == "route"
    assert res.candidates[0].cmd == "/menu_photo"


def test_resolve_unknown_text_is_none():
    res = ir.resolve("расскажи про квантовую запутанность подробно", "admin")
    assert res.decision == "none"


def test_resolve_gibberish_is_none():
    res = ir.resolve("асдфгхйцукен", "admin")
    assert res.decision == "none"


def test_resolve_ambiguous_short_phrase_clarifies():
    # "фото" alone matches many photo/me commands → clarify, not a blind guess
    res = ir.resolve("фото", "admin")
    assert res.decision == "clarify"
    assert len(res.candidates) >= 2
