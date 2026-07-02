# Vizir честная приёмка `/task` — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Заменить слабую `accept_generic` (completed+непустой) на детерминированную `accept_task(value, goal)`, ловящую структурные галлюцинации/пустышки (указатель/отсрочка + build-task-без-кода), чтобы бот честно эскалировал вместо лживого ✅.

**Architecture:** Новый чистый модуль `app/services/vizir/task_acceptance.py` с `accept_task(value, goal)`: Gate 0 (completed) + Gate 1 (непустой) + Слой 1 (1a completion+location co-occurrence, 1b уточнение/невозможность) + Слой 2 (build-task из goal → требовать код-маркер). Handler `vizir_task_handler.py` связывает `goal=base_prompt` в момент запуска и передаёт goal-bound `accept_task` в LoopController. `loop.py`/`coordinator.py`/`handlers*.py`/`hermes_acceptance.py` и existing-команды НЕ трогаются. Спай-зубы мутацией на реальных вчерашних артефактах (task-2/-3/-4).

**Tech Stack:** Python 3.11, pytest. Запуск тестов из корня worktree `C:\jarvis_worktrees\vizir-honest-accept`.

**Дисциплина:** спай-зубы ПЕРВЫМИ, мутацией в обе стороны. $0 до живого. Аддитивно, worktree `vizir-honest-accept` @ `3113377`. Бот приоритет, existing целы.

---

## Файловая структура

- **Create:** `app/services/vizir/task_acceptance.py` — `accept_task(value: dict, goal: str) -> AcceptanceResult` + приватные хелперы/константы маркеров. Единственная ответственность: детерминированная приёмка произвольной `/task`.
- **Create:** `tests/test_vizir_task_acceptance.py` — спай-зубы A–H с реальными фикстурами task-2/-3/-4.
- **Modify:** `app/handlers/vizir_task_handler.py` — дефолт `accept_generic` → goal-bound `accept_task` (правка `__init__`, `_build_loop`, `run_task_phase`). `accept_generic` ОСТАЁТСЯ в файле (его прямо тестируют существующие юниты — не удалять).
- **НЕ ТРОГАТЬ:** `app/services/vizir/loop.py`, `coordinator.py`, `handlers.py`, `handlers_hermes.py`, `hermes_acceptance.py`, все существующие бот-команды/`tools/jarvis_smart_telegram_control.py`.

**Реальные фикстуры** (вставить в тест как строковые константы — верны фактически, из `state/vizir_tasks/task-237616472-{2,3,4}.html`):

```python
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
```

---

## Task 1: Слой 1 + Gate 0/1 — детектор указателя/отсрочки

**Files:**
- Create: `app/services/vizir/task_acceptance.py`
- Test: `tests/test_vizir_task_acceptance.py`

- [ ] **Step 1: Написать падающие спай-зубы (Слой 1)**

Создать `tests/test_vizir_task_acceptance.py` с фикстурами (см. выше) и тестами:

```python
# tests/test_vizir_task_acceptance.py
# -*- coding: utf-8 -*-
"""Спай-зубы честной приёмки /task (детект галлюцинаций/пустышек). $0, чистая функция."""
from app.services.vizir.task_acceptance import accept_task

# ---- реальные фикстуры вчерашних прогонов (вставить блок FIX_TASK*/GOAL_TASK* сверху) ----
# (скопировать константы FIX_TASK4/GOAL_TASK4/FIX_TASK3/GOAL_TASK3/FIX_TASK2/GOAL_TASK2)

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
    # "готово" + URL с портом, но БЕЗ файлового пути/фразы-локации → 1a не должен бить
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
```

- [ ] **Step 2: Прогнать — убедиться, что падает**

Run: `cd /c/jarvis_worktrees/vizir-honest-accept && python -m pytest tests/test_vizir_task_acceptance.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.vizir.task_acceptance'`.

- [ ] **Step 3: Реализовать Gate 0/1 + Слой 1 (минимально)**

