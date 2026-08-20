# -*- coding: utf-8 -*-
"""Одобренный владельцем текст живёт в ТРЁХ местах — сверять их обязан механизм.

Спека `docs/superpowers/specs/2026-08-20-approved-text-ast-guard.md`.

Фразы модалки `stop_all` согласованы владельцем дословно и физически лежат:

1. в коде — `app/routers/tamapi_dashboard.py`, литерал внутри f-строки;
2. в стороже — `APPROVED_1`/`APPROVED_2` в `tests/chatter/test_panels_stopall_ux.py`;
3. в спеке — §0-бис `docs/superpowers/specs/2026-08-20-panel-client-name.md`.

Сторож `test_approved_wording_is_untouched` связывает только (1) и (2). Спека
не была привязана ничем, и 20.08 три места совпали ровно потому, что человек
разобрал AST руками и посмотрел. Разовая проверка человеком — не механизм.

Сценарий отказа конкретен: фразу правят в коде, сторож краснеет, правку
переносят в `APPROVED_1` — оба зелены, а спека, то есть ТО, НА ЧТО ВЛАДЕЛЕЦ
СКАЗАЛ «ОК», тихо описывает несуществующий текст. Слово владельца перестаёт
быть привязано к чему-либо.

ПОЧЕМУ AST, А НЕ GREP. Grep не отличает код от комментария, а в
`tamapi_dashboard.py` фраза рядом ЦИТИРУЕТСЯ в комментариях к правке: вынеси
её из кода в комментарий — и `grep -c` скажет «текст на месте». Комментариев в
AST нет по определению, и эта слепота здесь — ровно то, что нужно. Плюс
`APPROVED_1` собран неявной склейкой двух литералов: `ast.literal_eval` отдаёт
склеенное значение, регулярка — нет.

Импортировать модуль сторожа ради константы нельзя: это сбор pytest'а внутри
теста. Поэтому файл РАЗБИРАЕТСЯ, а не импортируется.

ГРАНИЦЫ. Сторож не правит текст и не предлагает правку — он только фиксирует
расхождение. Менять одобренный текст по-прежнему может только слово владельца.
"""
from __future__ import annotations

import ast
import re
import shutil
from pathlib import Path
from typing import NamedTuple

import pytest

ROOT = Path(__file__).resolve().parents[1]


class Entry(NamedTuple):
    """Одна согласованная фраза и все три её места."""
    spec: str
    anchor: str
    phrase_index: int
    code: str
    guard: str
    const: str


# Литеральный реестр, а не обход каталога спек. Выведенный список согласен с
# реализацией по определению и молчит ровно там, где о фразе забыли: спека без
# якоря просто не попала бы в обход, и сторож остался бы зелёным. Список пишет
# человек — добавить фразу стоит одну строку.
REGISTRY: tuple[Entry, ...] = (
    Entry(spec="docs/superpowers/specs/2026-08-20-panel-client-name.md",
          anchor="## 0-бис.",
          phrase_index=0,
          code="app/routers/tamapi_dashboard.py",
          guard="tests/chatter/test_panels_stopall_ux.py",
          const="APPROVED_1"),
    Entry(spec="docs/superpowers/specs/2026-08-20-panel-client-name.md",
          anchor="## 0-бис.",
          phrase_index=1,
          code="app/routers/tamapi_dashboard.py",
          guard="tests/chatter/test_panels_stopall_ux.py",
          const="APPROVED_2"),
)


def _norm(s: str) -> str:
    """Схлопнуть ТОЛЬКО пробельные последовательности.

    В HTML фраза разбита переносом строки с отступом, в спеке — одной строкой.
    Больше не нормализуем НИЧЕГО: ни регистр, ни кавычки, ни `<b>` — тег часть
    согласованного текста, а «умный» апостроф вместо прямого обязан быть
    расхождением, а не сглаженной мелочью.
    """
    return re.sub(r"\s+", " ", s).strip()


def _read(path: Path, what: str) -> str:
    # Явный utf-8: `utf-8-sig` не годится, BOM в .py запрещён, а молчаливое
    # чтение системной cp1251 уже однажды сделало пойманную мутацию слепой.
    if not path.exists():
        raise LookupError("%s не найден: %s" % (what, path))
    return path.read_text(encoding="utf-8")


