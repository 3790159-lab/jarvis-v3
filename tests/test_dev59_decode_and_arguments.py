# -*- coding: utf-8 -*-
"""Сторожа DEV-59, часть 2: ДЕКОДИРОВАНИЕ файла и РАЗБОР строки запуска.

Стережётся `decode_ps1_bytes` (контракт §4, ловушка №3) и
`script_path_from_arguments` (контракт §5, ловушка №4).

Написано ОТ СПЕКИ И КОНТРАКТА, БЕЗ просмотра реализации.

ЛОВУШКА №3 — почему тут всюду кириллица, а не ASCII. На ASCII правило «с BOM
читаем utf-8» и правило «без BOM читаем cp1251» СОВПАДАЮТ по построению, и
сторож, проверивший только ASCII, зелен по построению. Поэтому оба правила
пинятся ФАКТОМ РАЗНОГО ИСХОДА на одних и тех же кириллических байтах.

ЛОВУШКА №4 — у задачи бывает ХВОСТ после пути: `JarvisPanelClientGuardian`
запускается как `... -File "...panel_client_guardian_detached.ps1" -Slug yarina`.
Хвост в путь попадать не должен, иначе читатель получит несуществующее имя и
живая задача уедет в `unreadable` навсегда.
"""
from __future__ import annotations

import pytest

from app.services import task_encoding as mod


UTF8_BOM = b"\xef\xbb\xbf"

# Кириллическая строка, у которой utf-8-байты и cp1251-байты РАЗНЫЕ.
CYRILLIC = "Привет, сторож"


def _need(name):
    obj = getattr(mod, name, None)
    if obj is None:
        pytest.fail(
            "контракт DEV-59 §2: в app/services/task_encoding.py нет `%s`"
            % name)
    return obj


# ─────────────────────────────────────────────────────────────────────────────
# §4 — decode_ps1_bytes. Правило ровно то, по которому файл читает PS 5.1
# ─────────────────────────────────────────────────────────────────────────────

def test_with_bom_decodes_as_utf8_and_drops_the_bom():
    decode = _need("decode_ps1_bytes")
    out = decode(UTF8_BOM + CYRILLIC.encode("utf-8"))
    assert out == CYRILLIC
    assert not out.startswith("\ufeff"), (
        "контракт §4: BOM обязан быть ОТБРОШЕН, иначе первая строка файла "
        "перестанет совпадать с искомым присваиванием")


def test_without_bom_decodes_as_cp1251():
    """Путь без BOM живой: из восьми файлов его нет у трёх
    (`backend_guardian_detached`, `run_sniper_detached`,
    `infra_restart_cloudflared_action`)."""
    decode = _need("decode_ps1_bytes")
    out = decode(CYRILLIC.encode("cp1251"))
    assert out == CYRILLIC


def test_trap_3_same_cyrillic_bytes_give_DIFFERENT_text_with_and_without_bom():
    """ЛОВУШКА №3, главный пин файла.

    Одни и те же байты, поданные с BOM и без, обязаны дать РАЗНЫЙ текст.
    Реализация, которая всегда декодирует utf-8 (или всегда cp1251), даст
    здесь одинаковый результат либо исключение — и это ровно тот сторож,
    который проверял бы не тот текст, который исполняется.
    """
    decode = _need("decode_ps1_bytes")
    raw = CYRILLIC.encode("utf-8")

    with_bom = decode(UTF8_BOM + raw)
    without_bom = decode(raw)

    assert with_bom == CYRILLIC
    assert without_bom == raw.decode("cp1251")
    assert with_bom != without_bom, (
        "оба правила §4 дали ОДИН исход на кириллице — значит правило "
        "по BOM не применяется, и сторож зелен по построению")


def test_ascii_is_why_ascii_is_not_a_guard():
    """Документирующий пин: на ASCII оба правила совпадают.

    Он зелёный и у ПРАВИЛЬНОЙ, и у сломанной реализации — и стоит тут именно
    затем, чтобы следующий читатель не принял такой пин за проверку §4.
    """
    decode = _need("decode_ps1_bytes")
    assert decode(b"Write-Host hello") == "Write-Host hello"
    assert decode(UTF8_BOM + b"Write-Host hello") == "Write-Host hello"


def test_empty_bytes_and_bom_only():
    decode = _need("decode_ps1_bytes")
    assert decode(b"") == ""
    assert decode(UTF8_BOM) == ""


def test_undecodable_bytes_without_bom_raise():
    """«декодирование не удалось -> поднять исключение» (§4).

    Байт `0x98` в cp1251 не определён. Молчаливая подмена (`errors="replace"`)
    здесь запрещена: она превратила бы нечитаемый файл в «прочитали, защиты
    нет», то есть одно красное в другое.
    """
    decode = _need("decode_ps1_bytes")
    with pytest.raises(Exception):
        decode(b"\x98\x98\x98")


def test_undecodable_bytes_with_bom_raise():
    decode = _need("decode_ps1_bytes")
    with pytest.raises(Exception):
        decode(UTF8_BOM + b"\xff\xfe\xff\xfe")


def test_declaration_is_found_in_a_bomless_cp1251_file():
    """Сцепка §4 и §3 на модели живого файла без BOM.

    `backend_guardian_detached.ps1` BOM не несёт. Если декодировать его
    utf-8, кириллические комментарии либо взорвутся, либо приедут мусором —
    и объявление, стоящее ниже, найдено не будет.
    """
    decode = _need("decode_ps1_bytes")
    declares = _need("declares_console_utf8")
    source = ("# Сторож бэкенда: поднимает :8010 и следит за пульсом.\n"
              "[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)\n"
              "Write-Host 'поехали'\n")
    assert declares(decode(source.encode("cp1251"))) is True


