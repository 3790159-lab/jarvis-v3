"""Автономный запуск обеих панелей + синтетические данные для скриншотов.

ЗАЧЕМ ОТДЕЛЬНО ОТ app/main.py: поднимать весь прод-бэкенд ради картинок — это
десятки роутеров, внешние коннекты и риск задеть живое. Здесь монтируются
РОВНО два роутера и ничего больше.

ДАННЫЕ СИНТЕТИЧЕСКИЕ. Скриншоты уходят человеку, а боевая БД — это переписка
живых лидов; показывать её имена и тексты в картинках нельзя. Все контакты,
реплики и суммы ниже выдуманы.

Запуск:
    python scripts/panels_demo.py --seed --port 8099
"""
from __future__ import annotations

import argparse
import os
import random
import sys
import time
from pathlib import Path

# Скрипт лежит в scripts/, а импортирует app/ и chatter/ из корня репозитория.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from chatter.runtime_paths import chatter_beat_path  # noqa: E402

DAY = 86400.0

NAMES = ["Марина К.", "Олег В.", "Ірина П.", "Тарас М.", "Ганна Л.", "Дмитро С.",
         "Юлія Р.", "Богдан Ч.", "Оксана Т.", "Артем Н."]
ASKS = [
    "Скільки коштує ведення соцмереж?",
    "Потрібен логотип для нового бренду",
    "А маркетингова стратегія у вас є?",
    "Можете прорахувати упаковку для косметики?",
    "Цікавить редизайн логотипа",
    "Скільки часу займе презентація?",
]
REPLIES = [
    "Вітаю. Розкажіть, будь ласка, яка у вас задача?",
    "SMM-ведення — 750–900 $ за місяць, мінімальний строк від 2 місяців.",
    "Маркетингова стратегія — 600–800 $, термін 21 календарний день.",
    "Передала ваше питання керівниці — щойно відповість, напишу.",
]


def seed(path: str, *, now: float | None = None) -> None:
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from chatter.storage.db import Store
    from chatter.payments.model import PaymentRecord, make_dedup_key
    from chatter.payments.money import from_major

    now = now or time.time()
    p = Path(path)
    if p.exists():
        p.unlink()
    p.parent.mkdir(parents=True, exist_ok=True)
    s = Store(str(p))
    rnd = random.Random(20260729)

    total = 47
    for i in range(total):
        cid = f"{500000 + i}:volska"
        s.get_or_create_contact(cid)
        # Первые двое пишут СЕГОДНЯ: без свежей эскалации блок «Требує вас»
        # состоит из одной свёртки застарелых, и главную карточку экрана на
        # скриншоте просто не видно. Порог свежести — 48 годин.
        start = (now - rnd.uniform(0.1, 0.7) * DAY if i < 2
                 else now - rnd.uniform(2.5, 29.0) * DAY)
        n_msgs = rnd.randint(2, 14)
        t = start
        for j in range(n_msgs):
            role = "user" if j % 2 == 0 else "assistant"
            text = (rnd.choice(ASKS) if role == "user" else rnd.choice(REPLIES))
            s.add_message(cid, role, text, ts=t)
            t += rnd.uniform(40, 900)

        # воронка: 47 диалогов → 18 квалифицировано → 9 передано → 3 оплаты
        s.record_transition(cid, from_state="new", to_state="qualifying",
                            signal="engaged", ts=start + 120)
        if i < 18:
            s.record_transition(cid, from_state="qualifying", to_state="hot",
                                signal="interested", ts=start + 600)
        if i < 9:
            s.record_transition(cid, from_state="hot", to_state="escalated",
                                signal="needs_human", ts=start + 900)
            s.add_card(msg_id=9000 + i, contact_id=cid, kind="escalation",
                       ts=start + 905)
            s.set_state(cid, "escalated")
            if i < 3:
                s.set_runtime_flag(f"esc_active:{cid}", f"bot:1:{9000+i}", ts=start + 905)
        if 3 <= i < 6:
            amount = rnd.choice([300.0, 400.0, 750.0, 900.0, None])
            s.record_transition(cid, from_state="escalated", to_state="closed",
                                signal="bought", ts=start + 1200)
            s.set_state(cid, "closed")
            s.record_payment(PaymentRecord(
                contact_id=cid, dedup_key=make_dedup_key("tap", 9000 + i),
                ts=start + 1200, confirmed_by="owner",
                # «Без суми» — легальный случай («Оплачено» без цифры), и сеялка
                # обязана его порождать: str(None) сюда влетал как строка.
                amount=(from_major(str(amount), "USD") if amount is not None
                        else None)))

    s.set_runtime_flag("kill_switch", "0", ts=now)
    # Детали БОЕВОГО вида, а не пустые строки. Приёмка 13.08: стенд с пустыми
    # деталями прошёл узкий экран, а живая панель на тех же 390 px уехала вбок
    # на 43 px — её ленту распирал `stop_reason=max_tokens`, слово без единой
    # возможности переноса. Вежливые демо-данные делают приёмку слепой ровно к
    # тому классу дефектов, ради которого она заводилась.
    # Первая строка — снятая с прода: 56 символов без единого пробела. Именно
    # она распирала таблицу, а не длинные фразы: у фразы есть где переноситься.
    EVENTS = [
        ("unbacked_redacted",
         "deadline:30,price:40,price:80,price:1000,large_number:1000"),
        ("classifier_error",
         "ответ обрезан (stop_reason=max_tokens при лимите 500) — "
         "поднимите _CLASSIFIER_MAX_TOKENS"),
        ("stale_reply_cancelled", "не відправлено 1 з 2 бабблів"),
        ("auto_resume", "8849893367:volska"),
        ("takeover", "447"),
    ]
    for k, detail in EVENTS:
        s.add_event(k, contact_id=f"{500001}:volska", detail=detail,
                    ts=now - rnd.uniform(60, 5000))
    del s
    print(f"[seed] синтетична БД готова: {p}  ({total} діалогів, 3 оплати)")


def build_app():
    from fastapi import FastAPI
    from app.routers.jarvis_panel import router as jarvis_router
    from app.routers.tamapi_dashboard import router as tamapi_router

    app = FastAPI(title="Jarvis panels (standalone)")
    app.include_router(tamapi_router)
    app.include_router(jarvis_router)
    return app


def _keep_heartbeat(path: str, every: float = 30.0) -> None:
    import threading

    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)

    def loop():
        while True:
            try:
                p.write_text(str(time.time()), encoding="utf-8")
            except OSError:
                # Демо-стенд не имеет права падать из-за картинки: экран сам
                # покажет «немає зв'язку», и это будет видно.
                pass
            time.sleep(every)

    threading.Thread(target=loop, daemon=True).start()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", action="store_true")
    ap.add_argument("--db", default="state/panels_demo.db")
    ap.add_argument("--port", type=int, default=8099)
    ap.add_argument("--host", default="127.0.0.1")
    a = ap.parse_args()

    if a.seed:
        seed(a.db)
    os.environ.setdefault("TAMAPI_DB", a.db)
    os.environ.setdefault("JARVIS_PANELS_KEY", "demo-local-key")
    os.environ.setdefault("TAMAPI_HEARTBEAT",
                          chatter_beat_path(None).as_posix())

    # Стенд сам держит heartbeat свежим. Иначе через 90 с после запуска экран
    # честно скажет «немає зв'язку» — и на скриншотах вместо разбираемой
    # раскладки окажется авария демо-стенда.
    _keep_heartbeat(os.environ["TAMAPI_HEARTBEAT"])

    import uvicorn
    uvicorn.run(build_app(), host=a.host, port=a.port, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
