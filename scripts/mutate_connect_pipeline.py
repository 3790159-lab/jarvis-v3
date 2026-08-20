"""DEV-26 для арки T7 «подключение под ключ»: снимаем МЕХАНИКУ ВОРОТ и требуем красного.

Мутируются решения, а не тексты. Семь мишеней названы владельцем и записаны в
§8 спеки `docs/superpowers/specs/2026-08-20-t7-turnkey-connect.md`:

    1. снятая проверка порядка — S11 поднимает раннера без СВОИХ предусловий
       (конфиг не грузится, а `enabled: true` всё равно выставляется);
    2. `alive` из `starting` — приёмка живости засчитывает «поднимаю» за
       «поднялся» (Д10);
    3. повторный платный дрил — второй прогон без явного `--drill-again` (Д3);
    4. состояние из ЖУРНАЛА — вердикт шага берётся из `state/connect/<slug>.md`,
       который по §1 состоянием не является (Д7);
    5. подстановка ключа принуждения — `-Force` уезжает в `chatter_client.ps1`
       и замер catch-up радиуса пропускается (Д4);
    6. пропуск проверки уникальности имени env-переменной токена (Д5);
    7. снятый отказ ЖИВОМУ клиенту — «клиент уже подключён и работает» (Д8).

Каждая мутация обязана краснеть РОВНО у того сторожа, который на неё заведён.
Мутация, которую не поймал никто, — находка. Мутация, от которой краснеет
полсуиты, — тоже находка: мишень выбрана не по предмету. Второе видно, только
если посчитать, поэтому у гейта есть ПЕРЕПИСЬ (включена по умолчанию,
выключается `--no-census`): мутант остаётся на месте после прицельного
прогона, и по нему гоняется вся связка сторожей арки.

────────────────────────────────────────────────────────────────────────────
ЧЕТЫРЕ СПОСОБА, КОТОРЫМИ ГЕЙТЫ В ЭТОМ ДОМЕ УЖЕ ВРАЛИ. Что сделано против
каждого — здесь, а не в памяти автора.

1. ЧУЖОЙ БАЙТКОД. Python признаёт `.pyc` актуальным по паре (mtime в ЦЕЛЫХ
   секундах, размер исходника); две мутации одного размера в одну секунду
   неотличимы, и вторая исполняется байткодом первой. Каждая запись получает
   УНИКАЛЬНЫЙ mtime (`write_mutant`), и откат тоже (`restore`): восстановленный
   оригинал того же размера иначе прочитался бы байткодом мутанта.

2. МИШЕНЬ УЕХАЛА ОТ КОДА. Гейт мутирует то, чего уже нет, и молча зеленеет.
   Поэтому СТАТИЧЕСКАЯ СВЕРКА (`verify_targets`) идёт ДО первой записи и
   требует РОВНО ожидаемого числа вхождений каждой подстроки, а не «хотя бы
   одного». Сверка гоняется и отдельно: `--check-targets` (диска не меняет).
   Сверяются и ИМЕНА сторожей: `--collect-only` по каждому node id, потому что
   мутация в переименованный тест даёт rc 4 «ничего не собрали», а не красное.

3. БУКВА «И» И cp1251. Вывод подпроцессов читается ЯВНО в UTF-8, детям
   ставится `PYTHONUTF8=1`: байт `0x98` из «И» роняет читающий поток, и
   пойманная мутация печатается слепой.

4. «КРАСНЫЙ» — ЭТО РОВНО rc 1. rc 2 (ошибка сбора), 4, 5 (тестов нет) красным
   не считаются, иначе гейт зеленеет на сломанном прогоне.

────────────────────────────────────────────────────────────────────────────
ДЕРЕВО. Гейт правит файлы на диске, поэтому откат обязан быть точным и
безусловным. Откат идёт ПОБАЙТОВЫМ возвратом снятого снимка и стоит в
`finally`, а не на счастливом пути; `git checkout --` остаётся аварийным
вторым эшелоном для ОДНОГО файла. `git status --porcelain` сверяется со
СНИМКОМ ДО ПРОГОНА, а не с пустотой: рядом может работать другой автор, и его
незакоммиченная правка — не наш след. Мутировать при этом можно только
ЗАКОММИЧЕННЫЕ файлы: аварийный откат опирается на git и снёс бы чужое.

Прогон (ТОЛЬКО из PowerShell, только в worktree):

    C:\\jarvis\\.venv\\Scripts\\python.exe scripts\\mutate_connect_pipeline.py
    C:\\jarvis\\.venv\\Scripts\\python.exe scripts\\mutate_connect_pipeline.py --check-targets
"""
from __future__ import annotations

