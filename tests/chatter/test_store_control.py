"""Пауза в SQLite: атрибуция обязательна, состояние переживает рестарт."""
from __future__ import annotations

import pytest

from chatter.storage.db import Store


def test_mute_requires_a_known_source():
    # Пауза без причины = баг-класс (спека §4): самозаглушка тиха и вечна,
    # поэтому поставить её без атрибуции должно быть ТЕХНИЧЕСКИ невозможно.
    with Store(":memory:") as s:
        s.get_or_create_contact("c1")
        with pytest.raises(ValueError):
            s.mute("c1", source="потому что", now=100.0)


def test_mute_records_who_and_why():
    with Store(":memory:") as s:
        s.get_or_create_contact("c1")
        s.mute("c1", source="human_takeover", msg_id=4821,
               detail="Здравствуйте, я сам перезвоню", now=100.0)
        row = s.get_or_create_contact("c1")
        assert row["paused"] == 1
        assert row["pause_source"] == "human_takeover"
        assert row["pause_msg_id"] == 4821
        assert row["pause_detail"] == "Здравствуйте, я сам перезвоню"
        assert row["paused_at"] == 100.0
        assert row["pause_until"] is None


def test_pause_survives_restart(tmp_path):
    # Гардиан перезапускает раннер штатно — пауза в памяти была бы потеряна
    # ровно тогда, когда владелец на неё рассчитывает.
    db = tmp_path / "s.db"
    with Store(db) as s:
        s.get_or_create_contact("c1")
        s.mute("c1", source="command", until=999.0, now=100.0)
    with Store(db) as s2:
        row = s2.get_or_create_contact("c1")
        assert row["paused"] == 1
        assert row["pause_source"] == "command"
        assert row["pause_until"] == 999.0


def test_unmute_clears_every_attribution_field():
    with Store(":memory:") as s:
        s.get_or_create_contact("c1")
        s.mute("c1", source="human_takeover", msg_id=7, detail="x", now=100.0)
        s.unmute("c1")
        row = s.get_or_create_contact("c1")
        assert row["paused"] == 0
        # Осколки старой атрибуции соврут в /status при следующей паузе.
        assert row["pause_source"] is None
        assert row["pause_msg_id"] is None
        assert row["pause_detail"] is None
        assert row["pause_until"] is None
        assert row["paused_at"] is None


def test_muted_contacts_returns_only_paused_rows():
    with Store(":memory:") as s:
        s.get_or_create_contact("c1")
        s.get_or_create_contact("c2")
        s.mute("c1", source="command", now=100.0)
        assert [r["contact_id"] for r in s.muted_contacts()] == ["c1"]


def test_note_human_out_tracks_owner_last_manual_message():
    with Store(":memory:") as s:
        s.get_or_create_contact("c1")
        s.note_human_out("c1", ts=500.0)
        assert s.get_or_create_contact("c1")["last_human_out_ts"] == 500.0


def test_runtime_flag_roundtrip_and_default():
    with Store(":memory:") as s:
        assert s.get_runtime_flag("kill_switch") is None
        s.set_runtime_flag("kill_switch", "1", ts=100.0)
        assert s.get_runtime_flag("kill_switch") == "1"
        s.set_runtime_flag("kill_switch", "0", ts=200.0)   # /start перезаписывает
        assert s.get_runtime_flag("kill_switch") == "0"


def test_events_count_within_window_only():
    with Store(":memory:") as s:
        s.add_event("takeover", contact_id="c1", detail="msg 1", ts=100.0)
        s.add_event("takeover", contact_id="c1", detail="msg 2", ts=200.0)
        s.add_event("unknown_outgoing", ts=200.0)
        assert s.count_events("takeover", since_ts=150.0) == 1
        assert s.count_events("takeover", since_ts=0.0) == 2
        assert s.count_events("unattributed_pause", since_ts=0.0) == 0


def test_card_maps_saved_message_to_contact_and_survives_restart(tmp_path):
    # Адресация реплаем обязана пережить рестарт: карточка остаётся лежать в
    # Saved Messages, и владелец ответит на неё через час.
    db = tmp_path / "s.db"
    with Store(db) as s:
        s.add_card(msg_id=555, contact_id="237616472:demo", kind="pause", ts=100.0)
    with Store(db) as s2:
        assert s2.card_contact(555) == "237616472:demo"
        assert s2.card_contact(999) is None


def test_has_contact_true_for_existing_false_for_unknown():
    with Store(":memory:") as s:
        s.get_or_create_contact("c1")
        assert s.has_contact("c1") is True
        assert s.has_contact("нет-такого") is False