Создать `app/services/vizir/task_acceptance.py`:

```python
# app/services/vizir/task_acceptance.py
# -*- coding: utf-8 -*-
"""Детерминированная приёмка произвольной /task — ловит структурные галлюцинации/пустышки.

Слой 1: указатель/отсрочка (1a completion+location co-occurrence; 1b уточнение/невозможность).
Слой 2: build-task из goal → ответ ДОЛЖЕН содержать реальный код, не прозу о нём.
$0, без платного судьи (задел под опц. судью — off, отдельной аркой). См.
docs/superpowers/specs/2026-07-02-vizir-honest-acceptance-design.md.
"""
from __future__ import annotations

import re
from pathlib import Path

from .hermes_acceptance import AcceptanceResult

# ---- Слой 1: маркеры указателя/отсрочки (RU+EN, low-case сравнение) ----
_COMPLETION = ("готово", "создан", "создана", "создал", "сохран", "записал",
               "сделал", "done", "created", "saved", "generated", "ready", "built")
_LOCATION_PHRASES = ("по адресу", "по пути", "файл находится", "файл на",
                     "на рабочем столе", "открой этот файл", "открой файл",
                     "открой в браузере", "saved to", "saved at", "file is located",
                     "located at", "created at", "open the file", "open this file")
# файловый путь: диск-буква + \ или / (со \b, чтобы "http://" НЕ матчилось), либо POSIX-каталоги
_PATH_RE = re.compile(r"\b[a-z]:[\\/]|/home/|/root/|/mnt/|/tmp/|~[\\/]", re.I)
_DEFERRAL = ("не могу найти", "не удалось найти", "не могу определить",
             "укажите путь", "укажите папку", "укажите где", "предоставьте доступ",
             "предоставьте путь", "куда сохранить", "где находится", "уточните",
             "пожалуйста, укажите", "please provide", "please specify",
             "could not find", "couldn't find", "unable to locate", "where is",
             "where should i", "provide the path")


def _text(value: dict) -> str:
    html = value.get("final_response") or ""
    if not html:
        path = value.get("artifact_path")
        if path and Path(path).exists():
            html = Path(path).read_text(encoding="utf-8", errors="replace")
    return html or ""


def _layer1(low: str, raw: str, reasons: list) -> None:
    # 1a: completion-токен СО-ВСТРЕЧАЕТСЯ с external-location → "ложное готово"
    if any(c in low for c in _COMPLETION) and (
            bool(_PATH_RE.search(raw)) or any(p in low for p in _LOCATION_PHRASES)):
        reasons.append("вернул ссылку/путь на файл вместо самого результата — "
                       "верни полный артефакт (код) прямо в ответе, не ссылку и не описание")
    # 1b: уточнение/невозможность → агент не сделал, попросил ввод
    if any(d in low for d in _DEFERRAL):
        reasons.append("задача не выполнена: агент попросил ввод / не смог продолжить "
                       "вместо результата")


def accept_task(value: dict, goal: str) -> AcceptanceResult:
    """v1 детерминированная приёмка. Собираем ВСЕ причины (не короткое замыкание)."""
    reasons: list[str] = []
    # Gate 0 — run завершился
    stopped = value.get("stopped_reason")
    if stopped and stopped != "completed":
        reasons.append(f"run did not complete (stopped_reason={stopped})")
    # Gate 1 — непустой вывод
    raw = _text(value)
    if not raw:
        reasons.append("empty output (no final_response/artifact produced)")
        return AcceptanceResult(accepted=False, reasons=reasons)
    low = raw.lower()
    # Gate 2 — Слой 1
    _layer1(low, raw, reasons)
    return AcceptanceResult(accepted=not reasons, reasons=reasons)
```

- [ ] **Step 4: Прогнать — убедиться, что зелено**

