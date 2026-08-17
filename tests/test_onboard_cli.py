# -*- coding: utf-8 -*-
"""T5 арки `chatter.onboard`: командная строка (спека §0 и §6, план T5).

Сторожа написаны ОТ СПЕКИ. `chatter/onboard/__main__.py` при написании НЕ
открывался и не грепался — на T1–T4 ровно эта дисциплина дала по три-восемь
настоящих дефектов. Читались: спека, план, `brief.py`, `render.py`,
`report.py`, `checks.py`, `vocabulary.py`, `form_schema.yaml`.

**Чем CLI отличается от остальных слоёв арки.** Это ЕДИНСТВЕННАЯ дверь, через
которую человек пользуется пайплайном. Всё, что четыре предыдущих слоя честно
нашли, доезжает до человека только через код выхода и напечатанный текст —
значит здесь живут четыре класса ошибки, и каждый из них дорог по-своему:

  1. **Тихий успех.** `0` при непроведённой работе. Спека требует ТРИ кода:
     `0` зелёное, `1` красное, `2` НЕ СОСТОЯЛОСЬ. Прогон, который ничего не
     доказал (битый бриф, схема не совпала, каталога нет), обязан отличаться от
     прогона, нашедшего дефект. Склеить `1` и `2` значит принять по приёмке,
     которая не открыла ни одного файла.
  2. **Запись не туда.** Результат кладётся в `build/onboard/<slug>/`, и CLI НЕ
     ПИШЕТ в `chatter/clients/` ни при каких аргументах: боевой каталог — деньги
     клиента, там сейчас живут два бота на живых людях.
  3. **Персональные данные в репозитории.** Бриф содержит имя, контакты и
     внутренние цены клиента. `build/` обязан быть в `.gitignore` — один
     `git add .` и это уезжает в историю навсегда, откуда не отзывается.
  4. **Зелёная ширма (спека §7).** Без файла `REVIEWED` `--check` не зелёный
     (решение владельца 17.08, q3): если разделы 2 и 3 отчёта не читать,
     дефолты доедут до прода как «решения».

Плюс `--diff` (§6 шаг 3) — инструмент ПРИЁМКИ, а не украшение. Он существует,
чтобы человек классифицировал КАЖДОЕ расхождение письменно, и третьего варианта
нет. Значит расхождения обязаны быть НАЗВАНЫ (файл и что разошлось), эталон
обязан остаться нетронутым, а «расхождений нет» обязано быть СКАЗАНО словами:
молчание и отсутствие проверки должны различаться.

Настоящий бриф клиента (`.secrets/briefs/brief.xlsx`) не читается ни одной
строкой — там имя, контакты и цены живого человека. Схема-совместимый xlsx
собирается здесь же из `form_schema.yaml`: отпечатки в схеме уже нормализованы,
поэтому склеенные пробелом токены дают Jaccard 1.00 и бриф проходит сверку
схемы, ни разу не коснувшись чужих данных.
"""
from __future__ import annotations

import hashlib
import importlib
import io
import json
import os
import subprocess
import sys
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import dataclass
from pathlib import Path

import pytest
from openpyxl import Workbook

from chatter.onboard import brief as brief_mod
from chatter.onboard import checks

