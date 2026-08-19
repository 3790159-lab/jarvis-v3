"""DEV-26 для §9 спеки Хайку: ломаем сторож дрейфа префикса brain и требуем КРАСНОГО.

Предмет — сторож, поставленный ради ОДНОГО класса дефекта: величина, от которой
прямо зависит цена, росла молча (brain $0.0134 → $0.02184 за месяц, полосы
790 → 653 диалога, узнали случайно). Сторож на такую величину опаснее её
отсутствия ровно тем, что выглядит защитой. Поэтому каждая защита здесь
снимается по одной, и сторож обязан покраснеть на КАЖДОЙ.

ГЛАВНАЯ мутация файла — самообновляющийся эталон (§9.2). Она стоит первой и
прогоняется по ТРЁМ разным жертвам, потому что путей к записи в файл эталонов
три: `brain_drift_verdict`, `check_client_prefixes` и `--check` онбординга.
Эталон, догоняющий дрейф, воспроизводит РОВНО тот дефект, ради которого раздел
написан: он всегда молчит, и молчит правдоподобно.

Красное — РОВНО rc 1 плюс `failed`. rc 2 это «тест не собрался»; зачесть его
себе значит объявить сломанный прогон пойманной мутацией.

BOM здесь НЕ ставится: предмет мутации `.py`, а BOM в нём Python исполняет
молча, зато `ast.parse` краснеет — мутант «не применился» выглядел бы пойманным.

Прогон: .venv\\Scripts\\python.exe scripts/mutate_prefix_drift_guard.py
(только в worktree — DEV-31, см. gate_guard).
"""
from __future__ import annotations

import os
import subprocess
import sys
from itertools import count
from pathlib import Path

from gate_guard import refuse_if_live_tree   # DEV-31: гейт мутирует только worktree

ROOT = Path(__file__).resolve().parents[1]

# Уникальный mtime каждому мутанту: DEV-26 ловил случай, когда pytest исполнял
# ЧУЖОЙ байткод и гейт зеленел на неизменённом коде.
_MTIME_BASE = 2_000_000_000
_mtime_seq = count()

P = "chatter/core/prefix_budget.py"
R = "chatter/telethon_run.py"
K = "chatter/onboard/checks.py"
Y = "chatter/prefix_baselines.yaml"
T = "tests/chatter/test_prefix_drift_guard.py"

# Откат делается по ВСЕМУ `chatter/`, а не по одному мутировавшему файлу:
# главная мутация этого гейта — самообновляющийся эталон, то есть мутант в
# `.py` ПИШЕТ в `prefix_baselines.yaml`. Откат одного файла оставил бы
# переписанный эталон в дереве, и следующая мутация мерила бы уже не то.
REVERT_SCOPE = "chatter"

# ── Заготовки мутантов, слишком длинные для строчки таблицы ──────────────────

# §9.2 снят: после срабатывания сторож сам вписывает новое число в файл
# эталонов. Так выглядит «удобная» правка, которую пишут, устав от алертов.
_SELF_UPDATE_IN_VERDICT = (
    "    grown = (actual - bl.brain_tokens) * 100 / bl.brain_tokens\n",
    "    grown = (actual - bl.brain_tokens) * 100 / bl.brain_tokens\n"
    "    try:\n"
    "        _doc = yaml.safe_load(path.read_text(encoding='utf-8')) or {}\n"
    "        _doc.setdefault('clients', {})[slug] = {\n"
    "            'brain': actual, 'measured_with': bl.measured_with,\n"
    "            'measured_on': _date.today().isoformat()}\n"
    "        path.write_text(\n"
    "            yaml.safe_dump(_doc, allow_unicode=True, sort_keys=False),\n"
    "            encoding='utf-8')\n"
    "    except OSError:\n"
    "        pass\n",
)