Run: `cd /c/jarvis_worktrees/vizir-honest-accept && python -m pytest tests/test_vizir_task_acceptance.py -v`
Expected: PASS — все 6 тестов Task 1 зелёные (A, D, C, H, gate0, gate1).

- [ ] **Step 5: Коммит**

```bash
cd /c/jarvis_worktrees/vizir-honest-accept
git add app/services/vizir/task_acceptance.py tests/test_vizir_task_acceptance.py
git commit -m "feat(vizir): Layer 1 pointer/deferral acceptance + spy-teeth (task-3/4 reject, task-2 pass)"
```

---

## Task 2: Слой 2 — build-task должен содержать реальный код

**Files:**
- Modify: `app/services/vizir/task_acceptance.py`
- Test: `tests/test_vizir_task_acceptance.py`

- [ ] **Step 1: Дописать падающие спай-зубы (Слой 2)**

Добавить в `tests/test_vizir_task_acceptance.py`:

```python
# --- Зуб B: build-task БЕЗ кода (task-4 проза) → reject по Слою 2 [двойное покрытие] ---
def test_toothB_build_task_without_code_rejected():
    # чистая проза без пути/фразы (изолируем именно Слой 2): убираем pointer, оставляем "нет кода"
    prose = ("Я подумал над игрой крестики-нолики. Это будет поле 3x3, "
             "два игрока ходят по очереди, есть проверка победы и ничьи.")
    acc = accept_task(_val(prose), goal=GOAL_TASK4)
    assert acc.accepted is False
    assert any("реального кода" in r or "сам артефакт" in r for r in acc.reasons)


# --- Зуб E: build-task С реальным кодом → ПРОХОДИТ (честный успех не ломаем) ---
def test_toothE_build_task_with_real_code_accepted():
    code = ("<!doctype html><html><head><style>.cell{}</style></head>"
            "<body><div id='board'></div><script>"
            "function move(i){return i}</script></body></html>")
    acc = accept_task(_val(code), goal=GOAL_TASK4)
    assert acc.accepted is True, acc.reasons


# --- Зуб build-detect precision: НЕ build-task (совет/анализ) → Слой 2 пропущен ---
def test_build_detection_precision_non_build_skips_layer2():
    # goal просит АНАЛИЗ+советы (нет artifact-существительного) → проза о советах проходит Слой 2
    tips = "Вот 5 советов: 1) добавьте кэш 2) логируйте 3) тесты 4) типы 5) CI."
    acc = accept_task(_val(tips), goal="проанализируй проект и дай 5 советов")
    assert acc.accepted is True, acc.reasons


# --- Зуб B2: task-4 реальный (double coverage) — pointer И no-code вместе ---
def test_toothB2_real_task4_double_coverage():
    acc = accept_task(_val(FIX_TASK4), goal=GOAL_TASK4)
    assert acc.accepted is False
    # обе причины присутствуют: и указатель (1a), и нет кода (Слой 2)
    joined = " ".join(acc.reasons)
    assert ("ссылк" in joined or "путь" in joined) and "кода" in joined
```

- [ ] **Step 2: Прогнать — убедиться, что новые падают**

Run: `cd /c/jarvis_worktrees/vizir-honest-accept && python -m pytest tests/test_vizir_task_acceptance.py -v`
Expected: `test_toothB_...`, `test_build_detection_precision_...`, `test_toothB2_...` FAIL (Слоя 2 ещё нет; build-task-проза принимается). `test_toothE_...` может проходить случайно (код есть, но Слой 2 не бьёт) — ок.

- [ ] **Step 3: Реализовать Слой 2 (build-task + код-маркер)**

В `app/services/vizir/task_acceptance.py` добавить константы и хелперы ПОСЛЕ `_DEFERRAL`:

