# -*- coding: utf-8 -*-
"""Сторожа DEV-59, часть 1: ПРИЗНАК `ps_console`.

Что здесь стережётся: `declares_console_utf8` и `strip_ps_comments` из
`app/services/task_encoding.py` (контракт §3, §3.1, §3.2, §3.3; спека §4).

Написано ОТ СПЕКИ И КОНТРАКТА, ДО и БЕЗ просмотра реализации: дерево автора
кода не открывалось, диффы его ветки не смотрелись. Иначе сторож и код
унаследуют одно неверное допущение и оба будут зелёными на неверном.

Первый прогон ОБЯЗАН быть красным: этих имён в дереве сторожей ещё нет.

Ловушки контракта, ради которых файл и существует:

  №1  Признак обязан смотреть на ПРАВУЮ часть присваивания. Иначе объявление
      кодировки в cp1251 (`GetEncoding(1251)`) пройдёт как защита.
  №2  Слепота к комментариям нужна ДВАЖДЫ — и в поиске присваивания, и в
      поиске первой печати. У `ops_watchdog_detached.ps1` «печать» текстом
      стоит на строке 17, а `param(` — на 20; читай сторож комментарии
      буквально, требование «объявить раньше печати» стало бы НЕВЫПОЛНИМЫМ,
      и правильная правка выглядела бы как красная.
"""
from __future__ import annotations

import pytest

from app.services import task_encoding as mod


def _need(name):
    """Достать имя из модуля или упасть ГРОМКО, назвав контракт.

    Через `getattr`, а не через `from ... import`: отсутствующее имя обязано
    ронять ОДИН тест, а не сбор всего файла. «Не смогли проверить» не имеет
    права выглядеть как «нечего проверять».
    """
    obj = getattr(mod, name, None)
    if obj is None:
        pytest.fail(
            "контракт DEV-59 §2: в app/services/task_encoding.py нет `%s`"
            % name)
    return obj


def _declares(source):
    fn = _need("declares_console_utf8")
    out = fn(source)
    assert isinstance(out, bool), (
        "контракт §3: `declares_console_utf8` отвечает одним bool, "
        "получено %r" % (out,))
    return out


# ─────────────────────────────────────────────────────────────────────────────
# §3 — правая часть. Четыре живущие в репозитории формы + `Text.` без `System.`
# ─────────────────────────────────────────────────────────────────────────────

ACCEPTED_RIGHT = [
    "[System.Text.UTF8Encoding]::new($false)",
    "[System.Text.UTF8Encoding]::new()",
    "[Text.Encoding]::UTF8",
    "[System.Text.Encoding]::UTF8",
    # «`Text.UTF8Encoding` без `System.` — тоже засчитывается: PowerShell так
    # разрешает» (контракт §3 п.3).
    "[Text.UTF8Encoding]::new($false)",
    "[Text.UTF8Encoding]::new()",
]


@pytest.mark.parametrize("right", ACCEPTED_RIGHT)
def test_accepted_right_hand_forms(right):
    assert _declares("[Console]::OutputEncoding = %s\n" % right) is True


@pytest.mark.parametrize("left", [
    "[Console]::OutputEncoding",
    "[System.Console]::OutputEncoding",
])
def test_accepted_left_hand_forms(left):
    assert _declares("%s = [Text.Encoding]::UTF8\n" % left) is True


def test_case_insensitive_everywhere():
    """PowerShell регистронезависим — признак обязан быть тоже (контракт §3)."""
    assert _declares("[console]::outputencoding = [text.encoding]::utf8\n") is True
    assert _declares("[CONSOLE]::OUTPUTENCODING = [TEXT.ENCODING]::UTF8\n") is True
    assert _declares(
        "[System.Console]::OutputEncoding = "
        "[system.text.utf8encoding]::NEW($FALSE)\n") is True


def test_whitespace_around_equals_and_inside_parens_is_layout_not_meaning():
    """«Пробелы вокруг `=` и внутри скобок — оформление, не смысл» (§3)."""
    assert _declares("[Console]::OutputEncoding=[Text.Encoding]::UTF8\n") is True
    assert _declares(
        "[Console]::OutputEncoding    =    [Text.Encoding]::UTF8\n") is True
    assert _declares(
        "[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new( $false )\n"
    ) is True
    assert _declares(
        "[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new(  )\n"
    ) is True


# ─────────────────────────────────────────────────────────────────────────────
# §3.1 + ЛОВУШКА №1 — что обязано ОТКАЗАТЬ
# ─────────────────────────────────────────────────────────────────────────────

