# -*- coding: utf-8 -*-
"""T5 — командная строка пайплайна онбординга: сборка, автоприёмка, `--diff`.

Спека: `docs/superpowers/specs/2026-08-14-chatter-onboarding-pipeline.md`
(§0 «Состав модуля» — три команды и коды выхода, §4 — автоприёмка,
§6 — приёмка арки, §7 — риск «зелёной ширмы»).
План:  `docs/superpowers/plans/2026-08-17-onboard-pipeline-plan.md` (T5).

    python -m chatter.onboard <slug> --brief <файл.xlsx> [--out build/onboard/<slug>]
    python -m chatter.onboard <slug> --check [DIR] [--out ...]
    python -m chatter.onboard <slug> --diff <каталог-эталона> [--out ...]

КОДЫ ВЫХОДА — три, и третий существует не для красоты:

    0  зелёное;
    1  красное — прогон СОСТОЯЛСЯ и нашёл дефект;
    2  не состоялось — битый бриф, схема не совпала с формой, каталога нет,
       проверка упала внутри себя. Прогон, который ничего не доказал, обязан
       отличаться от прогона, который нашёл дефект: иначе «починил и стало
       зелено» неотличимо от «перестало запускаться».

ГРАНИЦА МОДУЛЯ. Здесь нет ни одного правила формата, ни одного правила
приёмки и ни одной копии словаря. Всё это живёт в `brief/render/report/checks`,
а CLI только склеивает их и решает, куда писать. Своя копия любого из этих
правил разъехалась бы с оригиналом молча — и зеленела бы ровно тогда, когда
разъехалась.

── КУДА CLI ПИШЕТ И КУДА НЕ ПИШЕТ НИКОГДА ──────────────────────────────────
Результат кладётся в `build/onboard/<slug>/`. Боевой `chatter/clients/<slug>` —
деньги клиента; перенос делает человек после вычитки. Поэтому запись туда
запрещена ФИЗИЧЕСКИ (`_assert_writable`), при любых аргументах, а не «по
договорённости»: `--out chatter/clients/yarina` — это одна опечатка, после
которой живой клиент подменён сгенерированным черновиком, и узнаем мы об этом
от лида.

`--check` и `--diff` не пишут вообще ничего: `--check` ходит в том числе по
боевому каталогу (спека §6 шаг 6, калибровка), а эталон в `--diff` только
читается.

── РЕШЕНИЯ, ГДЕ СПЕКА МОЛЧАЛА (каждое названо здесь и в отчёте прогона) ──
* **Сборка не выносит вердикт приёмки.** `--brief` даёт 0, если артефакты
  собраны, и 2, если собрать не удалось; 1 не возвращает никогда. Вердикт
  «зелёное/красное» — работа `--check`, и она печатается сводкой в конце
  сборки вместе с прямым указанием запустить `--check`. Сборка, возвращающая 1
  из-за отсутствующей метки вычитки, возвращала бы 1 ВСЕГДА (метку ставят
  после сборки) — то есть код перестал бы что-либо значить.
* **Автоприёмка внутри сборки всё же прогоняется — ради блока ФЛАГИ отчёта.**
  C12/C13 по спеке живут в отчёте (§3), а собрать их можно только по готовым
  файлам. Отсюда две записи отчёта: первая (`flags=None`) даёт `report.json`,
  на котором стоят C4 и C10, вторая переписывает его же с флагами. Разница
  между `flags=None` и `flags=[]` контрактная: «проверки не запускались» и
  «проверки прошли, флагов нет» — разные вещи.
* **Метка вычитки блокирует ПЕРЕСБОРКУ.** Если в каталоге лежит `REVIEWED`,
  `--brief` отказывается (rc 2). Пересборка поверх метки оставила бы метку
  прочитанной вчера на файлах, собранных сегодня, — то есть зелёный `--check`
  на невычитанном отчёте, ровно та ширма из §7. Метку снимает человек.
* **`--diff` печатает расхождения ЦЕЛИКОМ, без обрезки.** Приёмка арки требует
  классифицировать КАЖДОЕ расхождение письменно (§6 шаг 4); обрезанный вывод
  превращает «классифицировано всё» в «классифицировано то, что поместилось».
  Числа идут отдельной сводкой сверху, чтобы объём был виден до чтения.
* **`--diff` даёт rc 1 при любом расхождении** (и 0 при полном совпадении):
  расхождение — это работа для человека, а не «зелёное». Отсутствие каталога
  (нашего или эталона) — 2.
* **Дрил-сценарий генерируется здесь же** (решение владельца q1 от 17.08:
  «заготовку генерируем, человек правит»). Пишется ДО автоприёмки — C7 смотрит
  на файл в каталоге, и после неё было бы поздно. Контакт НЕ выдумывается:
  берётся из канона `DRILL_CONTACTS` по суффиксу клиента. Нет его там — значит
  дрил-контакт клиенту ещё не заводили, сценария не будет, и C7 скажет об этом
  вслух. Сочинить адресат платного прогона — это отправить реплики стенда
  живому человеку.
"""
from __future__ import annotations

