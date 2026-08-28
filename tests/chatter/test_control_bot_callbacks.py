from __future__ import annotations

from chatter.notify.control_bot import CallbackResult, route_callback
from chatter.storage.db import Store


def _store(contact="telegram:42:demo", *, muted=False):
    s = Store(":memory:")
    s.get_or_create_contact(contact)
    if muted:
        s.mute(contact, source="human_takeover", now=100.0)
    return s


def test_resume_unmutes_and_reports():
    s = _store(muted=True)
    r = route_callback("resume:42:demo", store=s, now=200.0, language="ru", snooze_seconds=3600)
    assert isinstance(r, CallbackResult)
    assert s.get_or_create_contact("telegram:42:demo")["paused"] == 0
    assert "верну" in r.feedback_html.casefold()


def test_snooze_mutes_for_snooze_seconds():
    s = _store()
    route_callback("snooze:42:demo", store=s, now=1000.0, language="ru", snooze_seconds=3600)
    row = s.get_or_create_contact("telegram:42:demo")
    assert row["paused"] == 1
    assert row["pause_until"] == 1000.0 + 3600
    assert row["pause_source"] == "command"


def test_snooze_creates_contact_if_absent():
    # владелец может нажать «ещё 1ч» на диалог, которого Store ещё не знает
    s = Store(":memory:")
    r = route_callback("snooze:99:demo", store=s, now=0.0, language="ru", snooze_seconds=1800)
    assert s.get_or_create_contact("telegram:99:demo")["paused"] == 1
    assert "пауза" in r.feedback_html.casefold()


def test_stop_sets_kill_switch():
    s = _store()
    r = route_callback("stop:42:demo", store=s, now=5.0, language="ru", snooze_seconds=3600)
    assert s.get_runtime_flag("kill_switch") == "1"
    assert "останов" in r.feedback_html.casefold()


def test_keep_records_event_without_muting():
    s = _store()
    r = route_callback("keep:42:demo", store=s, now=5.0, language="ru", snooze_seconds=3600)
    assert s.get_or_create_contact("telegram:42:demo")["paused"] == 0     # не трогаем паузу
    assert s.count_events("escalation_kept", since_ts=0.0) == 1
    assert "оставл" in r.feedback_html.casefold()


def test_open_returns_link_no_state_change():
    s = _store()
    r = route_callback("open:42:demo", store=s, now=5.0, language="ru", snooze_seconds=3600)
    assert "42" in r.feedback_html            # ссылка на диалог с peer 42
    assert s.get_or_create_contact("telegram:42:demo")["paused"] == 0
    assert s.get_runtime_flag("kill_switch") in (None, "0")


def test_malformed_data_no_mutation():
    s = _store(muted=True)
    r = route_callback("garbage-no-colon", store=s, now=5.0, language="ru", snooze_seconds=3600)
    assert s.get_or_create_contact("telegram:42:demo")["paused"] == 1     # ничего не размутили
    assert r.feedback_html                                       # но что-то сказали


def test_unknown_action_no_mutation():
    s = _store(muted=True)
    r = route_callback("explode:42:demo", store=s, now=5.0, language="ru", snooze_seconds=3600)
    assert s.get_or_create_contact("telegram:42:demo")["paused"] == 1
    assert s.get_runtime_flag("kill_switch") in (None, "0")


def test_owner_action_clears_active_escalation_card():
    # Fix 2: тап владельца закрывает активную карточку эскалации → следующая
    # эскалация того же контакта создаст НОВУЮ, а не будет править закрытую.
    from chatter.core.escalation import esc_active_key
    s = _store()
    s.set_runtime_flag(esc_active_key("telegram:42:demo"), "bot:1:5", ts=0.0)
    route_callback("resume:42:demo", store=s, now=200.0, language="ru", snooze_seconds=3600)
    assert not s.get_runtime_flag(esc_active_key("telegram:42:demo"))   # очищен


# --- инцидент 2026-07-22: фидбек кнопок хардкодил имя «Аня» ------------------

def test_feedback_names_the_persona_of_the_contact():
    """На volska-раннере тап «Залишити боту» отвечал «✅ Залишено Ані» —
    имя персоны было зашито в строки. Фидбек обязан называть персону
    ИМЕННО ЭТОГО контакта (slug из contact_id)."""
    s = _store(contact="telegram:42:volska")
    resolver = {"telegram:42:volska": "Ольга"}.get
    r = route_callback("keep:42:volska", store=s, now=5.0, language="uk",
                       snooze_seconds=3600, persona_name_for=resolver)
    assert "Ольга" in r.feedback_html
    assert "Ані" not in r.feedback_html

    r = route_callback("resume:42:volska", store=s, now=6.0, language="uk",
                       snooze_seconds=3600, persona_name_for=resolver)
    assert "Ольга" in r.feedback_html

    r = route_callback("stop:42:volska", store=s, now=7.0, language="uk",
                       snooze_seconds=3600, persona_name_for=resolver)
    assert "Ольга" in r.feedback_html


def test_feedback_without_resolver_stays_generic_not_anya():
    # Фолбэк без резолвера — нейтральное слово, а не имя чужой персоны.
    s = _store()
    r = route_callback("keep:42:demo", store=s, now=5.0, language="ru", snooze_seconds=3600)
    assert "Ан" not in r.feedback_html    # ни «Аня», ни «Ане»
