# -*- coding: utf-8 -*-
"""СТОРОЖ НА ПОРОГ ВКЛЮЧЕНИЯ КЭША — §2 спеки
`docs/superpowers/specs/2026-08-19-classifier-on-haiku.md` (§2.0, §2.1, §2.2,
§2.3).

Написано ОТ СПЕКИ И ОТ ПУБЛИЧНОГО КОНТРАКТА, как и соседний файл про дрейф:
план реализации не читался. Сторож, написанный по плану, наследует допущения
реализации и зеленеет вместе с её ошибкой.

──────────────────────────────────────────────────────────────────────────────
ЧЕТЫРЕ СПОСОБА ЭТОМУ СТОРОЖУ СТАТЬ ЗЕЛЁНОЙ ШИРМОЙ — и где каждый сторожится

  1. **Считать НЕ ТОЙ моделью** (§2.0). Токенизаторы haiku-4-5 и sonnet-5
     расходятся на 7–12% — это величина порядка самого порога, и у клиента
     около границы чужая модель скажет «проходит» там, где не проходит.
     Сторожит K3: проверяется не «посчитали», а ЧЕМ посчитали.
  2. **Мерить НЕ ТУ форму префикса.** Гардиан ставит
     `CHATTER_OBLIGATIONS_SLOT=1`, и боевой префикс классификатора длиннее
     выключенного. Сторожит K7.
  3. **Отказывать по НЕИЗМЕРЕННОМУ числу.** Упавший `count_tokens` (нет сети,
     нет кредитов) обязан быть громким, но НЕ отказом: иначе первая же икота
     API кладёт весь парк, и сторож стоимости становится причиной простоя.
     Сторожит K5 — и это самый дорогой тест файла: ночь 19→20.08 показала,
     что 400 от Anthropic бывает на ЛЮБОМ вызове, включая бесплатный.
  4. **Молча пропускать незнакомую модель.** Порог, взятый у чужого семейства,
     это выдуманное число. Сторожит K6.

Ноль сети: `count_tokens` ВСЕГДА инъектируется фейком.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from chatter.config.loader import load_config
from chatter.core import prefix_budget
from chatter.core.classifier import classifier_stable_prefix
from chatter.core.llm import CACHE_MIN_PROMPT_TOKENS, cache_min_prompt_tokens
from chatter.core.prefix_budget import (
    CACHE_THRESHOLD, PrefixGuardRefusal, cache_threshold_verdict,
    check_client_prefixes, classifier_model_of)
from tests.chatter.test_loader import SETTINGS as _LOADER_SETTINGS, _make_client

# Числа §2 ЛИТЕРАЛАМИ, один раз, здесь: таблица, разошедшаяся со спекой, —
# это сторож, который продолжает сверять, но уже не с тем, о чём договорились.
SPEC_THRESHOLD_HAIKU = 4096
SPEC_THRESHOLD_SONNET = 1024
HAIKU = "claude-haiku-4-5"
SONNET = "claude-sonnet-5"


def _cfg(tmp_path: Path, *, model: str = HAIKU, playbook: str = "Стадии воронки."):
    settings = _LOADER_SETTINGS.replace("model: claude-haiku-4-5", f"model: {model}", 1)
    d = _make_client(tmp_path, settings=settings)
    (d / "playbook.md").write_text(playbook, encoding="utf-8")
    return load_config(tmp_path, "demo")


class _Counter:
    """Фейковый `count_tokens`: возвращает заданное число и ЗАПОМИНАЕТ, какой
    моделью и какой текст его просили посчитать. Сторожа проверяют оба."""

    def __init__(self, value=None, raises: Exception | None = None):
        self.value, self.raises = value, raises
        self.calls: list[tuple[str, str]] = []

    def __call__(self, model, text):
        self.calls.append((model, text))
        if self.raises is not None:
            raise self.raises
        return self.value


# ── K0. Таблица порогов — ОДНА, и она совпадает со спекой ────────────────────

def test_k0_table_matches_spec():
    """§2.2 «порог и семейство модели — из таблицы в одном месте, не литералом
    в трёх». Проверяем ЗНАЧЕНИЯ, а не наличие ключей: таблица с правильными
    именами и неправильными числами — худший вид зелени."""
    assert cache_min_prompt_tokens(HAIKU) == SPEC_THRESHOLD_HAIKU
    assert cache_min_prompt_tokens(SONNET) == SPEC_THRESHOLD_SONNET
    assert cache_min_prompt_tokens("claude-haiku-4-5-20251001") == SPEC_THRESHOLD_HAIKU


def test_k0_unknown_family_is_none_not_default():
    """Умолчание на месте незнакомой модели дало бы сторожа, «успешно»
    проверившего новую модель по чужому числу."""
    assert cache_min_prompt_tokens("gpt-5") is None
    assert cache_min_prompt_tokens("") is None


def test_k0_longest_key_wins():
    """Если завтра появится семейство со своим порогом, точный ключ обязан
    победить общий — иначе новая модель молча унаследует старое число."""
    CACHE_MIN_PROMPT_TOKENS["claude-haiku-9"] = 99999
    try:
        assert cache_min_prompt_tokens("claude-haiku-9-mini") == 99999
        assert cache_min_prompt_tokens(HAIKU) == SPEC_THRESHOLD_HAIKU
    finally:
        del CACHE_MIN_PROMPT_TOKENS["claude-haiku-9"]


# ── K1. Ниже порога = ОТКАЗ, и отказ называет оба выхода ─────────────────────

def test_k1_below_threshold_is_fatal(tmp_path):
    v = cache_threshold_verdict(_cfg(tmp_path), counter=_Counter(2124), slot_on=True)
    assert v.check == CACHE_THRESHOLD
    assert v.kind == "below_threshold"
    assert (v.fatal, v.loud, v.ok) == (True, True, False)
    assert v.actual == 2124 and v.baseline == SPEC_THRESHOLD_HAIKU


def test_k1_refusal_names_exits_that_actually_work(tmp_path):
    """§2.2: «отказ обязан называть оба выхода». Отказ без выхода — это тупик,
    в котором клиента чинят наугад.

    И выход обязан РАБОТАТЬ. Спека §2.2 предлагает `classifier_model:
    claude-sonnet-5`, но это поле вводит §1, арка которого остановлена красным
    гейтом Х2. Проверено живьём: загрузчик такую строку ПРИНИМАЕТ и молча
    игнорирует — атрибута на настройках не появляется. Отказ, отправляющий
    владельца дописать её, вернул бы ровно тот же отказ после рестарта, и
    выглядело бы это как сломанный сторож. Поэтому текст обязан назвать выходы,
    работающие сегодня, и вслух сказать про несуществующий тумблер."""
    msg = cache_threshold_verdict(_cfg(tmp_path), counter=_Counter(2124),
                                  slot_on=True).message
    assert "2124" in msg and str(SPEC_THRESHOLD_HAIKU) in msg
    assert "плейбук" in msg.lower()                          # выход 1
    assert f"model: {SONNET}" in msg                          # выход 2, РАБОЧИЙ
    assert "речь" in msg.lower()          # и его цена названа, а не умолчана
    # про несуществующий тумблер сказано, что его НЕТ
    i = msg.find("classifier_model")
    assert i != -1 and "НЕТ" in msg[max(0, i - 60):i + 60]


# ── K2. Граница проверяется НА ГРАНИЦЕ ───────────────────────────────────────

@pytest.mark.parametrize("value,expect_fatal", [
    (SPEC_THRESHOLD_HAIKU - 1, True),
    (SPEC_THRESHOLD_HAIKU, False),      # РОВНО порог уже кэшируется
    (SPEC_THRESHOLD_HAIKU + 1, False),
])
def test_k2_boundary(tmp_path, value, expect_fatal):
    v = cache_threshold_verdict(_cfg(tmp_path), counter=_Counter(value), slot_on=True)
    assert v.fatal is expect_fatal
    assert v.ok is (not expect_fatal)
    if not expect_fatal:
        assert v.loud is False          # проходящий клиент не будит владельца


# ── K3. Считает ЦЕЛЕВАЯ модель (§2.0) ────────────────────────────────────────

def test_k3_counts_with_classifier_model(tmp_path):
    c = _Counter(9000)
    cache_threshold_verdict(_cfg(tmp_path, model=HAIKU), counter=c, slot_on=True)
    assert [m for m, _ in c.calls] == [HAIKU]


def test_k3_sonnet_client_judged_by_sonnet_floor(tmp_path):
    """Тот же префикс: у haiku это отказ, у sonnet — норма. Порог обязан быть
    порогом СВОЕЙ модели, иначе клиент на sonnet отвергается ни за что."""
    v = cache_threshold_verdict(_cfg(tmp_path, model=SONNET),
                                counter=_Counter(2124), slot_on=True)
    assert v.ok is True and v.fatal is False and v.baseline == SPEC_THRESHOLD_SONNET


def test_k3_classifier_model_field_wins_when_it_appears(tmp_path):
    """§1.1 вводит `classifier_model`; арка остановлена, но появиться поле может
    в любой день. Резолвер обязан пережить это, не поменяв ни одного вызова."""
    cfg = _cfg(tmp_path, model=SONNET)
    assert classifier_model_of(cfg) == SONNET
    object.__setattr__(cfg.settings, "classifier_model", HAIKU)
    assert classifier_model_of(cfg) == HAIKU


# ── K5/K6. Непроверенное НЕ выглядит проверенным, но и НЕ роняет парк ────────

def test_k5_measure_failure_is_loud_but_not_fatal(tmp_path):
    """Ночь 19→20.08: Anthropic отдавал 400 на ЛЮБОЙ вызов, включая бесплатный
    `count_tokens`. Отказ по упавшему замеру положил бы обоих живых клиентов."""
    v = cache_threshold_verdict(
        _cfg(tmp_path), counter=_Counter(raises=RuntimeError("credit balance too low")),
        slot_on=True)
    assert v.kind == "measure_failed"
    assert (v.fatal, v.loud, v.ok) == (False, True, False)
    assert v.actual is None
    assert "credit balance too low" in v.message      # причина названа (DEV-18)


def test_k6_unknown_model_is_loud_not_fatal_and_not_measured(tmp_path):
    c = _Counter(10)
    v = cache_threshold_verdict(_cfg(tmp_path, model="gpt-5"), counter=c, slot_on=True)
    assert v.kind == "unknown_model"
    assert (v.fatal, v.loud, v.ok) == (False, True, False)
    assert c.calls == []          # незачем звать сеть, судить всё равно нечем


# ── K7. Мерится БОЕВАЯ форма префикса ────────────────────────────────────────

def test_k7_measures_the_live_prefix_shape(tmp_path):
    """Слот обязательств удлиняет префикс классификатора. Сторож, меряющий
    выключенную форму, у клиента около границы соврёт в безопасную сторону."""
    cfg = _cfg(tmp_path)
    c_on, c_off = _Counter(9000), _Counter(9000)
    cache_threshold_verdict(cfg, counter=c_on, slot_on=True)
    cache_threshold_verdict(cfg, counter=c_off, slot_on=False)
    text_on, text_off = c_on.calls[0][1], c_off.calls[0][1]
    assert text_on != text_off
    assert len(text_on) > len(text_off)
    # и это ровно тот текст, который строит ПРОДАКШЕН-код, а не копия правила
    assert text_on == classifier_stable_prefix(
        cfg.playbook, cfg.settings.language,
        cfg.settings.limits.profile_budget_tokens, track_obligations=True)


def test_k7_slot_read_from_the_single_source(tmp_path, monkeypatch):
    """`slot_on=None` → флаг берётся из ЕДИНСТВЕННОГО источника
    (`chatter.run`), а не из своего `os.getenv`."""
    cfg = _cfg(tmp_path)
    monkeypatch.setenv("CHATTER_OBLIGATIONS_SLOT", "1")
    c = _Counter(9000)
    cache_threshold_verdict(cfg, counter=c)
    assert c.calls[0][1] == classifier_stable_prefix(
        cfg.playbook, cfg.settings.language,
        cfg.settings.limits.profile_budget_tokens, track_obligations=True)


# ── K8. Две проверки, один отчёт (§2.2 + §9) ─────────────────────────────────

def test_k8_report_carries_both_checks_fatal_first(tmp_path):
    vs = check_client_prefixes(_cfg(tmp_path), counter=_Counter(2124))
    assert [v.check for v in vs] == [CACHE_THRESHOLD, prefix_budget.BRAIN_DRIFT]


def test_k8_broken_baselines_do_not_disable_the_cache_guard(tmp_path):
    """Битый yaml эталонов — не повод молча пустить клиента с выключенным
    кэшем: проверки независимы, и одна не имеет права глушить другую."""
    bad = tmp_path / "bad.yaml"
    bad.write_text("clients: [не словарь]", encoding="utf-8")
    vs = check_client_prefixes(_cfg(tmp_path), counter=_Counter(2124),
                               baselines_path=bad)
    cache = [v for v in vs if v.check == CACHE_THRESHOLD]
    assert len(cache) == 1 and cache[0].fatal is True


# ── K9. Отказ доезжает до СТАРТА, а не остаётся вердиктом в списке ───────────

def test_k9_load_personas_refuses(tmp_path, monkeypatch):
    """Вердикт, который никто не читает, — это не отказ. Проверяем ту функцию,
    через которую поднимается боевой раннер.

    Счётчик здесь НЕ инъектируется: `load_personas` зовёт сторож без параметра,
    и подмена дефолта у `check_client_prefixes` доказала бы работу тестового
    стенда, а не боевого пути. Поэтому фейком подменяется САМ SDK — тем же
    приёмом, что в файле §9, — и путь до сети остаётся настоящим до последнего
    шага."""
    from tests.chatter.test_prefix_drift_guard import (
        _CountTokensSpy, _install_fake_anthropic)
    from chatter import telethon_run
    from chatter.storage.db import Store

    _cfg(tmp_path)      # каталог клиента `demo`, модель haiku → порог 4096
    _install_fake_anthropic(monkeypatch, _CountTokensSpy(2124))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    with pytest.raises(PrefixGuardRefusal) as e:
        telethon_run.load_personas(tmp_path, ["demo"], Store(":memory:"), llm_mode="real")
    assert "2124" in str(e.value) and str(SPEC_THRESHOLD_HAIKU) in str(e.value)


def test_k9_refusal_is_not_a_configerror():
    """Стартовый fail-safe ловит `ConfigError` и откатывает на last-known-good.
    Провал под порог откатом «чинить» нельзя: это подменило бы ответ на вопрос
    «почему клиент не поднялся» вчерашним плейбуком."""
    from chatter.config.loader import ConfigError
    assert not issubclass(PrefixGuardRefusal, ConfigError)
