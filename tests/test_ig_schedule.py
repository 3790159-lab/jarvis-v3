# -*- coding: utf-8 -*-
"""Отложенная публикация IG — pure queue logic (``app.services.ig_schedule``).

$0, ноль сети — очередь на диске (``state/ig_scheduled_posts.json``, изолирован
conftest'ным ``_isolate_ig_schedule_file``), публикация/уведомления инжектятся
как ``publish_fn``/``notify_fn`` (см. ``process_due``). Покрывает всю спеку
таргет-тестов: постановка, срабатывание по времени (mock clock), отмена,
fail при протухшем медиа, квота 25/сутки.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from app.services import ig_schedule as igs


# ---- parse_schedule_args / parse_when --------------------------------------


def test_parse_schedule_args_splits_account_and_datetime():
    account, when = igs.parse_schedule_args("vera_ai_ua 2026-07-14 09:00")
    assert account == "vera_ai_ua"
    assert when == "2026-07-14 09:00"


def test_parse_schedule_args_strips_leading_at():
    account, when = igs.parse_schedule_args("@vera_ai_ua 2026-07-14 09:00")
    assert account == "vera_ai_ua"


def test_parse_schedule_args_empty_query():
    assert igs.parse_schedule_args("") == ("", "")
    assert igs.parse_schedule_args(None) == ("", "")


def test_parse_when_valid_future_datetime():
    now = datetime(2026, 7, 13, 12, 0)
    when = igs.parse_when("2026-07-14 09:00", now)
    assert when == datetime(2026, 7, 14, 9, 0)


def test_parse_when_rejects_bad_format():
    now = datetime(2026, 7, 13, 12, 0)
    with pytest.raises(igs.IGScheduleError):
        igs.parse_when("завтра утром", now)


def test_parse_when_rejects_past_time():
    now = datetime(2026, 7, 13, 12, 0)
    with pytest.raises(igs.IGScheduleError):
        igs.parse_when("2026-07-13 09:00", now)


def test_parse_when_rejects_empty():
    now = datetime(2026, 7, 13, 12, 0)
    with pytest.raises(igs.IGScheduleError):
        igs.parse_when("", now)


# ---- enqueue / list_posts / get_post ---------------------------------------


_PENDING = {
    "photo_url": "https://pub/x.jpg",
    "caption": "Смачна кава ☕",
    "topic": "кава",
    "source": "C:/tmp/x.jpg",
}


def test_enqueue_stores_post_and_returns_record(tmp_path, monkeypatch):
    monkeypatch.setenv("IG_SCHEDULE_FILE", str(tmp_path / "sched.json"))
    now = datetime(2026, 7, 13, 12, 0)
    run_at = datetime(2026, 7, 14, 9, 0)
    record = igs.enqueue(chat_id="237616472", account_key="vera_ai_ua",
                          run_at=run_at, pending=_PENDING, now=now)

    assert record["id"].startswith("sched_")
    assert record["status"] == "pending"
    assert record["photo_url"] == "https://pub/x.jpg"
    assert record["caption"] == "Смачна кава ☕"
    assert record["account_key"] == "vera_ai_ua"
    assert record["run_at"] == run_at.isoformat()

    stored = igs.get_post(record["id"])
    assert stored == record


def test_enqueue_two_posts_list_sorted_by_run_at(tmp_path, monkeypatch):
    monkeypatch.setenv("IG_SCHEDULE_FILE", str(tmp_path / "sched.json"))
    now = datetime(2026, 7, 13, 12, 0)
    later = igs.enqueue(chat_id="1", account_key="a", run_at=datetime(2026, 7, 15, 9, 0),
                         pending=_PENDING, now=now)
    earlier = igs.enqueue(chat_id="1", account_key="a", run_at=datetime(2026, 7, 14, 9, 0),
                           pending=_PENDING, now=now)

    posts = igs.list_posts()
    assert [p["id"] for p in posts] == [earlier["id"], later["id"]]


def test_get_post_unknown_id_returns_none(tmp_path, monkeypatch):
    monkeypatch.setenv("IG_SCHEDULE_FILE", str(tmp_path / "sched.json"))
    assert igs.get_post("sched_doesnotexist") is None


# ---- cancel -----------------------------------------------------------------


def test_cancel_pending_post_marks_cancelled(tmp_path, monkeypatch):
    monkeypatch.setenv("IG_SCHEDULE_FILE", str(tmp_path / "sched.json"))
    now = datetime(2026, 7, 13, 12, 0)
    record = igs.enqueue(chat_id="1", account_key="a", run_at=datetime(2026, 7, 14, 9, 0),
                          pending=_PENDING, now=now)

    assert igs.cancel(record["id"]) is True
    stored = igs.get_post(record["id"])
    assert stored["status"] == "cancelled"
    assert igs.list_posts(status="pending") == []


def test_cancel_unknown_id_returns_false(tmp_path, monkeypatch):
    monkeypatch.setenv("IG_SCHEDULE_FILE", str(tmp_path / "sched.json"))
    assert igs.cancel("sched_nope") is False


def test_cancel_already_cancelled_post_returns_false(tmp_path, monkeypatch):
    monkeypatch.setenv("IG_SCHEDULE_FILE", str(tmp_path / "sched.json"))
    now = datetime(2026, 7, 13, 12, 0)
    record = igs.enqueue(chat_id="1", account_key="a", run_at=datetime(2026, 7, 14, 9, 0),
                          pending=_PENDING, now=now)
    igs.cancel(record["id"])
    assert igs.cancel(record["id"]) is False


# ---- due_posts (mock clock) -------------------------------------------------


def test_due_posts_returns_only_posts_at_or_before_now(tmp_path, monkeypatch):
    monkeypatch.setenv("IG_SCHEDULE_FILE", str(tmp_path / "sched.json"))
    now = datetime(2026, 7, 13, 12, 0)
    due_one = igs.enqueue(chat_id="1", account_key="a", run_at=datetime(2026, 7, 14, 9, 0),
                           pending=_PENDING, now=now)
    future_one = igs.enqueue(chat_id="1", account_key="a", run_at=datetime(2026, 7, 20, 9, 0),
                              pending=_PENDING, now=now)

    check_time = datetime(2026, 7, 14, 9, 0)
    due = igs.due_posts(check_time)
    assert [p["id"] for p in due] == [due_one["id"]]

    earlier_check = datetime(2026, 7, 14, 8, 59)
    assert igs.due_posts(earlier_check) == []

    assert future_one["id"] not in [p["id"] for p in igs.due_posts(check_time)]


def test_due_posts_excludes_cancelled_and_published(tmp_path, monkeypatch):
    monkeypatch.setenv("IG_SCHEDULE_FILE", str(tmp_path / "sched.json"))
    now = datetime(2026, 7, 13, 12, 0)
    run_at = datetime(2026, 7, 14, 9, 0)
    cancelled = igs.enqueue(chat_id="1", account_key="a", run_at=run_at, pending=_PENDING, now=now)
    published = igs.enqueue(chat_id="1", account_key="a", run_at=run_at, pending=_PENDING, now=now)
    igs.cancel(cancelled["id"])
    igs.mark_published(published["id"], media_id="m1", permalink="https://ig/p/1", now=run_at)

    assert igs.due_posts(run_at) == []


# ---- mark_published / mark_failed / count_published_since -------------------


def test_mark_published_sets_fields(tmp_path, monkeypatch):
    monkeypatch.setenv("IG_SCHEDULE_FILE", str(tmp_path / "sched.json"))
    now = datetime(2026, 7, 13, 12, 0)
    record = igs.enqueue(chat_id="1", account_key="a", run_at=datetime(2026, 7, 14, 9, 0),
                          pending=_PENDING, now=now)
    igs.mark_published(record["id"], media_id="media_1",
                        permalink="https://instagram.com/p/AAA/", now=datetime(2026, 7, 14, 9, 1))
    stored = igs.get_post(record["id"])
    assert stored["status"] == "published"
    assert stored["media_id"] == "media_1"
    assert stored["permalink"] == "https://instagram.com/p/AAA/"
    assert stored["published_at"] == datetime(2026, 7, 14, 9, 1).isoformat()


def test_mark_failed_retry_true_keeps_pending(tmp_path, monkeypatch):
    monkeypatch.setenv("IG_SCHEDULE_FILE", str(tmp_path / "sched.json"))
    now = datetime(2026, 7, 13, 12, 0)
    record = igs.enqueue(chat_id="1", account_key="a", run_at=datetime(2026, 7, 14, 9, 0),
                          pending=_PENDING, now=now)
    igs.mark_failed(record["id"], error="quota", now=now, retry=True)
    stored = igs.get_post(record["id"])
    assert stored["status"] == "pending"
    assert stored["error"] == "quota"


def test_mark_failed_retry_false_marks_failed(tmp_path, monkeypatch):
    monkeypatch.setenv("IG_SCHEDULE_FILE", str(tmp_path / "sched.json"))
    now = datetime(2026, 7, 13, 12, 0)
    record = igs.enqueue(chat_id="1", account_key="a", run_at=datetime(2026, 7, 14, 9, 0),
                          pending=_PENDING, now=now)
    igs.mark_failed(record["id"], error="dead media url", now=now, retry=False)
    stored = igs.get_post(record["id"])
    assert stored["status"] == "failed"
    assert stored["error"] == "dead media url"
    # a failed (non-retry) post must never come up as due again
    assert igs.due_posts(datetime(2026, 7, 14, 9, 0)) == []


def test_count_published_since_filters_by_account_and_window(tmp_path, monkeypatch):
    monkeypatch.setenv("IG_SCHEDULE_FILE", str(tmp_path / "sched.json"))
    now = datetime(2026, 7, 13, 12, 0)
    a1 = igs.enqueue(chat_id="1", account_key="a", run_at=now, pending=_PENDING, now=now)
    a2 = igs.enqueue(chat_id="1", account_key="a", run_at=now, pending=_PENDING, now=now)
    b1 = igs.enqueue(chat_id="1", account_key="b", run_at=now, pending=_PENDING, now=now)
    igs.mark_published(a1["id"], media_id="m1", permalink="p1", now=datetime(2026, 7, 13, 8, 0))
    igs.mark_published(a2["id"], media_id="m2", permalink="p2", now=datetime(2026, 7, 12, 8, 0))  # >24h old
    igs.mark_published(b1["id"], media_id="m3", permalink="p3", now=datetime(2026, 7, 13, 8, 0))

    since = now - timedelta(hours=24)
    assert igs.count_published_since("a", since) == 1
    assert igs.count_published_since("b", since) == 1
    assert igs.count_published_since("c", since) == 0


# ---- process_due (mock clock + injected publish/notify) ---------------------


def test_process_due_publishes_due_post_and_notifies(tmp_path, monkeypatch):
    monkeypatch.setenv("IG_SCHEDULE_FILE", str(tmp_path / "sched.json"))
    now = datetime(2026, 7, 13, 12, 0)
    record = igs.enqueue(chat_id="237616472", account_key="a", run_at=now, pending=_PENDING, now=now)

    published = []
    notified = []
    result = igs.process_due(
        now,
        publish_fn=lambda post: published.append(post["id"]) or
        {"id": "media_1", "permalink": "https://instagram.com/p/BBB/"},
        notify_fn=lambda chat_id, text: notified.append((chat_id, text)),
    )

    assert result == {"published": [record["id"]], "failed": [], "skipped_quota": []}
    assert published == [record["id"]]
    assert notified and notified[0][0] == "237616472"
    assert "instagram.com/p/BBB" in notified[0][1]
    stored = igs.get_post(record["id"])
    assert stored["status"] == "published"


def test_process_due_ignores_not_yet_due_post(tmp_path, monkeypatch):
    monkeypatch.setenv("IG_SCHEDULE_FILE", str(tmp_path / "sched.json"))
    now = datetime(2026, 7, 13, 12, 0)
    igs.enqueue(chat_id="1", account_key="a", run_at=datetime(2026, 7, 20, 9, 0),
                pending=_PENDING, now=now)

    calls = {"n": 0}
    result = igs.process_due(
        now,
        publish_fn=lambda post: calls.__setitem__("n", calls["n"] + 1) or {},
        notify_fn=lambda *a, **k: None,
    )
    assert calls["n"] == 0
    assert result == {"published": [], "failed": [], "skipped_quota": []}


def test_process_due_fails_closed_on_stale_media_error(tmp_path, monkeypatch):
    """'Протухшее медиа' — publish_fn raises a non-quota error (e.g. Graph API
    couldn't fetch the image URL). Fail-closed: NOT marked published, no fake
    permalink notified, status flips to 'failed' so it's never retried forever,
    and the admin gets an honest failure message."""
    monkeypatch.setenv("IG_SCHEDULE_FILE", str(tmp_path / "sched.json"))
    now = datetime(2026, 7, 13, 12, 0)
    record = igs.enqueue(chat_id="237616472", account_key="a", run_at=now, pending=_PENDING, now=now)

    class _StaleMediaError(RuntimeError):
        def __init__(self):
            super().__init__("Image url could not be downloaded")
            self.code = 100
            self.subcode = None

    def _publish(post):
        raise _StaleMediaError()

    notified = []
    result = igs.process_due(
        now, publish_fn=_publish,
        notify_fn=lambda chat_id, text: notified.append((chat_id, text)),
    )

    assert result == {"published": [], "failed": [record["id"]], "skipped_quota": []}
    stored = igs.get_post(record["id"])
    assert stored["status"] == "failed"
    assert "could not be downloaded" in stored["error"]
    assert notified and "не опубликован" in notified[0][1].lower()
    assert "instagram.com/p/" not in notified[0][1]
    # никогда больше не попадёт в due_posts (не крутим публикацию по кругу)
    assert igs.due_posts(now) == []


def test_process_due_quota_error_keeps_pending_for_retry(tmp_path, monkeypatch):
    monkeypatch.setenv("IG_SCHEDULE_FILE", str(tmp_path / "sched.json"))
    now = datetime(2026, 7, 13, 12, 0)
    record = igs.enqueue(chat_id="1", account_key="a", run_at=now, pending=_PENDING, now=now)

    class _QuotaError(RuntimeError):
        def __init__(self):
            super().__init__("The media posting limit has been reached.")
            self.code = 9
            self.subcode = 2207042

    def _publish(post):
        raise _QuotaError()

    result = igs.process_due(now, publish_fn=_publish, notify_fn=lambda *a, **k: None)

    assert result == {"published": [], "failed": [record["id"]], "skipped_quota": []}
    stored = igs.get_post(record["id"])
    assert stored["status"] == "pending"  # quota resets — worth retrying later
    assert igs.due_posts(now) == [stored]


def test_process_due_skips_when_account_quota_already_hit_today(tmp_path, monkeypatch):
    monkeypatch.setenv("IG_SCHEDULE_FILE", str(tmp_path / "sched.json"))
    now = datetime(2026, 7, 13, 12, 0)
    # 25 already-published posts for account "a" in the last 24h.
    for i in range(25):
        p = igs.enqueue(chat_id="1", account_key="a", run_at=now, pending=_PENDING, now=now)
        igs.mark_published(p["id"], media_id=f"m{i}", permalink=f"p{i}",
                            now=now - timedelta(hours=1))
    due_record = igs.enqueue(chat_id="1", account_key="a", run_at=now, pending=_PENDING, now=now)

    calls = {"n": 0}
    result = igs.process_due(
        now,
        publish_fn=lambda post: calls.__setitem__("n", calls["n"] + 1) or {},
        notify_fn=lambda *a, **k: None,
    )

    assert calls["n"] == 0  # never even attempted — quota pre-check
    assert result["skipped_quota"] == [due_record["id"]]
    stored = igs.get_post(due_record["id"])
    assert stored["status"] == "pending"  # stays queued for a later tick


def test_process_due_other_account_not_blocked_by_quota(tmp_path, monkeypatch):
    monkeypatch.setenv("IG_SCHEDULE_FILE", str(tmp_path / "sched.json"))
    now = datetime(2026, 7, 13, 12, 0)
    for i in range(25):
        p = igs.enqueue(chat_id="1", account_key="a", run_at=now, pending=_PENDING, now=now)
        igs.mark_published(p["id"], media_id=f"m{i}", permalink=f"p{i}",
                            now=now - timedelta(hours=1))
    other = igs.enqueue(chat_id="1", account_key="b", run_at=now, pending=_PENDING, now=now)

    result = igs.process_due(
        now, publish_fn=lambda post: {"id": "mx", "permalink": "https://instagram.com/p/X/"},
        notify_fn=lambda *a, **k: None,
    )
    assert result["published"] == [other["id"]]


# ---- build_queue_text / queue_keyboard --------------------------------------


def test_build_queue_text_empty():
    assert "пуста" in igs.build_queue_text([])


def test_build_queue_text_lists_posts(tmp_path, monkeypatch):
    monkeypatch.setenv("IG_SCHEDULE_FILE", str(tmp_path / "sched.json"))
    now = datetime(2026, 7, 13, 12, 0)
    record = igs.enqueue(chat_id="1", account_key="vera_ai_ua",
                          run_at=datetime(2026, 7, 14, 9, 0), pending=_PENDING, now=now)
    text = igs.build_queue_text([record])
    assert "vera_ai_ua" in text
    assert "кава" in text


def test_queue_keyboard_has_one_cancel_button_per_post(tmp_path, monkeypatch):
    monkeypatch.setenv("IG_SCHEDULE_FILE", str(tmp_path / "sched.json"))
    now = datetime(2026, 7, 13, 12, 0)
    record = igs.enqueue(chat_id="1", account_key="a", run_at=datetime(2026, 7, 14, 9, 0),
                          pending=_PENDING, now=now)
    kb = igs.queue_keyboard([record])
    assert len(kb) == 1
    assert kb[0][0]["callback_data"] == "igsched:cancel:%s" % record["id"]