import os
import subprocess
import sys
from itertools import count
from pathlib import Path

from gate_guard import refuse_if_live_tree   # DEV-31: гейт мутирует только worktree

ROOT = Path(__file__).resolve().parents[1]   # работает и в worktree

# Уникальный mtime на каждую запись — см. п.1 шапки. База в будущем и
# монотонный счётчик, а НЕ часы: две записи подряд занимают микросекунды, и
# писатель «по часам» упирается в потолок «одно значение в секунду».
_MTIME_BASE = 2_000_000_000
_mtime_seq = count()

ACTIONS = "chatter/connect/actions.py"
PROBES = "chatter/connect/probes.py"
MAIN = "chatter/connect/__main__.py"

T_ORDER = "tests/test_connect_order_gates.py"
T_MONEY = "tests/test_connect_human_and_money.py"
T_RESUME = "tests/test_connect_resume.py"
T_CLI = "tests/test_connect_cli.py"

# Вся связка сторожей арки — для ПЕРЕПИСИ (сколько всего покраснело).
# `test_no_other_automatic_step_reaches_the_drill_harness` исключён поимённо:
# он один идёт ~300 с, и в мутационном прогоне это лишние минуты на КАЖДУЮ
# мутацию. Исключение названо здесь, а не спрятано в командной строке.
SUITE = (T_CLI, T_ORDER, T_RESUME, T_MONEY)
SLOW_DESELECT = (
    "tests/test_connect_human_and_money.py"
    "::test_no_other_automatic_step_reaches_the_drill_harness")

# ─────────────────────────────────────────────────────────────────────────────
# МУТАЦИИ. (имя, файл, [(мишень, чем заменить, сколько вхождений ждём)], сторож)
#
# Сторож НАЗВАН ОДИН на мутацию, и это принципиально: «покраснела хоть одна
# проверка из трёхсот» доказывает, что суита чувствительна, а не что мишень
# охраняется ПО ПРЕДМЕТУ. Ровно на этом гейты в этом доме уже врали.
# ─────────────────────────────────────────────────────────────────────────────