_SELF_UPDATE_IN_CHECK = (
    "    return [brain_drift_verdict(\n"
    "        cfg, baselines=baselines, threshold_percent=threshold, counter=counter,\n"
    "        baselines_path=baselines_path)]",
    "    out = [brain_drift_verdict(\n"
    "        cfg, baselines=baselines, threshold_percent=threshold, counter=counter,\n"
    "        baselines_path=baselines_path)]\n"
    "    for _v in out:\n"
    "        if _v.kind == 'drift' and _v.actual is not None:\n"
    "            _doc = yaml.safe_load(path.read_text(encoding='utf-8')) or {}\n"
    "            _row = dict((_doc.get('clients') or {}).get(_v.slug) or {})\n"
    "            _row['brain'] = _v.actual\n"
    "            _doc.setdefault('clients', {})[_v.slug] = _row\n"
    "            path.write_text(\n"
    "                yaml.safe_dump(_doc, allow_unicode=True, sort_keys=False),\n"
    "                encoding='utf-8')\n"
    "    return out",
)

_SWALLOW_MEASURE_FAILURE = (
    '        log.warning("prefix-guard [%s]: замер префикса brain не выполнен", slug,\n'
    "                    exc_info=True)\n",
    "        return PrefixVerdict(\n"
    "            slug=slug, check=BRAIN_DRIFT, ok=True, loud=False, fatal=False,\n"
    '            kind="within", actual=None,\n'
    "            baseline=bl.brain_tokens if bl is not None else None,\n"
    "            threshold_percent=threshold_percent,\n"
    '            message="[%s] префикс brain: сверка пропущена" % slug)\n',
)

_MEASURE_FAILED_HEAD = (
    "            slug=slug, check=BRAIN_DRIFT, ok=False, loud=True, fatal=False,\n"
    '            kind="measure_failed", actual=None,')
_NO_BASELINE_HEAD = (
    "            slug=slug, check=BRAIN_DRIFT, ok=False, loud=True, fatal=False,\n"
    '            kind="no_baseline", actual=actual, baseline=None,')
_BROKEN_HEAD = (
    "            slug=slug, check=BRAIN_DRIFT, ok=False, loud=True, fatal=False,\n"
    '            kind="baselines_broken", actual=None, baseline=None,')
_DRIFT_HEAD = (
    "        slug=slug, check=BRAIN_DRIFT, ok=False, loud=True, fatal=False,\n"
    '        kind="drift", actual=actual, baseline=bl.brain_tokens,')

_C16_NO_COUNTER = (
    '        return _red("C16", (\n'
    '            f"ЗАМЕР НЕ ВЫПОЛНЕН: счётчик токенов не задан (офлайн-прогон). "')

_C16_VERDICT_TAIL = (
    "    if all(v.ok for v in verdicts):\n"
    '        return _ok("C16", message)\n'
    '    return _red("C16", message)')

_LOAD_PERSONAS_GUARD = (
    "        findings: tuple[str, ...] = ()\n"
    "        if isinstance(llm, AnthropicLLM):\n"
    "            findings = tuple(v.message for v in check_client_prefixes(cfg) if v.loud)")


def d(name: str) -> str:
    return f"{T}::{name}"