# Фикстура клиента берётся у сторожа T4, а не переписывается заново. Причина
# та же, по которой `vocabulary.py` один на три слоя: вторая копия «правильного
# каталога клиента» разъехалась бы с первой, и мой сторож зеленел бы на своей
# копии. Эта фикстура вдобавок ДОКАЗАНА продакшен-кодом
# (`test_golden_fixture_is_genuinely_clean`), а не заявлена.
from tests.test_onboard_checks import (  # noqa: E402
    SLUG,
    report_document,
    write_client,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = REPO_ROOT / "chatter" / "onboard" / "form_schema.yaml"
PROD_CLIENTS = REPO_ROOT / "chatter" / "clients"

ALL_CHECK_IDS = tuple(f"C{i}" for i in range(1, 16))


# ═════════════════════════════════════════════════════════════════════════════
# Запуск CLI
#
# Предпочтительно в процессе, через `main(argv)`: так тест не зависит от
# кодировки консоли Windows (cp1251 уже глушила сторожей мутационного гейта) и
# так работает monkeypatch. Подпроцесс — запасной путь, чтобы отсутствие
# `main` роняло ОДИН тест-контракт, а не превращало весь файл в тесты импорта.
# ═════════════════════════════════════════════════════════════════════════════

MAIN_MODULE = "chatter.onboard.__main__"


@dataclass
class CliRun:
    rc: int
    out: str

    def __repr__(self) -> str:  # чтобы падение показывало ЧТО напечатал CLI
        return f"CliRun(rc={self.rc}, out={self.out!r})"


def _main_callable():
    try:
        mod = importlib.import_module(MAIN_MODULE)
    except Exception:  # noqa: BLE001
        return None, None
    fn = getattr(mod, "main", None)
    return (mod, fn) if callable(fn) else (mod, None)


def run_cli(argv, *, cwd: Path | None = None) -> CliRun:
    """Один прогон CLI. Возвращает код выхода и ВЕСЬ напечатанный текст.

    stdout и stderr склеены намеренно: спека требует, чтобы красное называло
    файл и строку, и не говорит, в какой поток. Тест, придирающийся к потоку,
    сторожил бы стиль, а не поведение.
    """
    argv = [str(a) for a in argv]
    _, fn = _main_callable()
    prev = Path.cwd()
    if cwd is not None:
        os.chdir(cwd)
    try:
        if fn is not None:
            buf = io.StringIO()
            try:
                with redirect_stdout(buf), redirect_stderr(buf):
                    rc = fn(argv)
            except SystemExit as exc:
                rc = exc.code
            return CliRun(0 if rc is None else int(rc), buf.getvalue())
        env = dict(os.environ)
        env["PYTHONUTF8"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONPATH"] = str(REPO_ROOT) + os.pathsep + env.get("PYTHONPATH", "")
        env.setdefault("JARVIS_ENV", "test")
        proc = subprocess.run(
            [sys.executable, "-m", "chatter.onboard", *argv],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            env=env, cwd=str(cwd or REPO_ROOT), timeout=180,
        )
        return CliRun(proc.returncode, (proc.stdout or "") + (proc.stderr or ""))
    finally:
        os.chdir(prev)


def _argparse_rejected(run: CliRun) -> bool:
    """Аргумент не опознан парсером (а не отвергнут логикой пайплайна)."""
    low = run.out.lower()
    return run.rc != 0 and ("usage:" in low or "unrecognized arguments" in low)


def cli_at(work: Path, argv, out_dir: Path) -> CliRun:
    """Прогон, у которого каталог результата — РОВНО `out_dir`.

    Спека даёт `--out` только в строке с `--brief`, но `--check` и `--diff`
    обязаны уметь смотреть в тот же каталог, иначе их вообще невозможно навести
    на результат. Реализация вправе решить это двумя способами: явным `--out`
    либо умолчанием `build/onboard/<slug>` от текущего каталога. Помощник
    пробует первый и откатывается ко второму, а `work` всегда выбран так, что
    `work/build/onboard/<slug> == out_dir` — то есть ОБЕ формы наводятся на
    один каталог, и сторожи ниже меряют поведение, а не спор о написании флага.
    Сам факт поддержки `--out` сторожится отдельным тестом.
    """
    run = run_cli([*argv, "--out", str(out_dir)], cwd=work)
    if _argparse_rejected(run):
        run = run_cli(argv, cwd=work)
    return run


# ═════════════════════════════════════════════════════════════════════════════
# Фикстуры
# ═════════════════════════════════════════════════════════════════════════════

def _schema_headers() -> list[str]:
    schema = brief_mod.load_schema(SCHEMA_PATH)
    by_col = {f["col"]: f for f in schema["fields"].values()}
    width = max(by_col) + 1
    return [" ".join(by_col[c]["fingerprint"]) if c in by_col else "" for c in range(width)]


def write_xlsx(path: Path, headers: list[str], answers: list) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    wb = Workbook()
    ws = wb.active
    ws.append(headers)
    ws.append(answers)
    wb.save(path)
    wb.close()
    return path


@pytest.fixture()
def matching_brief(tmp_path) -> Path:
    """Бриф, сходящийся со схемой по шапке, но пустой по смыслу.

    Схема совпала — значит прогон ДОЙДЁТ до генерации и до записи, и сторожа
    записи не проходят вхолостую на «бриф не разобрался». Ответы намеренно
    мусорные: собирать содержательный бриф из 57 колонок дорого, а все сторожа
    здесь про КОД ВЫХОДА и МЕСТО ЗАПИСИ, а не про качество генерации.
    """
    headers = _schema_headers()
    return write_xlsx(tmp_path / "briefs" / "form.xlsx", headers, ["тест"] * len(headers))


@pytest.fixture()
def mismatching_brief(tmp_path) -> Path:
    """Бриф, у которого шапка не отвечает ни одному отпечатку схемы."""
    headers = [f"невідоме питання номер {i}" for i in range(len(_schema_headers()))]
    return write_xlsx(tmp_path / "briefs" / "alien.xlsx", headers, ["значення"] * len(headers))


@pytest.fixture()
def client(tmp_path):
    """Каталог клиента в `build/onboard/<slug>/` + согласованный `report.json`.

    Возвращает `(work, out_dir)`: `work` — каталог, от которого умолчание
    `build/onboard/<slug>` даёт `out_dir`.
    """
    out = write_client(tmp_path)
    (out / "report.json").write_text(
        json.dumps(report_document(), ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "REPORT.md").write_text("# Звіт\n\nрозділ 1\n", encoding="utf-8")
    return tmp_path, out


def snapshot(root: Path) -> dict[str, str]:
    """Содержимое дерева по байтам. Имя файла → sha256."""
    if not root.exists():
        return {}
    return {
        str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*")) if p.is_file()
    }


def client_files_in(path: Path) -> list[str]:
    return sorted(n for n in checks.CLIENT_FILES if (path / n).exists())


def result(cid: str, *, ok: bool = True, blocked: bool = False):
    return checks.CheckResult(id=cid, ok=ok, is_flag=cid in checks.FLAG_IDS,
                              message=f"фикстура {cid}", blocked=blocked)


def patch_run_checks(monkeypatch, results):
    """Подменяет автоприёмку заранее известным вердиктом.

    Зачем подмена. Довести настоящий каталог до ЧЕТЫРНАДЦАТИ зелёных в tmp
    невозможно по конструкции: C7 требует контакт `<id>:<slug>` в боевом
    `DRILL_CONTACTS`, и выдуманный slug там не появится никогда (замерено:
    золотой клиент даёт ровно одно красное — C7). Значит проверить обе стороны
    метки вычитки на живых проверках нельзя в принципе, а проверить их надо:
    это прямое решение владельца. Подменяется РОВНО источник вердикта, всё
    остальное в CLI остаётся настоящим.

    Патчатся обе точки: атрибут модуля `checks` (если CLI зовёт
    `checks.run_checks`) и одноимённое имя внутри `__main__` (если CLI сделал
    `from ... import run_checks`). Одна точка из двух дала бы сторожа, зелёного
    от того, что подмена не сработала.
    """
    def fake(client_dir, report_document, *, slug):  # noqa: ARG001
        return list(results)

    monkeypatch.setattr(checks, "run_checks", fake, raising=True)
    mod, _ = _main_callable()
    if mod is not None and hasattr(mod, "run_checks"):
        monkeypatch.setattr(mod, "run_checks", fake, raising=True)
    return fake


# ═════════════════════════════════════════════════════════════════════════════
# 0. ДВЕРЬ СУЩЕСТВУЕТ
# ═════════════════════════════════════════════════════════════════════════════

def test_the_cli_module_exists_and_exposes_main():
    """Ловит: арку, у которой нет двери.

    Спека §0 обещает `python -m chatter.onboard`. `main(argv) -> int` сверх
    того — требование ТЕСТИРУЕМОСТИ, зафиксированное консервативно: логика
    кодов выхода, запертая внутри `if __name__ == "__main__"`, проверяется
    только подпроцессом, то есть через консольную кодировку Windows, которая в
    этом проекте уже глушила сторожей (cp1251 не знает байт 0x98). Цена
    отсутствия — сторожа, зелёные из-за мусора в декодировании.
    """
    mod = importlib.import_module(MAIN_MODULE)
    fn = getattr(mod, "main", None)
    assert callable(fn), (
        f"{MAIN_MODULE} не отдаёт вызываемый `main(argv)` — коды выхода "
        f"проверяемы только через консоль подпроцесса")


def test_check_accepts_an_explicit_output_directory(client):
    """Ловит: `--check`, который умеет смотреть только в один зашитый каталог.

    §6 шаг 6 прямо требует прогнать `--check` по КАТАЛОГУ, названному человеком
    (калибровка C9–C14 на ручном эталоне). Без явного указания каталога этот
    шаг приёмки невыполним.

    ⚠️ Зафиксировано консервативно: спека пишет `--out` только в строке с
    `--brief`. Если реализация выбрала другое написание — это находка про
    интерфейс, а не про безопасность; тест назван так, чтобы падение читалось
    именно так.
    """
    work, out = client
    run = run_cli([SLUG, "--check", "--out", str(out)], cwd=work)
    assert not _argparse_rejected(run), (
        f"`--check --out <каталог>` не опознан парсером — §6 шаг 6 (калибровка "
        f"на ручном эталоне) выполнить нечем: {run}")


# ═════════════════════════════════════════════════════════════════════════════
# 1. ТРИ КОДА ВЫХОДА, И ОСОБЕННО ГРАНИЦА 1 ПРОТИВ 2
#
# «Нашли дефект» и «ничего не проверили» — разные события. Склеенные в один
# ненулевой код, они дают человеку право пожать плечами: «ну красное и красное».
# Склеенные с нулём — принятую приёмку, не открывшую ни одного файла.
# ═════════════════════════════════════════════════════════════════════════════

def test_a_brief_that_does_not_exist_is_two(tmp_path):
    """Ловит: тихий ноль (или traceback) на отсутствующем файле брифа.

    Опечатка в пути — самая частая ошибка человека за клавиатурой. `0` здесь
    означал бы «клиент собран» при не открытом брифе; `1` — «нашли дефект»,
    хотя не смотрели.
    """
    run = run_cli([SLUG, "--brief", str(tmp_path / "нет-такого.xlsx"),
                   "--out", str(tmp_path / "build" / "onboard" / SLUG)])
    assert run.rc == 2, f"брифа нет, а код {run.rc}: {run}"
    assert "Traceback" not in run.out, (
        f"вместо диагностики напечатан traceback — человек читает его как "
        f"поломку инструмента, а не как «поправь путь»: {run}")


def test_a_file_that_is_not_a_workbook_is_two_not_a_traceback(tmp_path):
    """Ловит: непойманное исключение openpyxl (DEV-18: не глотать и не ронять).

    Человек показывает `.csv`, переименованный в `.xlsx`, или недокачанный
    файл. Прогон не состоялся — код `2`, а не стек вызовов на экран.
    """
    fake = tmp_path / "briefs" / "form.xlsx"
    fake.parent.mkdir(parents=True, exist_ok=True)
    fake.write_text("это не книга, это текст", encoding="utf-8")
    run = run_cli([SLUG, "--brief", str(fake),
                   "--out", str(tmp_path / "build" / "onboard" / SLUG)])
    assert run.rc == 2, f"бриф не книга, а код {run.rc}: {run}"
    assert "Traceback" not in run.out, f"traceback вместо диагностики: {run}"


def test_a_schema_mismatch_is_two_and_prints_the_map(tmp_path, mismatching_brief):
    """Ловит: тихий фолбэк на несовпавшей схеме (спека §1.1, риск §7).

    Форму правят, схему забывают. Молча угадать колонку значит собрать клиенту
    прайс ИЗ ЧУЖОГО СТОЛБЦА, и узнаем мы об этом от лида. Поэтому мало кода
    `2` — обязана быть КАРТА: какая колонка, чего ждали, что нашли. Без карты
    на сверку формы со схемой уходит вечер сличения глазами.
    """
    run = run_cli([SLUG, "--brief", str(mismatching_brief),
                   "--out", str(tmp_path / "build" / "onboard" / SLUG)])
    assert run.rc == 2, f"схема не совпала, а код {run.rc}: {run}"
    low = run.out.lower()
    assert "колонка" in low or "колонк" in low, (
        f"схема не совпала, а расхождение не названо по колонкам: {run}")
    assert "ожидали" in low or "очікува" in low, (
        f"в карте не сказано, ЧЕГО ждали — сверять не с чем: {run}")
    assert "нашли" in low or "знайшл" in low, (
        f"в карте не сказано, что НАШЛИ в брифе: {run}")


def test_a_failed_brief_run_leaves_no_half_written_client(tmp_path, matching_brief):
    """Ловит: полуготовый каталог клиента после несостоявшегося прогона.

    Шапка сошлась, значит прогон дошёл до генерации и упал уже на смысле
    (прайса в мусорном брифе нет). Если часть файлов при этом легла на диск,
    следующий `--check` увидит «каталог есть» и станет судить огрызок — а
    человек унесёт огрызок в боевой каталог. Либо все пять файлов, либо ни
    одного.
    """
    out = tmp_path / "build" / "onboard" / SLUG
    run = run_cli([SLUG, "--brief", str(matching_brief), "--out", str(out)])
    assert run.rc != 0, (
        f"бриф без прайса, а прогон объявлен зелёным — клиент, собранный без "
        f"цен, будет выдумывать их лиду: {run}")
    assert client_files_in(out) == [], (
        f"после несостоявшегося прогона на диске остались {client_files_in(out)}")


def test_check_on_a_missing_client_directory_is_two_not_zero(tmp_path):
    """Ловит: первую форму тихого успеха — «проверять было нечего → всё в порядке».

    `checks.verdict` уже отвечает `2` на отсутствующий каталог; сторожится
    ровно то, что CLI это ДОНЁС, а не перевёл в `0` по дороге.
    """
    ghost = tmp_path / "build" / "onboard" / SLUG
    run = cli_at(tmp_path, [SLUG, "--check"], ghost)
    assert run.rc == 2, (
        f"каталога клиента нет, а код {run.rc}. `0` здесь означал бы «приёмка "
        f"прошла», не открыв ни одного файла: {run}")


def test_check_without_a_report_json_is_two_not_zero(client):
    """Ловит: приёмку по отчёту, которого нет.

    C10 держит парность «заглушка в knowledge ⇔ строка раздела 3 отчёта», C4
    сверяет список эскалации со списком отчёта. Без отчёта эти проверки не
    состоялись — а «не состоялись» и «претензий нет» обязаны различаться кодом.
    Цена ошибки: зелёная приёмка клиента, у которого раздел 3 никто не собирал.
    """
    work, out = client
    (out / "report.json").unlink()
    run = cli_at(work, [SLUG, "--check"], out)
    assert run.rc == 2, f"отчёта нет, а код {run.rc}: {run}"


def test_check_with_a_broken_report_json_is_two(client):
    """Ловит: битый отчёт, проглоченный как пустой.

    `{}` вместо отчёта неотличим от «раздел 3 пуст», и C10 зазеленела бы на
    пустоте. Прогон не доказал ничего — код `2`.
    """
    work, out = client
    (out / "report.json").write_text("{ это не json", encoding="utf-8")
    run = cli_at(work, [SLUG, "--check"], out)
    assert run.rc == 2, f"отчёт не разбирается, а код {run.rc}: {run}"


def test_check_on_a_defective_client_is_one_not_two(client):
    """Ловит: РАЗМЫТИЕ ГРАНИЦЫ 1/2 — «нашли дефект» выданное за «не смогли».

    Обратная сторона предыдущих двух тестов, и без неё они бессмысленны: код,
    возвращающий `2` на всё подряд, прошёл бы их все. Здесь проверки ОТРАБОТАЛИ
    и нашли настоящее красное (замерено: у золотого клиента это C7 — дрил-
    сценария нет). Такой прогон обязан быть `1`: он доказал дефект.
    """
    work, out = client
    (out / checks.REVIEWED_FILENAME).write_text("вичитано\n", encoding="utf-8")
    run = cli_at(work, [SLUG, "--check"], out)
    assert run.rc == 1, (
        f"проверки отработали и нашли красное, а код {run.rc}. `2` здесь "
        f"стирает разницу между «дефект» и «прогон не состоялся»: {run}")


def test_a_red_check_names_the_culprit_and_not_only_its_own_number(client):
    """Ловит: красное, которое не будет прочитано.

    «C7: проверка не прошла» заставляет искать руками; через неделю такое
    красное начинают пролистывать, и приёмка превращается в ритуал. Спека §4
    требует прямо: красное называет МЕСТО (файл, по возможности строку) и
    виновника, а не только собственный номер.
    """
    work, out = client
    (out / checks.REVIEWED_FILENAME).write_text("вичитано\n", encoding="utf-8")
    run = cli_at(work, [SLUG, "--check"], out)
    assert run.rc == 1
    located = str(out) in run.out or any(n in run.out for n in checks.CLIENT_FILES)
    assert located, (
        f"красное не называет ни каталога, ни файла — место дефекта придётся "
        f"искать руками: {run}")


# ═════════════════════════════════════════════════════════════════════════════
# 2. БОЕВОЙ КАТАЛОГ — ЭТО ДЕНЬГИ КЛИЕНТА
#
# `chatter/clients/` читает живой раннер двух ботов на живых людях. Спека §0 и
# §6 говорят одно и то же дважды: результат кладём в `build/onboard/<slug>/`,
# боевой каталог в приёмке ТОЛЬКО ЧИТАЕТСЯ. Ошибка здесь не «тест покраснел»,
# а испорченный конфиг у клиента, который платит.
# ═════════════════════════════════════════════════════════════════════════════

def test_an_output_path_inside_chatter_clients_writes_nothing_there(tmp_path, matching_brief):
    """Ловит: `--out`, ведущий в боевой каталог, исполненный молча.

    Путь подсунут ровно тот, который человек напишет по ошибке или по инерции
    («сразу к клиенту, чего два раза копировать»). Перенос делает ЧЕЛОВЕК после
    вычитки — это записано в спеке дважды.
    """
    prod_like = tmp_path / "chatter" / "clients" / SLUG
    prod_like.mkdir(parents=True)
    (prod_like / "knowledge.md").write_text("# бойовий файл\n", encoding="utf-8")
    before = snapshot(prod_like)

    run = run_cli([SLUG, "--brief", str(matching_brief), "--out", str(prod_like)])

    assert run.rc != 0, (
        f"запись в боевой каталог объявлена успешной: {run}")
    assert snapshot(prod_like) == before, (
        f"CLI изменил каталог, выглядящий как боевой: было {sorted(before)}, "
        f"стало {sorted(snapshot(prod_like))}")


def test_the_production_target_is_refused_as_a_target_not_by_accident(tmp_path, matching_brief):
    """Ловит: ОТСУТСТВИЕ сторожа, замаскированное совпадением.

    Предыдущий тест пройдёт и у CLI, который про `chatter/clients` не знает
    ничего: мусорный бриф всё равно упадёт на генерации, и в боевом каталоге
    ничего не появится — вхолостую, по случайности. Здесь тот же бриф
    запускается ДВАЖДЫ, в обычный каталог и в боевой, и из вывода вычёркивается
    сам путь. Если оба прогона отвечают одним и тем же — про боевой каталог CLI
    не знает, и первый сторож зелен по совпадению. Ровно этот класс («зелёное
    по случайности») уже ловили на T2.
    """
    normal = tmp_path / "build" / "onboard" / SLUG
    prod_like = tmp_path / "chatter" / "clients" / SLUG
    prod_like.mkdir(parents=True)

    a = run_cli([SLUG, "--brief", str(matching_brief), "--out", str(normal)])
    b = run_cli([SLUG, "--brief", str(matching_brief), "--out", str(prod_like)])

    scrub = lambda text, path: text.replace(str(path), "<OUT>").replace(  # noqa: E731
        str(path).replace("\\", "/"), "<OUT>")
    assert (b.rc, scrub(b.out, prod_like)) != (a.rc, scrub(a.out, normal)), (
        "прогон в боевой каталог неотличим от обычного — значит `--out` не "
        f"сторожится вовсе, а пустой боевой каталог получился по случайности "
        f"(обычный: {a}; боевой: {b})")
    assert "clients" in b.out.lower(), (
        f"отказ не называет запретную цель — человек не поймёт, что именно "
        f"нельзя, и повторит: {b}")


def test_the_live_client_tree_is_byte_identical_after_a_full_run(tmp_path, matching_brief):
    """Ловит: любую запись в НАСТОЯЩИЙ `chatter/clients` (спека §6: только чтение).

    Здесь снимается не выдуманный, а живой каталог репозитория — тот самый, из
    которого поднимаются клиенты. `--diff` наводится на боевой эталон, как и
    предписывает §6 шаг 3, и после трёх прогонов дерево обязано совпасть
    побайтно. Цена ошибки — испорченный конфиг у платящего клиента и разбор
    того, что именно уехало.
    """
    before = snapshot(PROD_CLIENTS)
    assert before, f"боевое дерево {PROD_CLIENTS} не найдено — сторож ничего не меряет"

    out = tmp_path / "build" / "onboard" / SLUG
    run_cli([SLUG, "--brief", str(matching_brief), "--out", str(out)], cwd=tmp_path)
    cli_at(tmp_path, [SLUG, "--check"], out)
    cli_at(tmp_path, [SLUG, "--diff", str(PROD_CLIENTS / "yarina")], out)

    after = snapshot(PROD_CLIENTS)
    assert after == before, (
        "боевое дерево клиентов изменилось после прогона CLI. Появилось: "
        f"{sorted(set(after) - set(before))}; исчезло: "
        f"{sorted(set(before) - set(after))}; изменилось: "
        f"{sorted(k for k in set(after) & set(before) if after[k] != before[k])}")


# ═════════════════════════════════════════════════════════════════════════════
# 3. ПЕРСОНАЛЬНЫЕ ДАННЫЕ КЛИЕНТА НЕ УЕЗЖАЮТ В ИСТОРИЮ
#
# В `build/onboard/<slug>/` лежит `brief.json` с именем, контактами и
# внутренними ценами живого человека. Утечка в публичную историю не
# отзывается никогда — ровно поэтому в этом репозитории уже стоит сплошная
# маска на `.env.*` и `requisites.yaml`. Тест дешёвый, ошибка дорогая.
# ═════════════════════════════════════════════════════════════════════════════

def test_gitignore_has_a_rule_for_build():
    """Ловит: отсутствие строки `build/` в `.gitignore` (замерено 17.08: её НЕТ)."""
    lines = [ln.strip() for ln in (REPO_ROOT / ".gitignore").read_text(
        encoding="utf-8").splitlines()]
    assert any(ln.rstrip("/") in ("build", "/build") for ln in lines if not ln.startswith("#")), (
        "в .gitignore нет правила на `build/`, а туда кладётся brief.json с "
        "именем, контактами и внутренними ценами клиента — один `git add .` и "
        "это в истории навсегда")


def test_git_really_ignores_a_generated_brief():
    """Ловит: правило, которое есть, но не работает.

    Строку `build/` можно написать и тут же обезвредить более поздним
    отрицанием или неверным якорем пути. Спрашиваем не файл, а сам git —
    «два числа на одну вещь» здесь означало бы, что мы сверяем текст правила
    вместо его действия.
    """
    probe = "build/onboard/detailpro/brief.json"
    proc = subprocess.run(["git", "check-ignore", "-q", probe],
                          cwd=str(REPO_ROOT), capture_output=True, text=True)
    assert proc.returncode == 0, (
        f"git не считает `{probe}` игнорируемым (rc={proc.returncode}) — "
        f"персональные данные клиента попадут в индекс при `git add .`")


# ═════════════════════════════════════════════════════════════════════════════
# 4. МЕТКА ВЫЧИТКИ (решение владельца 17.08, вопрос 3 спеки)
#
# Причина в §7: отчёт может стать зелёной ширмой. Если разделы 2 и 3 не читать,
# дефолты доедут до прода как «решения владельца», ими не будучи. Метку ставит
# ЧЕЛОВЕК, файлом, руками.
#
# Обе стороны проверяются отдельно и обязательно: «без метки не зелёный» без
# «с меткой зелёный» описывает и код, который не зеленеет никогда, — а
# проверка, которая не может стать зелёной, будет обойдена в первый же вечер.
# ═════════════════════════════════════════════════════════════════════════════

def test_all_green_without_the_reviewed_mark_is_one(client, monkeypatch):
    """Ловит: зелёную ширму — прямое решение владельца 17.08 (q3)."""
    work, out = client
    patch_run_checks(monkeypatch, [result(cid) for cid in ALL_CHECK_IDS])
    assert not (out / checks.REVIEWED_FILENAME).exists()
    run = cli_at(work, [SLUG, "--check"], out)
    assert run.rc == 1, (
        f"все проверки зелёные, отчёт не вычитан, а код {run.rc}. `0` здесь и "
        f"есть та ширма, ради которой метку завели: {run}")


def test_all_green_with_the_reviewed_mark_is_zero(client, monkeypatch):
    """Ловит: `--check`, который не зеленеет НИКОГДА.

    Вторая сторона решения. Без неё предыдущий тест проходит и у кода,
    возвращающего `1` всегда, — а такой инструмент человек через неделю
    перестанет запускать.
    """
    work, out = client
    patch_run_checks(monkeypatch, [result(cid) for cid in ALL_CHECK_IDS])
    (out / checks.REVIEWED_FILENAME).write_text("вичитав, Владислав\n", encoding="utf-8")
    run = cli_at(work, [SLUG, "--check"], out)
    assert run.rc == 0, (
        f"всё зелёное и метка вычитки стоит, а код {run.rc}: {run}")


def test_the_reviewed_mark_does_not_paint_over_a_red(client, monkeypatch):
    """Ловит: метку, превращённую в «принять всё».

    Метка говорит «я прочитал разделы 2 и 3», а не «дефектов нет». Если она
    гасит красное, человек получил кнопку «принять» на непочиненном клиенте.
    """
    work, out = client
    results = [result(cid) for cid in ALL_CHECK_IDS]
    results[1] = result("C2", ok=False)      # цена не обеспечена — настоящее красное
    patch_run_checks(monkeypatch, results)
    (out / checks.REVIEWED_FILENAME).write_text("вичитано\n", encoding="utf-8")
    run = cli_at(work, [SLUG, "--check"], out)
    assert run.rc == 1, f"красное погашено меткой вычитки, код {run.rc}: {run}"


def test_the_reviewed_mark_does_not_paint_over_a_check_that_never_ran(client, monkeypatch):
    """Ловит: метку, гасящую «не состоялось».

    Самый дорогой вариант предыдущего класса: проверка не отработала вовсе, а
    подпись человека выдаёт прогон за состоявшийся. Код обязан остаться `2` —
    непроведённая проверка не становится проведённой оттого, что отчёт прочли.
    """
    work, out = client
    results = [result(cid) for cid in ALL_CHECK_IDS]
    results[6] = result("C7", ok=False, blocked=True)
    patch_run_checks(monkeypatch, results)
    (out / checks.REVIEWED_FILENAME).write_text("вичитано\n", encoding="utf-8")
    run = cli_at(work, [SLUG, "--check"], out)
    assert run.rc == 2, (
        f"проверка не состоялась, но метка вычитки дала код {run.rc}: {run}")


def test_the_mark_is_read_from_the_client_directory_only(client, monkeypatch):
    """Ловит: метку, найденную НЕ ТАМ (слишком широкий поиск).

    `REVIEWED`, положенный рядом — в `build/onboard/` — относится к другому
    прогону или ни к чему. Метка одного клиента, зачитанная за другого, — это
    подпись человека под текстом, которого он не видел. Имя файла берётся из
    `checks.REVIEWED_FILENAME`, а не пишется здесь руками: два написания одного
    имени и есть «два числа на одну вещь».
    """
    work, out = client
    patch_run_checks(monkeypatch, [result(cid) for cid in ALL_CHECK_IDS])
    (out.parent / checks.REVIEWED_FILENAME).write_text("чужа мітка\n", encoding="utf-8")
    run = cli_at(work, [SLUG, "--check"], out)
    assert run.rc == 1, (
        f"метка лежит в {out.parent}, а не в каталоге клиента, но прогон "
        f"объявлен вычитанным (код {run.rc}): {run}")


def test_a_rebuild_over_the_reviewed_mark_is_refused(tmp_path, matching_brief):
    """Ловит: подпись человека, унаследованную ЧУЖИМИ файлами.

    Метка говорит «я прочитал разделы 2 и 3 ЭТОГО отчёта». Пересборка меняет
    файлы под ней, и следующий `--check` отвечает `0` — зелёное на тексте,
    которого никто не видел. Это ровно та ширма, ради которой метку и завели
    (решение владельца q3), только заходящая с другой стороны: не «забыли
    метку», а «метка пережила свой отчёт».

    Меряется и последствие, и ПРИЧИНА, и второе здесь не украшение. Мусорный
    бриф фикстуры не собирается и сам по себе (прайса в нём нет), поэтому один
    только код выхода одинаков с меткой и без неё — то есть ничего не
    различает: замер показал `2` в обоих случаях. Различает ровно причина:
    отказ обязан назвать МЕТКУ и выход («сними сознательно»), а не отчитаться
    о прайсе, до которого прогон не имеет права дойти.
    """
    out = tmp_path / "build" / "onboard" / SLUG
    out.mkdir(parents=True)
    (out / "knowledge.md").write_text("# Стара збірка\n", encoding="utf-8")
    (out / checks.REVIEWED_FILENAME).write_text("вичитав, Владислав\n", encoding="utf-8")
    before = snapshot(out)

    run = cli_at(tmp_path, [SLUG, "--brief", str(matching_brief)], out)

    assert run.rc == 2, (
        f"пересборка поверх метки вычитки обязана быть «не состоялось», "
        f"а код {run.rc}: {run}")
    assert snapshot(out) == before, (
        "каталог под меткой вычитки изменился — подпись человека теперь стоит "
        "под файлами, которых он не видел")
    assert checks.REVIEWED_FILENAME in run.out, (
        f"отказ не назвал метку вычитки — человек прочитает его как дефект "
        f"брифа и пойдёт чинить не то: {run}")


# ═════════════════════════════════════════════════════════════════════════════
# 5. `--diff` — ИНСТРУМЕНТ ПРИЁМКИ (спека §6 шаги 3–4)
#
# Он существует ради одного: человек классифицирует КАЖДОЕ расхождение
# письменно — баг пайплайна, решение человека или улучшение эталона. Третьего
# варианта нет. Отсюда три требования, и все три сторожатся ниже:
#   · расхождение НАЗВАНО (файл и что разошлось), а не сведено к числу;
#   · эталон остался НЕТРОНУТЫМ;
#   · «расхождений нет» СКАЗАНО словами — молчание и отсутствие проверки
#     обязаны различаться.
# ═════════════════════════════════════════════════════════════════════════════

MARK_BUILD = "МАРКЕР-ЗБІРКИ-77"
MARK_REF = "МАРКЕР-ЕТАЛОНА-88"


@pytest.fixture()
def pair(tmp_path):
    """Две одинаковые копии каталога клиента: сборка и эталон."""
    build = write_client(tmp_path)
    ref = tmp_path / "reference" / SLUG
    ref.mkdir(parents=True)
    for name in checks.CLIENT_FILES:
        (ref / name).write_bytes((build / name).read_bytes())
    return tmp_path, build, ref


def mentions(text: str, name: str) -> list[str]:
    """Строки вывода, где назван файл `name`.

    Сравнивается СТРОКА, а не факт присутствия имени. Имя файла печатается и
    при полном совпадении — в сводке по файлам, — и `name in вывод` истинно
    всегда. Такой сторож зеленел бы, ничего не проверив: ровно эта форма
    зелёной ширмы уже стоила арке отдельного разбора. А вот СТРОКА про
    совпавший файл и строка про разошедшийся обязаны различаться — иначе
    расхождение и совпадение выглядят на экране одинаково, и классифицировать
    (§6 шаг 4) человеку нечего.
    """
    return [ln.strip() for ln in text.splitlines() if name in ln]


def diverge(build: Path, ref: Path, name: str) -> None:
    (build / name).write_text(
        (build / name).read_text(encoding="utf-8") + f"\n{MARK_BUILD}\n", encoding="utf-8")
    (ref / name).write_text(
        (ref / name).read_text(encoding="utf-8") + f"\n{MARK_REF}\n", encoding="utf-8")


def test_identical_directories_are_green(pair):
    """Ловит: `--diff`, который считает расхождением любую мелочь.

    Опорная точка для всех тестов ниже: если совпадающие каталоги дают
    ненулевой код, «расхождений нет» недостижимо и приёмка §6 не закрывается
    никогда.
    """
    work, build, ref = pair
    run = cli_at(work, [SLUG, "--diff", str(ref)], build)
    assert run.rc == 0, f"каталоги совпадают, а код {run.rc}: {run}"


def test_no_divergences_is_spoken_not_silent(pair):
    """Ловит: пустой вывод на совпавших каталогах.

    Тот же инвариант, что у блока флагов отчёта: молчание и отсутствие проверки
    обязаны различаться. Пустой экран человек читает как «`--diff` не
    отработал» — и приёмка §6 шаг 3 остаётся невыполненной при зелёном коде.
    """
    work, build, ref = pair
    run = cli_at(work, [SLUG, "--diff", str(ref)], build)
    assert run.out.strip(), (
        f"на совпавших каталогах CLI не сказал ничего (rc={run.rc}) — "
        f"неотличимо от «сравнение не выполнялось»")


def test_no_divergences_says_so_in_words(pair):
    """Ловит: «отработал, но не сказал ЧТО» — вывод без вердикта.

    ⚠️ Ожидаемая формулировка зафиксирована списком допустимых вариантов, а не
    одной строкой: спека не диктует слова, она диктует смысл. Падение этого
    теста при зелёном соседе означает спор о формулировке, а не дефект.
    """
    work, build, ref = pair
    run = cli_at(work, [SLUG, "--diff", str(ref)], build)
    low = run.out.lower()
    # Корни, а не полные формы: спека диктует смысл, а не слова, и требовать
    # одну формулировку значило бы сторожить стиль. Ровно та же логика, по
    # которой R6 берёт запрещённые термины полными фразами, здесь работает
    # наоборот: там ложное срабатывание стоит ответа лиду, здесь — ничего.
    spoken = ("розходжень немає", "немає розходжень", "расхождений нет",
              "нет расхождений", "різниць немає", "различий нет",
              "розійшлися 0", "разошлись 0", "збіг", "совпал",
              "identical", "no differences")
    assert any(s in low for s in spoken), (
        f"вердикт «расхождений нет» не произнесён; вывод: {run.out!r}")


def test_a_divergence_is_not_green(pair):
    """Ловит: тихий успех приёмки — расхождения найдены и объявлены нормой.

    §6 шаг 4 требует классифицировать каждое расхождение письменно. Зелёный код
    снимает этот шаг: прогон выглядит принятым, и никто не откроет вывод.
    """
    work, build, ref = pair
    diverge(build, ref, "knowledge.md")
    run = cli_at(work, [SLUG, "--diff", str(ref)], build)
    assert run.rc != 0, f"расхождение найдено, а прогон зелёный: {run}"


def test_a_divergence_uses_the_red_code(pair):
    """Ловит: расхождение, выданное за «не состоялось» (или иной код).

    Спека §0: кодов ровно три и они одинаковы для всех команд. Сравнение
    ОТРАБОТАЛО и нашло разницу — это `1`. ⚠️ Требование зафиксировано
    консервативно: спека не проговаривает код `--diff` отдельной строкой.
    Отдельный тест от соседа выше — чтобы падение читалось как выбор кода, а не
    как дыра в безопасности.
    """
    work, build, ref = pair
    diverge(build, ref, "knowledge.md")
    run = cli_at(work, [SLUG, "--diff", str(ref)], build)
    assert run.rc == 1, f"расхождение дало код {run.rc}, ожидался 1: {run}"


def test_every_diverging_file_is_named(pair):
    """Ловит: отчёт о расхождениях, свёрнутый к первому файлу или к числу.

    «Расхождений: 3» не даёт классифицировать НИ ОДНО — а классифицировать
    обязаны каждое (§6 шаг 4). Разошлись три файла из пяти, и все три обязаны
    быть названы ИМЕННО КАК РАЗОШЕДШИЕСЯ.

    Сравнение идёт с ХОЛОСТЫМ прогоном по тем же каталогам, а не с пустотой.
    Причина: имя файла может печататься всегда — в сводке, в шапке, в перечне
    сравненного. Тогда `"knowledge.md" in вывод` истинно и при полном
    совпадении, и сторож зеленеет, ничего не проверив. Требуется РОСТ
    упоминаний относительно совпавшего прогона, и он невозможен без того, что
    файл назван в расхождении.
    """
    work, build, ref = pair
    diverged = ("knowledge.md", "playbook.md", "examples.yaml")
    baseline = cli_at(work, [SLUG, "--diff", str(ref)], build).out
    for name in diverged:
        diverge(build, ref, name)
    run = cli_at(work, [SLUG, "--diff", str(ref)], build)
    for name in diverged:
        assert mentions(run.out, name) != mentions(baseline, name), (
            f"файл {name} разошёлся, но говорится о нём ровно то же, что при "
            f"полном совпадении ({mentions(run.out, name)}) — расхождение и "
            f"совпадение выглядят одинаково: {run}")
    for name in ("persona.md", "settings.yaml"):
        assert mentions(run.out, name) == mentions(baseline, name), (
            f"файл {name} НЕ расходился, но о нём сказано иначе, чем при "
            f"совпадении — сравнение шумит, и настоящие расхождения утонут "
            f"среди ложных: {run}")


def test_the_diff_says_what_diverged_inside_the_file(pair):
    """Ловит: список имён файлов вместо содержания расхождения.

    Имени файла мало: пять файлов клиента — это тысячи строк, и «knowledge.md
    отличается» отправляет человека сличать глазами (ровно тот вечер, ради
    отмены которого арка и пишется). Обязано быть видно, ЧТО разошлось.
    """
    work, build, ref = pair
    diverge(build, ref, "knowledge.md")
    run = cli_at(work, [SLUG, "--diff", str(ref)], build)
    assert MARK_BUILD in run.out or MARK_REF in run.out, (
        f"названо только имя файла; разошедшийся текст ({MARK_BUILD!r} против "
        f"{MARK_REF!r}) в выводе не показан: {run}")


def test_a_file_missing_from_the_build_is_named(pair):
    """Ловит: пропажу целого файла, не замеченную сравнением.

    Ненаписанный `examples.yaml` — это молча уехавший голос персоны. Сравнение,
    которое ходит только по файлам сборки, объявит такой прогон совпавшим:
    обойти нечего — значит расхождений нет.
    """
    work, build, ref = pair
    baseline = cli_at(work, [SLUG, "--diff", str(ref)], build).out
    (build / "examples.yaml").unlink()
    run = cli_at(work, [SLUG, "--diff", str(ref)], build)
    assert run.rc != 0, f"файла нет в сборке, а `--diff` зелёный: {run}"
    assert mentions(run.out, "examples.yaml") != mentions(baseline, "examples.yaml"), (
        f"о пропавшем файле сказано ровно то же, что о совпавшем "
        f"({mentions(run.out, 'examples.yaml')}) — пропажа неотличима от "
        f"совпадения: {run}")


def test_a_file_missing_from_the_reference_is_named(pair):
    """Ловит: зеркальную слепоту — лишний файл в сборке, выпавший из вывода.

    Обратная сторона предыдущего теста: сравнение, которое ходит только по
    файлам эталона, не заметит, что генератор написал лишнее. Оба списка
    обязаны быть пройдены, иначе целый файл исчезает из приёмки бесследно.

    ⚠️ Код выхода здесь НЕ требуется красным, и это осознанное послабление, а
    не забытая проверка. В `build/onboard/<slug>/` по конструкции живут файлы,
    которых в ручном эталоне нет и быть не может: `brief.json`, `report.json`,
    `REPORT.md`, заготовка дрила. Требование «лишний файл = красное» сделало бы
    `--diff` красным ВСЕГДА и у всех клиентов — а сигнал, всегда красный при
    законной работе, это фон, а не сторож. Поэтому обязательное здесь ровно
    одно: файл НАЗВАН, и человек классифицирует его сам (§6 шаг 4).
    """
    work, build, ref = pair
    (build / "drill.yaml").write_text("steps: []\n", encoding="utf-8")
    run = cli_at(work, [SLUG, "--diff", str(ref)], build)
    assert "drill.yaml" in run.out, (
        f"файла нет в эталоне, и в выводе он не назван — целый файл выпал из "
        f"приёмки: {run}")


def test_the_reference_is_left_untouched(pair):
    """Ловит: «сравнение», которое правит эталон.

    §6 говорит прямо: боевой каталог только читается, ничего не мержим.
    Улучшение эталона — отдельное решение человека и отдельный коммит, а не
    побочный эффект приёмки. Снимается байтовый слепок ДО и ПОСЛЕ, плюс состав
    каталога: подложенный `.orig` или `REVIEWED` — та же правка эталона.
    """
    work, build, ref = pair
    for name in ("knowledge.md", "playbook.md"):
        diverge(build, ref, name)
    before = snapshot(ref)
    run = cli_at(work, [SLUG, "--diff", str(ref)], build)
    after = snapshot(ref)
    assert after == before, (
        f"эталон изменён сравнением (rc={run.rc}). Появилось: "
        f"{sorted(set(after) - set(before))}; изменилось: "
        f"{sorted(k for k in set(after) & set(before) if after[k] != before[k])}")


def test_the_build_is_left_untouched_by_diff(pair):
    """Ловит: `--diff`, подтягивающий сборку под эталон.

    Сравнение — чтение. Правка сборки «чтобы сошлось» уничтожает улику: второй
    прогон покажет совпадение, и настоящее расхождение исчезнет незамеченным.
    """
    work, build, ref = pair
    diverge(build, ref, "knowledge.md")
    before = snapshot(build)
    cli_at(work, [SLUG, "--diff", str(ref)], build)
    assert snapshot(build) == before, "`--diff` изменил каталог сборки"


def test_a_missing_reference_is_two_not_no_divergences(pair):
    """Ловит: САМЫЙ дорогой вариант тихого успеха у `--diff`.

    Эталона нет — сравнивать не с чем. Наивная реализация обойдёт пустой
    список файлов, не найдёт ни одного расхождения и скажет «расхождений нет».
    Это ноль, за которым не стоит ни одного сравнения, а приёмка арки (§6 шаг
    3) будет считаться пройденной.
    """
    work, build, _ = pair
    run = cli_at(work, [SLUG, "--diff", str(work / "нет-такого-эталона")], build)
    assert run.rc == 2, (
        f"эталона нет, а код {run.rc}. `0` здесь означает «сравнили с пустотой "
        f"и всё сошлось»: {run}")


def test_a_missing_build_directory_is_two_for_diff(pair):
    """Ловит: ту же слепоту с другой стороны — нечего сравнивать со стороны сборки.

    Прогон генерации не состоялся, каталога нет, а `--diff` отвечает «всё
    совпало». Класс тот же: пустой обход выдан за успешную приёмку.
    """
    work, _, ref = pair
    ghost = work / "build" / "onboard" / "нет-такого-клиента"
    run = cli_at(work, ["нет-такого-клиента", "--diff", str(ref)], ghost)
    assert run.rc == 2, f"каталога сборки нет, а код {run.rc}: {run}"