MUTATIONS = [
    # ── 1. Снятая проверка порядка: S11 без своих предусловий ─────────────
    # `act_s11` проверяет `load_config` СВОИМИ руками именно потому, что
    # «порядок вызовет меня правильно» — надежда, а не предусловие. Снимаем
    # надежду: подъём выставляет `enabled: true` при конфиге, которого S4 ещё
    # не переносил, — то есть S11 исполняется раньше S4. Цена названа в §2.1
    # дословно: гардиан поднимает и роняет раннер по кругу каждые ~30 с.
    ("порядок снят: S11 поднимает клиента с НЕГРУЗЯЩИМСЯ конфигом (S4 не был)",
     ACTIONS,
     [('    if not _loads(ctx):\n        return _conflict(\n            "S11",',
       '    if False:\n        return _conflict(\n            "S11",', 1)],
     # Сторож назван ПО ПРЕДМЕТУ: мутация снимает собственное предусловие
     # ДЕЙСТВИЯ, а `test_d2_enabled_true_is_never_set_without_config_or_session`
     # проверяет ПОРЯДОК и до `act_s11` с битым конфигом не доходит — он был
     # зелёным законно (замер 21.08: перепись нашла красными именно эти два).
     T_ORDER + "::test_d2_act_refuses_on_its_own_when_called_out_of_order"),

    # ── 2. `alive` из `starting` (Д10) ────────────────────────────────────
    # «Поднимаю» и «поднялся» — разные утверждения. Мутация сливает их в одно,
    # и приёмка живости закрывается на клиенте, который ещё стартует.
    ("приёмка живости засчитывает «starting» за «alive»", PROBES,
     [('    if state != "alive":',
       '    if state not in ("alive", "starting"):', 1)],
     T_ORDER + "::test_d10_starting_is_not_alive"),

    # ── 3. Повторный платный дрил без `--drill-again` (Д3) ────────────────
    # `--drill-yes` разрешает ОДИН прогон; повтор поверх готового результата —
    # вторая трата денег клиента и посторонний трафик в его БД, поэтому
    # требуются ОБА флага.
    #
    # Мишень стоит В ДЕЙСТВИИ, а не в `forced` у `__main__`, и это результат
    # замера 21.08: снятый `ctx.drill_again` из `forced` не потратил ни цента —
    # цикл звал `act_s13` поверх закрытого шага, а тот отказывался сам. Ноль
    # красных в переписи означал не дыру в сторожах, а мишень на втором
    # рубеже: деньги лежат здесь.
    ("повторный ПЛАТНЫЙ дрил запускается без явного --drill-again", ACTIONS,
     [('    if already and not ctx.drill_again:\n'
       '        return _open(\n'
       '            "S13",',
       '    if already and False:\n'
       '        return _open(\n'
       '            "S13",', 1)],
     T_MONEY + "::test_an_existing_drill_result_is_not_rerun_even_with_drill_yes"),

    # ── 4. Состояние из ЖУРНАЛА, а не с диска (Д7) ────────────────────────
    # По §1 журнал заводится ДЛЯ ЧЕЛОВЕКА и пробами не читается никогда: второе
    # представление состояния молча погасило бы первое. Мутация делает журнал
    # источником вердикта на единственном ПЛАТНОМ шаге — то есть подделанная
    # строка закрывает дрил, которого не было.
    ("вердикт S13 берётся из журнала state/connect/<slug>.md, а не с диска",
     PROBES,
     [('                   "estimate_usd": DRILL_COST_CEILING_USD, "estimate_read": False}\n'
       '    try:\n'
       '        scenario_path, scenario = _scenario(ctx)',
       '                   "estimate_usd": DRILL_COST_CEILING_USD, "estimate_read": False}\n'
       '    _journal = _state_dir(ctx) / "connect" / f"{ctx.slug}.md"\n'
       '    if _journal.is_file() and "S13" in _journal.read_text(\n'
       '            encoding="utf-8", errors="replace"):\n'
       '        return _closed("S13", "журнал говорит, что дрил прогнан", facts)\n'
       '    try:\n'
       '        scenario_path, scenario = _scenario(ctx)', 1)],
     T_RESUME + "::test_d7_forged_journal_changes_no_verdict"),

    # ── 5. Подстановка ключа принуждения (Д4) ─────────────────────────────
    # `-Force` — ветка подъёма, ПРОПУСКАЮЩАЯ замер catch-up радиуса.
    # Подключение не подставляет его никогда: у нового клиента радиус нулевой
    # по построению, а если он вдруг не нулевой — это и есть то, что надо
    # прочитать глазами.
    ("-Force подставлен в вызов chatter_client.ps1 — замер радиуса пропущен",
     ACTIONS,
     [('        "-Slug", ctx.slug, "-Action", "start", "-Root", str(ctx.root),',
       '        "-Slug", ctx.slug, "-Action", "start", "-Force", "-Root", str(ctx.root),',
       1)],
     T_ORDER + "::test_d4_force_is_never_substituted_in_any_branch"),

    # ── 6. Пропуск проверки уникальности имени env-переменной (Д5) ────────
    # Общее имя переменной = два раннера в `getUpdates` на одном токене:
    # Telegram отдаёт long-poll одному, второй ловит 409, и команды пульта
    # ходят через раз БЕЗ ошибок в логе. Мутация оставляет проверку на месте и
    # делает её недостижимой — ровно так дефект и выглядит в жизни.
    ("совпадение имени токена с ВКЛЮЧЁННЫМ клиентом больше не ловится",
     PROBES,
     [('            if other_name and other_name.upper() == env_name.upper():\n'
       '                clash.append(entry.slug)',
       '            if False:\n'
       '                clash.append(entry.slug)', 1)],
     T_MONEY + "::test_a_token_env_name_shared_with_an_enabled_client_is_a_stop"),

    # ── 7. Снятый отказ ЖИВОМУ клиенту (Д8) ───────────────────────────────
    # Отказ живёт в `probe_s0` и стоит первым: подключать уже подключённого
    # НЕЧЕГО, а команда правит `settings.yaml`, пишет реестр и гоняет платный
    # дрил в БОЕВОЙ БД при открытой воронке. Мутация делает ворота
    # недостижимыми — тот же класс, что «отказ написан, но стоит не там, где
    # на него смотрят».
    ("отказ «клиент уже подключён и работает» снят (probe_s0)", PROBES,
     [('    if dict(probe_s15(ctx).facts).get("funnel_gate") is True:',
       '    if False:', 1)],
     T_ORDER + "::test_d8_alive_client_with_open_gate_is_refused_entirely"),
]


