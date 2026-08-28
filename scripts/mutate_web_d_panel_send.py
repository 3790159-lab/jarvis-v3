# -*- coding: utf-8 -*-
"""Мутационный гейт арки web-d «отправка из панели» (спека §8).

Мишени — РЕШЕНИЯ спеки §2.1–§2.8, а не слова кода: ядро доставки не знает
транспорта и не сравнивает имя канала с литералом, доставщик берётся из
реестра, `can_send_now` — ОДНА функция на двух вызывателей, перехват
открывается ДО отправки и ПОВЫШАЕТ силу паузы (снуз его больше не съедает),
одно задание даёт одну строку в ленте и один `sent`, страница диалога
fail-closed по адресу, а токен формы случаен.

Сторожа писал ДРУГОЙ заход, от текста спеки. Гейт проверяет не код, а ИХ:
ломаем решение и требуем красного от НАЗВАННОГО сторожа (`файл::имя`), а не от
файла — прогон по файлу зеленел бы за счёт соседа.

Харнесс тот же, что у гейтов DEV-48, web-a, web-b и web-c, включая обе
поправки: откат по СОХРАНЁННЫМ БАЙТАМ и поиск фрагмента в обеих формах концов
строк.

Прогон: python scripts/mutate_web_d_panel_send.py  (в worktree, дерево чистое)
"""
from __future__ import annotations

import os
import subprocess
import sys
from itertools import count
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from gate_guard import refuse_if_live_tree   # DEV-31: гейт мутирует только worktree

ROOT = Path(__file__).resolve().parents[1]

_MTIME_BASE = 2_000_000_000
_mtime_seq = count()

OUT = "chatter/core/outgoing.py"
CREF = "chatter/core/channel_ref.py"
DB = "chatter/storage/db.py"
PANEL = "app/routers/tamapi_dashboard.py"

G = "tests/chatter/test_web_d_panel_send.py"
GF = "tests/chatter/test_web_d_dialog_foreign_persona.py"
GG = "tests/chatter/test_web_d_gate_gaps.py"
GQ = "tests/chatter/test_outgoing_queue.py"

# ── ЧТО ПЕРВЫЙ ПРОГОН ГЕЙТА ИЗМЕНИЛ, И ПОЧЕМУ ЭТО ЗАПИСАНО ЗДЕСЬ ────────────
#
# Первый прогон дал 14 слепых из 24. Разбор поимённо развёл их на три кучи, и
# кучи эти НЕ равноценны — гейт, не различивший их, соврал бы трижды подряд:
#
# 1. МОЯ ОШИБКА НАВОДКИ (7 шт.). Мишень ломала одно решение, а красного я ждал
#    от сторожа другого. Две страничные были прямо перепутаны местами: снятие
#    сверки слуга видит сторож «чужая персона в ТОЙ ЖЕ базе», а снятие
#    `has_contact` — сторож fail-closed по адресу, и наоборот ни один.
#
# 2. НАСТОЯЩАЯ ДЫРА (4 шт.). Решение принято кодом, названо в его комментарии
#    и не проверено НИКЕМ. Под них написаны сторожа
#    `tests/chatter/test_web_d_gate_gaps.py` — отдельным файлом, потому что
#    они написаны ПОСЛЕ кода, а §5 писались ДО, и через месяц это различие
#    важнее удобства.
#
# 3. НЕОТЛИЧИМО БЕЗ ИНЪЕКЦИИ СБОЯ (3 шт.), сняты вместе с причиной:
#    * ПОРЯДОК `mark_outgoing_sent` относительно записи в историю. Перестановка
#      наблюдаема ТОЛЬКО если между ними что-то падает; окно сужается порядком,
#      а не проверкой, и мишень без инъекции сбоя красит воздух.
#    * `can_send_now` перестала отказывать на неразбираемом id: следом стоит
#      ВТОРАЯ задвижка (`has_contact`), и задание всё равно получает отказ.
#      Мишень, которую переживает вторая задвижка, меряет не сторожа, а
#      наличие резерва.
#    * переходный шов `channel_ref._transitional` угадывает канал вместо
#      отказа: перекрыт сверкой слуга на странице по той же причине.
#    Обе «резервные» — не слепота сторожей, а глубина обороны, и записаны
#    здесь именно так, чтобы завтра их не «починили» ослаблением резерва.

