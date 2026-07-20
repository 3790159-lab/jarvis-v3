"""Два per-client тумблера (settings.yaml, не хардкод):

  strict_knowledge (дефолт true)  — Аня говорит только из knowledge;
  honesty_mode     (дефолт honest) — на «ты бот?» раскалывается честно.

Оба — осознанное решение владельца под его ответственность. Дефолт у обоих
БЕЗОПАСНЫЙ, и оба режима остаются механикой, а не удалением механики: свободный
режим ослабляет РОВНО один слой каждый, всё остальное держится.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from chatter.config.loader import ConfigError, load_config
from chatter.core.escalation import deterministic_escalation

KB = "Консультация 5000 грн. Съёмка 15000 грн."

SETTINGS = """\
model: claude-haiku-4-5
language: ru
owner_id: "owner-1"
persona_name: "Аня"
"""


def _make_client(root: Path, settings: str = SETTINGS, slug: str = "demo") -> Path:
    d = root / slug
    d.mkdir(parents=True)
    (d / "persona.md").write_text("Меня зовут Аня.", encoding="utf-8")
    (d / "knowledge.md").write_text("Консультация 5000 грн.", encoding="utf-8")
    (d / "playbook.md").write_text("Стадии воронки.", encoding="utf-8")
    (d / "settings.yaml").write_text(settings, encoding="utf-8")
    return d


# ---------------------------------------------------------------------------
# loader: дефолты и разбор
# ---------------------------------------------------------------------------

def test_strict_knowledge_defaults_to_true_when_absent(tmp_path):
    """Клиент, не написавший тумблер вовсе, получает СТРОГИЙ режим."""
    _make_client(tmp_path)
    cfg = load_config(tmp_path, "demo")
    assert cfg.settings.strict_knowledge is True


def test_strict_knowledge_false_is_parsed(tmp_path):
    _make_client(tmp_path, SETTINGS + "strict_knowledge: false\n")
    cfg = load_config(tmp_path, "demo")
    assert cfg.settings.strict_knowledge is False


def test_honesty_mode_defaults_to_honest_when_absent(tmp_path):
    """Дефолт честности — не «пусто», а явный honest."""
    _make_client(tmp_path)
    cfg = load_config(tmp_path, "demo")
    assert cfg.settings.honesty_mode == "honest"


def test_honesty_mode_free_requires_the_long_liability_value(tmp_path):
    """Выключение честности = Аня перестаёт признаваться, что она не человек.
    Такое не должно происходить по опечатке, поэтому значение — длинное и
    самоописывающее: владелец, печатая его, читает «ответственность на мне»."""
    _make_client(tmp_path, SETTINGS + "honesty_mode: free_owner_liability\n")
    cfg = load_config(tmp_path, "demo")
    assert cfg.settings.honesty_mode == "free_owner_liability"


def test_honesty_mode_short_free_is_rejected(tmp_path):
    """Именно короткое `free` — самая вероятная опечатка/копипаста из чужого
    конфига, и она НЕ должна выключать честность молча."""
    _make_client(tmp_path, SETTINGS + "honesty_mode: free\n")
    with pytest.raises(ConfigError) as e:
        load_config(tmp_path, "demo")
    assert "honesty_mode" in str(e.value)


def test_honesty_mode_garbage_value_is_rejected_not_silently_honest(tmp_path):
    """Мусорное значение — ГРОМКАЯ ошибка, а не тихий фолбэк: тихий фолбэк в
    honest скрыл бы от владельца, что его настройка не применилась (DEV-18)."""
    _make_client(tmp_path, SETTINGS + "honesty_mode: свободный\n")
    with pytest.raises(ConfigError) as e:
        load_config(tmp_path, "demo")
    assert "honesty_mode" in str(e.value)


# ---------------------------------------------------------------------------
# strict_knowledge: что именно ослабляется, а что держится в ОБОИХ режимах
# ---------------------------------------------------------------------------

def _det(reply: str, *, incoming: str = "привет", strict: bool = True,
         keywords=(), forbidden=()):
    return deterministic_escalation(
        incoming_text=incoming, reply=reply, knowledge=KB,
        keywords=list(keywords), forbidden_terms=tuple(forbidden),
        strict_knowledge=strict)


def test_strict_mode_suppresses_unbacked_promise():
    """Текущее поведение сохранено: в строгом режиме обещание вне базы
    подавляется (и эскалируется)."""
    det = _det("Конечно, сделаю вам скидку.", strict=True)
    assert det is not None
    assert det.tag == "unbacked_promise"
    assert det.suppress is True


def test_free_mode_escalates_promise_but_does_not_suppress_it():
    """Свободный режим: обещание доезжает до лида, НО владелец всё равно
    получает карточку. Смягчение ≠ ослепление владельца."""
    det = _det("Конечно, сделаю вам скидку.", strict=False)
    assert det is not None
    assert det.tag == "unbacked_promise"
    assert det.suppress is False


def test_free_mode_promise_does_not_shadow_an_unbacked_number():
    """ДЫРА, которую легко не заметить: `unbacked_promise` проверяется РАНЬШЕ
    `unbacked_claim`. Если в свободном режиме просто снять у него подавление,
    ответ «сделаю скидку 700 грн» вернёт неподавляющий promise ПЕРВЫМ, и
    выдуманная цифра уедет лиду — при том, что цифры мы держим жёстко в обоих
    режимах. Подавляющий триггер обязан выиграть."""
    det = _det("Сделаю вам скидку 700 грн.", strict=False)
    assert det is not None
    assert det.tag == "unbacked_claim"
    assert det.suppress is True


def test_free_mode_still_suppresses_forbidden_terms():
    """Brand-safety (рубли/росбанк) не зависит от режима знаний вообще."""
    det = _det("Оплата в рублях на Сбербанк.", strict=False,
               forbidden=("рубл", "сбербанк"))
    assert det is not None
    assert det.tag == "forbidden_reply"
    assert det.suppress is True


def test_free_mode_still_escalates_on_keyword():
    det = _det("Хорошо, посмотрим.", incoming="хочу вернуть деньги",
               strict=False, keywords=("вернуть деньги",))
    assert det is not None
    assert det.tag == "keyword"
    assert det.suppress is False


def test_honest_refusal_passes_in_both_modes():
    """Аня обязана уметь сказать «нет» в любом режиме — бот, который не может
    отказать, хуже болтливого."""
    for strict in (True, False):
        assert _det("Скидок нет, цена фиксированная.", strict=strict) is None


# ---------------------------------------------------------------------------
# Живой шов: process_batch honours both toggles end-to-end
# ---------------------------------------------------------------------------

def _run(tmp_path, *, scripted, incoming, extra_settings=""):
    """Прогнать одно входящее через настоящий process_batch и вернуть то, что
    реально ушло лиду."""
    import random

    from chatter.config.loader import load_config
    from chatter.core.brain import Brain
    from chatter.core.llm import FakeLLM
    from chatter.run import Deps, process_batch
    from chatter.storage.db import Store
    from chatter.transport.fake import FakeConsoleTransport

    _make_client(tmp_path, SETTINGS + extra_settings)
    cfg = load_config(tmp_path, "demo")
    deps = Deps(
        cfg=cfg, store=Store(tmp_path / "c.db"), brain=Brain(FakeLLM(scripted=scripted), cfg),
        rng=random.Random(0), clock=lambda: 1000.0, sleep=lambda s: None)
    t = FakeConsoleTransport(preload=[], echo=False)
    deps.store.get_or_create_contact("u1")
    process_batch("u1", [incoming], t, deps)
    return " ".join(t.sent)


FREE_HONESTY = "honesty_mode: free_owner_liability\n"


def test_default_client_still_admits_being_a_bot(tmp_path):
    """Дефолт не тронут: без тумблеров Аня раскалывается, как и раньше."""
    sent = _run(tmp_path, scripted=["Я живой человек, конечно!"], incoming="ты бот?")
    assert "виртуальный ассистент" in sent


def test_free_honesty_lets_the_brain_answer_the_bot_question(tmp_path):
    """Свободный режим: гарантия честности снята, ответ идёт через brain."""
    sent = _run(tmp_path, scripted=["Я Аня, менеджер студии."], incoming="ты бот?",
                extra_settings=FREE_HONESTY)
    assert "виртуальный ассистент" not in sent
    assert "Аня" in sent


def test_free_honesty_still_escalates_the_bot_question_to_the_owner():
    """Владелец обязан ВИДЕТЬ, что у Ани спросили про бота, даже когда она
    больше не обязана признаваться: тумблер меняет ОТВЕТ лиду, а не видимость
    для владельца."""
    det = _det("Я Аня, менеджер студии.", incoming="ты бот?", strict=True)
    assert det is not None and det.tag == "bot_question"


def test_free_honesty_does_not_protect_an_undelivered_owner_promise(tmp_path):
    """Ловушка шва: `protected` привязан к ТЕГУ bot_question, а тег ставится по
    ВХОДЯЩЕМУ и срабатывает в обоих режимах. В честном режиме защита верна —
    упоминание владельца часть раскрытия. В свободном режиме ответ пишет brain,
    и та же защита пропустила бы недоставленное обещание «свяжу с владельцем»
    (карточка не ушла — notifier'а нет). Защищать надо ФАКТ раскрытия, а не тег."""
    sent = _run(tmp_path, scripted=["Конечно, свяжу вас с владельцем."],
                incoming="ты бот?", extra_settings=FREE_HONESTY)
    assert "свяжу" not in sent
    assert "вернусь" in sent


def test_honest_mode_keeps_protecting_the_disclosure(tmp_path):
    """Обратная сторона: в честном режиме упоминание владельца в раскрытии
    трогать нельзя даже без доставленной карточки."""
    sent = _run(tmp_path, scripted=["неважно"], incoming="ты бот?")
    assert "виртуальный ассистент" in sent
    assert "владельца" in sent


def test_strict_knowledge_default_replaces_an_unbacked_promise(tmp_path):
    sent = _run(tmp_path, scripted=["Конечно, сделаю вам скидку."], incoming="а скидка будет?")
    assert "скидк" not in sent.casefold()
    assert "вернусь" in sent


def test_free_knowledge_lets_the_promise_reach_the_lead(tmp_path):
    sent = _run(tmp_path, scripted=["Конечно, сделаю вам скидку."], incoming="а скидка будет?",
                extra_settings="strict_knowledge: false\n")
    assert "скидку" in sent


def test_free_knowledge_still_replaces_an_invented_price(tmp_path):
    """Цифры держим жёстко в обоих режимах."""
    sent = _run(tmp_path, scripted=["Сделаю скидку 700 грн."], incoming="а скидка будет?",
                extra_settings="strict_knowledge: false\n")
    assert "700" not in sent


# ---------------------------------------------------------------------------
# Системный промпт: тумблеры меняют инструкцию, а не только пост-фильтр
# ---------------------------------------------------------------------------

def _prompt(tmp_path, extra_settings=""):
    from chatter.core.brain import build_system_prompt

    _make_client(tmp_path, SETTINGS + extra_settings)
    return build_system_prompt(load_config(tmp_path, "demo"))


def test_strict_prompt_forbids_answering_beyond_knowledge(tmp_path):
    assert "не выдумывай" in _prompt(tmp_path).casefold()


def test_free_prompt_allows_answering_wider(tmp_path):
    p = _prompt(tmp_path, "strict_knowledge: false\n").casefold()
    assert "шире" in p


def test_free_prompt_still_forbids_inventing_prices(tmp_path):
    """Свободный режим ослабляет ОБЯЗАТЕЛЬСТВА, а не право врать про цифры —
    инструкция обязана это сохранять, иначе пост-фильтр будет глушить ответы,
    которые модель уверенно генерирует."""
    p = _prompt(tmp_path, "strict_knowledge: false\n").casefold()
    assert "цен" in p and "не выдумыв" in p


def test_honest_prompt_instructs_to_admit_being_an_assistant(tmp_path):
    assert "виртуальный ассистент" in _prompt(tmp_path).casefold()


def test_free_honesty_prompt_drops_the_honesty_instruction(tmp_path):
    """Иначе промпт («признайся честно») спорил бы с конфигом («не обязана») —
    модель бы металась между ними, а владелец не понимал, что у него включено."""
    assert "виртуальный ассистент" not in _prompt(tmp_path, FREE_HONESTY).casefold()


# ---------------------------------------------------------------------------
# /config: владелец видит оба тумблера, не открывая yaml
# ---------------------------------------------------------------------------

def _cfg_card(*, strict=True, honesty="honest", lang="ru"):
    from chatter.core.console import format_config

    return format_config(
        persona_name="Аня", persona_age=None, language="ru", model="claude-haiku-4-5",
        knowledge="## Услуги\n- Съёмка", funnel_gate=False, allow_count=1, deny_count=0,
        changed_ago=None, lang=lang, currency="грн", forbidden_count=3,
        strict_knowledge=strict, honesty_mode=honesty)


def test_config_card_labels_the_knowledge_mode_distinctly_from_the_stats_line():
    """Строка режима не должна начинаться так же, как строка статистики
    («База знаний: 2 разделов…») — иначе в карточке подряд идут два разных
    «База знаний:», и владелец читает их как одно поле."""
    card = _cfg_card(strict=True)
    assert "Режим знаний: строго" in card
    assert card.count("База знаний:") == 1


def test_config_card_shows_strict_knowledge_off_distinctly():
    """Два режима обязаны читаться РАЗНЫМИ строками — иначе владелец не поймёт
    по карточке, что у него включено."""
    assert _cfg_card(strict=True) != _cfg_card(strict=False)


def test_config_card_shows_honesty_honest():
    assert "честно" in _cfg_card(honesty="honest").casefold()


def test_config_card_flags_free_honesty_with_a_warning():
    """Выключенная честность не должна выглядеть как рядовая строка настроек:
    владелец обязан видеть, что у него включён самый опасный режим продукта."""
    card = _cfg_card(honesty="free_owner_liability")
    assert "⚠️" in card


@pytest.mark.parametrize("lang", ["ru", "en", "uk"])
def test_config_card_renders_both_toggles_in_every_language(lang):
    """Строки живут в трёх словарях — забытый ключ ронял бы /config KeyError'ом
    именно у того клиента, чей язык забыли."""
    for strict in (True, False):
        for honesty in ("honest", "free_owner_liability"):
            assert _cfg_card(strict=strict, honesty=honesty, lang=lang)


# ---------------------------------------------------------------------------
# След в логе: выключенная честность — осознанное решение, а не наш дефолт
# ---------------------------------------------------------------------------

def test_loading_free_honesty_logs_a_warning(tmp_path, caplog):
    _make_client(tmp_path, SETTINGS + FREE_HONESTY)
    with caplog.at_level("WARNING"):
        load_config(tmp_path, "demo")
    assert any("honesty" in r.message.casefold() for r in caplog.records)


def test_loading_default_client_logs_no_honesty_warning(tmp_path, caplog):
    _make_client(tmp_path)
    with caplog.at_level("WARNING"):
        load_config(tmp_path, "demo")
    assert not [r for r in caplog.records if "honesty" in r.message.casefold()]


# ---------------------------------------------------------------------------
# Новый клиент рождается в безопасном режиме и знает про оба тумблера
# ---------------------------------------------------------------------------

def _render_new_client(tmp_path):
    from chatter.create_client import render_client

    files = render_client(slug="acme", persona_name="Аня", owner_id="Дмитрий")
    d = tmp_path / "acme"
    d.mkdir(parents=True)
    for name, body in files.items():
        (d / name).write_text(body, encoding="utf-8")
    return d, files["settings.yaml"]


def test_new_client_defaults_to_the_safe_mode_of_both_toggles(tmp_path):
    d, _ = _render_new_client(tmp_path)
    cfg = load_config(tmp_path, "acme")
    assert cfg.settings.strict_knowledge is True
    assert cfg.settings.honesty_mode == "honest"


def test_new_client_settings_document_both_toggles(tmp_path):
    """Тумблер, о котором владелец не знает, не является выбором. Шаблон обязан
    называть оба — иначе «клиентский конфиг» на практике остаётся хардкодом."""
    _, settings = _render_new_client(tmp_path)
    assert "strict_knowledge" in settings
    assert "honesty_mode" in settings