def test_has_contact_does_not_create_a_row():
    # Главный тест: has_contact — это ТОЛЬКО проверка, get_or_create_contact
    # для этого не годится, потому что он создал бы строку и объявил
    # управляемым любой чат, куда владелец написал с этого же аккаунта.
    with Store(":memory:") as s:
        s.has_contact("нет-такого")
        rows = s._conn.execute("SELECT COUNT(*) FROM contacts").fetchone()[0]
        assert rows == 0


def test_mute_raises_for_unknown_contact():
    # Сейчас UPDATE ... WHERE contact_id=? на несуществующей строке молча
    # обновляет 0 строк и возвращает None — ровно как при успехе. Пауза НЕ
    # встаёт, а вызывающий код (и /status) этого не узнают: Аня продолжит
    # отвечать поверх владельца, который уже пишет клиенту руками. mute()
    # уже параноит про причину паузы (source обязателен) — та же паранойя
    # обязана распространяться на существование самого контакта.
    with Store(":memory:") as s:
        with pytest.raises(KeyError):
            s.mute("нет-такого-контакта", source="command", now=100.0)


def test_unmute_on_unknown_contact_is_a_quiet_noop():
    # В отличие от mute(), unmute() на несуществующем контакте — ЖЕЛАЕМОЕ
    # идемпотентное поведение: "снять паузу с того, у кого её нет" уже
    # достигнуто, это не потерянное действие, а не-действие. Раздутие до
    # исключения тут было бы шумом без пользы (например /resume на уже
    # отвеченный диалог не должен падать).
    with Store(":memory:") as s:
        s.unmute("нет-такого-контакта")  # не должно поднять исключение
        rows = s._conn.execute("SELECT COUNT(*) FROM contacts").fetchone()[0]
        assert rows == 0  # и уж тем более не создаёт строку


def test_muted_contacts_orders_by_paused_at_ascending():
    with Store(":memory:") as s:
        s.get_or_create_contact("c1")
        s.get_or_create_contact("c2")
        s.get_or_create_contact("c3")
        s.mute("c1", source="command", now=200.0)
        s.mute("c2", source="command", now=100.0)
        s.mute("c3", source="command", now=150.0)
        # Фактический порядок выдачи — от самой старой паузы к новой.
        assert [r["contact_id"] for r in s.muted_contacts()] == ["c2", "c3", "c1"]


def test_muted_contacts_includes_legacy_row_with_null_paused_at():
    # Легаси-строка из арки 1 (старый set_flag("paused", True)) писала
    # paused=1 БЕЗ paused_at/pause_source. mute() такого сам не создаст
    # (now — обязательный аргумент), поэтому имитируем сырым SQL, как это
    # реально выглядит после миграции существующей продовой базы.
    with Store(":memory:") as s:
        s._conn.execute(
            "INSERT INTO contacts(contact_id, paused) VALUES (?, 1)", ("legacy1",))
        s._conn.commit()
        s.get_or_create_contact("c1")
        s.mute("c1", source="command", now=100.0)
        rows = s.muted_contacts()
        ids = [r["contact_id"] for r in rows]
        # Обязана попасть в выдачу: строка, которая глушит Аню и невидима в
        # /status, — это и есть тихая вечная самозаглушка, ровно то, от чего
        # вся эта арка.
        assert "legacy1" in ids
        # Фиксируем фактическое поведение SQLite: ORDER BY ... ASC ставит
        # NULL раньше любого не-NULL значения, так что легаси-строка без
        # paused_at всегда первая — что уместно, её "возраст" неизвестен и
        # безопаснее показать её владельцу первой, а не похоронить в хвосте.
        assert ids[0] == "legacy1"


def test_begin_takeover_wins_on_a_free_contact():
    with Store(":memory:") as s:
        s.get_or_create_contact("c1")
        assert s.begin_takeover("c1", msg_id=100, detail="msg1", now=50.0) is True
        row = s.get_or_create_contact("c1")
        assert row["paused"] == 1
        assert row["pause_source"] == "human_takeover"
        assert row["pause_msg_id"] == 100
        assert row["pause_detail"] == "msg1"
        assert row["paused_at"] == 50.0