MUTATIONS = [
    # ── §2.1 ядро канало-НЕзависимо ─────────────────────────────────────
    # Признак провала, объявленный §1 спеки веба: код, который знает про один
    # канал и не знает про «канал вообще».
    ("ядро доставки притащило телетон", OUT,
     [(b"import inspect\nimport logging",
       b"import inspect\nimport logging\nimport telethon  # noqa: F401")],
     G + "::test_yadro_dostavki_ne_znaet_pro_telethon"),

    ("ядро сравнивает имя канала с литералом", OUT,
     [(b"    deliverer = deliverer_for(channel)\n"
       b"    if deliverer is None:",
       b"    deliverer = deliverer_for(channel)\n"
       b'    if channel == "telegram":\n        return None\n'
       b"    if deliverer is None:")],
     G + "::test_yadro_ne_sravnivaet_nazvanie_kanala_s_literalom"),

    # ── §2.2 реестр доставщиков: одна регистрация на канал ───────────────
    ("второй доставщик молча побеждает первого", OUT,
     [(b"    if existing is not None and existing is not deliverer:",
       b"    if False:")],
     GG + "::test_povtornaya_registraciya_dostavshika_GROMKAYA"),

    # Мишень бьёт по `deliverer_for`, а НЕ по `registered_channels`: сторож
    # спрашивает «есть ли доставщик у канала, которого быть не должно», и
    # подделка списка каналов мимо него проезжает — первый прогон это и показал.
    ("незарегистрированный канал получил доставщика-пустышку", OUT,
     [(b"    return _DELIVERERS.get(channel)",
       b"    return _DELIVERERS.get(channel) or (lambda *a, **k: None)")],
     G + "::test_spisok_zaregistrirovannyh_kanalov_LITERALEN_v_obe_storony"),

    ("канал без доставщика больше не отказывает словами", OUT,
     [(b"    deliverer = deliverer_for(channel)\n"
       b"    if deliverer is None:\n"
       b"        return Refusal(",
       b"    deliverer = deliverer_for(channel)\n"
       b"    if False:\n"
       b"        return Refusal(")],
     G + "::test_kanal_bez_dostavshika_otkazyvaet_SLOVAMI_i_ne_visit_pending"),

    # ── §2.6 can_send_now — ОДНА функция на ДВУХ вызывателей ─────────────
    # Две реализации одного вопроса разъедутся, и меньшая погасит большую
    # молча: панель покажет поле ввода там, где доставка откажет.
    ("ядро перестало спрашивать ту же can_send_now", OUT,
     [(b"        refusal = can_send_now(contact_id, ctx)\n"
       b"        if refusal is not None:\n"
       b"            raise refusal",
       b"        refusal = None")],
     G + "::test_podmena_can_send_now_menyaet_OBA_otveta"),

    ("канальную половину ответа больше не спрашивают у доставщика", OUT,
     [(b"    if ctx is not None:\n"
       b'        ask = getattr(deliverer, "can_send_now", None)',
       b"    if False:\n"
       b'        ask = getattr(deliverer, "can_send_now", None)')],
     GG + "::test_kanalnaya_polovina_otveta_zhivyot_u_dostavshika"),

    # ── §2.3 порядок: СНАЧАЛА перехват, ПОТОМ отправка ──────────────────
    ("контакта нет в базе, но задание всё равно едет", OUT,
     [(b"        if not store.has_contact(contact_id):",
       b"        if False:")],
     GG + "::test_zadanie_v_dialog_kotorogo_net_v_baze_OTKAZYVAET"),

    ("ответ доставщика не проверяется на SentRef — неотправленное в истории", OUT,
     [(b"        if not isinstance(sent, SentRef):", b"        if False:")],
     GG + "::test_otvet_dostavshika_ne_SentRef_eto_POVTOR_a_ne_uspeh"),

    # ── §2.4 у паузы появилась СИЛА: снуз не съедает перехват ────────────
    ("повышение силы паузы снято — снуз снова съедает перехват", DB,
     [(b'        where = ("contact_id=? AND paused=0" if not escalate else\n'
       b'                 "contact_id=? AND (paused=0 OR pause_source IS NULL "\n'
       b"                 \"OR pause_source<>'human_takeover')\")",
       b'        where = "contact_id=? AND paused=0"')],
     G + "::test_snuz_NE_SEDAET_perehvat_bot_ne_govorit_poverh_cheloveka"),

    ("чужой срок остаётся на диалоге, который ведёт человек", DB,
     [(b'                "UPDATE contacts SET paused=1, pause_source=\'human_takeover\', "\n'
       b'                "pause_msg_id=?, pause_detail=?, pause_until=NULL, paused_at=? "',
       b'                "UPDATE contacts SET paused=1, pause_source=\'human_takeover\', "\n'
       b'                "pause_msg_id=?, pause_detail=?, paused_at=? "')],
     G + "::test_snuz_NE_SEDAET_perehvat_bot_ne_govorit_poverh_cheloveka"),

    ("повышает ВСЯКОЕ открытие — чужая пауза становится перехватом", OUT,
     [(b"    if escalate is None:\n        escalate = msg_id is None",
       b"    if escalate is None:\n        escalate = True")],
     GQ + "::test_chuzhaya_pauza_ne_perepisyvaetsya_perehvatom"),

    # ── §2.5 страница диалога fail-closed ПО АДРЕСУ ──────────────────────
    # Пустая лента здесь хуже отказа вдвойне: она врёт молча и приглашает
    # написать в чужой диалог из формы.
    ("страница диалога перестала сверять слуг с инстансом", PANEL,
     [(b"    if slug != _slug():\n        raise _closed()",
       b"    if False:\n        raise _closed()")],
     GF + "::test_chuzhaya_persona_v_TOI_ZHE_baze_ne_pokazyvaetsya"),

    ("контакта нет в базе — показываем пустую ленту вместо отказа", PANEL,
     [(b"        if not store.has_contact(contact_id):\n            raise _closed()",
       b"        if False:\n            raise _closed()")],
     G + "::test_stranica_dialoga_FAIL_CLOSED_po_adresu"),

    ("панель отвечает про канал СВОИМ ответом, а не той же функцией", PANEL,
     [(b"    refusal = delivery.can_send_now(contact_id)",
       b"    refusal = None")],
     G + "::test_podmena_can_send_now_menyaet_OBA_otveta"),

    ("Store страницы диалога открыт без with — хэндл течёт", PANEL,
     [(b"    with Store(_db_path()) as store:\n"
       b"        if not store.has_contact(contact_id):",
       b"    if True:\n        store = Store(_db_path())\n"
       b"        if not store.has_contact(contact_id):")],
     G + "::test_ni_odnogo_Store_vne_with"),

    # ── §2.7 токен формы СЛУЧАЕН ────────────────────────────────────────
    ("токен формы стал предсказуемым", PANEL,
     [(b"    import secrets\n    return secrets.token_urlsafe(_FORM_TOKEN_BYTES)",
       b'    import time\n    return "t%d" % int(time.time())')],
     G + "::test_generator_tokenov_SLUCHAEN_a_ne_schetchik_i_ne_vremya"),

    # ── §2.1 разбор головы contact_id — ЧУЖОЙ, а не свой ────────────────
    ("доставка завела СВОЙ разбор головы contact_id", OUT,
     [(b"        channel = channel_ref.channel_of(contact_id)\n"
       b"        deliverer = deliverer_for(channel)",
       b'        channel = contact_id.split(":")[0]\n'
       b"        deliverer = deliverer_for(channel)")],
     G + "::test_v_dostavke_net_svoego_razbora_golovy_contact_id"),

    # ── ВСТРЕЧНЫЕ половины ──────────────────────────────────────────────
    # Без них «отказывать всегда» проходит гейт целиком: каждая мишень выше
    # требует красного, а красное даёт и сломанный наглухо код.
    ("ВСТРЕЧНАЯ: can_send_now отказывает ВСЕГДА", OUT,
     [(b"    deliverer = deliverer_for(channel)\n"
       b"    if deliverer is None:\n"
       b"        return Refusal(\n"
       b'            "no_transport",',
       b"    deliverer = deliverer_for(channel)\n"
       b"    if True:\n"
       b"        return Refusal(\n"
       b'            "no_transport",')],
     G + "::test_tumblery_ne_meshayut_cheloveku_na_LYUBOM_kanale_i_glushat_bota"),

    ("ВСТРЕЧНАЯ: перехват не открывается вовсе", OUT,
     [(b"    started = store.begin_takeover(contact_id, msg_id=msg_id, detail=detail,\n"
       b"                                   now=now, escalate=escalate)",
       b"    started = False")],
     G + "::test_snuz_NE_SEDAET_perehvat_bot_ne_govorit_poverh_cheloveka"),
]


