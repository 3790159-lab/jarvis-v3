# -*- coding: utf-8 -*-
"""Мутационный гейт арки web-b «публичная дверь» (спека §6).

Мишени — РЕШЕНИЯ спеки §2.1–§2.9, а не слова кода: дверь не существует без
ключа, полу-настройка ОТКАЗЫВАЕТ, три «нет» отвечают одинаково, токен
сравнивается постоянным временем, лимиты стоят и на клиента И на адрес, тело
режется ДО парсера, упавший приёмник не выдаёт себя за принявшего, след не
несёт секрета, — и три ВСТРЕЧНЫЕ половины: «отказывать всегда» не должно
пройти, выключенная дверь обязана остаться ЗЕЛЁНОЙ, а распахнутая — красной.

Сторожа писал ДРУГОЙ заход, от текста спеки. Гейт проверяет не код, а ИХ:
ломаем решение и требуем красного от НАЗВАННОГО сторожа (`файл::имя`), а не от
файла — прогон по файлу зеленел бы за счёт соседа.

Харнесс тот же, что у гейтов DEV-48 и web-a, включая обе поправки: откат по
СОХРАНЁННЫМ БАЙТАМ и поиск фрагмента в обеих формах концов строк.
`write_mutant` принимает и строку, и байты — этого требует мета-сторож
`test_mutation_gate_bytecode`.

Прогон: python scripts/mutate_web_public_door.py  (в worktree, дерево чистое)
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

D = "app/routers/public_door.py"
W = "scripts/ops_watchdog.py"

G = "tests/test_public_door.py"

MUTATIONS = [
    # ── §2.1 двери НЕТ по умолчанию ──────────────────────────────────────
    ("дверь монтируется без ключа — «выключено по умолчанию» отменено", D,
     [(b'    return bool((src.get(ENV_KEY) or "").strip())',
       b"    return True")],
     G + "::test_door_without_key_is_not_mounted_at_all"),

    # ── §2.2 полу-настройка — ОТКАЗ, а не «как-нибудь» ───────────────────
    ("пустой список клиентов проходит сборку", D,
     [(b"    if not isinstance(config.clients, dict) or not config.clients:",
       b"    if False:")],
     G + "::test_door_with_key_but_no_clients_refuses_to_build"),

    ("короткий токен принимается — перебор станет дешевле следа", D,
     [(b"        if len(token) < MIN_TOKEN_LEN:", b"        if False:")],
     G + "::test_client_without_token_or_with_short_token_refuses_to_build"),

    # ── §2.4 три «нет» отвечают ОДИНАКОВО ────────────────────────────────
    # Утечка причины в ответ — это подсказка подбирающему: «клиент есть, токен
    # не тот» отделяет верный слаг от неверного за один запрос.
    ("причина отказа утекла в ответ — три «нет» перестали совпадать", D,
     [(b"            return JSONResponse(dict(REFUSAL_BODY), status_code=REFUSAL_STATUS)",
       b'            return JSONResponse({"error": reason}, status_code=REFUSAL_STATUS)')],
     G + "::test_three_kinds_of_refusal_are_byte_identical"),

    ("токен сравнивается `==` — утечка по времени вернулась", D,
     [(b"        ok_token = bool(expected) and hmac.compare_digest(token, expected)",
       b"        ok_token = bool(expected) and (token == expected)")],
     G + "::test_token_is_compared_in_constant_time_not_by_equals"),

    # ── §2.5 лимиты стоят НА ОБОИХ, иначе каждый обходится другим ─────────
    ("лимит на клиента снят", D,
     [(b'        if not windows.allow("c:%s" % client, int(config.per_client_per_minute), now):',
       b"        if False:")],
     G + "::test_per_client_flood_gets_429_with_retry_after"),

    ("лимит на адрес снят — обходится сменой клиента", D,
     [(b'        if not windows.allow("i:%s" % peer_ip, int(config.per_ip_per_minute), now):',
       b"        if False:")],
     G + "::test_ip_flood_is_capped_even_when_the_client_changes"),

    # ── §2.6 тело режется ДО парсера ─────────────────────────────────────
    # Порядок здесь и есть решение: разбор неограниченного тела роняет процесс
    # одним запросом, и на это не нужно ни токена, ни клиента.
    ("потолок тела снят — парсер увидит запрос целиком", D,
     [(b"        raw = await _read_capped(request, int(config.max_body_bytes))",
       b"        raw = await request.body()")],
     G + "::test_oversized_body_is_refused_before_the_json_parser_runs"),

    # ── §2.7 упавший приёмник не выдаёт себя за принявшего ───────────────
    ("упавший приёмник отвечает 202 — «приняли» без принявшего", D,
     [(b'            return JSONResponse({"error": "unavailable"}, status_code=503)',
       b'            return JSONResponse({"status": "accepted"}, status_code=202)')],
     G + "::test_failed_sink_answers_503_and_is_not_written_down_as_accepted"),

    # ── §2.8 след без секрета ────────────────────────────────────────────
    ("тело запроса уехало в след — вторая копия секрета без охраны", D,
     [(b'        _trail(trail_path, client=client, outcome=202, reason="accepted", now=now)',
       b"        _trail(trail_path, client=client, outcome=202, reason=str(payload), now=now)")],
     G + "::test_every_outcome_leaves_a_trail_line_without_token_or_body"),

    # ВСТРЕЧНАЯ ПОЛОВИНА №1. Без неё «отказывать ВСЕГДА» прошло бы гейт: все
    # мишени на отказ остались бы зелёными, а дверь не пропускала бы никого.
    ("дверь отказывает ВСЕГДА — даже верному токену", D,
     [(b'        return JSONResponse({"status": "accepted"}, status_code=202)',
       b"        return JSONResponse(dict(REFUSAL_BODY), status_code=REFUSAL_STATUS)")],
     G + "::test_valid_token_yields_202_and_calls_sink_exactly_once"),

    # ── §2.9 проба меряет ОТКАЗ, а не живость ────────────────────────────
    ("распахнутая дверь признана ЗЕЛЁНОЙ", W,
     [(b'        return {"ok": False, "reason": "door_accepts_without_token",',
       b'        return {"ok": True, "reason": "door_accepts_without_token",')],
     G + "::test_probe_says_off_when_disabled_and_red_when_the_door_lets_anyone_in"),

    # ВСТРЕЧНАЯ ПОЛОВИНА №2. Выключенная дверь — НОРМАЛЬНОЕ состояние: до
    # включения владельцем её и не должно быть. Сделай её красной — и лампа
    # станет вечным фоном ещё до того, как дверь вообще откроют.
    ("выключенная дверь стала КРАСНОЙ — вечный фон до включения", W,
     [(b'        return {"ok": True, "reason": "off"}',
       b'        return {"ok": False, "reason": "off"}')],
     G + "::test_probe_says_off_when_disabled_and_red_when_the_door_lets_anyone_in"),
]


def write_mutant(path: Path, text) -> None:
    """Записать мутанта с УНИКАЛЬНЫМ mtime. Всегда `write_bytes`.

    Принимает И байты, И строку: мета-сторож на гейты зовёт этот метод строкой,
    и суженная до байтов сигнатура ломает ЗАМЕР гейта, а не сам гейт — то есть
    выглядит как «гейт не проверен», что в этом доме опаснее красного.
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