MUTATIONS = [
    # ── §9.2: эталон догоняет дрейф сам. ГЛАВНОЕ, ради чего написан раздел ──
    ("ГЛАВНОЕ (§9.2): сторож дописывает новое число в эталон сам — «чтобы не "
     "шуметь на законном росте»", P, [_SELF_UPDATE_IN_VERDICT],
     d("test_d3_baselines_file_is_byte_identical_after_the_guard_fires")),

    ("ГЛАВНОЕ (§9.2), вторая жертва: то же самообновление доезжает до БОЕВОГО "
     "файла эталонов при прогоне по умолчанию", P, [_SELF_UPDATE_IN_VERDICT],
     d("test_d3_repo_baselines_file_untouched_by_a_default_path_run")),

    ("ГЛАВНОЕ (§9.2), третья жертва: эталон переписывает себя из `--check` "
     "онбординга — там, где модуль обещал ноль записи на диск", P,
     [_SELF_UPDATE_IN_VERDICT],
     d("test_d10_check_run_does_not_touch_the_baselines_file")),

    ("ГЛАВНОЕ (§9.2), второй способ: подтяжка эталона вынесена этажом выше, в "
     "check_client_prefixes — «сторож-то чистый»", P, [_SELF_UPDATE_IN_CHECK],
     d("test_d3_baselines_file_is_byte_identical_after_the_guard_fires")),

    # ── Порог и его граница ──────────────────────────────────────────────────
    ("порог снят: предел равен эталону — сторож будит владельца на любом росте",
     P, [("    limit = bl.brain_tokens * (100 + threshold_percent) // 100",
          "    limit = bl.brain_tokens")],
     d("test_d2_the_boundary_is_the_limit_itself")),

    ("предел вдвое больше эталона — «чтобы не дёргало по мелочи»; настоящий "
     "дрейф проходит молча", P,
     [("    limit = bl.brain_tokens * (100 + threshold_percent) // 100",
       "    limit = bl.brain_tokens * 2")],
     d("test_d1_one_token_past_the_limit_is_loud")),

    ("граница съехала внутрь: РОВНО предел уже считается дрейфом — ложный "
     "алерт каждому клиенту у границы", P,
     [("    if actual <= limit:", "    if actual < limit:")],
     d("test_d2_the_boundary_is_the_limit_itself")),

    ("граница съехала наружу: предел+1 ещё молчит — лишний процент дрейфа "
     "подарен молча", P,
     [("    if actual <= limit:", "    if actual <= limit + 1:")],
     d("test_d1_one_token_past_the_limit_is_loud")),

    # ── no_baseline ──────────────────────────────────────────────────────────
    ("no_baseline перестал делать замер — человеку велено вписать число, "
     "которого никто не посчитал", P,
     [('            kind="no_baseline", actual=actual, baseline=None,',
       '            kind="no_baseline", actual=None, baseline=None,')],
     d("test_d5_missing_baseline_is_loud_and_names_the_actual_number")),

    ("no_baseline называет клиента, но не число — строку эталона придётся "
     "сочинять, и на шестом клиенте её не впишут вовсе", P,
     [('                f"клиента не сторожит НИКТО. Замер сейчас: {actual} токенов "',
       '                f"клиента не сторожит НИКТО. Посчитай префикс сам "'),
      ('                f"{slug}: {{brain: {actual}, measured_with: {model}, "',
       '                f"{slug}: {{brain: <число>, measured_with: {model}, "')],
     d("test_d5_missing_baseline_is_loud_and_names_the_actual_number")),

    # ── §2.0: чем и что меряем ───────────────────────────────────────────────
    ("считаем ТЕКУЩЕЙ моделью клиента, а не моделью эталона — сверка разными "
     "токенизаторами (§2.0), 7–12% дрейфа из воздуха", P,
     [("        model = bl.measured_with if bl is not None else str(cfg.settings.model)",
       "        model = str(cfg.settings.model)")],
     d("test_d6_counting_uses_the_model_the_baseline_was_measured_with")),

    ("меряем плейбук вместо собранного системного промпта — сторож честно "
     "работает и честно врёт: сверяет с эталоном другой величины", P,
     [("        actual = int(counter(model, build_system_prompt(cfg)))",
       "        actual = int(counter(model, cfg.playbook))")],
     d("test_d6_the_measured_text_is_the_brain_stable_prefix")),

    # ── Сторож, который молчит ───────────────────────────────────────────────
    ("дрейф перестал быть громким: вердикт в списке есть, алерта владельцу нет",
     P, [(_DRIFT_HEAD, _DRIFT_HEAD.replace("loud=True", "loud=False"))],
     d("test_d1_one_token_past_the_limit_is_loud")),

    ("новый клиент без эталона живёт тихо: no_baseline больше не громкий",
     P, [(_NO_BASELINE_HEAD, _NO_BASELINE_HEAD.replace("loud=True", "loud=False"))],
     d("test_d5_missing_baseline_is_loud_and_names_the_actual_number")),

    ("сбой замера перестал быть громким — «сеть моргнула, чего шуметь»; "
     "непроведённая сверка неотличима от пройденной", P,
     [(_MEASURE_FAILED_HEAD, _MEASURE_FAILED_HEAD.replace("loud=True", "loud=False"))],
     d("test_d4_counter_failure_is_measure_failed_and_does_not_escape")),

    ("битый файл эталонов перестал быть громким — весь парк молча без сверки",
     P, [(_BROKEN_HEAD, _BROKEN_HEAD.replace("loud=True", "loud=False"))],
     d("test_d8_broken_baselines_file_does_not_escape_check_client_prefixes")),

    # ── §9.2 «не отказ» и обратная сторона ───────────────────────────────────
    ("дрейф стал ОТКАЗОМ — законная правка плейбука через пульт не пускает "
     "клиента (§9.2 прямо запрещает)", P,
     [(_DRIFT_HEAD, _DRIFT_HEAD.replace("fatal=False", "fatal=True"))],
     d("test_d1_one_token_past_the_limit_is_loud")),

    ("дрейф объявлен зелёным при полном тексте алерта — ok=True «раз это не "
     "отказ»", P,
     [(_DRIFT_HEAD, _DRIFT_HEAD.replace("ok=False", "ok=True"))],
     d("test_d1_one_token_past_the_limit_is_loud")),

    ("сетевой чих роняет клиента: measure_failed стал отказом — лид остаётся "
     "без ответа из-за $0-запроса", P,
     [(_MEASURE_FAILED_HEAD, _MEASURE_FAILED_HEAD.replace("fatal=False", "fatal=True"))],
     d("test_d4_counter_failure_is_measure_failed_and_does_not_escape")),

    # ── DEV-18 ───────────────────────────────────────────────────────────────
    ("сбой замера проглочен молча: вместо measure_failed возвращается «в "
     "пределах» на НЕПРОВЕДЁННОМ замере (DEV-18)", P, [_SWALLOW_MEASURE_FAILURE],
     d("test_d4_counter_failure_is_measure_failed_and_does_not_escape")),

    ("битый файл эталонов читается как пустой словарь — все клиенты молча "
     "становятся no_baseline вместо громкой поломки", P,
     [("    except yaml.YAMLError as exc:\n"
       '        raise PrefixBaselineError(f"{p} не парсится: {exc}") from exc',
       "    except yaml.YAMLError:\n"
       "        return {}, DEFAULT_THRESHOLD_PERCENT")],
     d("test_d8_broken_baselines_file_raises_from_load_baselines")),

    ("битый файл эталонов отдаёт ПУСТОЙ список вердиктов — исчезнувшая "
     "проверка неотличима от пройденной", P,
     [("        return [PrefixVerdict(\n" + _BROKEN_HEAD,
       "        return []\n"
       "        return [PrefixVerdict(\n" + _BROKEN_HEAD)],
     d("test_d8_broken_baselines_file_does_not_escape_check_client_prefixes")),

    # ── Точка вызова №1: load_personas ───────────────────────────────────────
    ("замер префикса делается ВСЕГДА, включая fake-режим — каждый офлайн-тест "
     "и каждый дрил пошли в API", R,
     [(_LOAD_PERSONAS_GUARD,
       _LOAD_PERSONAS_GUARD.replace("if isinstance(llm, AnthropicLLM):", "if True:"))],
     d("test_d9_fake_llm_mode_never_measures")),

    ("замер префикса не делается НИКОГДА — модуль есть, вердиктов нет, старт "
     "клиента ничего не сторожит", R,
     [(_LOAD_PERSONAS_GUARD,
       _LOAD_PERSONAS_GUARD.replace("if isinstance(llm, AnthropicLLM):", "if False:"))],
     d("test_d9_real_llm_mode_measures_and_reports_loud_findings")),

    ("владельцу едут ВСЕ вердикты, а не только громкие — настоящий дрейф "
     "тонет в шести спокойных «в пределах»", R,
     [("            findings = tuple(v.message for v in check_client_prefixes(cfg) if v.loud)",
       "            findings = tuple(v.message for v in check_client_prefixes(cfg))")],
     d("test_d9_a_silent_within_measurement_leaves_no_findings")),

    # ── Точка вызова №2: онбординг, C16 ──────────────────────────────────────
    ("C16 выпала из списка флагов — законный рост плейбука роняет код возврата "
     "онбординга и блокирует подключение клиента", K,
     [('FLAG_IDS: frozenset[str] = frozenset({"C12", "C13", "C16"})',
       'FLAG_IDS: frozenset[str] = frozenset({"C12", "C13"})')],
     d("test_d10_c16_does_not_change_the_exit_code")),

    ("C16 не доехала до реестра проверок — строки в отчёте просто нет",
     K, [('CHECK_IDS: tuple[str, ...] = tuple(f"C{i}" for i in range(1, 17))',
          'CHECK_IDS: tuple[str, ...] = tuple(f"C{i}" for i in range(1, 16))')],
     d("test_d10_c16_is_registered_as_a_flag_not_as_a_red")),

    ("офлайн-прогон без ключа ЗЕЛЕНЕЕТ при том же тексте — непроведённая "
     "проверка выдана за пройденную", K,
     [(_C16_NO_COUNTER, _C16_NO_COUNTER.replace('_red("C16"', '_ok("C16"'))],
     d("test_d10_without_a_counter_c16_says_the_measurement_did_not_happen")),

    ("офлайн-прогон без ключа рапортует «префикс в пределах» — замера не было "
     "вовсе", K,
     [('        return _red("C16", (\n'
       '            f"ЗАМЕР НЕ ВЫПОЛНЕН: счётчик токенов не задан (офлайн-прогон). "\n'
       '            f"Размер префикса brain НЕ сверялся с эталоном в "\n'
       '            f"{DEFAULT_BASELINES_PATH}. Прогони с ANTHROPIC_API_KEY в "\n'
       '            f"окружении — тогда строка станет замером, а не отметкой о пропуске"))',
       '        return _ok("C16", (\n'
       '            f"префикс brain в пределах эталона "\n'
       '            f"(сверка по {DEFAULT_BASELINES_PATH})"))')],
     d("test_d10_without_a_counter_c16_says_the_measurement_did_not_happen")),

    ("отсутствие ключа СРЫВАЕТ прогон онбординга целиком — офлайновая приёмка "
     "перестала быть офлайновой", K,
     [(_C16_NO_COUNTER, _C16_NO_COUNTER.replace('_red("C16"', '_blocked("C16"'))],
     d("test_d10_without_a_counter_c16_says_the_measurement_did_not_happen")),

    ("C16 зелёная при найденном дрейфе: текст вердикта напечатан, а строка "
     "отчёта зелёная — читают статус, а не текст", K,
     [(_C16_VERDICT_TAIL, '    return _ok("C16", message)')],
     d("test_d10_with_a_drifting_counter_c16_names_the_numbers_and_stays_a_flag")),

    # ── Сами числа эталона ───────────────────────────────────────────────────
    ("эталон volska подтянут к сегодняшнему замеру руками — «дрейф же "
     "законный»; сторож продолжает сверять, но уже не с тем, о чём "
     "договорились", Y,
     [("  volska: {brain: 9131,  measured_with: claude-sonnet-5",
       "  volska: {brain: 10500, measured_with: claude-sonnet-5")],
     d("test_d7_repo_baselines_match_the_spec_table_exactly")),

    ("порог поднят с 15% до 25% — «чтобы не шумело»; договорённость §9.4 "
     "разошлась с файлом молча", Y,
     [("threshold_percent: 15          # N%", "threshold_percent: 25          # N%")],
     d("test_d7_repo_baselines_match_the_spec_table_exactly")),

    ("файл эталонов переехал в рантайм-состояние — правка эталона перестала "
     "быть коммитом с ревью", P,
     [('DEFAULT_BASELINES_PATH: Path = Path(__file__).resolve().parents[1] / "prefix_baselines.yaml"',
       'DEFAULT_BASELINES_PATH: Path = Path(__file__).resolve().parents[2] / "state" / "prefix_baselines.yaml"')],
     d("test_d7_repo_baselines_match_the_spec_table_exactly")),
]