```python
# ---- Слой 2: build-task (из goal) требует реальный код в ответе ----
_BUILD_VERBS = ("сдела", "созда", "напиши", "сгенерир", "свёрстай", "сверстай",
                "собери", "запили", "build", "make", "create", "write",
                "generate", "code", "implement")
_ARTIFACT_NOUNS = ("игр", "сайт", "страниц", "html", "css", "скрипт", "код",
                   "приложени", "компонент", "виджет", "форм", "калькулятор",
                   "таблиц", "бот", "лендинг", "game", "app", "page", "site",
                   "script", "component", "widget", "form", "landing", "file")
_CODE_MARKERS = ("<html", "<!doctype html", "<script", "<style", "<body", "<div",
                 "</", "function ", "def ", "class ", "=>", "const ", "let ",
                 "var ", "import ", "return ")
_CODE_FENCE = re.compile(r"```[^\n]*\n.+?```", re.S)


def _is_build_task(goal_low: str) -> bool:
    return (any(v in goal_low for v in _BUILD_VERBS)
            and any(n in goal_low for n in _ARTIFACT_NOUNS))


def _has_code(low: str, raw: str) -> bool:
    return any(m in low for m in _CODE_MARKERS) or bool(_CODE_FENCE.search(raw))
```

И вставить Gate 3 в `accept_task` ПЕРЕД `return`:

```python
    # Gate 3 — Слой 2: если задача просит артефакт, ответ обязан содержать реальный код
    if _is_build_task((goal or "").lower()) and not _has_code(low, raw):
        reasons.append("вернул описание/текст вместо реального кода — "
                       "верни сам артефакт (полный код) инлайн в ответе")
    return AcceptanceResult(accepted=not reasons, reasons=reasons)
```

- [ ] **Step 4: Прогнать — убедиться, что зелено**

Run: `cd /c/jarvis_worktrees/vizir-honest-accept && python -m pytest tests/test_vizir_task_acceptance.py -v`
Expected: PASS — все тесты (Task 1 + Task 2) зелёные.

- [ ] **Step 5: Коммит**

```bash
cd /c/jarvis_worktrees/vizir-honest-accept
git add app/services/vizir/task_acceptance.py tests/test_vizir_task_acceptance.py
git commit -m "feat(vizir): Layer 2 build-task requires real code + spy-teeth (double coverage on task-4)"
```

---

## ✅ ЧЕК-ПОИНТ 1 (после спай-зубов) — МУТАЦИЯ + ОК Daniil

**Доказать, что зубы реально кусаются** (мутация в обе стороны, saboteur → красный, чинка → зелёный). Выполнить и показать вывод:

- [ ] **Мутация Слоя 1a:** временно закомментировать блок `1a` в `_layer1` (строки reasons.append для «ссылк/путь»). Run: `python -m pytest tests/test_vizir_task_acceptance.py -k "toothA or toothB2" -v`. Expected: `test_toothA_...` КРАСНЫЙ (без 1a task-4 прошёл бы по этому пути), `toothB2` частично. Вернуть код → зелёный.
- [ ] **Мутация Слоя 1b:** закомментировать блок `1b`. Run: `-k toothD`. Expected: `test_toothD_...` КРАСНЫЙ. Вернуть → зелёный.
- [ ] **Мутация Слоя 2:** в `accept_task` заменить условие Gate 3 на `if False and ...`. Run: `-k "toothB or toothB2"`. Expected: `test_toothB_...` КРАСНЫЙ. Вернуть → зелёный.
- [ ] **Регресс-страховка (зуб C):** убедиться, что при ВСЕХ включённых слоях `test_toothC_legit_text_answer_accepted` и `test_toothH_url_is_not_a_filepath` зелёные (легит не поглощён, URL не ложит).

**СТОП — показать Daniil вывод мутаций. Продолжать после ОК.** (Зубы кусаются на реальных task-2/-3/-4?)

---

## Task 3: Проводка в handler (goal-bound accept_task) + регресс existing

**Files:**
- Modify: `app/handlers/vizir_task_handler.py`
- Test: `tests/test_vizir_task_handler.py` (ДОБАВИТЬ 1 тест; existing НЕ менять)