def test_begin_takeover_loses_on_an_already_paused_contact():
    # Три параллельных _outgoing_handler на быстрый залп сообщений владельца
    # (Telethon без sequential_updates=True): только ПЕРВЫЙ обязан выиграть
    # эпизод и получить право слать карточку/событие takeover.
    with Store(":memory:") as s:
        s.get_or_create_contact("c1")
        assert s.begin_takeover("c1", msg_id=100, detail="msg1", now=50.0) is True
        assert s.begin_takeover("c1", msg_id=101, detail="msg2", now=51.0) is False
        # Проигравший вызов НЕ переписал атрибуцию (это работа
        # update_pause_attribution, отдельного вызова).
        row = s.get_or_create_contact("c1")
        assert row["pause_msg_id"] == 100


def test_begin_takeover_wins_again_after_unmute():
    # unmute -> заново свободный контакт -> следующий takeover это НОВЫЙ
    # эпизод, а не продолжение старого.
    with Store(":memory:") as s:
        s.get_or_create_contact("c1")
        assert s.begin_takeover("c1", msg_id=100, detail="msg1", now=50.0) is True
        s.unmute("c1")
        assert s.begin_takeover("c1", msg_id=200, detail="msg2", now=99.0) is True
        row = s.get_or_create_contact("c1")
        assert row["pause_msg_id"] == 200


def test_update_pause_attribution_advances_to_a_newer_message():
    with Store(":memory:") as s:
        s.get_or_create_contact("c1")
        s.begin_takeover("c1", msg_id=100, detail="msg1", now=50.0)
        s.update_pause_attribution("c1", msg_id=105, detail="msg2 -- свежее")
        row = s.get_or_create_contact("c1")
        assert row["pause_msg_id"] == 105
        assert row["pause_detail"] == "msg2 -- свежее"


def test_update_pause_attribution_ignores_an_older_message_arriving_late():
    # Гонка завершения параллельных задач: более РАННЕЕ сообщение (msg_id
    # меньше) может обработаться ПОСЛЕ более позднего. /status обязан
    # показывать самую свежую реплику по id, а не по порядку завершения.
    with Store(":memory:") as s:
        s.get_or_create_contact("c1")
        s.begin_takeover("c1", msg_id=100, detail="msg1", now=50.0)
        s.update_pause_attribution("c1", msg_id=105, detail="msg2 -- свежее")
        s.update_pause_attribution("c1", msg_id=101, detail="msg1.5 -- опоздавшее")
        row = s.get_or_create_contact("c1")
        assert row["pause_msg_id"] == 105
        assert row["pause_detail"] == "msg2 -- свежее"


def test_last_human_out_ts_updates_on_every_call_regardless_of_takeover_outcome():
    # last_human_out_ts -- от него авто-возврат (спека §8) отсчитывает
    # молчание владельца, и он обязан двигаться на КАЖДОМ его ручном
    # сообщении, не только на первом в эпизоде (иначе владелец активно
    # пишет второй час, а таймер думает, что молчит с первой реплики).
    # note_human_out — отдельный вызов от begin_takeover, поэтому он
    # обновляется одинаково независимо от True/False результата захвата.
    with Store(":memory:") as s:
        s.get_or_create_contact("c1")
        s.note_human_out("c1", ts=50.0)
        assert s.begin_takeover("c1", msg_id=100, detail="msg1", now=50.0) is True
        assert s.get_or_create_contact("c1")["last_human_out_ts"] == 50.0
        s.note_human_out("c1", ts=99.0)   # второе сообщение того же эпизода
        assert s.begin_takeover("c1", msg_id=101, detail="msg2", now=99.0) is False
        assert s.get_or_create_contact("c1")["last_human_out_ts"] == 99.0


def test_add_card_upsert_overwrites_kind_and_ts_too():
    # add_card при конфликте msg_id раньше обновлял только contact_id,
    # оставляя старые kind/ts — частичный апдейт без объяснения. В реальности
    # msg_id в Saved Messages не переиспользуется, так что конфликт — это
    # либо повторная попытка записать ТУ ЖЕ карточку (все поля совпадут), либо
    # программная ошибка. В обоих случаях полный оверрайт безопаснее частичного:
    # частичный апдейт мог бы оставить kind/ts, которые лгут о новом contact_id.
    with Store(":memory:") as s:
        s.add_card(msg_id=1, contact_id="c1", kind="pause", ts=100.0)
        s.add_card(msg_id=1, contact_id="c2", kind="unattributed", ts=200.0)
        row = s._conn.execute(
            "SELECT contact_id, kind, ts FROM console_cards WHERE msg_id=1").fetchone()
        assert row["contact_id"] == "c2"
        assert row["kind"] == "unattributed"
        assert row["ts"] == 200.0


