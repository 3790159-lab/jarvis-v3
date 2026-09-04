# -*- coding: utf-8 -*-
"""Мутационный гейт C-1 / D2-1 «числа лида обеспечены» (sales-competence §4).

Мишени — РЕШЕНИЯ спеки, а не слова кода: число лида перестаёт быть выдумкой;
граница «пересказ можно, НАША цена нельзя» держится ДВУМЯ условиями (атрибуция
+ вето по ценовому слову), и каждое проверяется отдельно; нормализация пробелов
обязана быть той же, что у `_numbers`; оба публичных фасада обязаны пробрасывать
числа лида. Плюс две ВСТРЕЧНЫЕ половины: «обеспечить всё подряд» не должно
пройти, а `large_number` обязан продолжать ловить ЧУЖИЕ числа.

Мишень 3 заведена после того, как гейт показал: вето по ценовому слову было
МЁРТВОЙ ветвью — ни один сторож её не трогал, потому что в негативных примерах
атрибуции не было вовсе. Сторож на неё дописан ([[jarvis-guard-caught-dead-branch]]).

Прогон: python scripts/mutate_sales_c1_lead_numbers.py  (в worktree, дерево чистое)
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

D = "chatter/core/guardrails.py"
L = "chatter/core/langdetect.py"

G = "tests/test_sales_c1_lead_numbers.py"
GD = "tests/test_sales_c1_formula_dedup.py"
GL = "tests/test_sales_c1_reply_language.py"
GR = "tests/chatter/test_guardrails_redaction.py"

MUTATIONS = [
    # ── D2-1: число лида больше не обеспечено ────────────────────────────
    ("правило D2-1 отменено — число лида снова считается выдумкой бота", D,
     [(b"                and num not in lead_nums):",
       b"                and True):")],
     G + "::test_bare_echo_of_lead_number_is_backed"),

    # ── граница §4: ДВА условия, и каждое обязано быть живым ─────────────
    ("атрибуция к лиду больше не требуется — эхо и наша цена сравнялись", D,
     [(b'    return _LEAD_ATTRIBUTION.search(fragment or "") is not None',
       b"    return True")],
     G + "::test_our_price_in_lead_number_stays_flagged"),

    ("вето по ценовому слову снято — лид диктует НАШУ цену", D,
     [(b'    if _OUR_PRICE_ASSERTION.search(fragment or ""):\n        return False',
       b"    if False:\n        return False")],
     G + "::test_attribution_plus_our_price_word_still_dies"),

    # ── нормализация: «1 000» лида и «1000» ответа — одно число ──────────
    ("числа лида собираются без нормализации пробелов", D,
     [(b'        out |= _numbers(t or "")',
       b'        out |= {m.group() for m in _NUMBER.finditer(t or "")}')],
     G + "::test_lead_numbers_normalises_thin_spaces_like_findings_do"),

    # ── проброс через оба публичных фасада ───────────────────────────────
    ("redact_unbacked не пробрасывает числа лида в разбор", D,
     [(b"    findings = _findings(text, knowledge, lead_nums=lead_numbers)",
       b"    findings = _findings(text, knowledge)")],
     G + "::test_retelling_survives_redaction_intact"),

    ("contains_unbacked_claim не пробрасывает числа лида", D,
     [(b"    return bool(_findings(reply, knowledge, lead_nums=lead_numbers))",
       b"    return bool(_findings(reply, knowledge))")],
     G + "::test_contains_unbacked_claim_honours_lead_numbers"),

    # ── ВСТРЕЧНЫЕ половины ──────────────────────────────────────────────
    ("«обеспечить всё» — принадлежность числа лиду не проверяется", D,
     [(b"    if not number or number not in lead_nums:\n        return False",
       b"    if False:\n        return False")],
     G + "::test_number_lead_never_said_stays_flagged"),

    ("backstop large_number перестал ловить ЧУЖИЕ числа", D,
     [(b"        if (digits and int(digits) >= 100",
       b"        if (digits and int(digits) >= 100000000")],
     G + "::test_bare_echo_of_lead_number_is_backed"),

    # ── D2-3: одна формула — один раз на ответ ──────────────────────────
    ("дедуп формул снят — редактор снова ставит константу трижды", D,
     [(b"        if base in used:", b"        if False:")],
     GD + "::test_three_identical_deadline_formulas_collapse_to_one"),

    ("схлопнутая клауза не забирает свой разделитель", D,
     [(b"            trimmed = _drop_trailing_delim(gap)",
       b"            trimmed = gap")],
     GD + "::test_three_identical_deadline_formulas_collapse_to_one"),

    ("дедуп сравнивает НАПИСАНИЕ, а не базовую константу", D,
     [(b"            used.add(base)\n"
       b"            if _starts_sentence(text, cs + len(lead_ws)):\n"
       b"                phrase = phrase[0].upper() + phrase[1:]",
       b"            if _starts_sentence(text, cs + len(lead_ws)):\n"
       b"                phrase = phrase[0].upper() + phrase[1:]\n"
       b"            used.add(phrase)")],
     GD + "::test_capitalised_first_and_lowercase_second_are_the_SAME_formula"),

    ("ВСТРЕЧНАЯ: дедуп схлопывает РАЗНЫЕ формулы в одну", D,
     [(b"        if base in used:", b"        if used:")],
     GD + "::test_different_formulas_both_survive"),

    # ── D2-4: язык формулы = язык ответа ────────────────────────────────
    ("язык формулы снова берётся из скалярки клиента", D,
     [(b"    reply_language = detect_language(text, default=language)",
       b"    reply_language = language")],
     GL + "::test_russian_reply_gets_russian_formula_even_when_client_is_uk"),

    ("язык из скалярки — переписанный сторож редакции обязан покраснеть", D,
     [(b"    reply_language = detect_language(text, default=language)",
       b"    reply_language = language")],
     GR + "::test_replacement_speaks_the_language_of_the_reply"),

    ("язык из скалярки — сторож на все девять пар обязан покраснеть", D,
     [(b"    reply_language = detect_language(text, default=language)",
       b"    reply_language = language")],
     GR + "::test_settings_language_never_decides_the_formula"),

    ("детектор перестал различать украинские буквы", L,
     [('_UK_ONLY = set("іїєґ")'.encode(), b"_UK_ONLY = set()")],
     GL + "::test_detector_reads_the_alphabet"),

    ("детектор перестал различать русские буквы", L,
     [('_RU_ONLY = set("ыэъё")'.encode(), b"_RU_ONLY = set()")],
     GL + "::test_detector_reads_the_alphabet"),

    ("ВСТРЕЧНАЯ: без сигнала детектор выдумывает язык вместо фолбэка", L,
     [(b"    return default", b'    return "en"')],
     GL + "::test_no_signal_falls_back_to_default"),
    # ── вето обязано знать ДЕНЬГИ словом, а не только «ціна» ────────────
    ("вето не знает слова «сума» — бюджет лида становится нашим прайсом", D,
     [(b'    r"|\xd1\x81\xd1\x83\xd0\xbc\xd0\xbc?[\xd0\xb0\xd0\xb8\xd1\x83\xd0\xbe\xd0\xb5\xd1\x94\xd1\x96\xd1\x8b]\\w*|\\bsum\\b"',
       b'    r"|(?!x)x"')],
     G + "::test_lead_number_named_as_our_sum_stays_flagged"),
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