def write_mutant(path: Path, text) -> None:
    """Записать мутанта с УНИКАЛЬНЫМ mtime. Всегда `write_bytes`."""
    blob = text if isinstance(text, (bytes, bytearray)) else str(text).encode("utf-8")
    path.write_bytes(blob)
    stamp = _MTIME_BASE + next(_mtime_seq)
    os.utime(path, (stamp, stamp))


def run(test: str) -> tuple[bool, str]:
    """(поймана ли мутация, чем ответил pytest).

    КРАСНОЕ — РОВНО `rc 1` плюс `failed`: сломавшая СБОР мутация отвечает
    rc 2/4/5, и критерий «не 0 значит покраснел» принял бы её за пойманную.
    """
    env = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
    p = subprocess.run(
        [sys.executable, "-m", "pytest", test, "-q", "--no-header",
         "-p", "no:cacheprovider"],
        cwd=ROOT, capture_output=True, text=True,
        encoding="utf-8", errors="replace", env=env)
    out = (p.stdout or "") + (p.stderr or "")
    tail = out.strip().splitlines()[-1] if out.strip() else "(пусто)"
    return (p.returncode == 1 and "failed" in out,
            "rc=%d | %s" % (p.returncode, tail[:140]))


def revert(rel: str, original: bytes) -> None:
    path = ROOT / rel
    path.write_bytes(original)
    stamp = _MTIME_BASE + next(_mtime_seq)
    os.utime(path, (stamp, stamp))


