from __future__ import annotations
import re
from chatter.core import humanizer as H
from tests.chatter.test_humanizer_delays import TIMINGS

_SENTENCE_END = (".", "!", "?", "…")

def test_short_message_not_split():
    assert H.split_message("Привет!", TIMINGS) == ["Привет!"]

def test_long_message_split_into_2_or_3_parts():
    text = ("Первое предложение здесь, и оно довольно длинное само по себе. "
            "Второе предложение тоже тут, и добавляет ещё немного текста. "
            "Третье предложение продолжает мысль дальше и дальше. "
            "Четвёртое завершает мысль окончательно и бесповоротно.")
    parts = H.split_message(text, TIMINGS)
    assert 2 <= len(parts) <= 3
    # No content lost (ignoring whitespace differences)
    assert "".join(parts).replace(" ", "") == text.replace(" ", "")

def test_split_never_exceeds_max_parts():
    text = " ".join(f"Предложение номер {i}." for i in range(20))
    parts = H.split_message(text, TIMINGS)
    assert len(parts) <= 3

def test_each_part_nonempty():
    text = "Раз. Два. Три. Четыре. Пять. Шесть."
    for p in H.split_message(text, TIMINGS):
        assert p.strip()

def test_split_only_on_sentence_boundaries_not_commas():
    # Realistic long RU reply mixing commas inside sentences with several
    # sentence boundaries. Commas must never trigger a split.
    text = (
        "Супер, что написали, очень рада знакомству! "
        "У нас есть консультация, фотосессия и полное сопровождение, если нужно. "
        "Консультация стоит 5000 рублей, а фотосессия — от 15000, в зависимости от пакета. "
        "Расскажите, пожалуйста, что именно вас интересует, чтобы я могла подсказать точнее?"
    )
    parts = H.split_message(text, TIMINGS)
    assert len(parts) <= 3
    # every part except possibly the last ends with sentence punctuation
    for p in parts[:-1]:
        assert p.rstrip().endswith(_SENTENCE_END), p
    # no part boundary falls inside a word: every part, stripped, starts
    # with an uppercase letter (a new sentence) not a lowercase continuation
    for p in parts:
        first_alpha = next((c for c in p if c.isalpha()), "")
        assert first_alpha == "" or first_alpha.isupper(), p
    # content preserved ignoring whitespace
    assert "".join(parts).replace(" ", "").replace("\n", "") == \
        text.replace(" ", "").replace("\n", "")

def test_collapses_stray_blank_lines_inside_a_bubble():
    # A live reply rendered with a stray blank line because a model put
    # "\n\n" inside its text and it flowed into one Say bubble. Whitespace
    # (including newlines) must be normalized to single spaces so a DM
    # bubble is always clean single-flow text.
    text = "Фраза раз.\n\nФраза два."
    parts = H.split_message(text, TIMINGS)
    assert all(p != "" for p in parts)
    assert all("\n" not in p for p in parts)
    assert "".join(parts).replace(" ", "") == text.replace(" ", "").replace("\n", "")

def test_no_part_ends_mid_word():
    text = (
        "Работаю с портретами, свадьбами и репортажной съёмкой уже пять лет подряд. "
        "Обычно съёмка занимает пару часов, а обработка — до недели, зависит от объёма. "
        "Если хотите, пришлю примеры работ и расскажу подробнее про форматы."
    )
    parts = H.split_message(text, TIMINGS)
    for p in parts[:-1]:
        stripped = p.rstrip()
        assert stripped and stripped[-1] in _SENTENCE_END