def write_mutant(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")
    stamp = _MTIME_BASE + next(_mtime_seq)
    os.utime(path, (stamp, stamp))


def run(test: str) -> tuple[bool, str]:
    """(поймана ли мутация, чем ответил pytest). КРАСНОЕ — РОВНО rc 1 плюс
    `failed`: rc 2 это «тест не собрался», и зачесть его себе значит объявить
    сломанный прогон пойманной мутацией."""
    p = subprocess.run(
        [sys.executable, "-m", "pytest", test, "-q", "--no-header",
         "-p", "no:cacheprovider"],
        cwd=ROOT, capture_output=True, text=True,
        encoding="utf-8", errors="replace")
    out = (p.stdout or "") + (p.stderr or "")
    tail = out.strip().splitlines()[-1] if out.strip() else "(пусто)"
    return (p.returncode == 1 and "failed" in out,
            "rc=%d | %s" % (p.returncode, tail[:140]))


def revert() -> None:
    """Откат ВСЕГО `chatter/`, а не одного файла: главный мутант пишет в
    `prefix_baselines.yaml`, то есть портит файл, которого не касался."""
    subprocess.run(["git", "checkout", "--", REVERT_SCOPE], cwd=ROOT, check=True)


def dirty(scope=None) -> str:
    cmd = ["git", "status", "--porcelain", "--untracked-files=no"]
    if scope:
        cmd += ["--", scope]
    return subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True,
                          encoding="utf-8", errors="replace").stdout.strip()