- [ ] **Step 1: Написать падающий интеграционный тест (goal-bound + actionable фидбек)**

Добавить в `tests/test_vizir_task_handler.py` (использует существующие `_handler`, `_mock_hermes`):

```python
def test_task_acceptance_rejects_hallucination_and_feeds_back(tmp_path):
    # build-task, Hermes возвращает описание+путь БЕЗ кода дважды → reject → эскалация,
    # причём фидбек про "сам артефакт" впрыснут в retry (виден в attempt_rejected).
    hallu = "Готово! Игра создана по адресу C:\\Users\\Admin\\Desktop\\ttt\\index.html"
    hermes = _mock_hermes(final_response=hallu, stopped_reason="completed", cost=0.05)
    stages = []
    h, _ = _handler(tmp_path, hermes, max_attempts=2)
    rep = _run(h.run_task_phase(chat_id=1, base_prompt="сделай веб игру крестики нолики",
                                progress_cb=lambda s, p: stages.append((s, p)),
                                user_id=1, username="daniil"))
    assert rep.escalated is True
    assert rep.document_path is None                    # НЕТ лживого артефакта
    rej = [p for s, p in stages if s == "attempt_rejected"]
    assert rej and any("артефакт" in r for pr in rej for r in pr["reasons"])
```

- [ ] **Step 2: Прогнать — убедиться, что падает**

Run: `cd /c/jarvis_worktrees/vizir-honest-accept && python -m pytest tests/test_vizir_task_handler.py::test_task_acceptance_rejects_hallucination_and_feeds_back -v`
Expected: FAIL — сейчас дефолт `accept_generic` принимает описание как ✅ (`rep.escalated is False`, artifact пишется).

- [ ] **Step 3: Переключить дефолт на goal-bound `accept_task`**

В `app/handlers/vizir_task_handler.py`:

1) Добавить импорт рядом с другими vizir-импортами:
```python
from app.services.vizir.task_acceptance import accept_task
```

2) В `__init__` заменить строку
```python
        self._accept_fn = accept_fn or accept_generic
```
на
```python
        self._accept_fn = accept_fn   # None => goal-bound accept_task, связывается в run_task_phase
```

3) Изменить сигнатуру `_build_loop`, добавив `accept_fn`:
```python
    def _build_loop(self, actor, username, on_event, accept_fn):
```
и внутри — передать его в LoopController (заменить `accept_fn=self._accept_fn` на `accept_fn=accept_fn`):
```python
        return LoopController(
            coord, build_plan=build_plan, accept_fn=accept_fn,
            config=LoopConfig(max_attempts=self._max_attempts,
                              min_attempt_usd=self._min_attempt_usd,
                              loop_deadline_s=tmo),
            on_event=on_event)
```

4) В `run_task_phase` заменить
```python
        loop = self._build_loop(actor, username, on_event=on_event)
```
на
```python
        accept_fn = self._accept_fn or (lambda v: accept_task(v, goal=base_prompt))
        loop = self._build_loop(actor, username, on_event=on_event, accept_fn=accept_fn)
```

`accept_generic` НЕ удалять (его тестируют существующие юниты).

- [ ] **Step 4: Прогнать новый тест + ВЕСЬ handler-файл (регресс existing)**

Run: `cd /c/jarvis_worktrees/vizir-honest-accept && python -m pytest tests/test_vizir_task_handler.py -v`
Expected: PASS — новый тест зелёный И все существующие зелёные. Проверка совместимости (проверено логикой):
- `test_accepted_writes_and_returns_artifact` (goal="make page" = build-task, вывод `<html><body>` = код) → accepted ✓
- `test_generic_acceptance_completed_accepts_first_try` (goal="anything" не build; вывод `<html>done</html>`: "done"-completion БЕЗ location → 1a молчит) → accepted ✓
- `test_progress_maps_...` / `test_generic_acceptance_always_truncated_...` (max_iterations → Gate 0 "did not complete") → эскалация ✓
- `test_escalation_carries_honest_refusal_reason` (FAILED-шаг ловится loop до приёмки) → ✓