def phrase_from_spec(root: Path, entry: Entry) -> str:
    """Фраза №N из фенсед-блока под якорем-заголовком спеки."""
    text = _read(root / entry.spec, "файл спеки")
    lines = text.splitlines()
    start = next((i for i, ln in enumerate(lines)
                  if ln.startswith(entry.anchor)), None)
    if start is None:
        raise LookupError("якорь %r не найден в спеке %s"
                          % (entry.anchor, entry.spec))
    block: list[str] = []
    fence = False
    for ln in lines[start + 1:]:
        if ln.startswith("## "):
            break
        if ln.startswith("```"):
            if fence:
                break
            fence = True
            continue
        if fence:
            block.append(ln)
    block = [ln for ln in block if ln.strip()]
    if not block:
        raise LookupError("под якорем %r в спеке %s нет непустого кодового блока"
                          % (entry.anchor, entry.spec))
    if entry.phrase_index >= len(block):
        raise LookupError("в блоке под %r строк %d, а нужна №%d"
                          % (entry.anchor, len(block), entry.phrase_index))
    return block[entry.phrase_index]


def const_from_guard(root: Path, entry: Entry) -> str:
    """Значение модульной константы сторожа — разбором, а не импортом."""
    tree = ast.parse(_read(root / entry.guard, "файл сторожа"))
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id == entry.const:
                try:
                    value = ast.literal_eval(node.value)
                except ValueError as exc:
                    raise LookupError(
                        "%s в %s не литерал: %s"
                        % (entry.const, entry.guard, exc)) from exc
                if not isinstance(value, str):
                    raise LookupError("%s в %s не строка, а %s"
                                      % (entry.const, entry.guard, type(value)))
                return value
    raise LookupError("константы %s нет в стороже %s"
                      % (entry.const, entry.guard))


def code_string_constants(root: Path, entry: Entry) -> list[str]:
    """Все строковые константы кода, включая куски f-строк.

    Комментарии сюда не попадают по определению — это и есть предмет.
    """
    tree = ast.parse(_read(root / entry.code, "файл кода"))
    return [n.value for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and isinstance(n.value, str)]


def occurrences_in_code(root: Path, entry: Entry, phrase: str) -> int:
    needle = _norm(phrase)
    return sum(_norm(c).count(needle)
               for c in code_string_constants(root, entry))


def problems(root: Path, entry: Entry) -> list[str]:
    """Расхождения по одной записи реестра. Пустой список = три места сошлись.

    Fail-closed: каждое извлечение сначала УТВЕРЖДАЕТ факт находки. Наивное
    «сравнить то, что нашли» на переименованном якоре и удалённой константе
    сравнило бы пустоту с пустотой и было бы зелёным по построению.
    """
    out: list[str] = []
    try:
        spec_phrase = phrase_from_spec(root, entry)
    except LookupError as exc:
        return ["СПЕКА: %s" % exc]
    try:
        guard_const = const_from_guard(root, entry)
    except LookupError as exc:
        return ["СТОРОЖ: %s" % exc]

    if _norm(spec_phrase) != _norm(guard_const):
        out.append("спека и сторож разошлись:\n  спека : %r\n  сторож: %r"
                   % (spec_phrase, guard_const))
    try:
        n = occurrences_in_code(root, entry, guard_const)
    except LookupError as exc:
        return out + ["КОД: %s" % exc]
    if n == 0:
        out.append("фразы %s нет среди строковых констант %s — если она "
                   "уехала в комментарий, для клиента её больше нет: %r"
                   % (entry.const, entry.code, guard_const))
    elif n > 1:
        # Ровно один, а не «хотя бы один»: два вхождения значат, что текст
        # начали дублировать, и следующая правка поправит одно из них.
        out.append("фраза %s встречается в %s %d раза — одобренный текст "
                   "начали дублировать" % (entry.const, entry.code, n))
    return out


# ── А1: базовый зелёный ─────────────────────────────────────────────────────

@pytest.mark.parametrize("entry", REGISTRY, ids=lambda e: e.const)
def test_a1_three_places_agree(entry: Entry):
    assert problems(ROOT, entry) == []


def test_a1_the_registry_is_not_empty():
    """Пустой реестр прошёл бы все проверки выше молча."""
    assert REGISTRY, "реестр пуст — сторож зелен по построению"


# ── А2–А7: сторож обязан УМЕТЬ краснеть ─────────────────────────────────────