# ─────────────────────────────────────────────────────────────────────────────
# Запись и откат
# ─────────────────────────────────────────────────────────────────────────────

def write_mutant(path: Path, text: str) -> None:
    """Записать мутанта и ПОДПИСАТЬ уникальным mtime.

    Контракт с `tests/test_mutation_gate_bytecode.py`: первые два параметра
    позиционны (путь, текст), всё сверх них обязано иметь умолчание. Здесь
    сверх них нет ничего.
    """
    path.write_text(text, encoding="utf-8")
    stamp = _MTIME_BASE + next(_mtime_seq)
    os.utime(path, (stamp, stamp))


def restore(path: Path, blob: bytes) -> None:
    """Вернуть снятый ПОБАЙТОВО снимок и подписать его тоже.

    Байты, а не `git checkout --`: снимок точен и не зависит от того, что
    думает индекс, а рядом может лежать чужая незакоммиченная работа, которой
    команды git по каталогу опасны.

    Подпись обязательна и здесь: оригинал того же РАЗМЕРА, что мутант,
    прочитался бы его байткодом (DEV-26, тот же механизм, только наоборот).
    """
    path.write_bytes(blob)
    stamp = _MTIME_BASE + next(_mtime_seq)
    os.utime(path, (stamp, stamp))


def git_restore(rel: str) -> None:
    """Аварийный откат ОДНОГО файла через git. Зовётся, только если побайтовый
    возврат не сошёлся, — и тогда об этом кричат вслух."""
    subprocess.run(["git", "checkout", "--", rel], cwd=ROOT, check=True)


# ─────────────────────────────────────────────────────────────────────────────
# Прогон pytest
# ─────────────────────────────────────────────────────────────────────────────

def _env() -> dict:
    """`PYTHONUTF8=1` детям: иначе кириллица в ассертах роняет вывод (п.3 шапки)."""
    env = dict(os.environ)
    env["PYTHONUTF8"] = "1"
    return env


def _pytest(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "pytest", *args, "-q", "--no-header",
         "-p", "no:cacheprovider"],
        cwd=ROOT, capture_output=True, text=True,
        encoding="utf-8", errors="replace", env=_env())


def _failed_names(out: str) -> list[str]:
    names = []
    for line in out.splitlines():
        line = line.strip()
        if line.startswith("FAILED ") or line.startswith("ERROR "):
            names.append(line.split(" ", 1)[1].split(" - ")[0].strip())
    return names


def run_guard(test: str) -> tuple[bool, str]:
    """(поймана ли мутация, чем именно ответил pytest).

    КРАСНОЕ — РОВНО `rc 1` плюс `failed` в выводе. rc 2 (ошибка сбора), 4, 5
    (тестов нет) означают, что не выполнилась НИ ОДНА проверка, и критерий
    «не ноль значит покраснел» зачёл бы сломанный прогон за пойманную мутацию.
    """
    p = _pytest([test, "--tb=no", "-rf"])
    out = (p.stdout or "") + (p.stderr or "")
    tail = out.strip().splitlines()[-1] if out.strip() else "(пусто)"
    return (p.returncode == 1 and "failed" in out,
            "rc=%d | %s" % (p.returncode, tail[:160]))


def run_census() -> tuple[int, list[str], str]:
    """Перепись: сколько сторожей арки покраснело на этом мутанте и каких.

    Нужна ровно затем, чтобы «покраснело полсуиты» было ЧИСЛОМ, а не
    впечатлением: мишень, задевающая пол-арки, выбрана не по предмету.
    """
    p = _pytest([*SUITE, "--deselect", SLOW_DESELECT, "--tb=no", "-rf"])
    out = (p.stdout or "") + (p.stderr or "")
    tail = out.strip().splitlines()[-1] if out.strip() else "(пусто)"
    return p.returncode, _failed_names(out), tail[:160]


# ─────────────────────────────────────────────────────────────────────────────
# Статическая сверка мишеней — ДО первой записи (§8 спеки, дословно)
# ─────────────────────────────────────────────────────────────────────────────

