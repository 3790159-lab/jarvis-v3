"""Выполненная работа обязана быть описана, а не проглочена (DEV-101).

Замер 06.09.2026, запрос «Сравни FAL и Replicate и оформи в таблицу»:

    ✅ [1/1] таблицы Excel/CSV: готово (14.9s)
    ИТОГ: план done за 14.9s, ответ 18 симв.
    ОТВЕТ: Результат получен.

Шаг настоящий: живая ручка, Tavily + LLM, на выходе XLSX на 2 строки и 8
колонок. Но `synthesize_results` искал только `answer` / `plan` / `text`, а
агент вернул пути к файлам — и человек получал строку-затычку.

Ключи здесь ЗАМЕРЕНЫ живым вызовом, а не взяты из `output_format` каталога:
каталог обещает `file_path` / `drive_url`, а реальность — `xlsx_path`,
`csv_path`, `table_path`, `json_path`, `rows_count`, `columns`. Чинить по
каталогу значило бы промахнуться мимо боевого ответа.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.services.smart_router import (  # noqa: E402
    describe_result,
    synthesize_results,
)

# Форма ответа smart_table, снятая живым вызовом 06.09.2026 (пути укорочены).
SMART_TABLE_RESULT = {
    "ok": True,
    "query": "Сравни FAL и Replicate и оформи в таблицу",
    "csv_path": r"C:\jarvis\jarvis_stage3_artifacts\telegram_tables\cmp_20260906.csv",
    "xlsx_path": r"C:\jarvis\jarvis_stage3_artifacts\telegram_tables\cmp_20260906.xlsx",
    "table_path": r"C:\jarvis\jarvis_stage3_artifacts\telegram_tables\cmp_20260906.xlsx",
    "json_path": r"C:\jarvis\jarvis_stage3_artifacts\telegram_tables\cmp_20260906.json",
    "rows_count": 2,
    "columns": ["Service", "Category", "Pricing Model"],
}


def test_text_answer_still_wins():
    """Текстовый ответ не должен пострадать от правки."""
    assert describe_result({"answer": "$3 за 1 млн токенов"}) == "$3 за 1 млн токенов"


def test_artifact_result_names_the_file():
    out = describe_result(SMART_TABLE_RESULT)
    assert "cmp_20260906.xlsx" in out


def test_artifact_result_names_the_size():
    """Путь без объёма не даёт понять, пустая таблица или нет."""
    out = describe_result(SMART_TABLE_RESULT)
    assert "строк: 2" in out
    assert "колонок: 3" in out


def test_artifact_result_is_not_the_stub_phrase():
    assert describe_result(SMART_TABLE_RESULT) != "Результат получен."


def test_empty_result_still_degrades_gracefully():
    assert describe_result({}) == "Результат получен."


def test_single_step_synthesis_reports_the_artifact():
    """Сквозная проверка ровно того случая, что дал 18 символов."""
    out = synthesize_results(
        "Сравни FAL и Replicate и оформи в таблицу",
        [{"agent": "smart_table", "result": SMART_TABLE_RESULT}],
    )
    assert "cmp_20260906.xlsx" in out
    assert out != "Результат получен."


def test_multi_step_fallback_reports_the_artifact(monkeypatch):
    """Ветка склейки без LLM: бэкенд недоступен — синтез обязан упасть в неё.

    Порт 1 закрыт, `urlopen` падает сразу, поэтому исполняется НАСТОЯЩАЯ
    ветка отката, а не её имитация.
    """
    monkeypatch.setenv("TELEGRAM_BACKEND_URL", "http://127.0.0.1:1")
    out = synthesize_results(
        "Сравни FAL и Replicate и оформи в таблицу",
        [
            {"agent": "internet_research", "result": {"answer": "FAL дешевле на пакетах"}},
            {"agent": "smart_table", "result": SMART_TABLE_RESULT},
        ],
    )
    assert "FAL дешевле на пакетах" in out
    assert "cmp_20260906.xlsx" in out
