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


def test_system_prompt_language_directive_for_en_and_uk(tmp_path):
    _make_client(tmp_path, slug="demo_en", settings=SETTINGS.replace("language: ru", "language: en"))
    cfg_en = load_config(tmp_path, "demo_en")
    sp_en = build_system_prompt(cfg_en).lower()
    assert "английском" in sp_en

    _make_client(tmp_path, slug="demo_uk", settings=SETTINGS.replace("language: ru", "language: uk"))
    cfg_uk = load_config(tmp_path, "demo_uk")
    sp_uk = build_system_prompt(cfg_uk).lower()
    assert "украинском" in sp_uk


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