# ─────────────────────────────────────────────────────────────────────────────
# §5 — script_path_from_arguments
# ─────────────────────────────────────────────────────────────────────────────

LIVE_FORM = (r'-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden '
             r'-File "C:\jarvis\scripts\ops_watchdog_detached.ps1"')


def _path(arguments):
    return _need("script_path_from_arguments")(arguments)


def test_live_form_of_all_eight_tasks():
    assert _path(LIVE_FORM) == r"C:\jarvis\scripts\ops_watchdog_detached.ps1"


def test_path_without_quotes_runs_to_the_first_space():
    args = r"-NoProfile -File C:\jarvis\scripts\healthchecks_ping.ps1"
    assert _path(args) == r"C:\jarvis\scripts\healthchecks_ping.ps1"


@pytest.mark.parametrize("flag", ["-File", "-file", "-FILE", "-FiLe"])
def test_file_flag_is_case_insensitive(flag):
    args = '%s "C:\\jarvis\\scripts\\x.ps1"' % flag
    assert _path(args) == r"C:\jarvis\scripts\x.ps1"


def test_trap_4_tail_after_the_path_must_not_land_in_it():
    """ЛОВУШКА №4. Живой пример: `JarvisPanelClientGuardian`."""
    args = (r'-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden '
            r'-File "C:\jarvis\scripts\panel_client_guardian_detached.ps1" '
            r'-Slug yarina')
    assert _path(args) == \
        r"C:\jarvis\scripts\panel_client_guardian_detached.ps1"


def test_trap_4_tail_after_an_unquoted_path_must_not_land_in_it():
    args = r"-File C:\jarvis\scripts\panel_client_guardian_detached.ps1 -Slug yarina"
    assert _path(args) == \
        r"C:\jarvis\scripts\panel_client_guardian_detached.ps1"


def test_file_inside_quotes_is_not_a_flag():
    """Дословный пример контракта §5.

    Та же оговорка, что уже сделана для `-X utf8` в путях: подстрока внутри
    закавыченного пути флагом не является.
    """
    args = r'-File "C:\dir -File fake.ps1\real.ps1"'
    assert _path(args) == r"C:\dir -File fake.ps1\real.ps1"


@pytest.mark.parametrize("args", [
    '-NoProfile -Command "& {Write-Host 1}"',
    "-NoProfile -EncodedCommand VwByAGkAdABlAA==",
    "-NoProfile -ExecutionPolicy Bypass",
    "",
    "   ",
])
def test_forms_that_must_give_none(args):
    """«`-Command`, `-EncodedCommand`, отсутствие `-File`, пустая строка ->
    вернуть `None`» — и задача получит `unreadable`, а НЕ зелёное."""
    assert _path(args) is None


def test_none_arguments_give_none():
    assert _path(None) is None


@pytest.mark.parametrize("short", ["-f", "-fi", "-fil"])
def test_abbreviations_are_deliberately_not_supported(short):
    """«сокращения НЕ поддерживаем — осознанно: живая форма одна, а угадывание
    сокращений есть источник ложного зелёного» (§5)."""
    args = '%s "C:\\jarvis\\scripts\\x.ps1"' % short
    assert _path(args) is None


def test_file_flag_needs_a_left_token_boundary():
    """`--File` и `-NoFile` — не `-File`: понимается ТОЛЬКО явный флаг (§5).

    Контракт эту границу называет словом «явный», но букву не диктует; здесь
    она прочитана так же, как в уже живущем `-X utf8` (`(?<![\\w-])`).
    """
    assert _path(r'--File "C:\jarvis\scripts\x.ps1"') is None
    assert _path(r'-NoFile "C:\jarvis\scripts\x.ps1"') is None


def test_file_flag_without_a_value_gives_none():
    assert _path("-NoProfile -File") is None
    assert _path("-NoProfile -File   ") is None


# ═════════════════════════════════════════════════════════════════════════════
# АМЕНДМЕНТ А.4 — граница токена у `-File` с ОБЕИХ сторон.
#
# На первом круге контракт говорил только «понимается ТОЛЬКО явный `-File`», и
# я прочитал границу так же, как у живущего рядом `-X utf8`, назвав это своим
# решением. Дозадано: граница с обеих сторон, как у `-X utf8`.
# ═════════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("args", [
    r'--File "C:\jarvis\scripts\x.ps1"',
    r'-NoFile "C:\jarvis\scripts\x.ps1"',
    r'-XFile "C:\jarvis\scripts\x.ps1"',
    r'---File "C:\jarvis\scripts\x.ps1"',
    r'-Filex "C:\jarvis\scripts\x.ps1"',
    r'-Files "C:\jarvis\scripts\x.ps1"',
])
def test_a4_file_flag_boundary_on_both_sides(args):
    assert _path(args) is None, (
        "%r — не явный `-File`; угадывание похожего есть источник ложного "
        "зелёного" % args)


def test_a4_the_real_flag_still_works_next_to_the_lookalikes():
    """Зеркало: граница не имеет права съесть НАСТОЯЩИЙ флаг."""
    args = r'-NoProfile -File "C:\jarvis\scripts\x.ps1"'
    assert _path(args) == r"C:\jarvis\scripts\x.ps1"