- [ ] **Step 5: Коммит**

```bash
cd /c/jarvis_worktrees/vizir-honest-accept
git add app/handlers/vizir_task_handler.py tests/test_vizir_task_handler.py
git commit -m "feat(vizir): wire goal-bound accept_task into /task handler (honest reject over false success)"
```

---

## Task 4: Изоляция — широкий регресс + core diff пуст

**Files:** (только прогон/проверка, без правок кода)

- [ ] **Step 1: Прогнать всю vizir/hermes/task-сюиту**

Run: `cd /c/jarvis_worktrees/vizir-honest-accept && python -m pytest tests/ -k "vizir or hermes or task or loop or coordinator or acceptance" -v`
Expected: PASS без регрессий (док-число ориентир: ~204 vizir/hermes/task green + новые ~10). Известный флак `test_docker_exec_path_still_kills_child_on_cap_breach` (реальный subprocess под нагрузкой) — НЕ регрессия (см. RESUME); если красный — пере-прогнать одиночно.

- [ ] **Step 2: Доказать, что core/existing НЕ тронуты (diff пуст)**

Run:
```bash
cd /c/jarvis_worktrees/vizir-honest-accept
git diff --stat f53c256 -- app/services/vizir/loop.py app/services/vizir/coordinator.py app/services/vizir/handlers.py app/services/vizir/handlers_hermes.py app/services/vizir/hermes_acceptance.py tools/jarvis_smart_telegram_control.py
```
Expected: ПУСТОЙ вывод (ноль изменений в core и в bot-control). Изменения только в `task_acceptance.py` (new), `vizir_task_handler.py`, тестах, docs.

- [ ] **Step 3: Финальный лог веток**

Run: `git log --oneline f53c256..HEAD`
Expected: спека + 3 feat-коммита (Task1/2/3), чистая аддитивная арка.

---

## ✅ ЧЕК-ПОИНТ 2 (финал) — ОК Daniil перед живым

**Показать Daniil:**
- прогон вчерашних 4 кейсов через `accept_task` (можно ad-hoc): task-2 → accepted, task-3 → reject, task-4 → reject, (task-1 timeout — вне зоны приёмки, как было).
- вывод изоляции (core diff пуст), полная сюита зелёная.

**СТОП. Живой прогон (пере-тест «крестики» через `/task` в боте: должен дать реальный код инлайн ИЛИ честно эскалировать) — ОТДЕЛЬНЫМ ОК Daniil ПОСЛЕ зелёных моков + рестарт бота через гардиан.** Не мерджить и не рестартить без ОК.

---

## Self-Review (выполнено при написании плана)

**1. Покрытие спеки:** Слой 1 (1a/1b) → Task 1 (зубы A/D/C/H). Слой 2 (build-detect+код) → Task 2 (зубы B/E/precision/B2). Ложные срабатывания §6 → зуб H (URL) + co-occurrence в 1a. retry+actionable-фидбек §7 → Task 3 тест (attempt_rejected reasons). Деньги §8 (✅ только accepted) → Task 3 (`document_path is None`, `escalated`). Судья off §9 → не реализуем, чистая сигнатура `accept_task(value, goal)`. Спай-зубы A–G §11 → покрыты (G изоляция = Task 4). Все требования имеют задачу.

**2. Плейсхолдеры:** нет TBD/TODO; весь код показан целиком.

**3. Консистентность типов:** `accept_task(value: dict, goal: str) -> AcceptanceResult` (из `hermes_acceptance`) единообразно во всех задачах; `_layer1`/`_is_build_task`/`_has_code`/`_text` определены в Task 1/2 и используются согласованно; handler-правка использует ровно эту сигнатуру goal-bound.