# --- status_index: нумерованная адресация из /status (спека 3A-UX §3) ---


def test_issue_status_index_numbers_from_one_in_list_order():
    with Store(":memory:") as s:
        s.issue_status_index(["daniil", "vasya"], now=100.0)
        assert s.status_index_contact(1) == "daniil"
        assert s.status_index_contact(2) == "vasya"
        assert s.status_index_snapshot() == ["daniil", "vasya"]


def test_issue_status_index_overwrites_the_whole_table():
    # Вторая выдача — новый /status, старые номера не должны остаться
    # хвостом (например при уменьшении списка старый №3 обязан исчезнуть,
    # а не продолжать указывать на кого-то, кого уже нет в новом списке).
    with Store(":memory:") as s:
        s.issue_status_index(["a", "b", "c"], now=100.0)
        s.issue_status_index(["x", "y"], now=200.0)
        assert s.status_index_snapshot() == ["x", "y"]
        assert s.status_index_contact(3) is None


def test_status_index_contact_unknown_number_returns_none():
    with Store(":memory:") as s:
        s.issue_status_index(["a"], now=100.0)
        assert s.status_index_contact(999) is None


def test_status_index_number_stays_bound_to_the_original_contact():
    # ГЛАВНЫЙ тест этой таблицы (спека §3): номер привязан к контакту В
    # МОМЕНТ ВЫДАЧИ. Даниил (1) авто-вернулся (перестал быть заглушённым) —
    # это НЕ меняет то, что значит "1". Если бы номера пересчитывались "на
    # лету" от текущего muted_contacts(), "1" стал бы Васей — владелец,
    # набравший /resume 1, попал бы в ЧУЖОЙ диалог. Это и есть "промах в
    # чужой диалог = катастрофа доверия" из спеки.
    with Store(":memory:") as s:
        s.get_or_create_contact("daniil")
        s.get_or_create_contact("vasya")
        s.mute("daniil", source="human_takeover", now=100.0)
        s.mute("vasya", source="human_takeover", now=100.0)
        s.issue_status_index(["daniil", "vasya"], now=100.0)

        s.unmute("daniil")  # авто-возврат — daniil больше не заглушён

        # "1" всё ещё означает Даниила, а не сдвинулся на Васю.
        assert s.status_index_contact(1) == "daniil"
        assert s.status_index_contact(2) == "vasya"


def test_status_index_survives_restart(tmp_path):
    db = tmp_path / "s.db"
    with Store(db) as s:
        s.issue_status_index(["daniil", "vasya"], now=100.0)
    with Store(db) as s2:
        assert s2.status_index_contact(1) == "daniil"
        assert s2.status_index_contact(2) == "vasya"


def test_status_index_is_current_true_when_muted_set_matches_snapshot():
    with Store(":memory:") as s:
        s.get_or_create_contact("daniil")
        s.get_or_create_contact("vasya")
        s.mute("daniil", source="human_takeover", now=100.0)
        s.mute("vasya", source="human_takeover", now=100.0)
        s.issue_status_index(["daniil", "vasya"], now=100.0)
        assert s.status_index_is_current(["daniil", "vasya"]) is True


def test_status_index_is_current_false_when_a_new_contact_got_muted():
    # Картина мира устарела: владелец видел список без Пети, а сейчас Петя
    # тоже заглушён — номера больше не значат ровно то, что он видел.
    with Store(":memory:") as s:
        s.issue_status_index(["daniil", "vasya"], now=100.0)
        assert s.status_index_is_current(["daniil", "vasya", "petya"]) is False


def test_status_index_is_current_false_when_one_contact_returned():
    with Store(":memory:") as s:
        s.issue_status_index(["daniil", "vasya"], now=100.0)
        assert s.status_index_is_current(["vasya"]) is False


def test_status_index_is_current_ignores_order():
    # Сверка идёт по МНОЖЕСТВУ, а не по последовательности: /status показал
    # [даниил, вася], а muted_contacts() (ORDER BY paused_at) мог отдать их
    # в другом порядке при той же паузе (например обновление paused_at при
    # продолжении перехвата — see update_pause_attribution). Смысл вопроса
    # "тот ли это список, который он видел" — про СОСТАВ диалогов, а не про
    # то, в каком порядке их перечислили: номера уже привязаны к контактам
    # в status_index, порядок muted_contacts() их не переопределяет.
    with Store(":memory:") as s:
        s.issue_status_index(["daniil", "vasya"], now=100.0)
        assert s.status_index_is_current(["vasya", "daniil"]) is True