def assert_clean() -> None:
    out = subprocess.run(["git", "status", "--porcelain", "--untracked-files=no"],
                         cwd=ROOT, capture_output=True, text=True).stdout.strip()
    if out:
        raise SystemExit(
            "ОТКАЗ: рабочее дерево грязное — откат мутаций сотрёт эти правки.\n" + out)


def main() -> int:
    refuse_if_live_tree(ROOT)
    assert_clean()
    blind = []
    for name, rel, edits, test in MUTATIONS:
        path = ROOT / rel
        original = path.read_bytes()
        mutated = original
        edits = [(o, n) if o in mutated
                 else (o.replace(b"\n", b"\r\n"), n.replace(b"\n", b"\r\n"))
                 for o, n in edits]
        missing = [old for old, _new in edits if old not in mutated]
        if missing:
            print("[!] МУТАЦИЯ НЕ ПРИМЕНИЛАСЬ: %s — фрагмент не найден" % name)
            blind.append((name, "фрагмент не найден"))
            continue
        for old, new in edits:
            mutated = mutated.replace(old, new, 1)
        if mutated == original:
            print("[!] МУТАЦИЯ НИЧЕГО НЕ ИЗМЕНИЛА: %s" % name)
            blind.append((name, "файл не изменился"))
            continue
        write_mutant(path, mutated)
        try:
            caught, why = run(test)
        finally:
            revert(rel, original)
        if path.read_bytes() != original:
            raise SystemExit("ОТКАТ НЕ ВЕРНУЛ ФАЙЛ ПОБАЙТОВО: %s" % rel)
        if not caught:
            print("[СЛЕП] %s\n        %s\n        %s" % (name, test, why))
            blind.append((name, test))
        else:
            print("[ok]   %s -> сторож покраснел" % name)
    print()
    if blind:
        print("СЛЕПЫХ СТОРОЖЕЙ: %d из %d" % (len(blind), len(MUTATIONS)))
        return 1
    print("Все %d мутаций пойманы, файлы возвращены побайтово." % len(MUTATIONS))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