import argparse
import difflib
import json
import sys
from pathlib import Path

from chatter.onboard.brief import SchemaMismatch, load_schema, parse_brief
from chatter.onboard.checks import (
    CHECK_IDS, REVIEWED_FILENAME, is_reviewed, run_checks, verdict)
from chatter.onboard.render import FILE_NAMES, RenderError, render_all
from chatter.onboard.drill_scenario import (
    DrillScenarioError, SCENARIO_FILE_NAME, build_scenario, render_yaml,
)
from chatter.onboard.report import build_report, write_report
from chatter.payments.drill_gate import DRILL_CONTACTS

# Коды выхода — те же, что у `checks.verdict`; имена здесь для читаемости.
RC_GREEN, RC_RED, RC_NOT_RUN = 0, 1, 2

PACKAGE_DIR = Path(__file__).resolve().parent
REPO_ROOT = PACKAGE_DIR.parents[1]
SCHEMA_PATH = PACKAGE_DIR / "form_schema.yaml"
BUILD_ROOT = REPO_ROOT / "build" / "onboard"

BRIEF_FILENAME = "brief.json"

PREFIX = "[onboard]"


# ─────────────────────────────────────────────────────────────────────────────
# Вывод
# ─────────────────────────────────────────────────────────────────────────────

def _setup_stdout() -> None:
    """Консоль в UTF-8. Под Windows дефолт — cp1251, и первая же украинская
    цитата в сообщении проверки убила бы прогон исключением кодека: инструмент
    падал бы не потому, что нашёл дефект, а потому, что не смог о нём сказать.
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError, OSError):
            # Поток может быть подменён (pytest capture, пайп) — это не отказ
            # инструмента, и падать здесь не на чем. Молчание намеренное и
            # ограничено ровно настройкой кодировки.
            pass


def _say(text: str = "") -> None:
    print(text)


def _fail(text: str) -> None:
    print(text, file=sys.stderr)


# ─────────────────────────────────────────────────────────────────────────────
# Пути
# ─────────────────────────────────────────────────────────────────────────────

def _default_out(slug: str) -> Path:
    return BUILD_ROOT / slug


def _resolve(path_like) -> Path:
    return Path(path_like).expanduser().resolve()


def _is_clients_path(path: Path) -> bool:
    """Лежит ли путь внутри каталога боевых клиентов — ЛЮБОГО дерева.

    Сравнение по составу пути, а не по `relative_to(REPO_ROOT/chatter/clients)`:
    живое дерево (`C:\\jarvis`), worktree и чужая копия — разные корни, а
    `chatter/clients` в них означает одно и то же. Проверка, привязанная к
    одному корню, пропустила бы ровно тот случай, ради которого написана:
    запуск из worktree с `--out C:\\jarvis\\chatter\\clients\\yarina`.
    """
    parts = [p.casefold() for p in path.parts]
    return any(parts[i] == "chatter" and parts[i + 1] == "clients"
               for i in range(len(parts) - 1))


def _assert_writable(out_dir: Path) -> None:
    if _is_clients_path(out_dir):
        raise _Refused(
            f"{PREFIX} отказ: {out_dir} лежит в chatter/clients — боевой каталог "
            f"клиента. Пайплайн туда не пишет НИ ПРИ КАКИХ аргументах: перенос "
            f"делает человек после вычитки отчёта. Собери в build/onboard/<slug>.")


class _Refused(Exception):
    """Отказ CLI: прогон НЕ СОСТОЯЛСЯ (rc 2), а не «нашли дефект»."""


# ─────────────────────────────────────────────────────────────────────────────
# Автоприёмка: прогон и печать
# ─────────────────────────────────────────────────────────────────────────────

def _load_report_document(client_dir: Path):
    """`report.json` рядом с файлами — или None с громкой причиной.

    None здесь не «ничего страшного»: на отчёте стоят C4 и C10, и без него они
    станут `blocked`, а вердикт — 2. Поэтому причина печатается, а не глотается
    (DEV-18).
    """
    path = client_dir / "report.json"
    if not path.is_file():
        return None, f"{path.name} рядом с файлами нет"
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return None, f"{path.name} не читается или не разбирается: {exc}"
    if not isinstance(document, dict):
        return None, f"{path.name}: ожидался словарь, получено {type(document).__name__}"
    return document, None


def _print_checks(results) -> None:
    _say("АВТОПРИЁМКА C1–C14")
    for r in results:
        if r.blocked:
            mark = "??"
        elif r.ok:
            mark = "OK"
        elif r.is_flag:
            mark = "⚠️ "
        else:
            mark = "!!"
        where = ""
        if r.file:
            where = f" [{r.file}:{r.line}]" if r.line else f" [{r.file}]"
        _say(f"  {mark} {r.id:<4}{where} {r.message}")
    green = sum(1 for r in results if r.ok and not r.blocked)
    flags = sum(1 for r in results if r.is_flag and not r.ok and not r.blocked)
    red = sum(1 for r in results if not r.ok and not r.is_flag and not r.blocked)
    blocked = sum(1 for r in results if r.blocked)
    _say(f"  ИТОГО: зелёных {green}, красных {red}, флагов {flags}, "
         f"не состоялось {blocked} (проверок {len(results)} из {len(CHECK_IDS)})")


def _flag_rows(results) -> list[dict]:
    """C12/C13 → строки блока «ФЛАГИ» отчёта.

    Вопрос владельцу дописывается здесь, потому что он один и тот же для всех
    срабатываний проверки, а сообщение проверки уже несёт файл, строку и цитату.
    Флаг без вопроса — это утверждение «тут что-то не так», на которое нечего
    ответить.
    """
    questions = {
        "C12": "законная формулировка клиента или обещание за третье лицо?",
        "C13": "одна сущность или разные — противоречие или нет?",
    }
    rows = []
    for r in results:
        if not r.is_flag or r.ok or r.blocked:
            continue
        rows.append({
            "check": r.id,
            "file": r.file,
            "line": r.line,
            "quote": r.message,
            "question": questions.get(r.id, "решает владелец"),
        })
    return rows


# ─────────────────────────────────────────────────────────────────────────────
# Режим 1: сборка из брифа
# ─────────────────────────────────────────────────────────────────────────────

def _write_client_files(out_dir: Path, files: dict) -> None:
    """Пять файлов клиента. UTF-8 задан явно: дефолт Windows — cp1251, и
    украинский knowledge молча превратился бы в исключение или в мусор."""
    for name in FILE_NAMES:
        (out_dir / name).write_text(files[name], encoding="utf-8")


def cmd_build(slug: str, brief_path: Path, out_dir: Path) -> int:
    _assert_writable(out_dir)

    if not brief_path.is_file():
        raise _Refused(f"{PREFIX} брифа нет: {brief_path} — разбирать нечего")

    if is_reviewed(out_dir):
        raise _Refused(
            f"{PREFIX} отказ: в {out_dir} стоит метка вычитки {REVIEWED_FILENAME}. "
            f"Пересборка оставила бы метку, поставленную на ПРОШЛЫЕ файлы, — то "
            f"есть зелёный --check на невычитанном отчёте. Сними метку "
            f"сознательно и повтори.")

    # ── бриф ───────────────────────────────────────────────────────────────
    # `SchemaMismatch` (схема не совпала) уходит наверх со своей картой. Всё
    # остальное, чем может ответить чтение чужого файла, ловится ЗДЕСЬ и
    # становится тем же «не состоялось»: `openpyxl` на не-xlsx поднимает
    # `InvalidFileException`, битый архив — `BadZipFile`, и оба они ровно
    # «битый бриф» из спеки, а не «нашли дефект в конфиге клиента». Без этого
    # прогон падал бы трассировкой и кодом 1 — то есть врал бы кодом.
    schema = load_schema(SCHEMA_PATH)
    try:
        brief = parse_brief(brief_path, schema)
    except SchemaMismatch:
        raise
    except Exception as exc:                              # noqa: BLE001
        raise _Refused(
            f"{PREFIX} бриф {brief_path.name} не разбирается: "
            f"{type(exc).__name__}: {exc}") from exc
    _say(f"{PREFIX} бриф {brief_path.name}: схема v{brief.get('schema_version')}, "
         f"полей {len(brief.get('fields') or {})}")

    # ── генерация ──────────────────────────────────────────────────────────
    render_result = render_all(brief, slug=slug)   # RenderError → rc 2 выше

    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / BRIEF_FILENAME).write_text(
        json.dumps(brief, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    _write_client_files(out_dir, render_result.files)

    # ── отчёт, заход 1: без флагов ─────────────────────────────────────────
    # `flags=None` означает «проверки не запускались», и ровно это пока правда:
    # проверять было нечего до записи файлов.
    first = build_report(brief, render_result, slug=slug, flags=None)
    write_report(first, out_dir)

    # ── заготовка дрил-сценария (решение владельца q1 от 17.08) ────────────
    # Пишется ДО автоприёмки: C7 проверяет файл в каталоге, и после неё было бы
    # поздно. Контакт НЕ выдумывается — берётся из канона `DRILL_CONTACTS` по
    # суффиксу клиента. Нет его там — значит дрил-контакт этому клиенту ещё не
    # заводили, и сочинять его нельзя: реплики стенда уехали бы живому
    # человеку. Тогда сценария просто нет, C7 покраснеет и скажет, чего не
    # хватает, — это честнее, чем заготовка с придуманным адресатом.
    drill_written = None
    contact = next((c for c in sorted(DRILL_CONTACTS) if c.endswith(f":{slug}")), None)
    if contact is None:
        _say(f"{PREFIX} дрил-сценарий НЕ собран: в DRILL_CONTACTS нет контакта "
             f"вида «<id>:{slug}». Заведи его в ОБЕ копии канона "
             f"(payments/drill_gate.py и scripts/drill_reset.py) и пересобери — "
             f"выдумывать адресат платного прогона нельзя")
    else:
        try:
            scenario = build_scenario(brief, render_result, first.document,
                                      slug=slug, contact=contact)
            drill_written = out_dir / SCENARIO_FILE_NAME
            drill_written.write_text(render_yaml(scenario), encoding="utf-8")
        except DrillScenarioError as exc:
            # Громко и без падения всего прогона: остальные артефакты собраны,
            # и отчёт владельцу нужнее, чем отсутствующая заготовка.
            drill_written = None
            _say(f"{PREFIX} дрил-сценарий НЕ собран: {exc}")

    # ── автоприёмка по готовому каталогу ───────────────────────────────────
    results = run_checks(out_dir, first.document, slug=slug)

    # ── отчёт, заход 2: с флагами C12/C13 ──────────────────────────────────
    flags = _flag_rows(results)
    final = build_report(brief, render_result, slug=slug, flags=flags)
    md_path, json_path = write_report(final, out_dir)

    _say(f"{PREFIX} собрано в {out_dir}")
    for name in FILE_NAMES:
        _say(f"    {name}")
    _say(f"    {BRIEF_FILENAME}")
    _say(f"    {md_path.name}")
    _say(f"    {json_path.name}")
    if drill_written is not None:
        _say(f"    {drill_written.name}  (ЗАГОТОВКА дрила — вычитать до платного прогона)")

    counters = final.document.get("counters") or {}
    _say(f"{PREFIX} счётчики артефакта: " +
         ", ".join(f"{k}={v}" for k, v in counters.items()))

    # Спека §7: счётчики РАЗДЕЛОВ печатаются в конце прогона — отчёт, разделы 2
    # и 3 которого никто не открыл, доставляет дефолты в прод как «решения».
    sections = final.document.get("sections") or {}
    _say(f"{PREFIX} отчёт: раздел 1 «взято из брифа» — {len(sections.get('taken') or [])} строк, "
         f"раздел 2 «подставлено дефолтом» — {len(sections.get('defaulted') or [])}, "
         f"раздел 3 «в брифе нет» — {len(sections.get('missing') or [])}, "
         f"флагов — {len(final.document.get('flags') or [])}")
    dropped = list(render_result.dropped or [])
    if dropped:
        _say(f"{PREFIX} НЕ выпущено наружу: {len(dropped)} — "
             f"{', '.join(sorted({str(d.get('kind')) for d in dropped}))} "
             f"(подробности в разделе 3 отчёта)")
    _say(f"{PREFIX} ПРОЧИТАЙ разделы 2 и 3 в {md_path.name} глазами, потом поставь "
         f"метку: `{REVIEWED_FILENAME}` в {out_dir}")

    _say()
    _print_checks(results)
    rc_check = verdict(results, reviewed=is_reviewed(out_dir))
    _say(f"{PREFIX} вердикт автоприёмки на этих файлах: rc {rc_check} "
         f"(сборка своего кода не выносит — прогони "
         f"`python -m chatter.onboard {slug} --check`)")
    return RC_GREEN


# ─────────────────────────────────────────────────────────────────────────────
# Режим 2: автоприёмка
# ─────────────────────────────────────────────────────────────────────────────

def cmd_check(slug: str, client_dir: Path) -> int:
    if not client_dir.is_dir():
        raise _Refused(
            f"{PREFIX} каталога {client_dir} нет — проверять нечего. Это не "
            f"«четырнадцать красных»: красное утверждает, что проверка "
            f"отработала и нашла дефект.")

    document, why = _load_report_document(client_dir)
    if why:
        # Громко: без отчёта C4 и C10 станут `blocked`, вердикт — 2, и человек
        # обязан знать, что дело в отсутствующем отчёте, а не в файлах клиента.
        _say(f"{PREFIX} ВНИМАНИЕ: {why} — C4 и C10 опираются на отчёт и не "
             f"состоятся (это ожидаемо при калибровке на ручном эталоне, §6 шаг 6)")

    _say(f"{PREFIX} автоприёмка каталога {client_dir} (только чтение), slug={slug}")
    results = run_checks(client_dir, document, slug=slug)
    _print_checks(results)

    reviewed = is_reviewed(client_dir)
    if not reviewed:
        _say(f"{PREFIX} метки вычитки {REVIEWED_FILENAME} в каталоге НЕТ — "
             f"зелёным прогон не будет (решение владельца 17.08): отчёт, "
             f"который не читали, становится зелёной ширмой над дефолтами")
    rc = verdict(results, reviewed=reviewed)
    label = {RC_GREEN: "зелёное", RC_RED: "красное",
             RC_NOT_RUN: "не состоялось"}.get(rc, "?")
    _say(f"{PREFIX} rc {rc} ({label})")
    return rc


# ─────────────────────────────────────────────────────────────────────────────
# Режим 3: --diff, приёмка арки (спека §6 шаг 3)
# ─────────────────────────────────────────────────────────────────────────────
#
# Это инструмент ЧЕЛОВЕКА, а не машины: по нему владелец классифицирует каждое
# расхождение как баг пайплайна / решение человека / улучшение эталона.
# Поэтому вывод — сводка числами плюс сами расхождения по файлам, а не дамп.

def _read(path: Path) -> tuple[list[str] | None, str | None]:
    if not path.is_file():
        return None, "файла нет"
    try:
        return path.read_text(encoding="utf-8").splitlines(), None
    except (OSError, UnicodeDecodeError) as exc:
        return None, f"не читается: {exc}"


def _compare(ref_lines: list[str], our_lines: list[str]) -> dict:
    sm = difflib.SequenceMatcher(None, ref_lines, our_lines, autojunk=False)
    same = sum(b.size for b in sm.get_matching_blocks())
    chunks = sum(1 for tag, *_ in sm.get_opcodes() if tag != "equal")
    return {
        "ref": len(ref_lines),
        "our": len(our_lines),
        "same": same,
        "ref_only": len(ref_lines) - same,
        "our_only": len(our_lines) - same,
        "chunks": chunks,
        "ratio": sm.ratio(),
    }


def cmd_diff(slug: str, our_dir: Path, ref_dir: Path) -> int:
    if not our_dir.is_dir():
        raise _Refused(
            f"{PREFIX} сгенерированного каталога {our_dir} нет — сравнивать не с "
            f"чем. Сначала: python -m chatter.onboard {slug} --brief <файл.xlsx>")
    if not ref_dir.is_dir():
        raise _Refused(f"{PREFIX} каталога эталона {ref_dir} нет — сравнивать не с чем")

    _say(f"{PREFIX} --diff: сгенерированное {our_dir}")
    _say(f"{' ' * len(PREFIX)}      эталон       {ref_dir} (ТОЛЬКО ЧТЕНИЕ)")
    _say(f"{PREFIX} в расхождениях: «-» строка эталона, «+» строка сгенерированного")
    _say()

    rows: list[tuple[str, dict | None, str | None]] = []
    diffs: list[tuple[str, list[str]]] = []
    for name in FILE_NAMES:
        ref_lines, ref_err = _read(ref_dir / name)
        our_lines, our_err = _read(our_dir / name)
        if ref_err or our_err:
            note = "; ".join(
                p for p in (f"эталон: {ref_err}" if ref_err else None,
                            f"наше: {our_err}" if our_err else None) if p)
            rows.append((name, None, note))
            continue
        stats = _compare(ref_lines, our_lines)
        rows.append((name, stats, None))
        if stats["chunks"]:
            diffs.append((name, list(difflib.unified_diff(
                ref_lines, our_lines,
                fromfile=f"эталон/{name}", tofile=f"наше/{name}",
                lineterm="", n=2))))

    # ── сводка числами ─────────────────────────────────────────────────────
    _say("СВОДКА ПО ФАЙЛАМ")
    _say(f"  {'файл':<16}{'эталон':>8}{'наше':>7}{'совпало':>9}"
         f"{'нет у нас':>11}{'лишнее':>8}{'кусков':>8}  сходство")
    total = {"ref": 0, "our": 0, "same": 0, "ref_only": 0, "our_only": 0, "chunks": 0}
    identical, differing, unusable = 0, 0, 0
    for name, stats, note in rows:
        if stats is None:
            unusable += 1
            _say(f"  {name:<16} — {note}")
            continue
        for key in total:
            total[key] += stats[key]
        if stats["chunks"]:
            differing += 1
        else:
            identical += 1
        _say(f"  {name:<16}{stats['ref']:>8}{stats['our']:>7}{stats['same']:>9}"
             f"{stats['ref_only']:>11}{stats['our_only']:>8}{stats['chunks']:>8}"
             f"{stats['ratio']:>10.0%}")
    _say(f"  ИТОГО: файлов {len(FILE_NAMES)} — совпали целиком {identical}, "
         f"разошлись {differing}, не сравнились {unusable}; "
         f"строк совпало {total['same']} из {total['ref']} эталонных "
         f"(нет у нас {total['ref_only']}, лишних у нас {total['our_only']}), "
         f"кусков расхождения {total['chunks']}")

    # Файлы, которых нет в списке пяти, называются вслух: молчание о них
    # неотличимо от «их там нет».
    extra_ours = sorted(p.name for p in our_dir.iterdir()
                        if p.is_file() and p.name not in FILE_NAMES)
    extra_ref = sorted(p.name for p in ref_dir.iterdir()
                       if p.is_file() and p.name not in FILE_NAMES)
    if extra_ours:
        _say(f"  не сравнивались (артефакты прогона у нас): {', '.join(extra_ours)}")
    if extra_ref:
        _say(f"  не сравнивались (лишние файлы эталона): {', '.join(extra_ref)}")

    _say()
    _say("Каждое расхождение ниже классифицируется письменно (спека §6 шаг 4), "
         "третьего варианта нет:")
    _say("  1) баг пайплайна → чиним код;")
    _say("  2) решение человека (падеж, имя, honesty, что спросить у клиента) → "
         "уезжает в разделы 2 и 3 отчёта;")
    _say("  3) улучшение ручного варианта → правим эталон ОТДЕЛЬНЫМ коммитом, не молча.")

    for name, lines in diffs:
        _say()
        _say(f"── {name} " + "─" * max(0, 68 - len(name)))
        for line in lines:
            _say(line)

    if unusable:
        # Файл, который не прочитался, — это не «расхождение», это несравнение.
        raise _Refused(
            f"{PREFIX} {unusable} файл(ов) сравнить не удалось (см. сводку) — "
            f"сравнение неполное, и выдавать его за приёмку нельзя")
    if differing:
        _say()
        _say(f"{PREFIX} rc 1: расхождений {total['chunks']} в {differing} файл(ах) — "
             f"каждое требует письменной классификации")
        return RC_RED
    _say()
    _say(f"{PREFIX} rc 0: сгенерированное совпало с эталоном построчно")
    return RC_GREEN


# ─────────────────────────────────────────────────────────────────────────────
# Разбор аргументов
# ─────────────────────────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m chatter.onboard",
        description="Пайплайн онбординга клиента: бриф (xlsx) → каталог + отчёт.",
        epilog="Коды выхода: 0 зелёное, 1 красное, 2 прогон не состоялся.",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("slug", help="slug клиента, например yarina")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--brief", metavar="ФАЙЛ.XLSX",
                      help="собрать каталог и отчёт из брифа")
    mode.add_argument("--check", nargs="?", const="", metavar="КАТАЛОГ",
                      help="только автоприёмка C1–C14 по готовому каталогу "
                           "(по умолчанию build/onboard/<slug>)")
    mode.add_argument("--diff", metavar="КАТАЛОГ",
                      help="пофайловое сравнение с ручным эталоном (приёмка арки, §6)")
    parser.add_argument("--out", metavar="КАТАЛОГ", default=None,
                        help="куда класть/где искать результат "
                             "(по умолчанию build/onboard/<slug>)")
    return parser


def main(argv: list[str] | None = None) -> int:
    _setup_stdout()
    parser = build_parser()
    args = parser.parse_args(argv)          # ошибка разбора → argparse даёт rc 2

    slug = args.slug
    out_dir = _resolve(args.out) if args.out else _default_out(slug)

    try:
        if args.brief is not None:
            return cmd_build(slug, _resolve(args.brief), out_dir)

        if args.diff is not None:
            return cmd_diff(slug, out_dir, _resolve(args.diff))

        # --check: каталог можно дать значением («--check chatter/clients/yarina»,
        # спека §6 шаг 6) или через --out. Даны оба и разные — это НЕ повод
        # выбрать один молча: прогон пошёл бы не по тому каталогу, о котором
        # думает человек.
        target = args.check or None
        if target and args.out and _resolve(target) != out_dir:
            raise _Refused(
                f"{PREFIX} --check {target} и --out {args.out} указывают на разные "
                f"каталоги — какой из них проверять, решает человек, а не CLI")
        return cmd_check(slug, _resolve(target) if target else out_dir)

    except _Refused as exc:
        _fail(str(exc))
        return RC_NOT_RUN
    except SchemaMismatch as exc:
        # Схема не совпала с формой = прогон НЕ СОСТОЯЛСЯ, и в тексте уже лежит
        # карта расхождений (колонка, ожидали, нашли, совпадение). Молча угадать
        # колонку значит собрать клиенту прайс из чужого столбца.
        _fail(str(exc))
        return RC_NOT_RUN
    except RenderError as exc:
        # Генератор упал на своём же правиле (R1/R2/R6/R7/R8) — файлов на диске
        # нет, доказано ничего не было. Это «не состоялось», а не «красное»:
        # красное означало бы, что артефакт есть и в нём найден дефект.
        _fail(f"{PREFIX} генерация не состоялась: {exc}")
        return RC_NOT_RUN
    except OSError as exc:
        _fail(f"{PREFIX} файловая операция не удалась: {exc}")
        return RC_NOT_RUN
    except Exception:                                     # noqa: BLE001
        # Падение самого инструмента — это «не состоялось», а не «нашли дефект»:
        # артефакта нет, доказано ничего не было. Трассировка печатается
        # ЦЕЛИКОМ (DEV-18: не глотать) — код 2 говорит владельцу, что прогон не
        # считается, трассировка говорит разработчику, где чинить.
        import traceback
        traceback.print_exc()
        _fail(f"{PREFIX} прогон упал внутри себя — см. трассировку выше; "
              f"это rc «не состоялось», а не найденный дефект")
        return RC_NOT_RUN


if __name__ == "__main__":
    sys.exit(main())