def verify_targets(verbose: bool = True) -> list[str]:
    """Каждая мишень существует РОВНО в ожидаемом числе экземпляров.

    «Хотя бы одно вхождение» здесь не годится: `replace(old, new, 1)` правит
    ПЕРВОЕ, и мишень, размножившаяся по файлу, молча мутировала бы не то место.
    Сверяются и имена сторожей — мутация в переименованный тест даёт rc 4
    «ничего не собрали», а гейт с критерием «не ноль» зачёл бы это за красное.

    Возвращает список претензий. Пусто = можно мутировать.
    """
    problems: list[str] = []
    cache: dict[str, str] = {}
    for name, rel, edits, test in MUTATIONS:
        text = cache.get(rel)
        if text is None:
            path = ROOT / rel
            if not path.is_file():
                problems.append(f"{name}: файла {rel} нет")
                continue
            # ПОБАЙТОВО и БЕЗ перевода переводов строки — тем же способом,
            # каким читает писатель мутанта (`read_bytes().decode()`).
            # `read_text` переводит CRLF -> LF при чтении, и на CRLF-файле
            # сверка находила ВСЕ мишени, а `replace` не находил ни одной
            # многострочной: гейт печатал «все мишени на месте» и уходил в
            # «мутация не применилась» (замер 21.08).
            text = path.read_bytes().decode("utf-8")
            cache[rel] = text
        for old, _new, want in edits:
            got = text.count(old)
            head = old.strip().splitlines()[0][:70]
            if verbose:
                print(f"    {rel}: «{head}» -> вхождений {got} (ждём {want})")
            if got != want:
                problems.append(
                    f"МИШЕНЬ УЕХАЛА: {name} — в {rel} подстрока «{head}» "
                    f"встречается {got} раз, а ждали {want}")

    # Сторожа: node id обязан СОБИРАТЬСЯ. Один прогон на все имена сразу.
    tests = sorted({t for _n, _r, _e, t in MUTATIONS})
    p = _pytest([*tests, "--collect-only"])
    out = (p.stdout or "") + (p.stderr or "")
    if verbose:
        tail = out.strip().splitlines()[-1] if out.strip() else "(пусто)"
        print(f"    сборка {len(tests)} сторожей: rc={p.returncode} | {tail[:120]}")
    if p.returncode != 0:
        problems.append(
            "СТОРОЖ УЕХАЛ: не собрались node id " + ", ".join(tests)
            + f" (rc {p.returncode})")
    return problems


def baseline_green() -> list[str]:
    """Сторожа обязаны быть ЗЕЛЁНЫМИ до мутаций — иначе «красный» ничего не значит.

    Это и есть ноль гейта: красный сторож на чистом дереве превратил бы
    «мутация поймана» в «здесь и так было плохо».
    """
    tests = sorted({t for _n, _r, _e, t in MUTATIONS})
    p = _pytest([*tests, "--tb=no", "-rf"])
    out = (p.stdout or "") + (p.stderr or "")
    if p.returncode == 0:
        return []
    return [f"ноль не зелёный: rc={p.returncode}"] + _failed_names(out)


# ─────────────────────────────────────────────────────────────────────────────
# Дерево: чисто ли ТАМ, ГДЕ МЫ ПИШЕМ
# ─────────────────────────────────────────────────────────────────────────────

def porcelain() -> str:
    return subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=no"],
        cwd=ROOT, capture_output=True, text=True,
        encoding="utf-8", errors="replace").stdout.strip()


def dirty_set(text: str) -> set:
    out = set()
    for line in text.splitlines():
        rel = line[3:].strip().strip('"')
        if " -> " in rel:
            rel = rel.split(" -> ", 1)[1]
        out.add(rel)
    return out


def assert_targets_committed(dirty: set) -> None:
    """Файлы, которые мутируем, обязаны быть закоммичены.

    Требование АДРЕСНОЕ, а не «всё дерево чистое»: рядом может работать другой
    автор, и его незакоммиченный сторож нам не мешает — мы его не трогаем.
    А вот мутировать НЕЗАКОММИЧЕННЫЙ файл нельзя: аварийный откат опирается на
    git, и правка уехала бы вместе с мутантом.
    """
    touched = {rel for _n, rel, _e, _t in MUTATIONS}
    overlap = sorted(touched & dirty)
    if overlap:
        raise SystemExit(
            "ОТКАЗ: гейт мутирует файлы, у которых есть НЕЗАКОММИЧЕННЫЕ правки.\n"
            "Аварийный откат опирается на git и снёс бы их:\n"
            + "\n".join("  · " + rel for rel in overlap))


