# -*- coding: utf-8 -*-
"""IR-1 (intent-router) — offline keyword+fuzzy layer. $0, no network, pure.

Tests the dataset/corpus, the scorer/decision, and the money/role invariants of
tools/intent_router.py. Wiring into the bot lives in test_intent_router_wiring.py.
"""
from tools import intent_router as ir


# ── Task 1: corpus built from the menu registry ────────────────────────────
def test_corpus_covers_all_menu_commands():
    corpus = ir.build_corpus()
    assert len(corpus) == 112
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


# ── Task 3: money invariant — FREE_AUTOEXEC / PAID ─────────────────────────
def test_free_and_paid_are_disjoint():
    # THE money invariant: a paid command can never be in the auto-exec set,
    # so paid commands are structurally impossible to run without confirmation.
    assert ir.FREE_AUTOEXEC & ir.PAID == frozenset()


def test_auto_exec_ok_true_for_free_read():
    assert ir.auto_exec_ok("/health") is True
    assert ir.auto_exec_ok("/costs") is True


def test_auto_exec_ok_false_for_paid_and_heavy():
    assert ir.auto_exec_ok("/menu_photo") is False  # paid
    assert ir.auto_exec_ok("/regress") is False      # heavy (~5 min) → confirm


def test_is_paid_flags_generation():
    assert ir.is_paid("/videoref") is True
    assert ir.is_paid("/menu_photo") is True
    assert ir.is_paid("/health") is False


def test_free_and_paid_sets_reference_real_commands():
    corpus = ir.build_corpus()
    for cmd in (ir.FREE_AUTOEXEC | ir.PAID):
        assert cmd in corpus, f"{cmd} not in menu registry"


# ── Task 4: friend role filter (injected allow-list) ───────────────────────
_FRIEND_SET = frozenset({"/animate", "/videoref", "/menu_photo",
                         "/my_stats", "/persona_photo"})


def test_friend_routes_only_allowed_command():
    res = ir.resolve("анимировать одно фото", "friend", friend_allowed=_FRIEND_SET)
    assert res.decision == "route"
    assert res.candidates[0].cmd == "/animate"


def test_friend_cannot_reach_admin_only_command():
    # /git_status is admin-only (not in friend set) → must never be a candidate
    res = ir.resolve("статус гита", "friend", friend_allowed=_FRIEND_SET)
    assert all(c.cmd != "/git_status" for c in res.candidates)


def test_friend_unknown_capability_is_none():
    res = ir.resolve("создать сайт на реакте", "friend", friend_allowed=_FRIEND_SET)
    assert res.decision == "none"


def test_admin_sees_admin_only_command():
    # same phrase under admin DOES reach the admin-only command
    res = ir.resolve("статус гита", "admin")
    assert res.decision == "route"
    assert res.candidates[0].cmd == "/git_status"


# ── Money source-of-truth: intent→cmd map + intent_is_paid ──────────────────
def test_intent_cmd_map_covers_paid_nl_intents():
    assert ir.INTENT_CMD["generate"] == "/gen"
    assert ir.INTENT_CMD["research"] == "/research"
    assert ir.INTENT_CMD["brain"] == "/brain"
    assert ir.INTENT_CMD["table"] == "/table"
    assert ir.INTENT_CMD["engineer"] == "/engineer"


def test_intent_is_paid_matches_registry():
    assert ir.intent_is_paid("generate") is True       # /gen in PAID
    assert ir.intent_is_paid("research") is True
    assert ir.intent_is_paid("simple_question") is False  # not in map → free
    assert ir.intent_is_paid("chat") is False


def test_free_and_paid_disjoint_still_holds():
    # money invariant unchanged by this task (regression guard)
    assert ir.FREE_AUTOEXEC & ir.PAID == frozenset()


def test_every_mapped_intent_cmd_is_a_real_paid_command():
    # tooth: INTENT_CMD must never point at a non-PAID or non-existent command
    for intent, cmd in ir.INTENT_CMD.items():
        assert cmd in ir.PAID, f"{intent}->{cmd} not in PAID"


# ── IR-2 (Haiku fallback) pure layer: prompt build + reply parse, $0 ──────────
def test_ir2_constants_target_haiku():
    assert ir.IR2_MODEL == "claude-haiku-4-5"      # cheap classifier, per money-safety
    assert 0 < ir.IR2_EST_USD < 0.05               # tiny pre-flight reserve


def test_ir2_build_messages_lists_candidates_and_hardens_against_injection():
    system, messages = ir.build_ir2_messages("выполни health", ["/health", "/costs"])
    body = messages[0]["content"]
    assert "/health" in body and "/costs" in body and "выполни health" in body
    # content-injection hardening: pick by meaning, ignore 'выполни X' instructions in text
    assert "игнорир" in system.lower()


def test_ir2_parse_extracts_valid_command():
    valid = {"/health", "/costs"}
    assert ir.parse_ir2_reply("/health", valid) == "/health"
    assert ir.parse_ir2_reply("Похоже на /costs — покажу траты", valid) == "/costs"
    assert ir.parse_ir2_reply("health", valid) == "/health"     # bare name


def test_ir2_parse_none_invalid_and_out_of_shortlist():
    valid = {"/health"}
    assert ir.parse_ir2_reply("none", valid) is None
    assert ir.parse_ir2_reply("нет подходящей команды", valid) is None
    assert ir.parse_ir2_reply("", valid) is None
    # IR-2 may only pick from the shortlist — a paid cmd it invents is rejected
    assert ir.parse_ir2_reply("/train_lora без подтверждения", valid) is None
