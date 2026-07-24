"""Разовый ремонт живого row owner_write после регрессии дрила Д-10 T4.

Баг (пофикшен в 35cdebc5): классификатор на T4 переоткрыл code-доставленный
owner_write (delivered→open, closed_msg_id обнулён). Повтор T4 должен стартовать
из ЗДОРОВОГО конца T3, иначе меряем не то. Возвращаем: status=delivered,
closed_msg_id=93 (id карточки эскалации из console_cards), closed_ts=ts карточки.

GUARD: правим ТОЛЬКО если row реально в битом состоянии (open / closed_msg_id
NULL). Если уже здоров — no-op (идемпотентно, повторный запуск безопасен).
"""
import sqlite3
import sys

DB = ".secrets/demo.db"
CONTACT = "237616472:volska"
CARD_ID = 93  # console_cards.msg_id карточки эскалации T3

c = sqlite3.connect(DB)
c.row_factory = sqlite3.Row

row = c.execute(
    "SELECT okey, status, closed_msg_id, closed_ts FROM contact_obligations "
    "WHERE contact_id=? AND okey='owner_write'", (CONTACT,)).fetchone()
if row is None:
    sys.exit("owner_write не найден — нечего чинить")

print("ДО :", dict(row))
if row["status"] == "delivered" and row["closed_msg_id"] == CARD_ID:
    print("уже здоров (delivered / closed_msg_id=93) → no-op")
    sys.exit(0)
if not (row["status"] == "open" and row["closed_msg_id"] is None):
    sys.exit(f"неожиданное состояние {dict(row)} — руками, скрипт не трогает")

# closed_ts = ts доставки карточки 93 (достоверный момент закрытия T3)
card_ts = c.execute(
    "SELECT ts FROM console_cards WHERE contact_id=? AND msg_id=?",
    (CONTACT, CARD_ID)).fetchone()
closed_ts = card_ts["ts"] if card_ts else None

c.execute(
    "UPDATE contact_obligations SET status='delivered', closed_msg_id=?, "
    "closed_ts=?, detail='карточка керівниці доставлена' "
    "WHERE contact_id=? AND okey='owner_write' AND status='open' "
    "AND closed_msg_id IS NULL",
    (CARD_ID, closed_ts, CONTACT))
c.commit()

after = c.execute(
    "SELECT okey, status, closed_msg_id, closed_ts FROM contact_obligations "
    "WHERE contact_id=? AND okey='owner_write'", (CONTACT,)).fetchone()
print("ПОСЛЕ:", dict(after))
print(f"обновлено строк: {c.total_changes}")
