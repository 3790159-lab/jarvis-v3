"""М8: examples.yaml — эталонные пары «клієнт → персона» в системном промпте
brain (голос продавца). Внутри кэшируемого префикса, бюджет ~1.5k токенов,
в классификатор НЕ идёт (ROADMAP М8)."""
from __future__ import annotations

import logging
import re
from pathlib import Path

import pytest

from chatter.config.loader import ConfigError, load_config
from chatter.core.brain import EXAMPLES_CHAR_BUDGET, build_system_prompt
from chatter.core.config_versions import CONFIG_FILES
from tests.chatter.test_loader import _make_client


def _cfg(tmp_path, examples_yaml: str | None = None):
    _make_client(tmp_path)
    if examples_yaml is not None:
        (tmp_path / "demo" / "examples.yaml").write_text(examples_yaml, encoding="utf-8")
    return load_config(tmp_path, "demo")


VALID = """\
- client: "Скільки коштує лого?"
  olga: "Зазвичай 300–400 $. А для якої сфери?"
- client: "А терміни?"
  olga: "5–10 робочих днів. Скинути приклади робіт?"
"""


# --- loader -------------------------------------------------------------------
def test_examples_absent_is_empty_tuple(tmp_path):
    cfg = _cfg(tmp_path)
    assert cfg.examples == ()


def test_examples_loaded_in_order(tmp_path):
    cfg = _cfg(tmp_path, VALID)
    assert len(cfg.examples) == 2
    assert cfg.examples[0] == ("Скільки коштує лого?", "Зазвичай 300–400 $. А для якої сфери?")


@pytest.mark.parametrize("bad", [
    "просто строка",                        # не список
    "- client: x\n",                        # нет olga
    "- olga: y\n",                          # нет client
    "- client: ''\n  olga: 'y'\n",          # пустая реплика
    "- {client: [1], olga: y}\n",           # не строка
])
def test_examples_malformed_is_loud(tmp_path, bad):
    """Кривой examples.yaml — громкий ConfigError, не тихий пропуск (DEV-18):
    иначе клиент правит примеры, они молча выпадают из промпта, голос едет."""
    with pytest.raises(ConfigError):
        _cfg(tmp_path, bad)


# --- brain: секция в системном промпте ---------------------------------------
def test_system_prompt_contains_examples_section(tmp_path):
    cfg = _cfg(tmp_path, VALID)
    sp = build_system_prompt(cfg)
    assert "ПРИКЛАДИ ДІАЛОГІВ" in sp
    assert "Скільки коштує лого?" in sp
    assert "Зазвичай 300–400 $. А для якої сфери?" in sp
    # порядок пар сохранён
    assert sp.index("коштує лого") < sp.index("А терміни?")
    # факты — только из знаний: секция несёт явную оговорку
    assert "ЗНАННЯ" in sp.split("ПРИКЛАДИ ДІАЛОГІВ")[1]


def test_no_examples_no_section(tmp_path):
    cfg = _cfg(tmp_path)
    assert "ПРИКЛАДИ ДІАЛОГІВ" not in build_system_prompt(cfg)


def test_examples_budget_drops_tail_with_warning(tmp_path, caplog):
    """Отбор с бюджетом: пары включаются по порядку, пока влезают в
    EXAMPLES_CHAR_BUDGET; хвост отбрасывается с WARNING (не молча)."""
    big = "х" * (EXAMPLES_CHAR_BUDGET // 2)
    yaml_text = "".join(
        f"- client: \"питання {i} {big}\"\n  olga: \"відповідь {i}\"\n"
        for i in range(4))
    cfg = _cfg(tmp_path, yaml_text)
    with caplog.at_level(logging.WARNING, logger="chatter.core.brain"):
        sp = build_system_prompt(cfg)
    assert "питання 0" in sp
    assert "питання 3" not in sp  # хвост отброшен
    assert any("бюджет" in r.message.casefold() for r in caplog.records)


def test_classifier_prompt_has_no_examples(tmp_path):
    """М8 только для brain: классификатору примеры не нужны (лишние токены
    на каждый ход)."""
    from chatter.core.classifier import classifier_system_prompt
    cfg = _cfg(tmp_path, VALID)
    sp = classifier_system_prompt(cfg.playbook, cfg.settings.language)
    assert "ПРИКЛАДИ ДІАЛОГІВ" not in sp


# --- версионирование / reload -------------------------------------------------
def test_examples_yaml_in_config_files():
    """examples.yaml обязан жить в CONFIG_FILES: иначе /rollback и mtime-reload
    молча не видят правку примеров."""
    assert "examples.yaml" in CONFIG_FILES


# --- volska: анти-дрейф фактов -------------------------------------------------
_VOLSKA = Path(__file__).resolve().parents[2] / "chatter" / "clients" / "volska"


def test_volska_examples_load_and_facts_match_knowledge():
    """Каждая $-вилка из примеров ОБЯЗАНА существовать в knowledge.md:
    выдумка в эталоне воспроизводится системно (сверка Даниила 2026-07-23)."""
    cfg = load_config(_VOLSKA.parent, "volska")
    assert len(cfg.examples) >= 4
    knowledge = (_VOLSKA / "knowledge.md").read_text(encoding="utf-8")
    for client, olga in cfg.examples:
        for rng in re.findall(r"\d+[–-]\d+\s*\$?", olga):
            rng_norm = rng.replace(" ", "").rstrip("$")
            assert rng_norm in knowledge.replace(" ", ""), (
                f"вилка «{rng}» из примера отсутствует в knowledge.md: {olga[:60]}")


def test_volska_examples_every_reply_has_next_step():
    """Правило 2 «голоса продавца»: каждая эталонная реплика Ольги кончается
    вопросом или содержит следующий шаг (бриф/приклади/керівниц)."""
    cfg = load_config(_VOLSKA.parent, "volska")
    for _, olga in cfg.examples:
        low = olga.casefold()
        assert ("?" in olga or "бриф" in low or "приклад" in low
                or "керівниц" in low or "розкаж" in low), (
            f"реплика без следующего шага: {olga[:80]}")