def test_trap_1_get_encoding_1251_is_refused():
    """ЛОВУШКА №1. Левая часть та же, кодировка — cp1251.

    Признак, смотрящий только на левую часть, объявит это защитой. Тогда
    сверка станет зелёной ровно на файле, который гарантированно порвёт
    кириллицу.
    """
    assert _declares(
        "[Console]::OutputEncoding = [Text.Encoding]::GetEncoding(1251)\n"
    ) is False
    assert _declares(
        "[System.Console]::OutputEncoding = "
        "[System.Text.Encoding]::GetEncoding(1251)\n") is False


@pytest.mark.parametrize("right", [
    "[Text.Encoding]::ASCII",
    "[Text.Encoding]::Unicode",
    "[Text.Encoding]::Default",
    "[System.Text.Encoding]::Default",
    "[Text.Encoding]::GetEncoding('windows-1251')",
    "$null",
    "$OutputEncoding",
])
def test_non_utf8_right_hand_side_is_refused(right):
    """«и любая другая не-UTF-8 правая часть» (§3.1)."""
    assert _declares("[Console]::OutputEncoding = %s\n" % right) is False


def test_chcp_is_not_the_signal():
    """`chcp 65001` отвергнут решением владельца: признак, который можно
    удовлетворить враньём, признаком не является (§3.1, спека §0/§4)."""
    assert _declares("chcp 65001\nWrite-Host 'x'\n") is False
    assert _declares("chcp 65001 > $null\n") is False


def test_output_encoding_variable_alone_is_not_the_signal():
    """`$OutputEncoding = ...` — ДРУГОЕ направление (чем PowerShell кодирует
    ВХОД нативной команде). Ставить рядом можно, признаком не является."""
    assert _declares("$OutputEncoding = [Text.Encoding]::UTF8\n") is False
    assert _declares(
        "$OutputEncoding = [System.Text.UTF8Encoding]::new($false)\n") is False


def test_output_encoding_variable_next_to_the_real_thing_still_counts():
    """«Ставить рядом можно» — соседство не обязано ломать признак."""
    src = ("$OutputEncoding = [Text.Encoding]::UTF8\n"
           "[Console]::OutputEncoding = [Text.Encoding]::UTF8\n")
    assert _declares(src) is True


def test_input_encoding_is_a_different_property():
    """`[Console]::InputEncoding = ...` — другое свойство (§3.1)."""
    assert _declares(
        "[Console]::InputEncoding = [Text.Encoding]::UTF8\n") is False
    assert _declares(
        "[System.Console]::InputEncoding = "
        "[System.Text.UTF8Encoding]::new($false)\n") is False


def test_nothing_at_all_is_refused():
    assert _declares("") is False
    assert _declares("param()\nWrite-Host 'привет'\n") is False


# ─────────────────────────────────────────────────────────────────────────────
# §3.1 + ЛОВУШКА №2 (первое место) — присваивание в комментарии/кавычках
# ─────────────────────────────────────────────────────────────────────────────

def test_assignment_inside_line_comment_is_not_protection():
    assert _declares(
        "# [Console]::OutputEncoding = [Text.Encoding]::UTF8\n") is False
    assert _declares(
        "$x = 1   # [Console]::OutputEncoding = [Text.Encoding]::UTF8\n"
    ) is False


def test_assignment_inside_block_comment_is_not_protection():
    src = ("<#\n"
           "  [Console]::OutputEncoding = [Text.Encoding]::UTF8\n"
           "#>\n"
           "Write-Host 'привет'\n")
    assert _declares(src) is False


def test_assignment_inside_double_quoted_string_is_not_protection():
    assert _declares(
        '$doc = "[Console]::OutputEncoding = [Text.Encoding]::UTF8"\n'
    ) is False


def test_assignment_inside_single_quoted_string_is_not_protection():
    assert _declares(
        "$doc = '[Console]::OutputEncoding = [Text.Encoding]::UTF8'\n"
    ) is False


def test_assignment_inside_here_string_is_not_protection():
    """Обе формы here-string (§3.3)."""
    src_d = ('$doc = @"\n'
             "[Console]::OutputEncoding = [Text.Encoding]::UTF8\n"
             '"@\n')
    assert _declares(src_d) is False
    src_s = ("$doc = @'\n"
             "[Console]::OutputEncoding = [Text.Encoding]::UTF8\n"
             "'@\n")
    assert _declares(src_s) is False


def test_here_string_closes_only_at_line_start():
    """Закрывающая последовательность here-string — в НАЧАЛЕ строки (§3.3).

    Здесь она встречается ПОСРЕДИ строки и закрывать не имеет права, поэтому
    присваивание ниже всё ещё внутри here-string и защитой не является.
    """
    src = ('$doc = @"\n'
           'середина строки "@ закрывать не должна\n'
           "[Console]::OutputEncoding = [Text.Encoding]::UTF8\n"
           '"@\n')
    assert _declares(src) is False


