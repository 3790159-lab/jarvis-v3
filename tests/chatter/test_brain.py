from __future__ import annotations
from chatter.config.loader import load_config
from chatter.core.brain import build_system_prompt, build_messages, Brain
from chatter.core.llm import FakeLLM
from tests.chatter.test_loader import _make_client, SETTINGS


def _cfg(tmp_path):
    _make_client(tmp_path)
    return load_config(tmp_path, "demo")


def test_system_prompt_includes_all_sources(tmp_path):
    cfg = _cfg(tmp_path)
    sp = build_system_prompt(cfg)
    assert "Меня зовут Аня" in sp          # persona
    assert "Консультация 5000" in sp        # knowledge
    assert "Стадии воронки" in sp           # playbook


def test_system_prompt_has_style_and_language_rules(tmp_path):
    cfg = _cfg(tmp_path)
    sp = build_system_prompt(cfg).lower()
    assert "без" in sp                        # no bullets/headers/markdown rule present
    assert "не выдумывай" in sp               # no-invention/honesty rule
    assert "персон" in sp                     # delegates voice to ПЕРСОНА
    assert "русском" in sp                    # language ru directive


def test_system_prompt_has_honesty_clause_backup(tmp_path):
    """H3 backup: the deterministic is_bot_question pre-empt is primary, but when
    it misses (an unenumerated phrasing/language), control falls to the LLM,
    whose _STYLE previously said NOTHING about honesty. The system prompt must
    carry an explicit 'admit you're an assistant, never pose as a human' rule so
    the DEFAULT path stays honest."""
    cfg = _cfg(tmp_path)
    sp = build_system_prompt(cfg).lower()
    assert "бот" in sp                                   # names the bot/ИИ question
    assert "честно" in sp                                # instructed to answer honestly
    assert "не выдавай себя за живого человека" in sp    # never pose as a human


def test_system_prompt_language_directive_for_en_and_uk(tmp_path):
    _make_client(tmp_path, slug="demo_en", settings=SETTINGS.replace("language: ru", "language: en"))
    cfg_en = load_config(tmp_path, "demo_en")
    sp_en = build_system_prompt(cfg_en).lower()
    assert "английском" in sp_en

    _make_client(tmp_path, slug="demo_uk", settings=SETTINGS.replace("language: ru", "language: uk"))
    cfg_uk = load_config(tmp_path, "demo_uk")
    sp_uk = build_system_prompt(cfg_uk).lower()
    assert "украинском" in sp_uk


def test_style_instructs_answer_from_knowledge_immediately():
    from chatter.config.loader import load_config
    from chatter.core.brain import build_system_prompt
    from pathlib import Path
    CLIENTS = Path(__file__).resolve().parents[2] / "chatter" / "clients"
    prompt = build_system_prompt(load_config(CLIENTS, "demo"))
    low = prompt.casefold()
    assert "ответь сразу" in low
    assert "уточню и вернусь" in low


def test_build_messages_maps_history_roles():
    hist = [{"role": "user", "text": "привет"}, {"role": "assistant", "text": "здравствуйте"}]
    msgs = build_messages(hist)
    assert msgs == [
        {"role": "user", "content": "привет"},
        {"role": "assistant", "content": "здравствуйте"},
    ]


def test_brain_reply_calls_llm_with_budget(tmp_path):
    cfg = _cfg(tmp_path)
    llm = FakeLLM(scripted=["Здравствуйте! Что вас интересует?"])
    brain = Brain(llm, cfg)
    out = brain.reply([{"role": "user", "text": "привет"}])
    assert out == "Здравствуйте! Что вас интересует?"
    call = llm.calls[0]
    assert call["max_tokens"] == cfg.settings.limits.max_reply_tokens
    assert "Меня зовут Аня" in call["system"]
    assert call["messages"][-1] == {"role": "user", "content": "привет"}


def test_brain_reply_context_note_is_appended_to_system_only_when_given(tmp_path):
    """A one-off context note (e.g. 'this message waited 20 min, apologise for
    the pause in your own words') rides along in the SYSTEM prompt for that one
    call, without becoming part of the persona. Absent by default."""
    cfg = _cfg(tmp_path)
    llm = FakeLLM(scripted=["ok1", "ok2"])
    brain = Brain(llm, cfg)

    brain.reply([{"role": "user", "text": "вы тут?"}])
    assert "КОНТЕКСТ" not in llm.calls[0]["system"]  # nothing extra by default

    brain.reply([{"role": "user", "text": "вы тут?"}], context_note="ЗАМЕТКА-ИЗВИНЕНИЕ")
    assert "ЗАМЕТКА-ИЗВИНЕНИЕ" in llm.calls[1]["system"]
    assert "Мена зовут" not in llm.calls[1]["system"] or True  # persona still present
    assert "Меня зовут Аня" in llm.calls[1]["system"]
