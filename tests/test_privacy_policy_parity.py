"""Сторож паритета двух языковых версий политики конфиденциальности.

Требование владельца (25.08.2026): «одинаковые §, ноль `{{` перед публикацией».

🔴 Почему пин ЛИТЕРАЛЬНЫЙ, а не выведенный из файлов.

Сторож, который сравнивает украинскую версию с русской и только, зелен по
построению: удали §7 из ОБОИХ файлов — паритет сохранён, сторож молчит, а из
политики исчезла таблица сроков хранения. Сравнение двух копий ловит расхождение
и слепо к согласованной потере.

Поэтому ожидаемый состав параграфов задан здесь СПИСКОМ и сверяется в ОБЕ
стороны: ни один параграф не может исчезнуть незаметно, и ни один не может
появиться, не будучи вписанным сюда осознанно. Красный на добавлении нового §
— это не ложное срабатывание, а требование обновить пин руками.
См. [[jarvis-literal-lists-not-introspection]].

Оговорка о происхождении: текст политики писал тот же исполнитель, что и этот
файл, — то есть сторож наследует его представление о структуре
([[jarvis-guards-not-by-the-plan-author]]). Пин снят с ТРЕБОВАНИЯ владельца
(одинаковые §, ноль плейсхолдеров), а не с содержания текста; это ослабляет
наследование, но не снимает его. Вторая пара глаз на список ниже всё ещё нужна.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
LEGAL = ROOT / "docs" / "legal"

UK = LEGAL / "privacy-policy.uk.md"
RU = LEGAL / "privacy-policy.ru.md"

# ── ЛИТЕРАЛЬНЫЙ ПИН ─────────────────────────────────────────────────────────
# Ожидаемые параграфы верхнего уровня. Меняется ТОЛЬКО руками и вместе с обоими
# текстами.
EXPECTED_SECTIONS: tuple[str, ...] = (
    "1", "2", "3", "4", "5", "6", "7", "8", "9", "10", "11",
)

# Подпараграфы, размеченные жирным (`**3.1. ...**`). Пустой кортеж означает
# «у этого § подпараграфов нет» — и это тоже утверждение, а не умолчание.
EXPECTED_SUBSECTIONS: dict[str, tuple[str, ...]] = {
    "1": (),
    "2": (),
    "3": ("3.1", "3.2", "3.3", "3.4", "3.5", "3.6"),
    "4": (),
    "5": ("5.1", "5.2", "5.3"),
    "6": (),
    "7": (),
    "8": (),
    "9": (),
    "10": (),
    "11": (),
}

# Плейсхолдеры: пары «украинское имя — русское имя». Оба обязаны стоять или
# оба быть закрыты — иначе публичный текст и рабочий разойдутся по составу
# незаполненного.
EXPECTED_PLACEHOLDER_PAIRS: tuple[tuple[str, str], ...] = (
    ("ДАТА_РЕДАКЦІЇ", "ДАТА_РЕДАКЦИИ"),
    ("ОПЕРАТОР_ПІБ", "ОПЕРАТОР_ФИО"),
    ("ОПЕРАТОР_РНОКПП", "ОПЕРАТОР_РНОКПП"),
    ("ОПЕРАТОР_АДРЕСА", "ОПЕРАТОР_АДРЕС"),
    ("ПОСИЛАННЯ_НА_УМОВИ_ANTHROPIC", "ССЫЛКА_НА_УСЛОВИЯ_ANTHROPIC"),
    ("КОНТАКТ_ДЛЯ_ЗАПИТІВ", "КОНТАКТ_ДЛЯ_ЗАПРОСОВ"),
)

# Публикуется РОВНО этот файл. Второй — рабочий и на сайт не идёт.
PUBLISHED = UK

_SECTION_RE = re.compile(r"^##\s+(\d+)\.\s+\S", re.MULTILINE)
_SUBSECTION_RE = re.compile(r"^\*\*(\d+\.\d+)\.\s", re.MULTILINE)
_PLACEHOLDER_RE = re.compile(r"\{\{([^}]+)\}\}")


def _read(path: Path) -> str:
    """Fail-closed: пропавший файл — это красное, а не «нечего проверять».

    Сторож, который при отсутствии файла тихо пропускает проверку, зелен ровно
    в тот момент, когда политику удалили ([[jarvis-write-check-file-exists]]).
    """
    if not path.exists():
        pytest.fail(f"файла политики нет: {path.relative_to(ROOT)}")
    text = path.read_text(encoding="utf-8")
    if not text.strip():
        pytest.fail(f"файл политики пуст: {path.relative_to(ROOT)}")
    return text


def _sections(text: str) -> list[str]:
    return _SECTION_RE.findall(text)


def _subsections(text: str) -> list[str]:
    return _SUBSECTION_RE.findall(text)


# ── 1. Состав параграфов против ЛИТЕРАЛЬНОГО пина ───────────────────────────
@pytest.mark.parametrize("path", [UK, RU], ids=["uk", "ru"])
def test_sections_match_the_literal_pin(path: Path) -> None:
    """Ни один § не исчезает и не появляется мимо пина — в обе стороны."""
    got = tuple(_sections(_read(path)))
    assert got == EXPECTED_SECTIONS, (
        f"{path.name}: состав параграфов разошёлся с пином.\n"
        f"  ожидалось: {EXPECTED_SECTIONS}\n"
        f"  найдено:   {got}\n"
        "Если § добавлен/удалён осознанно — обнови EXPECTED_SECTIONS и ОБА текста."
    )


@pytest.mark.parametrize("path", [UK, RU], ids=["uk", "ru"])
def test_sections_are_not_duplicated(path: Path) -> None:
    """Повтор номера ломает ссылки «см. §5» внутри самого текста."""
    got = _sections(_read(path))
    dupes = sorted({n for n in got if got.count(n) > 1})
    assert not dupes, f"{path.name}: номер параграфа повторяется: {dupes}"


# ── 2. Подпараграфы против пина ─────────────────────────────────────────────
@pytest.mark.parametrize("path", [UK, RU], ids=["uk", "ru"])
def test_subsections_match_the_literal_pin(path: Path) -> None:
    expected = tuple(
        sub for sec in EXPECTED_SECTIONS for sub in EXPECTED_SUBSECTIONS[sec]
    )
    got = tuple(_subsections(_read(path)))
    assert got == expected, (
        f"{path.name}: состав подпараграфов разошёлся с пином.\n"
        f"  ожидалось: {expected}\n"
        f"  найдено:   {got}"
    )


def test_pin_covers_every_section() -> None:
    """Пин подпараграфов обязан знать про КАЖДЫЙ параграф из пина.

    Иначе добавленный § проскочит с молчаливым «подпараграфов нет».
    """
    assert tuple(EXPECTED_SUBSECTIONS) == EXPECTED_SECTIONS, (
        "EXPECTED_SUBSECTIONS не покрывает EXPECTED_SECTIONS один в один: "
        f"{tuple(EXPECTED_SUBSECTIONS)} против {EXPECTED_SECTIONS}"
    )


# ── 3. Паритет между версиями ───────────────────────────────────────────────
def test_two_versions_have_identical_numbering() -> None:
    """То, ради чего сторож заведён: публичный текст и рабочий не расходятся."""
    uk, ru = _read(UK), _read(RU)
    assert _sections(uk) == _sections(ru), (
        "нумерация параграфов разошлась между версиями:\n"
        f"  uk: {_sections(uk)}\n  ru: {_sections(ru)}"
    )
    assert _subsections(uk) == _subsections(ru), (
        "нумерация подпараграфов разошлась между версиями:\n"
        f"  uk: {_subsections(uk)}\n  ru: {_subsections(ru)}"
    )


# ── 4. Плейсхолдеры ─────────────────────────────────────────────────────────
def test_placeholders_match_the_literal_pin() -> None:
    """Оба текста держат РОВНО известный набор незаполненного.

    Новый плейсхолдер, не вписанный сюда, — красное: значит в тексте появилось
    место, о котором владелец не знает. Пропавший — тоже красное: либо его
    заполнили (тогда пин правится), либо потеряли вместе с абзацем.
    """
    uk_found = set(_PLACEHOLDER_RE.findall(_read(UK)))
    ru_found = set(_PLACEHOLDER_RE.findall(_read(RU)))
    uk_pin = {pair[0] for pair in EXPECTED_PLACEHOLDER_PAIRS}
    ru_pin = {pair[1] for pair in EXPECTED_PLACEHOLDER_PAIRS}

    assert uk_found == uk_pin, (
        f"uk: набор плейсхолдеров разошёлся с пином.\n"
        f"  лишние:  {sorted(uk_found - uk_pin)}\n"
        f"  пропали: {sorted(uk_pin - uk_found)}"
    )
    assert ru_found == ru_pin, (
        f"ru: набор плейсхолдеров разошёлся с пином.\n"
        f"  лишние:  {sorted(ru_found - ru_pin)}\n"
        f"  пропали: {sorted(ru_pin - ru_found)}"
    )


def test_placeholders_are_closed_in_pairs() -> None:
    """Заполнить контакт по-украински и забыть по-русски — самый вероятный
    способ разойтись. Считаем именно ПАРАМИ, а не общим числом: два числа на
    одну вещь гасят друг друга ([[jarvis-two-numbers-for-one-thing]])."""
    uk_found = set(_PLACEHOLDER_RE.findall(_read(UK)))
    ru_found = set(_PLACEHOLDER_RE.findall(_read(RU)))
    broken = [
        (u, r) for u, r in EXPECTED_PLACEHOLDER_PAIRS
        if (u in uk_found) != (r in ru_found)
    ]
    assert not broken, (
        "плейсхолдер закрыт только в одной версии — пары: "
        + ", ".join(f"{u} / {r}" for u, r in broken)
    )


# ── 5. Гейт публикации ──────────────────────────────────────────────────────
# В обычном прогоне ПРОПУСКАЕТСЯ. Сегодня плейсхолдеры стоят законно, и
# постоянно красный тест — это не сторож, а фон
# ([[jarvis-gate-mutates-the-deploy-tree]]). Своего маркера намеренно не завожу:
# `markers` в pytest.ini ничем не скипается, помеченный тест всё равно поехал бы
# в суиту и добавил новое красное к эталону из 97 имён.
#
# Запускать перед публикацией ЯВНО:
#     $env:POLICY_PUBLISH_CHECK=1
#     .venv\Scripts\python.exe -m pytest tests/test_privacy_policy_parity.py -q
@pytest.mark.skipif(
    not os.getenv("POLICY_PUBLISH_CHECK"),
    reason="гейт публикации: включается POLICY_PUBLISH_CHECK=1",
)
def test_published_file_has_no_placeholders_left() -> None:
    left = sorted(set(_PLACEHOLDER_RE.findall(_read(PUBLISHED))))
    assert not left, (
        f"{PUBLISHED.name} НЕЛЬЗЯ публиковать: осталось незаполненного {len(left)} — "
        + ", ".join(left)
    )
