# -*- coding: utf-8 -*-
"""C-1 / D2-4: язык формулы редакции = язык ОТВЕТА, а не `settings.language`.

Спека sales-competence §1.3. `language` — одна скалярка клиента, и она уезжает
и в редакцию, и в заглушки. Живой случай: диалог шёл по-русски, модель отвечала
по-русски, а код вставлял в её текст УКРАИНСКИЕ константы. Отчёт засчитал это
как «смешение языков и безграмотность модели» — хотя смешивал языки наш код.

Детектор — по буквам, различающим языки (і/ї/є/ґ против ы/э/ъ/ё), без внешних
зависимостей и без сети. Нет сигнала — падаем на `settings.language`, то есть
на сегодняшнее поведение.
"""
from __future__ import annotations

import pytest

from chatter.core.langdetect import detect_language
from chatter.core.guardrails import redact_unbacked

KNOWLEDGE = "Химчистка салона - 4 500 грн."


@pytest.mark.parametrize("text,expected", [
    ("Зробимо роботу і закриємо питання", "uk"),
    ("Мы сделаем работу и закроем этот вопрос", "ru"),
    ("We will do the work and close the question", "en"),
])
def test_detector_reads_the_alphabet(text, expected):
    assert detect_language(text, default="xx") == expected


@pytest.mark.parametrize("text", ["", "   ", "9900", "$1000 — 40/50"])
def test_no_signal_falls_back_to_default(text):
    assert detect_language(text, default="uk") == "uk"


def test_russian_reply_gets_russian_formula_even_when_client_is_uk():
    """Главный случай §1.3: клиент настроен на uk, ответ пришёл на ru."""
    res = redact_unbacked("Мы сделаем эту работу за 3 дня.",
                          KNOWLEDGE, language="uk")
    assert "точный срок согласовываем индивидуально" in res.text.lower()
    assert "узгоджуємо" not in res.text


def test_ukrainian_reply_keeps_ukrainian_formula():
    res = redact_unbacked("Зробимо цю роботу за 3 дні.", KNOWLEDGE, language="uk")
    assert "узгоджуємо індивідуально" in res.text


def test_reply_without_letters_falls_back_to_settings_language():
    """Негативный контроль: без сигнала поведение обязано остаться прежним."""
    res = redact_unbacked("9 900 грн.", KNOWLEDGE, language="uk")
    assert "узгоджуємо" in res.text