# ─────────────────────────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    only_targets = "--check-targets" in args
    census = "--no-census" not in args

    refuse_if_live_tree(ROOT)

    print("СТАТИЧЕСКАЯ СВЕРКА МИШЕНЕЙ (диска не меняет):")
    problems = verify_targets()
    if problems:
        print()
        for problem in problems:
            print("  [!] " + problem)
        print(f"\nСВЕРКА НЕ ПРОШЛА: {len(problems)}. Мутации не запускались — "
              f"гейт, мутирующий то, чего нет, зеленеет молча.")
        return 1
    print("  все мишени на месте.\n")
    if only_targets:
        return 0

    before = porcelain()
    assert_targets_committed(dirty_set(before))
    if before:
        print("ВНИМАНИЕ: в дереве есть незакоммиченные правки (не наши, не трогаем):")
        for line in before.splitlines():
            print("  " + line)
        print()

    print("НОЛЬ: названные сторожа ДО мутаций")
    zero = baseline_green()
    if zero:
        for line in zero:
            print("  [!] " + line)
        print("\nОТКАЗ: сторожа красные ДО мутаций — «покраснел» ничего не доказывает.")
        return 1
    print("  все названные сторожа зелёные.\n")

    blind: list[tuple[str, str]] = []
    rows: list[tuple[str, str, str, str]] = []
    for name, rel, edits, test in MUTATIONS:
        path = ROOT / rel
        blob = path.read_bytes()
        text = blob.decode("utf-8")
        mutated = text
        for old, new, _want in edits:
            mutated = mutated.replace(old, new, 1)
        if mutated == text:
            # Недостижимо после статической сверки — но «недостижимо» уже
            # оказывалось достижимым, а цена ошибки здесь «[ok] ни на чём».
            print(f"[!] МУТАЦИЯ НЕ ПРИМЕНИЛАСЬ: {name}")
            blind.append((name, "мутация не применилась"))
            rows.append((name, test, "не применилась", ""))
            continue

        census_row = ""
        shown: list[str] = []
        extra = ""
        write_mutant(path, mutated)
        try:
            caught, why = run_guard(test)
            if census:
                rc, failed, _tail = run_census()
                census_row = f"{len(failed)} красных в связке (rc={rc})"
                shown = failed[:12]
                if len(failed) > len(shown):
                    extra = f"… и ещё {len(failed) - len(shown)}"
        finally:
            restore(path, blob)
            if path.read_bytes() != blob:                      # pragma: no cover
                print(f"[!!] ПОБАЙТОВЫЙ ОТКАТ {rel} НЕ СОШЁЛСЯ — чиню через git")
                git_restore(rel)

        rows.append((name, test, why, census_row))
        if caught:
            print(f"[ok]   {name}\n        -> {test}\n        {why}")
        else:
            print(f"[СЛЕП] {name}\n        -> {test}\n        {why}")
            blind.append((name, test))
        if census:
            print(f"        перепись: {census_row}")
            for failed_name in shown:
                print(f"           · {failed_name}")
            if extra:
                print(f"           {extra}")
        print()

    after = porcelain()
    print("ДЕРЕВО ПОСЛЕ ПРОГОНА:")
    if after == before:
        print("  git status --porcelain совпал со снимком ДО прогона"
              + (" (дерево было чистым)" if not before
                 else " (чужие правки на месте, наших следов нет)"))
    else:
        print("  РАСХОЖДЕНИЕ со снимком ДО прогона:")
        for line in sorted(set(after.splitlines()) ^ set(before.splitlines())):
            print("    " + line)
    print()

    print("ИТОГ:")
    blind_names = {name for name, _t in blind}
    for name, test, why, census_row in rows:
        mark = "СЛЕП" if name in blind_names else "ok"
        print(f"  [{mark}] {name}")
        print(f"        {test}")
        print(f"        {why}" + (f" :: {census_row}" if census_row else ""))
    print()

    if after != before:
        print("ОТКАЗ: дерево не вернулось в исходное состояние.")
        return 1
    if blind:
        print(f"СЛЕПЫХ СТОРОЖЕЙ: {len(blind)} из {len(MUTATIONS)}")
        for name, test in blind:
            print(f"  · {name} -> {test}")
        return 1
    print(f"Все {len(MUTATIONS)} мутаций пойманы.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
