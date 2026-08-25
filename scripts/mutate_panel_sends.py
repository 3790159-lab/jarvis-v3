"""Мутационный гейт арки «панель учится отправлять»: ломаем решения, требуем КРАСНОГО.

Гейт пишет ИНТЕГРАТОР, а не автор кода и не автор сторожей: обе половины арки
писались вслепую друг от друга, и гейт здесь проверяет не чей-то текст, а то,
что сторож ловит РЕШЕНИЕ, а не совпадение.

Мишени — ровно те ветки, ради которых спека писалась: идемпотентность по
токену, порядок «перехват ДО отправки», регистрация своего исходящего,
терминальность отказа, недостижимость границ, молчаливый отказ, гибель цикла.

Правки пишутся БАЙТАМИ: `write_text` перевёл бы LF-файлы в CRLF целиком и
утопил мутацию в правке всего файла ([[jarvis-write-text-converts-newlines]]).
`write_mutant(path, text)` принимает `str` по контракту мета-сторожа DEV-26 —
гейт, которого мета-сторож не смог позвать, ВЫХОДИТ из-под проверки вместо
того, чтобы её провалить.

Прогон: python scripts/mutate_panel_sends.py  (в worktree, дерево чистое)
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

DB = "chatter/storage/db.py"
RUN = "chatter/telethon_run.py"
CORE = "chatter/run.py"
TG = "chatter/transport/telethon_tg.py"
TQ = "tests/chatter/test_outgoing_queue.py"

MUTATIONS = [
    # ── очередь: идемпотентность и порядок ───────────────────────────────
    # 🔴 МУТАЦИЯ ДВОЙНАЯ, И ЭТО НЕ ПРИДИРКА. Идемпотентность держат ДВЕ вещи:
    # проверка перед вставкой и `UNIQUE` в схеме. Снятая поодиночке, каждая
    # ничего не меняет — вторая подхватывает, — и одиночная мутация проходит
    # зелёной, ничего не доказав. Чтобы сторож доказал ИНВАРИАНТ, а не одну из
    # его подпорок, снимаются обе сразу.
    ("повтор по токену рождает ВТОРУЮ строку (двойное нажатие = два сообщения)", DB,
     [(b'            if row is not None:\n                return int(row["id"]), False',
       b'            if False:\n                return int(row["id"]), False'),
      (b'    token        TEXT NOT NULL UNIQUE,',
       b'    token        TEXT NOT NULL,')],
     f"{TQ}::test_povtor_po_tomu_zhe_tokenu_ne_rozhdaet_vtoruyu_stroku"),

    ("пустой текст доезжает до очереди", DB,
     [(b'        if not clean:\n            raise ValueError(', b'        if False:\n            raise ValueError(')],
     f"{TQ}::test_pustoi_tekst_otvergnut_DO_zapisi"),

    ("пустой токен доезжает до очереди (защита от двойного нажатия снята)", DB,
     [(b'        tok = (token or "").strip()\n        if not tok:',
       b'        tok = (token or "").strip()\n        if False:')],
     f"{TQ}::test_pustoi_token_otvergnut_DO_zapisi"),

    ("очередь отдаётся задом наперёд — владелец прочитает свой диалог наоборот", DB,
     [(b'"ORDER BY created_ts, id LIMIT ?"', b'"ORDER BY created_ts DESC, id LIMIT ?"')],
     f"{TQ}::test_pending_otdayot_samye_starye_pervymi"),

    ("отказ перестал быть терминальным — повтор каждые 5 секунд до конца времён", DB,
     [(b'        status = "refused" if terminal else "pending"',
       b'        status = "pending"')],
     f"{TQ}::test_otkaz_terminalen_i_povtorov_ne_budet"),

    ("попытки не считаются — «сколько раз не вышло» становится неизвестным", DB,
     [(b'"UPDATE outgoing_queue SET status=?, attempts=attempts+1, "',
       b'"UPDATE outgoing_queue SET status=?, attempts=attempts, "')],
     f"{TQ}::test_neterminalnyi_sboi_ostavlyaet_zadanie_v_ocheredi_i_schitaet_popytki"),

    # ── перехват: одна точка, один порядок ───────────────────────────────
    ("эпизод открыт, а событие в журнал не пишется — разбор «кто написал» слепнет", RUN,
     [(b'    started = store.begin_takeover(contact_id, msg_id=msg_id, detail=detail, now=now)\n    if started:',
       b'    started = store.begin_takeover(contact_id, msg_id=msg_id, detail=detail, now=now)\n    if False:')],
     f"{TQ}::test_KARTOCHKI_NET_a_SOBYTIE_takeover_EST"),

    ("ПОРЯДОК СЛОМАН: сначала отправка, потом перехват — окно, в котором бот не знает о человеке", RUN,
     [(b'        started = open_human_takeover(\n            store, contact_id, msg_id=None, detail=text[:200], now=now)',
       b'        started = True')],
     f"{TQ}::test_PEREHVAT_OTKRYVAETSYA_DO_OTPRAVKI"),

    ("своё исходящее не регистрируется — раннер поднимет карточку на нажатие владельца", TG,
     [(b'            self._sent.add(sent.id)', b'            pass')],
     f"{TQ}::test_sobstvennaya_otpravka_ne_chitaetsya_kak_chuzhoe_ishodyashchee"),

    # ── границы отказа: каждая недостижимая = вечный повтор вместо слов ──
    ("выключенный клиент больше не отказ — задание ждёт раннера, который его не возьмёт", RUN,
     [(b'        bundle = runner.personas.get(slug)\n        if bundle is None:',
       b'        bundle = runner.personas.get(slug)\n        if False:')],
     f"{TQ}::test_vyklyuchennyi_klient_otkazyvaet_SLOVAMI"),

    ("неподключённый канал больше не отказ", RUN,
     [(b'        if bundle.cfg.settings.telegram is None:', b'        if False:')],
     f"{TQ}::test_nepodklyuchennyi_kanal_otkazyvaet_SLOVAMI"),

    ("незнакомый диалог больше не отказ — панель и раннер смотрят в разные базы молча", RUN,
     [(b'        if not store.has_contact(contact_id):', b'        if False:')],
     f"{TQ}::test_neznakomyi_dialog_otkazyvaet_SLOVAMI"),

    # Мишень ASCII-only намеренно: байтовый литерал кириллицу не принимает, а
    # решение здесь всё равно не в тексте лога, а в ЗАПИСИ СОСТОЯНИЯ строки.
    ("неожиданная ошибка не ложится в строку — задание застревает без объяснения", RUN,
     [(b'        store.mark_outgoing_failed(\n            row_id, error="%s: %s" % (type(exc).__name__, exc), now=now, terminal=False)\n        return "retry"',
       b'        return "retry"')],
     f"{TQ}::test_MOLCHALIVYI_otkaz_zapreshchen_dazhe_na_neozhidannoi_oshibke"),

    # ── бот не говорит поверх человека ───────────────────────────────────
    ("бот договаривает поверх человека — тихий отказ, который дороже шумного", CORE,
     [(b'            if _human_holds_now(deps, contact_id):', b'            if False:')],
     f"{TQ}::test_bot_USTUPAET_cheloveku_vmeshavshemusya_POSREDI_hoda"),

    # ── цикл ─────────────────────────────────────────────────────────────
    ("одна взорвавшаяся строка забирает с собой всю очередь", RUN,
     [(b'            try:\n                await deliver_outgoing(runner, row, now=time.time())\n            except Exception:',
       b'            if True:\n                await deliver_outgoing(runner, row, now=time.time())\n            except_disabled = lambda: None\n            if False:')],
     f"{TQ}::test_sboi_odnoi_stroki_ne_ubivaet_cikl_dostavki"),
]


def write_mutant(path: Path, text) -> None:
    """Контракт мета-сторожа DEV-26: (путь, ТЕКСТ). Принимает и `str`, и
    `bytes`; пишет ВСЕГДА байтами — иначе LF-файл уехал бы в CRLF целиком."""
    blob = text if isinstance(text, (bytes, bytearray)) else str(text).encode("utf-8")
    path.write_bytes(blob)
    stamp = _MTIME_BASE + next(_mtime_seq)
    os.utime(path, (stamp, stamp))


def run(test: str) -> tuple[bool, str]:
    """КРАСНОЕ — РОВНО rc 1 плюс `failed`: сломавший СБОР мутант даёт 2/4/5,
    и критерий «не ноль значит покраснел» принял бы его за пойманного."""
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


def revert(rel: str) -> None:
    subprocess.run(["git", "checkout", "--", rel], cwd=ROOT, check=True)


def assert_clean() -> None:
    out = subprocess.run(["git", "status", "--porcelain", "--untracked-files=no"],
                         cwd=ROOT, capture_output=True, text=True).stdout.strip()
    if out:
        raise SystemExit("ОТКАЗ: дерево грязное — откат сотрёт эти правки.\n" + out)


def main() -> int:
    refuse_if_live_tree(ROOT)
    assert_clean()
    blind = []
    for name, rel, edits, test in MUTATIONS:
        path = ROOT / rel
        original = path.read_bytes()
        mutated = original
        if any(old not in mutated for old, _new in edits):
            print(f"[!] МУТАЦИЯ НЕ ПРИМЕНИЛАСЬ: {name} — фрагмент не найден")
            blind.append((name, "фрагмент не найден"))
            continue
        for old, new in edits:
            mutated = mutated.replace(old, new, 1)
        if mutated == original:
            print(f"[!] МУТАЦИЯ НИЧЕГО НЕ ИЗМЕНИЛА: {name}")
            blind.append((name, "файл не изменился"))
            continue
        write_mutant(path, mutated)
        try:
            caught, why = run(test)
        finally:
            revert(rel)
        if path.read_bytes() != original:
            raise SystemExit(f"ОТКАТ НЕ ВЕРНУЛ ФАЙЛ ПОБАЙТОВО: {rel}")
        if not caught:
            print(f"[СЛЕП] {name}\n        {test}\n        {why}")
            blind.append((name, test))
        else:
            print(f"[ok]   {name} -> сторож покраснел")
    print()
    if blind:
        print(f"СЛЕПЫХ СТОРОЖЕЙ: {len(blind)} из {len(MUTATIONS)}")
        return 1
    print(f"Все {len(MUTATIONS)} мутаций пойманы, файлы возвращены побайтово.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
