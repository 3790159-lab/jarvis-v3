# tests/test_vizir_task_acceptance.py
# -*- coding: utf-8 -*-
"""Спай-зубы честной приёмки /task (детект галлюцинаций/пустышек). $0, чистая функция."""
from app.services.vizir.task_acceptance import accept_task

# task-4: галлюцинация — описание+путь, БЕЗ кода игры
FIX_TASK4 = (
    "Готово! Игра создана по адресу `C:\\Users\\Admin\\Desktop\\tictactoe\\index.html`.\n\n"
    "Просто открой этот файл в браузере.\n\n"
    "## Что умеет игра\n"
    "- Два игрока по очереди\n- Против ИИ (Minimax)\n- Счёт побед сохраняется\n"
)
GOAL_TASK4 = "сделай мне простую веб игру в крестики нолики"

# task-3: уточнение/невозможность — агент попросил путь
FIX_TASK3 = (
    "Для начала изучу ваш проект.\n\n"
    "Не могу найти проект автоматически. Пожалуйста, укажите путь к папке проекта "
    "Jarvis V3. Например:\n- `C:\\Users\\Admin\\Projects\\JarvisV3`\n"
    "- или просто напишите, где он находится\n"
)
GOAL_TASK3 = "проанализируй мой проект Jarvis V3 и сделай 5 советов по улучшению, оптимизации"

# task-2: ЛЕГИТИМНЫЙ текст-ответ — self-contained, инлайн
FIX_TASK2 = (
    "Я — Claude Code, ИИ-агент от Anthropic. Вот что я умею:\n\n"
    "## Работа с кодом\n- Писать, анализировать, отлаживать и рефакторить код\n"
    "## Работа с файлами и системой\n- Читать, создавать и редактировать файлы\n"
    "- Выполнять команды в терминале\n"
    "## Общие задачи\n- Отвечать на вопросы\n- Решать математические задачи\n"
)
GOAL_TASK2 = "что ты умеешь?"


def _val(final_response, stopped="completed", **extra):
    d = {"final_response": final_response, "stopped_reason": stopped}
    d.update(extra)
    return d


# --- Зуб A: галлюцинация-указатель (task-4) → reject по Слою 1a ---
def test_toothA_pointer_hallucination_rejected():
    acc = accept_task(_val(FIX_TASK4), goal=GOAL_TASK4)
    assert acc.accepted is False
    assert any("ссылк" in r or "путь" in r for r in acc.reasons)


# --- Зуб D: уточнение/невозможность (task-3) → reject по Слою 1b ---
def test_toothD_clarification_inability_rejected():
    acc = accept_task(_val(FIX_TASK3), goal=GOAL_TASK3)
    assert acc.accepted is False
    assert any("не выполнена" in r or "попросил ввод" in r for r in acc.reasons)


# --- Зуб C (РЕГРЕССИЯ): легит текст-ответ (task-2) → ПРОХОДИТ ---
def test_toothC_legit_text_answer_accepted():
    acc = accept_task(_val(FIX_TASK2), goal=GOAL_TASK2)
    assert acc.accepted is True, acc.reasons
    assert acc.reasons == []


# --- Зуб H: URL (http://...:8010) НЕ считается файловым путём (Слой 1a не ложно-срабатывает) ---
def test_toothH_url_is_not_a_filepath():
    out = "Готово. Чат обращается к http://localhost:8010/chat через fetch()."
    acc = accept_task(_val(out), goal="что ты умеешь?")  # не build-task
    assert acc.accepted is True, acc.reasons


# --- Gate 0/1: не-completed и пустой → reject ---
def test_gate0_not_completed_rejected():
    acc = accept_task(_val("что-то", stopped="max_iterations"), goal="что ты умеешь?")
    assert acc.accepted is False
    assert any("did not complete" in r for r in acc.reasons)

def test_gate1_empty_rejected():
    acc = accept_task(_val(""), goal="что ты умеешь?")
    assert acc.accepted is False
    assert any("empty output" in r for r in acc.reasons)