def test_code_after_here_string_is_visible_again():
    src = ('$doc = @"\n'
           "текст\n"
           '"@\n'
           "[Console]::OutputEncoding = [Text.Encoding]::UTF8\n")
    assert _declares(src) is True


# ─────────────────────────────────────────────────────────────────────────────
# §3.3 — что обязан пережить разбор кавычек и комментариев.
# Пинится ПОВЕДЕНИЕ признака: если состояние разбора съедет, настоящее
# объявление станет невидимым и правильный файл ложно покраснеет.
# ─────────────────────────────────────────────────────────────────────────────

def test_hash_inside_quotes_does_not_start_a_comment():
    """«`#` внутри кавычек комментария НЕ начинает (например `"цена #5"`)».

    Проверяется НА ОДНОЙ строке: если `#` в кавычках начнёт комментарий, всё
    правее до конца строки исчезнет вместе с объявлением.
    """
    src = ('$msg = "цена #5"; '
           "[Console]::OutputEncoding = [Text.Encoding]::UTF8\n")
    assert _declares(src) is True


def test_block_comment_opener_inside_quotes_does_not_open_a_block():
    """«Открывающая последовательность блочного комментария внутри кавычек
    блока НЕ начинает» — иначе весь остаток файла станет комментарием."""
    src = ('$t = "<#"\n'
           "[Console]::OutputEncoding = [Text.Encoding]::UTF8\n")
    assert _declares(src) is True


def test_single_quote_inside_double_quotes_does_not_break_the_string():
    src = ('$a = "it\'s fine"\n'
           "[Console]::OutputEncoding = [Text.Encoding]::UTF8\n")
    assert _declares(src) is True


def test_double_quote_inside_single_quotes_does_not_break_the_string():
    src = ("$a = 'он сказал \"да\"'\n"
           "[Console]::OutputEncoding = [Text.Encoding]::UTF8\n")
    assert _declares(src) is True


def test_doubled_quote_inside_a_string_is_an_escape_not_an_end():
    """«Удвоенная кавычка внутри строки — экран, а не конец строки» (§3.3).

    Разбор без этого правила закроет строку на первой кавычке пары, откроет
    новую на второй и утащит объявление внутрь литерала.
    """
    src = ('$a = "он сказал ""да"" вслух"\n'
           "[Console]::OutputEncoding = [Text.Encoding]::UTF8\n")
    assert _declares(src) is True
    src2 = ("$a = 'он сказал ''да'' вслух'\n"
            "[Console]::OutputEncoding = [Text.Encoding]::UTF8\n")
    assert _declares(src2) is True


def test_block_comments_do_not_nest():
    """«Блочные комментарии PowerShell НЕ вкладываются: первая закрывающая
    последовательность закрывает» (§3.3)."""
    src = ("<# внешний <# внутренний #>\n"
           "[Console]::OutputEncoding = [Text.Encoding]::UTF8\n")
    assert _declares(src) is True


def test_multiline_block_comment_spans_lines():
    src = ("<#\n"
           "строка раз\n"
           "  [Console]::OutputEncoding = [Text.Encoding]::UTF8\n"
           "строка три\n"
           "#>\n"
           "$x = 1\n")
    assert _declares(src) is False


# ─────────────────────────────────────────────────────────────────────────────
# §3.2 — ПРАВИЛО ПОЗИЦИИ
# ─────────────────────────────────────────────────────────────────────────────

PRINTERS = [
    "Write-Host", "Write-Output", "Write-Error", "Write-Warning",
    "Write-Verbose", "Write-Information", "Write-Debug",
    "Out-Host", "Tee-Object",
]


@pytest.mark.parametrize("printer", PRINTERS)
def test_declaration_before_each_printer_is_ok(printer):
    src = ("[Console]::OutputEncoding = [Text.Encoding]::UTF8\n"
           "%s 'привет'\n" % printer)
    assert _declares(src) is True


@pytest.mark.parametrize("printer", PRINTERS)
def test_declaration_after_each_printer_is_refused(printer):
    """Объявление ПОСЛЕ первой печати оставляет эти строки битыми (§3.2)."""
    src = ("%s 'привет'\n"
           "[Console]::OutputEncoding = [Text.Encoding]::UTF8\n" % printer)
    assert _declares(src) is False


def test_printer_names_are_case_insensitive():
    src = ("write-host 'привет'\n"
           "[Console]::OutputEncoding = [Text.Encoding]::UTF8\n")
    assert _declares(src) is False


def test_no_printing_at_all_satisfies_the_position_rule():
    """«Печати нет вовсе -> правило позиции выполнено (пусто истинно). Это
    осознанно: файлу без печати ломать нечего» (§3.2)."""
    src = ("param()\n"
           "$x = 1\n"
           "[Console]::OutputEncoding = [Text.Encoding]::UTF8\n"
           "Start-Sleep -Seconds 1\n")
    assert _declares(src) is True


