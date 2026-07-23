from __future__ import annotations

from chatter.core.console import (
    console_text, escalation_buttons, format_escalation_card, pause_buttons,
)
from chatter.notify.base import Action


def test_escalation_buttons_full_set_localized():
    btns = escalation_buttons("ru")
    actions = [b.action for b in btns]
    assert actions == [Action.RESUME, Action.SNOOZE, Action.OPEN, Action.STOP, Action.KEEP]
    labels = " ".join(b.label for b in btns)
    assert "Вернуть Аню" in labels
    assert "Ещё 1ч" in labels
    assert "Открыть диалог" in labels
    assert "Стоп везде" in labels
    assert "Оставить Ане" in labels


def test_pause_buttons_omit_keep():
    # Карточка паузы не эскалация — кнопки «Оставить Ане» там нет.
    actions = [b.action for b in pause_buttons("ru")]
    assert actions == [Action.RESUME, Action.SNOOZE, Action.OPEN, Action.STOP]
    assert Action.KEEP not in actions


def test_buttons_english():
    labels = " ".join(b.label for b in escalation_buttons("en"))
    assert "Bring Anya back" in labels or "Bring" in labels
    assert "Stop everywhere" in labels or "Stop" in labels


def test_unknown_language_falls_back_to_ru():
    labels = " ".join(b.label for b in escalation_buttons("zz"))
    assert "Вернуть Аню" in labels


RECENT = [("user", "Хочу забронировать на субботу"),
          ("assistant", "Отлично, уточню детали"),
          ("user", "Готов внести предоплату")]


def test_escalation_card_has_who_wants_why_and_recent():
    out = format_escalation_card(
        name_html='<a href="t.me/dan">Даниил</a>', link="t.me/dan",
        summary="готов бронировать и платить", reason="ключевое слово «оплата»",
        recent=RECENT, language="ru")
    assert "Даниил" in out
    assert "готов бронировать" in out       # что хочет (одной строкой)
    assert "оплата" in out                   # почему эскалировано
    assert "Готов внести предоплату" in out  # последняя реплика


def test_escalation_card_escapes_raw_recent_text():
    out = format_escalation_card(
        name_html="Аноним", link="t.me/x", summary="s", reason="r",
        recent=[("user", "<script>alert(1)</script>")], language="ru")
    assert "<script>" not in out
    assert "&lt;script&gt;" in out


def test_escalation_card_caps_recent_lines():
    many = [("user", f"msg{i}") for i in range(10)]
    out = format_escalation_card(
        name_html="X", link="t.me/x", summary="s", reason="r",
        recent=many, language="ru")
    # последние 5, не все 10 (msg0..msg4 отброшены)
    assert "msg9" in out and "msg5" in out
    assert "msg4" not in out


def test_degraded_alert_string_localized():
    s = console_text("degraded_alert", "ru", count=7, hours=24)
    assert "7" in s and ("сбо" in s.casefold())
    # 2026-07-23: текст обязан признавать, что от классификатора зависит и
    # ПАМЯТЬ лида, а не только эскалации — иначе владелец недооценит сбой.
    assert "профиль" in s.casefold()
    ua = console_text("profile_stale_alert", "uk", count=3, name="42", link="tg://user?id=42")
    assert "3" in ua and "42" in ua
