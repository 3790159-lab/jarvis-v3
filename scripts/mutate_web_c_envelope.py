# -*- coding: utf-8 -*-
"""Мутационный гейт арки web-c «конверт входящего и личность с каналом» (§11).

Мишени — РЕШЕНИЯ спеки §2–§5, а не слова кода: конверт проверяет форму при
сборке и не носит запрещённых полей, реестр каналов ЛИТЕРАЛЕН и в обе стороны,
собеседник — середина, а не голова, послабление старых кнопок живёт отдельным
именем и не течёт в строгий разбор, перепись живых баз идёт ОДНОЙ транзакцией
со снятым бэкапом и сверкой числа строк, а ключ выставления переезжает в TEXT
с отказом на неприводимом значении.

Сторожа писал ДРУГОЙ заход, от текста спеки. Гейт проверяет не код, а ИХ:
ломаем решение и требуем красного от НАЗВАННОГО сторожа (`файл::имя`), а не от
файла — прогон по файлу зеленел бы за счёт соседа.

Харнесс тот же, что у гейтов DEV-48, web-a и web-b, включая обе поправки:
откат по СОХРАНЁННЫМ БАЙТАМ и поиск фрагмента в обеих формах концов строк.

Прогон: python scripts/mutate_web_c_envelope.py  (в worktree, дерево чистое)
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

# Уникальный mtime на каждую запись: Python признаёт кэш байткода актуальным по
# паре (mtime в целых секундах, размер), и две мутации одного размера в одну
# секунду неотличимы — вторая исполнилась бы байткодом первой (DEV-26).
_MTIME_BASE = 2_000_000_000
_mtime_seq = count()

INB = "chatter/core/inbound.py"
REF = "chatter/core/contact_ref.py"
DB = "chatter/storage/db.py"

GE = "tests/chatter/test_web_c_inbound_envelope.py"
GF = "tests/chatter/test_web_c_contact_format.py"
GL = "tests/chatter/test_web_c_legacy_button.py"
GM = "tests/chatter/test_web_c_migration.py"
GO = "tests/chatter/test_web_c_origin_msg_id.py"
GG = "tests/chatter/test_web_c_gate_gaps.py"

# ── ЧТО ПЕРВЫЙ ПРОГОН ГЕЙТА ИЗМЕНИЛ ────────────────────────────────────────
#
# Первый прогон: 17 из 28. Одиннадцать слепых разошлись на три кучи, и кучи
# неравноценны — гейт, их не различивший, соврал бы трижды:
#
# 1. МОЯ ОШИБКА НАВОДКИ (7). Мишень ломала одно решение, красного я ждал от
#    сторожа другого.
#
# 2. НАСТОЯЩАЯ ДЫРА (4) -> tests/chatter/test_web_c_gate_gaps.py. Самая дорогая
#    из них — первая: сторож 27 сверяет `MIGRATED_TABLES` со СХЕМОЙ и с КОДОМ,
#    и сверяет в обе стороны, но `MIGRATED_TABLES` — это литерал В ФАЙЛЕ
#    СТОРОЖА, а мигрирует `db._CONTACT_ID_TABLES`, и между ними не было НИ
#    ОДНОЙ проверки. Боевой список мог усохнуть молча, а сторож остался бы
#    зелёным. Тот же дефект, ради которого написана арка, только этажом выше.
#    Ещё три: чужая голова в трёхсегментной форме (её не судил никто), индексы
#    после НАСТОЯЩЕЙ перестройки (сторож 28 брал свежую базу, где перестройки
#    не происходит вовсе) и два предохранителя миграции, срабатывающие только
#    при аварии, — их пришлось вносить подменой.
#
# 3. НЕОТЛИЧИМО, И ЭТО ЗАМЕРЕНО (4), сняты вместе с причиной:
#    * `if not isinstance(self.text, str)` — сторож 2 НАМЕРЕННО принимает оба
#      честных исхода: отказ при сборке ЛИБО TypeError на первом `json.dumps`.
#      Мутация оставляет второй. Это не слепота, а объявленная развилка;
#    * проверка типа `display_name` — решение §2.1 звучит «None != ''», и его
#      сторож 5 держит. Тип — ремень поверх решения, отдельного сторожа спека
#      под него не просит;
#    * снятый `ROLLBACK` — соединение закрывается в `except` конструктора, и
#      SQLite откатывает незакоммиченную транзакцию сам. База остаётся той же
#      побайтово, то есть наблюдаемой разницы нет;
#    * `_origin_key` перестал приводить `int` — ЗАМЕРЕНО: TEXT-аффинность
#      приводит и ПРИ ЗАПИСИ (`typeof` даёт 'text'), и ПРИ СРАВНЕНИИ (поиск по
#      int 7 находит строку '7'). Премиса §5.3 («целое 7 и строка '7' не равны
#      никогда») верна для значений, но не для колонки с TEXT-аффинностью.
#      Настоящая работа `_origin_key` — не приведение, а ОТКАЗ на REAL/BLOB:
#      float 7.5 аффинность молча положила бы как '7.5'. Эта половина мишенью
#      покрыта и краснеет.

MUTATIONS = [
    # ── §2.2 конверт проверяет форму ПРИ СБОРКЕ ──────────────────────────
    # Без этого неразбираемый id рождается ВНУТРИ нашего кода, а краснеет через
    # слой, у чужого разбора: в трассировке будет невиновный.
    ("конверт не проверяет три сегмента при сборке", INB,
     [(b"        build(channel=self.channel, external_id=self.external_id,\n"
       b"              persona=self.persona)\n",
       b"        pass\n")],
     GE + "::test_guard03_pustoy_segment_ili_dvoetochie_vnutri_eto_isklyuchenie"),

    # ── §2.2 п.3 состав полей ЛИТЕРАЛЕН ──────────────────────────────────
    # Поле, которое все кладут и никто не читает, хранит неверное значение
    # ровно до дня, когда его прочтут.
    ("у конверта появилось запрещённое поле msg_id", INB,
     [(b"    attachments_dropped: int = 0    #",
       b"    attachments_dropped: int = 0\n"
       b"    msg_id: str | None = None    #")],
     GE + "::test_guard06_zapreshchyonnogo_polya_v_konverte_NET"),

    # ── §3.1 реестр каналов ЛИТЕРАЛЕН, и обе его половины ────────────────
    ("канал вне реестра принят сборкой", REF,
     [(b"    if channel not in KNOWN_CHANNELS:", b"    if False:")],
     GE + "::test_guard04_kanal_vne_literalnogo_reestra_eto_otkaz"),

    ("реестр каналов УСОХ — веб из него пропал", REF,
     [(b"KNOWN_CHANNELS: tuple[str, ...] = (TELEGRAM_CHANNEL, WEB_CHANNEL)",
       b"KNOWN_CHANNELS: tuple[str, ...] = (TELEGRAM_CHANNEL,)")],
     GE + "::test_guard04_VSTRECHNAYA_polovina_reestr_ne_usoh"),

    ("разбор принимает чужую голову — опознание «по числу» вернулось", REF,
     [(b"    if parts[0] not in KNOWN_CHANNELS:", b"    if False:")],
     GG + "::test_chuzhaya_golova_v_tryohsegmentnoy_forme_OTVERGAETSYA"),

    # ── §3.2 форма ТРЁХСЕГМЕНТНА; двухсегментная после арки — отказ ───────
    ("двухсегментная форма снова принимается строгим разбором", REF,
     [(b"_SEGMENTS = 3", b"_SEGMENTS = 2")],
     GF + "::test_guard08_peer_of_otdayot_SOBESEDNIKA_a_ne_golovu_kanala"),

    ("сборка пропускает двоеточие внутрь сегмента", REF,
     [(b"        if not isinstance(value, str) or not value or SEPARATOR in value:",
       b"        if not isinstance(value, str):")],
     GE + "::test_guard03_pustoy_segment_ili_dvoetochie_vnutri_eto_isklyuchenie"),

    # ── §3.3 собеседник — СЕРЕДИНА, а не голова ──────────────────────────
    # Тот самый дефект, ради которого писалась волна 1: «telegram» вместо
    # собеседника доехало бы до клиента.
    ("peer_of отдаёт ГОЛОВУ канала вместо собеседника", REF,
     [(b"\n    return _parts(contact_id)[1]\n\n\nUNKNOWN_LABEL",
       b"\n    return _parts(contact_id)[0]\n\n\nUNKNOWN_LABEL")],
     GF + "::test_guard08_peer_of_otdayot_SOBESEDNIKA_a_ne_golovu_kanala"),

    ("slug_of берёт голову вместо персоны", REF,
     [(b"    return _parts(contact_id)[2]", b"    return _parts(contact_id)[0]")],
     GF + "::test_guard09_slug_of_i_channel_of_na_tryohsegmentnoy_forme"),

    ("нечисловая голова стала telegram-peer'ом", REF,
     [(b"    peer = peer_of(contact_id)\n    try:\n        return int(peer)\n"
       b"    except ValueError:",
       b"    peer = peer_of(contact_id)\n    try:\n        return int(peer)\n"
       b"    except ValueError:\n        return 0\n    if False:")],
     GF + "::test_guard12_nechislovaya_golova_kanala_ne_stanovitsya_peer_om"),

    # ── §3.4 послабление старых кнопок УЗКОЕ и живёт ОТДЕЛЬНО ────────────
    ("апгрейд старой кнопки стал широким — гадает по чему угодно", REF,
     [(b"    if len(parts) != 2 or not all(parts):\n        return None",
       b"    if False:\n        return None")],
     GL + "::test_guard18_vsyo_prochee_eto_otkaz_a_ne_dogadka"),

    ("апгрейд принимает нечисловую голову", REF,
     [(b'    if not head.lstrip("-").isdigit():\n        return None',
       b"    if False:\n        return None")],
     GL + "::test_guard18_vsyo_prochee_eto_otkaz_a_ne_dogadka"),

    ("послабление ПРОТЕКЛО в строгий разбор", REF,
     [(b"    parts = contact_id.split(SEPARATOR)\n"
       b"    if len(parts) != _SEGMENTS or not all(parts):",
       b"    _up = upgrade_legacy_callback_contact(contact_id)\n"
       b"    if _up is not None:\n"
       b"        contact_id = _up\n"
       b"    parts = contact_id.split(SEPARATOR)\n"
       b"    if len(parts) != _SEGMENTS or not all(parts):")],
     GL + "::test_guard20_poslablenie_NE_proteklo_v_strogiy_razbor"),

    # ── §4.1 перепись знает ВСЕ места, где живёт contact_id ──────────────
    ("список таблиц переписи усох на одну", DB,
     [(b'    "funnel_transitions", "payments", "quotes", "invoices", "outgoing_queue",',
       b'    "funnel_transitions", "payments", "quotes", "invoices",')],
     GG + "::test_boevoy_spisok_tablits_soglasen_s_literalom_storozha"),

    ("ключи runtime_flags выпали из переписи — карточки осиротеют молча", DB,
     [(b'    "esc_active:", "profile_miss:", "profile_miss_alerted:",',
       b'    "esc_active:", "profile_miss:",')],
     GG + "::test_boevoy_spisok_prefiksov_soglasen_s_literalom_storozha"),

    ("перепись домигрировывает вокруг неизвестной формы вместо СТОПа", DB,
     [(b"                if legacy_ids or legacy_keys:\n"
       b"                    self._assert_contact_ids_migratable(path, legacy_ids,\n"
       b"                                                        legacy_keys)",
       b"                if False:\n"
       b"                    self._assert_contact_ids_migratable(path, legacy_ids,\n"
       b"                                                        legacy_keys)")],
     GM + "::test_guard25_nechislovaya_golova_OSTANAVLIVAET_migratsiyu"),

    ("потеря строк при переписи перестала быть отказом", DB,
     [(b"            if lost:", b"            if False:")],
     GG + "::test_poterya_strok_pri_perepisi_eto_OTKAZ"),

    ("перепись идёт БЕЗ откатной копии", DB,
     [(b"                    if pre_existing:\n"
       b'                        self._backup(path, tag="web-c")',
       b"                    if False:\n"
       b'                        self._backup(path, tag="web-c")')],
     GM + "::test_guard24_pri_realnoy_migratsii_bak_vsyo_taki_snimaetsya"),

    ("перепись перестала спрашивать о результате", DB,
     [(b"                    self._assert_identity_migrated()",
       b"                    pass")],
     GG + "::test_perepis_OBYAZANA_sprosit_o_rezultate"),

    # ── §5 ключ выставления: TEXT, граница приведения, окно ──────────────
    ("граница Store пропустила REAL — второй ключ на то же сообщение", DB,
     [(b"    if isinstance(value, int):\n        return str(value)\n    raise ValueError(",
       b"    if isinstance(value, int):\n        return str(value)\n"
       b"    if isinstance(value, float):\n        return str(value)\n"
       b"    raise ValueError(")],
     GO + "::test_guard30_granitsa_Store_privodit_int_no_otkazyvaet_na_ostalnom"),

    ("перестройка забыла пересоздать индекс по контакту", DB,
     [(b"        self._conn.execute(\n"
       b'            "CREATE INDEX IF NOT EXISTS idx_%s_contact ON %s(contact_id)"\n'
       b"            % (table, table))",
       b"        pass")],
     GG + "::test_indeksy_peresozdany_imenno_PERESTROYKOY"),

    ("неприводимое значение проезжает CAST-ом", DB,
     [(b'                   if r["t"] not in ("integer", "text")',
       b'                   if r["t"] not in ("integer", "text", "real", "blob")')],
     GO + "::test_guard33_okno_zakryto_chestno_neprivodimye_stroki_eto_otkaz"),

    ("список перестраиваемых таблиц усох — колонка осталась INTEGER", DB,
     [(b'_ORIGIN_KEY_TABLES: tuple[str, ...] = ("quotes", "invoices")',
       b'_ORIGIN_KEY_TABLES: tuple[str, ...] = ("invoices",)')],
     GG + "::test_boevoy_spisok_perestraivaemyh_tablits_ne_ussoh"),

    # ── ВСТРЕЧНЫЕ половины: сторож обязан ловить и ПЕРЕусердие ───────────
    # Без них «отказывать всегда» проходит гейт целиком: каждая мишень выше
    # требует красного, а красное даёт и сломанный наглухо код.
    ("ВСТРЕЧНАЯ: сборка отказывает ВСЕГДА, даже верной форме", REF,
     [(b'    peer_ref = f"{external_id}:{persona}"',
       b'    raise ContactRefError("MUTANT")\n'
       b'    peer_ref = f"{external_id}:{persona}"')],
     GE + "::test_guard01_contact_id_sobiraetsya_iz_tryoh_chastey"),

    ("ВСТРЕЧНАЯ: апгрейд старых кнопок УБИТ — живые тапы онемели", REF,
     [(b'    return f"{TELEGRAM_CHANNEL}:{s}"', b"    return None")],
     GL + "::test_guard17_staraya_forma_podnimaetsya_do_tryohsegmentnoy"),
]


def write_mutant(path: Path, text) -> None:
    """Записать мутанта с УНИКАЛЬНЫМ mtime. Всегда `write_bytes`.

    Принимает И байты, И строку: мета-сторож на гейты зовёт этот метод строкой,
    и суженная до байтов сигнатура ломает ЗАМЕР гейта, а не сам гейт.
    """
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