def assert_clean() -> None:
    """Дерево обязано быть чистым ТАМ, КУДА БЬЁТ ОТКАТ.

    Образец требует чистоты всего дерева по одной причине: `git checkout --`
    сотрёт незакоммиченное. Здесь откат прицелен в `chatter/`, поэтому и
    требование прицельно — иначе гейт отказывал бы себе из-за правок в файле
    сторожей, который он же и валидирует, и его пришлось бы обходить.

    Правки ВНЕ зоны отката — не отказ, но и не тишина: они печатаются, потому
    что «сторожа зелены» на незакоммиченном файле сторожей — другое
    утверждение, и читающий отчёт обязан об этом знать.
    """
    inside = dirty(REVERT_SCOPE)
    if inside:
        raise SystemExit(
            "ОТКАЗ: боевой код грязный — откат мутаций сотрёт эти правки.\n"
            "Закоммить их и повтори прогон:\n" + inside)
    outside = dirty()
    if outside:
        print("[i] вне зоны отката есть незакоммиченные правки — сторожа "
              "проверяются В ЭТОМ виде, а не в виде коммита:")
        for line in outside.splitlines():
            print("      " + line)
        print()


def main() -> int:
    refuse_if_live_tree(ROOT)
    assert_clean()
    blind = []
    for name, rel, edits, test in MUTATIONS:
        path = ROOT / rel
        text = path.read_text(encoding="utf-8")
        mutated = text
        missing = [old for old, _new in edits if old not in mutated]
        if missing:
            # НЕ засчитывается пойманной: мутация, которая не применилась,
            # ничего не проверила.
            print(f"[!] МУТАЦИЯ НЕ ПРИМЕНИЛАСЬ: {name} — фрагмент не найден")
            blind.append((name, "фрагмент не найден"))
            continue
        for old, new in edits:
            mutated = mutated.replace(old, new, 1)
        if mutated == text:
            print(f"[!] МУТАЦИЯ НИЧЕГО НЕ ИЗМЕНИЛА: {name}")
            blind.append((name, "текст не изменился"))
            continue
        write_mutant(path, mutated)
        try:
            caught, why = run(test)
        finally:
            revert()
        if not caught:
            print(f"[СЛЕП] {name}\n        {test}\n        {why}")
            blind.append((name, test))
        else:
            print(f"[ok]   {name}")

    print(f"\nмутаций {len(MUTATIONS)}, поймано {len(MUTATIONS) - len(blind)}")
    if blind:
        print("СЛЕПЫЕ:")
        for name, where in blind:
            print(f"  - {name} ({where})")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