def test_printer_name_needs_a_token_boundary():
    """`Write-HostName` — не печать: границы токена справа нет (§3.2)."""
    src = ("Write-HostName 'привет'\n"
           "[Console]::OutputEncoding = [Text.Encoding]::UTF8\n")
    assert _declares(src) is True


# ── ЛОВУШКА №2, второе место: слепота к комментариям в ПРАВИЛЕ ПОЗИЦИИ ───────

def test_trap_2_commented_printer_before_declaration_is_not_a_print():
    """Модель `ops_watchdog_detached.ps1` (замер контракта §3.2).

    «Печать» текстом стоит ВЫШЕ объявления, но она в блочном комментарии.
    `param()` обязан быть первым исполняемым оператором PowerShell, поэтому
    раньше него объявление поставить нельзя. Читай сторож комментарии
    буквально — правка стала бы НЕВЫПОЛНИМОЙ, а правильный файл выглядел бы
    красным.
    """
    src = ("<#\n"
           "  ops_watchdog_detached.ps1 — сторож проб.\n"
           "  Каждые 30 с печатает итог через Write-Host в журнал.\n"
           "#>\n"
           "param(\n"
           "    [int]$IntervalSec = 30\n"
           ")\n"
           "[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)\n"
           "Write-Host 'проба пошла'\n")
    assert _declares(src) is True


def test_trap_2_line_commented_printer_before_declaration_is_not_a_print():
    src = ("# Write-Host 'это только пример из документации'\n"
           "[Console]::OutputEncoding = [Text.Encoding]::UTF8\n"
           "Write-Host 'настоящая печать'\n")
    assert _declares(src) is True


def test_trap_2_printer_inside_string_literal_is_not_a_print():
    src = ('$hint = "используйте Write-Host для вывода"\n'
           "[Console]::OutputEncoding = [Text.Encoding]::UTF8\n"
           "Write-Host $hint\n")
    assert _declares(src) is True


def test_trap_2_same_file_with_declaration_moved_after_the_real_print():
    """Зеркало предыдущего: комментарий по-прежнему не печать, но НАСТОЯЩАЯ
    печать теперь выше объявления — обязано ОТКАЗАТЬ.

    Без этого зеркала пин выше проходил бы и у реализации, которая правило
    позиции вообще не применяет.
    """
    src = ("<#\n"
           "  ops_watchdog_detached.ps1 — сторож проб.\n"
           "  Каждые 30 с печатает итог через Write-Host в журнал.\n"
           "#>\n"
           "param(\n"
           "    [int]$IntervalSec = 30\n"
           ")\n"
           "Write-Host 'проба пошла'\n"
           "[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)\n")
    assert _declares(src) is False


def test_commented_out_declaration_plus_real_print_is_refused():
    """Демонстрация красного из §11 спеки: объявление ушло в комментарий."""
    src = ("param()\n"
           "# [Console]::OutputEncoding = [Text.Encoding]::UTF8\n"
           "Write-Host 'привет'\n")
    assert _declares(src) is False


# ─────────────────────────────────────────────────────────────────────────────
# §3.3 — `strip_ps_comments`: только «комментарий не виден, а код виден»
# ─────────────────────────────────────────────────────────────────────────────

def test_strip_ps_comments_keeps_the_same_line_split():
    """«Результат — текст ТОЙ ЖЕ разбивки по строкам» — иначе позиции
    (объявление против первой печати) сравнивать бессмысленно."""
    strip = _need("strip_ps_comments")
    src = ("# раз\n"
           "$x = 1\n"
           "<#\n"
           "два\n"
           "#>\n"
           "$y = 2\n")
    out = strip(src)
    assert isinstance(out, str)
    assert len(out.split("\n")) == len(src.split("\n"))


def test_strip_ps_comments_hides_comment_and_keeps_code():
    strip = _need("strip_ps_comments")
    src = ("<# [Console]::OutputEncoding = [Text.Encoding]::UTF8 #>\n"
           "$real = 1\n"
           "# [Console]::OutputEncoding = [Text.Encoding]::UTF8\n"
           "[Console]::OutputEncoding = [Text.Encoding]::UTF8\n")
    out = strip(src)
    lines = out.split("\n")
    assert "OutputEncoding" not in lines[0], "блочный комментарий остался видим"
    assert "$real" in lines[1], "код пропал вместе с комментарием"
    assert "OutputEncoding" not in lines[2], "строчный комментарий остался видим"
    assert "OutputEncoding" in lines[3], "настоящее объявление пропало"