@pytest.fixture()
def sandbox(tmp_path):
    """Копия трёх настоящих файлов по тем же относительным путям.

    Копия, а не синтетика: сторож, проверенный на выдуманном файле, доказывает
    свойства выдуманного файла.
    """
    entry = REGISTRY[0]
    for rel in {entry.spec, entry.code, entry.guard}:
        dst = tmp_path / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / rel, dst)
    assert problems(tmp_path, entry) == [], "песочница сломана до правки"
    return tmp_path


def _patch(root: Path, rel: str, old: str, new: str) -> None:
    path = root / rel
    text = path.read_text(encoding="utf-8")
    assert old in text, "нечего править: %r нет в %s" % (old, rel)
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def test_a2_a_phrase_edited_in_the_code_turns_it_red(sandbox):
    entry = REGISTRY[0]
    _patch(sandbox, entry.code, "доки ви не увімкнете його назад.",
           "доки ви не увімкнете його знову.")
    assert problems(sandbox, entry), "правка фразы в КОДЕ прошла незамеченной"


def test_a3_a_phrase_edited_in_the_spec_turns_it_red(sandbox):
    entry = REGISTRY[0]
    _patch(sandbox, entry.spec, "Бот перестане відповідати <b>ВСІМ</b> лідам",
           "Бот перестане відповідати <b>УСІМ</b> лідам")
    assert problems(sandbox, entry), "правка фразы в СПЕКЕ прошла незамеченной"


def test_a4_an_edited_guard_constant_turns_it_red(sandbox):
    entry = REGISTRY[0]
    _patch(sandbox, entry.guard, 'APPROVED_1 = ("Бот перестане відповідати',
           'APPROVED_1 = ("Бот перестане мовчати')
    assert problems(sandbox, entry), "правка КОНСТАНТЫ прошла незамеченной"


def test_a5_a_phrase_moved_from_code_into_a_comment_turns_it_red(sandbox):
    """Главная проверка на то, что сверка идёт по AST, а не по тексту файла:
    в сыром тексте фраза осталась, а для клиента её больше нет."""
    entry = REGISTRY[0]
    path = sandbox / entry.code
    phrase = ("Бот перестане відповідати <b>ВСІМ</b> лідам, "
              "доки ви не увімкнете його назад.")
    # Из ИСПОЛНЯЕМОГО кода фраза уходит, в ТЕКСТЕ файла остаётся — ровно тот
    # случай, на котором `grep -c` скажет «текст на месте».
    _patch(sandbox, entry.code, "  <p>" + phrase + "\n", "  <p>\n")
    path.write_text("# " + phrase + "\n" + path.read_text(encoding="utf-8"),
                    encoding="utf-8")
    assert phrase in path.read_text(encoding="utf-8"), (
        "правка должна оставить фразу в тексте файла, иначе проверка "
        "вырождается в А2")
    assert problems(sandbox, entry), (
        "фраза уехала в комментарий, а сторож остался зелёным — значит он "
        "сверяет ТЕКСТ файла, а не то, что исполняется")


def test_a6_a_second_occurrence_in_the_code_turns_it_red(sandbox):
    entry = REGISTRY[0]
    path = sandbox / entry.code
    text = path.read_text(encoding="utf-8")
    text += ('\n_DUPLICATE = ("Бот перестане відповідати <b>ВСІМ</b> лідам, '
             'доки ви не увімкнете його назад.")\n')
    path.write_text(text, encoding="utf-8")
    assert problems(sandbox, entry), "второе вхождение фразы прошло незамеченным"


def test_a7_a_missing_anchor_turns_it_red(sandbox):
    """Переименованный якорь обязан быть КРАСНЫМ, а не «нечего сравнивать»."""
    entry = REGISTRY[0]._replace(anchor="## 0-трижды-бис.")
    assert problems(sandbox, entry)


def test_a7_a_missing_constant_turns_it_red(sandbox):
    entry = REGISTRY[0]._replace(const="APPROVED_НЕТУ")
    assert problems(sandbox, entry)


def test_a7_a_missing_file_turns_it_red(sandbox):
    entry = REGISTRY[0]._replace(spec="docs/нет-такого-файла.md")
    assert problems(sandbox, entry)


def test_a7_an_empty_block_under_the_anchor_turns_it_red(sandbox):
    entry = REGISTRY[0]
    path = sandbox / entry.spec
    text = path.read_text(encoding="utf-8")
    start = text.index(entry.anchor)
    fence = text.index("```", start)
    end = text.index("```", fence + 3)
    path.write_text(text[:fence] + text[end:], encoding="utf-8")
    assert problems(sandbox, entry), "пустой блок под якорем сравнили с пустотой"
