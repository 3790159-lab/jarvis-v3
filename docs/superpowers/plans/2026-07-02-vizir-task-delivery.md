# Vizir `/task` output-contract + доставка — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Заставить `/task` отдавать РАБОЧИЙ результат в Telegram, открываемый с телефона: output-contract преамбула чинит источник (Hermes отдаёт код инлайн, не врёт про файл), а handler снимает code-fence и доставляет чистый `.html` документом (`text/html`); текст-ответы идут сообщением.

**Architecture:** Правки только в арк-файле `app/handlers/vizir_task_handler.py` (преамбула в Hermes-промт при неизменном приёмочном `goal=base_prompt`; извлечение артефакта из `final_response` со снятием fence; выбор формата код→`.html`/текст→сообщение) + обратно-совместимый `mime`-параметр в `tools/jarvis_smart_telegram_control.py` (`_send_local_document`/`_task_apply_reply`). `accept_task` и весь `app/services/vizir/*` core НЕ трогаются. Спай-зубы мутацией на моках, $0 до живого.

**Tech Stack:** Python 3.11+, pytest. Запуск из корня worktree `C:\jarvis_worktrees\vizir-task-delivery` интерпретатором `C:\jarvis\.venv\Scripts\python.exe`.

**Дисциплина:** спай-зубы ПЕРВЫМИ, мутацией в обе стороны. Аддитивно. Бот приоритет, core/existing целы. worktree `vizir-task-delivery` @ `d4fd98c`.

---

## Файловая структура
- **Modify** `app/handlers/vizir_task_handler.py`: +`OUTPUT_CONTRACT`, +`_compose_hermes_prompt`, +`_extract_artifact`/`_slice_html`, изменить `run_task_phase` (промт→loop, accepted-ветка формат-выбор). Одна ответственность: запуск `/task`-loop + доставка результата.
- **Modify** `tools/jarvis_smart_telegram_control.py`: `_send_local_document` +опц. `mime` (дефолт `application/zip`); `_task_apply_reply` шлёт документ с `mime="text/html"`.
- **Create** `tests/test_vizir_task_delivery.py`: зубы 1–6 (мок Hermes + importlib-загрузка бот-тула).
- **НЕ ТРОГАТЬ:** `app/services/vizir/loop.py|coordinator.py|handlers.py|handlers_hermes.py|hermes_acceptance.py|task_acceptance.py`, existing бот-команды/тесты.

**Тест-хелперы (существуют в `tests/test_vizir_task_handler.py`, переиспользуем паттерн):** `_run(coro)=asyncio.run`; `_mock_hermes(final_response, stopped_reason, ok, cost, error)`; `_handler(tmp_path, hermes, **cfg) -> (h, calls)`; `VizirTaskHandler(...)`; `HandlerResult`. Бот-тул грузится `importlib.util.spec_from_file_location` с `patch.dict(os.environ, {...})` (паттерн из `tests/test_animate_batch_wiring.py:28-39`).

---

## Task 1: Output-contract преамбула (источник) + goal-чистота

**Files:**
- Modify: `app/handlers/vizir_task_handler.py`
- Test: `tests/test_vizir_task_delivery.py`

- [ ] **Step 1: Написать падающие спай-зубы (преамбула + goal-чистота)**

Создать `tests/test_vizir_task_delivery.py`:

```python
# tests/test_vizir_task_delivery.py
# -*- coding: utf-8 -*-
"""Спай-зубы output-contract + доставки /task. $0 (real Coordinator + mock Hermes)."""
import asyncio
from pathlib import Path

from app.handlers.vizir_task_handler import VizirTaskHandler
from app.services.vizir.handlers import HandlerResult


def _run(coro):
    return asyncio.run(coro)


def _capturing_hermes(final_response, captured, *, stopped_reason="completed", cost=0.10):
    """Mock Hermes that records the prompt it received (to inspect the preamble)."""
    async def handler(step, ctx):
        captured["prompt"] = step.params["prompt"]
        rc = ctx.get("report_cost")
        if rc and cost:
            rc(cost)
        return HandlerResult(ok=True, cost_usd=cost, result={
            "final_response": final_response, "stopped_reason": stopped_reason})
    return handler


def _mk(tmp_path, hermes, **cfg):
    return VizirTaskHandler(
        hermes_handler=hermes, artifact_dir=tmp_path,
        budget_usd=cfg.get("budget_usd", 0.90), min_attempt_usd=0.20,
        max_usd=0.40, estimated_per_attempt_usd=0.15,
        max_attempts=cfg.get("max_attempts", 2), loop_deadline_s=600.0)


# --- Зуб 1: output-contract преамбула реально уходит в Hermes-промт ---
def test_tooth1_preamble_injected_into_hermes_prompt(tmp_path):
    cap = {}
    hermes = _capturing_hermes("<html><body>ok</body></html>", cap)
    h = _mk(tmp_path, hermes)
    _run(h.run_task_phase(chat_id=1, base_prompt="сделай веб игру крестики нолики",
                          progress_cb=lambda s, p: None, user_id=1, username="d"))
    assert "prompt" in cap
    assert "не можешь создавать файлы" in cap["prompt"].lower()
    assert "инлайн" in cap["prompt"].lower()
    # база юзера сохранена в промте (immutable base)
    assert "крестики" in cap["prompt"]


# --- Зуб 4a (goal-чистота): текст-задача "что умеешь" НЕ ложно-reject как build-task ---
def test_tooth4_text_task_accepted_not_false_build(tmp_path):
    # если бы приёмочный goal = преамбула+base (а не сырой base), слова
    # "создавать/код/файл" из преамбулы задетектили бы build-task -> текст без кода
    # ложно-reject. Этот зуб ловит регресс goal-чистоты.
    answer = "Я — Claude Code. Умею: писать код, отвечать на вопросы, работать с файлами."
    hermes = _mock_text(answer)
    h = _mk(tmp_path, hermes)
    rep = _run(h.run_task_phase(chat_id=1, base_prompt="что ты умеешь?",
                                progress_cb=lambda s, p: None, user_id=1, username="d"))
    assert rep.escalated is False, rep.text


def _mock_text(final_response, cost=0.10):
    async def handler(step, ctx):
        rc = ctx.get("report_cost")
        if rc and cost:
            rc(cost)
        return HandlerResult(ok=True, cost_usd=cost, result={
            "final_response": final_response, "stopped_reason": "completed"})
    return handler
```

- [ ] **Step 2: Прогнать — убедиться, что падает**

Run: `cd /c/jarvis_worktrees/vizir-task-delivery && /c/jarvis/.venv/Scripts/python.exe -m pytest tests/test_vizir_task_delivery.py -k "tooth1 or tooth4" -v`
Expected: `test_tooth1_...` FAIL (промт без преамбулы — `"не можешь создавать файлы"` отсутствует). `test_tooth4_...` — сейчас проходит (goal уже = base_prompt), это регресс-страховка; ок если green.

- [ ] **Step 3: Реализовать преамбулу + проводку**

В `app/handlers/vizir_task_handler.py` добавить ПОСЛЕ импортов (после строки `from app.services.vizir.task_acceptance import accept_task`):

```python
OUTPUT_CONTRACT = (
    "У тебя НЕТ инструментов file/terminal/web — ты НЕ можешь создавать файлы на диске. "
    "Верни ПОЛНЫЙ результат ИНЛАЙН прямо в ответе. Если задача просит код/артефакт — "
    "верни весь рабочий код одним блоком ```. Если задача просит текст/ответ — просто "
    "ответь инлайн. НИКОГДА не пиши «файл создан по адресу …» / «сохранил в …» — ты "
    "этого не можешь, это будет ложь."
)


def _compose_hermes_prompt(base_prompt: str) -> str:
    """Prepend the output-contract to the user's request. The ACCEPTANCE goal stays
    the raw base_prompt (see run_task_phase) so build-task detection is not polluted
    by the contract's words (создавать/код/файл)."""
    return OUTPUT_CONTRACT + "\n\n" + base_prompt
```

В `run_task_phase` заменить строку
```python
        rep = await loop.run(task, base_prompt)
```
на
```python
        rep = await loop.run(task, _compose_hermes_prompt(base_prompt))
```
`accept_fn = self._accept_fn or (lambda v: accept_task(v, goal=base_prompt))` — НЕ менять (goal остаётся сырым base_prompt). `task = Task(goal=base_prompt[:80], …)` — НЕ менять.

- [ ] **Step 4: Прогнать — зелено**

Run: `cd /c/jarvis_worktrees/vizir-task-delivery && /c/jarvis/.venv/Scripts/python.exe -m pytest tests/test_vizir_task_delivery.py -k "tooth1 or tooth4" -v`
Expected: PASS (оба).

- [ ] **Step 5: Мутация (доказать зуб 1 кусается)**

Временно вернуть `rep = await loop.run(task, base_prompt)` (без преамбулы). Run `-k tooth1` → КРАСНЫЙ. Вернуть → зелёный.

- [ ] **Step 6: Коммит**
```bash
cd /c/jarvis_worktrees/vizir-task-delivery
git add app/handlers/vizir_task_handler.py tests/test_vizir_task_delivery.py
git commit -m "feat(vizir): output-contract preamble into /task Hermes prompt (goal stays raw base)"
```

---

## Task 2: Извлечение артефакта (снятие fence) + выбор формата

**Files:**
- Modify: `app/handlers/vizir_task_handler.py`
- Test: `tests/test_vizir_task_delivery.py`

- [ ] **Step 1: Написать падающие спай-зубы (fence / формат)**

Добавить в `tests/test_vizir_task_delivery.py`:

```python
from app.handlers.vizir_task_handler import _extract_artifact


# --- unit: _extract_artifact снимает fence и определяет html vs text ---
def test_extract_fenced_html_stripped():
    fr = ("Готово, вот игра:\n```html\n<!doctype html><html><body>"
          "<script>function move(i){return i}</script></body></html>\n```\nОткрой в браузере.")
    kind, content = _extract_artifact(fr)
    assert kind == "html"
    assert content.lower().startswith("<!doctype html")
    assert "```" not in content
    assert content.rstrip().endswith("</html>")


def test_extract_raw_html_no_fence():
    fr = "<!doctype html><html><body>hi</body></html>"
    kind, content = _extract_artifact(fr)
    assert kind == "html" and "```" not in content


def test_extract_plain_text_is_text():
    fr = "Я умею писать код, отвечать на вопросы и решать задачи."
    kind, content = _extract_artifact(fr)
    assert kind == "text"
    assert content == fr


# --- Зуб 2 (КРИТИЧНО): fenced HTML -> записанный .html чист (без ```), открываем ---
def test_tooth2_written_html_is_clean(tmp_path):
    fr = "Вот:\n```html\n<!doctype html><html><body>X</body></html>\n```"
    hermes = _mock_text(fr)
    h = _mk(tmp_path, hermes)
    rep = _run(h.run_task_phase(chat_id=1, base_prompt="сделай веб страницу",
                                progress_cb=lambda s, p: None, user_id=1, username="d"))
    assert rep.document_path is not None
    body = Path(rep.document_path).read_text(encoding="utf-8")
    assert body.lower().startswith("<!doctype html")
    assert "```" not in body


# --- Зуб 3: build-task + инлайн HTML -> .html документ ---
def test_tooth3_code_task_delivers_html_document(tmp_path):
    fr = "```html\n<!doctype html><html><body>game</body></html>\n```"
    hermes = _mock_text(fr)
    h = _mk(tmp_path, hermes)
    rep = _run(h.run_task_phase(chat_id=1, base_prompt="сделай веб игру крестики нолики",
                                progress_cb=lambda s, p: None, user_id=1, username="d"))
    assert rep.escalated is False
    assert rep.document_path is not None
    assert str(rep.document_path).endswith(".html")


# --- Зуб 4b: текст-ответ -> сообщение, .html документ НЕ плодится ---
def test_tooth4b_text_answer_message_not_document(tmp_path):
    answer = "Я умею писать код, отвечать на вопросы, работать с файлами."
    hermes = _mock_text(answer)
    h = _mk(tmp_path, hermes)
    rep = _run(h.run_task_phase(chat_id=1, base_prompt="что ты умеешь?",
                                progress_cb=lambda s, p: None, user_id=1, username="d"))
    assert rep.escalated is False
    assert rep.document_path is None
    assert "умею" in rep.text.lower()


# --- Зуб 5: галлюцинация-указатель -> честная эскалация, без файла ---
def test_tooth5_hallucination_escalates_no_file(tmp_path):
    fr = "Готово! Игра создана по адресу C:\\Users\\Admin\\Desktop\\ttt\\index.html"
    hermes = _mock_text(fr)
    h = _mk(tmp_path, hermes, max_attempts=2)
    rep = _run(h.run_task_phase(chat_id=1, base_prompt="сделай веб игру крестики нолики",
                                progress_cb=lambda s, p: None, user_id=1, username="d"))
    assert rep.escalated is True
    assert rep.document_path is None
```

- [ ] **Step 2: Прогнать — падает**

Run: `cd /c/jarvis_worktrees/vizir-task-delivery && /c/jarvis/.venv/Scripts/python.exe -m pytest tests/test_vizir_task_delivery.py -k "extract or tooth2 or tooth3 or tooth4b or tooth5" -v`
Expected: `_extract_artifact`-тесты FAIL (`ImportError`/нет функции); `tooth2` FAIL (сейчас пишется сырой fence); `tooth4b` FAIL (сейчас accepted пишет `.html`, не сообщение). `tooth3`/`tooth5` могут проходить частично.

- [ ] **Step 3: Реализовать извлечение + формат-выбор**

В `app/handlers/vizir_task_handler.py`: добавить в шапку `import re` (рядом с `import itertools`), и добавить ПОСЛЕ `_compose_hermes_prompt`:

```python
_FENCE_RE = re.compile(r"```[a-zA-Z0-9]*\n(.*?)```", re.S)
_HTML_MARK = ("<!doctype html", "<html", "<script", "<body", "<div", "<style")


def _slice_html(s: str) -> str:
    """Trim prose around the HTML: from the first html marker to </html> (or end)."""
    low = s.lower()
    starts = [low.find(m) for m in ("<!doctype html", "<html") if low.find(m) != -1]
    start = min(starts) if starts else 0
    end_idx = low.rfind("</html>")
    end = end_idx + len("</html>") if end_idx != -1 else len(s)
    return s[start:end].strip()


def _extract_artifact(final_response: str):
    """(kind, content). 'html' -> clean HTML for a .html file (code-fence stripped,
    prose trimmed); 'text' -> deliver inline as a message. See design §5."""
    raw = (final_response or "").strip()
    for body in _FENCE_RE.findall(raw):            # prefer a fenced HTML block
        if any(m in body.lower() for m in _HTML_MARK):
            return ("html", _slice_html(body))
    if any(m in raw.lower() for m in _HTML_MARK):  # raw inline HTML (legacy path)
        return ("html", _slice_html(raw))
    return ("text", raw)                            # plain text answer
```

Заменить accepted-ветку в `run_task_phase`. БЫЛО:
```python
        if rep.accepted:
            html = ""
            if isinstance(rep.last_result, dict):
                html = rep.last_result.get("final_response") or ""
            self._artifact_dir.mkdir(parents=True, exist_ok=True)
            out = self._artifact_dir / ("%s.html" % task_id)
            out.write_text(html, encoding="utf-8")
            text = ("✅ Готово за %d попыток. Потрачено $%.4f (кап $%.2f). Приёмка пройдена."
                    % (rep.attempts, rep.loop_spent_usd, self._budget_usd))
            return VizirTaskReply(text=text, document_path=out, escalated=False)
```
СТАЛО:
```python
        if rep.accepted:
            final = ""
            if isinstance(rep.last_result, dict):
                final = rep.last_result.get("final_response") or ""
            kind, content = _extract_artifact(final)
            summary = ("✅ Готово за %d попыток. Потрачено $%.4f (кап $%.2f). Приёмка пройдена."
                       % (rep.attempts, rep.loop_spent_usd, self._budget_usd))
            if kind == "html":
                self._artifact_dir.mkdir(parents=True, exist_ok=True)
                out = self._artifact_dir / ("%s.html" % task_id)
                out.write_text(content, encoding="utf-8")
                return VizirTaskReply(text=summary, document_path=out, escalated=False)
            # текст-ответ -> сообщением, без .html документа
            return VizirTaskReply(text=summary + "\n\n" + content,
                                  document_path=None, escalated=False)
```
Эскалационную ветку (`return VizirTaskReply(text=…, document_path=None, escalated=True)`) НЕ менять.

- [ ] **Step 4: Прогнать — зелено**

Run: `cd /c/jarvis_worktrees/vizir-task-delivery && /c/jarvis/.venv/Scripts/python.exe -m pytest tests/test_vizir_task_delivery.py -v`
Expected: PASS — все зубы Task 1+2 зелёные.

- [ ] **Step 5: Мутации (зубы 2 и 4b кусаются)**

- Зуб 2: временно записывать `final` вместо `content` (`out.write_text(final,…)`). Run `-k tooth2` → КРАСНЫЙ (файл содержит ```` ``` ````). Вернуть → зелёный.
- Зуб 4b: временно всегда писать `.html` (убрать `if kind=="html"`, всегда документ). Run `-k tooth4b` → КРАСНЫЙ (`document_path` не None). Вернуть → зелёный.

- [ ] **Step 6: Коммит**
```bash
cd /c/jarvis_worktrees/vizir-task-delivery
git add app/handlers/vizir_task_handler.py tests/test_vizir_task_delivery.py
git commit -m "feat(vizir): extract inline artifact (strip fence) + code->.html / text->message delivery"
```

---

## ✅ ЧЕК-ПОИНТ 1 (после спай-зубов Task 1–2) — МУТАЦИЯ + ОК Daniil
Показать вывод мутаций: зуб 1 (преамбула убрана→красный), зуб 2 (fence не снят→красный),
зуб 4b (текст всегда файл→красный), при всех включённых — зелёные. **СТОП — ОК Daniil.**

---

## Task 3: MIME `text/html` в бот-туле (обратно-совместимо)

**Files:**
- Modify: `tools/jarvis_smart_telegram_control.py`
- Test: `tests/test_vizir_task_delivery.py`

- [ ] **Step 1: Написать падающие спай-зубы (MIME + роутинг)**

Добавить в `tests/test_vizir_task_delivery.py`:

```python
import importlib.util, os, sys
from unittest.mock import patch, MagicMock

_ROOT = Path(__file__).parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def _bot_module():
    spec = importlib.util.spec_from_file_location(
        "jarvis_tg_ctrl_delivery", _ROOT / "tools" / "jarvis_smart_telegram_control.py")
    mod = importlib.util.module_from_spec(spec)
    with patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "test",
                                 "TELEGRAM_ALLOWED_CHAT_ID": "123"}):
        spec.loader.exec_module(mod)
    return mod


# --- Зуб 6a: /task-путь шлёт .html документ с MIME text/html ---
def test_tooth6_task_apply_reply_sends_html_mime(tmp_path):
    bot = _bot_module()
    f = tmp_path / "task-1.html"; f.write_text("<!doctype html><html></html>", encoding="utf-8")
    reply = bot_reply(bot, text="ok", document_path=f, escalated=False)
    with patch.object(bot, "_send_local_document") as sld, patch.object(bot, "send"):
        bot._task_apply_reply("123", reply)
    sld.assert_called_once()
    assert sld.call_args.kwargs.get("mime") == "text/html"


# --- Зуб 6b (обратно-совместимость): _send_local_document без mime -> application/zip ---
def test_tooth6_send_document_default_mime_zip(tmp_path):
    bot = _bot_module()
    f = tmp_path / "r.zip"; f.write_bytes(b"PK\x03\x04zip")
    captured = {}
    def _fake_post(url, data=None, files=None, timeout=None):
        captured["mime"] = files["document"][2]
        return MagicMock()
    with patch("requests.post", _fake_post):
        bot._send_local_document("123", str(f))          # no mime -> default
    assert captured["mime"] == "application/zip"


def bot_reply(bot, **kw):
    # VizirTaskReply lives in the handler module; reuse it.
    from app.handlers.vizir_task_handler import VizirTaskReply
    return VizirTaskReply(**kw)
```

- [ ] **Step 2: Прогнать — падает**

Run: `cd /c/jarvis_worktrees/vizir-task-delivery && /c/jarvis/.venv/Scripts/python.exe -m pytest tests/test_vizir_task_delivery.py -k "tooth6" -v`
Expected: `test_tooth6_task_apply_reply_sends_html_mime` FAIL (сейчас `_send_local_document` без `mime`-kwarg → TypeError или mime не передан). `test_tooth6_send_document_default_mime_zip` — сейчас проходит (хардкод zip), станет регресс-страховкой.

- [ ] **Step 3: Реализовать mime-параметр + роутинг**

В `tools/jarvis_smart_telegram_control.py`, `_send_local_document` (около строки 301). БЫЛО:
```python
def _send_local_document(chat_id, path, caption: str = "") -> None:
    """Upload a local file (e.g. a results zip) via multipart sendDocument."""
    import requests as _req
    from pathlib import Path as _Path
    p = _Path(path)
    if not p.exists():
        send(str(chat_id), f"⚠️ Document not found: {p}")
        return
    with p.open("rb") as fh:
        _req.post(
            f"{TG}/sendDocument",
            data={"chat_id": str(chat_id), "caption": caption[:1024] if caption else ""},
            files={"document": (p.name, fh, "application/zip")},
            timeout=300,
        )
```
СТАЛО (добавлен `mime` с дефолтом — existing zip-вызовы целы):
```python
def _send_local_document(chat_id, path, caption: str = "",
                         mime: str = "application/zip") -> None:
    """Upload a local file via multipart sendDocument. `mime` defaults to zip for
    existing callers; /task passes text/html so a .html game opens on a phone."""
    import requests as _req
    from pathlib import Path as _Path
    p = _Path(path)
    if not p.exists():
        send(str(chat_id), f"⚠️ Document not found: {p}")
        return
    with p.open("rb") as fh:
        _req.post(
            f"{TG}/sendDocument",
            data={"chat_id": str(chat_id), "caption": caption[:1024] if caption else ""},
            files={"document": (p.name, fh, mime)},
            timeout=300,
        )
```
`_task_apply_reply` (около строки 1270). БЫЛО:
```python
    if reply.document_path is not None:
        _send_local_document(chat_id_s, str(reply.document_path))
```
СТАЛО:
```python
    if reply.document_path is not None:
        _send_local_document(chat_id_s, str(reply.document_path), mime="text/html")
```

- [ ] **Step 4: Прогнать — зелено**

Run: `cd /c/jarvis_worktrees/vizir-task-delivery && /c/jarvis/.venv/Scripts/python.exe -m pytest tests/test_vizir_task_delivery.py -v`
Expected: PASS — все зубы (Task 1–3) зелёные.

- [ ] **Step 5: Мутация (обратная совместимость)**

Временно поставить дефолт `mime: str = "text/html"`. Run `-k tooth6_send_document_default` → КРАСНЫЙ (existing zip-вызов получил бы text/html). Вернуть дефолт `application/zip` → зелёный. Доказывает, что existing zip не сломан.

- [ ] **Step 6: Коммит**
```bash
cd /c/jarvis_worktrees/vizir-task-delivery
git add tools/jarvis_smart_telegram_control.py tests/test_vizir_task_delivery.py
git commit -m "feat(vizir): deliver /task .html as text/html (backward-compat mime param, zip default kept)"
```

---

## Task 4: Изоляция — широкий регресс + core diff пуст

**Files:** (только прогон/проверка)

- [ ] **Step 1: Широкая vizir/hermes/task/бот-сюита**

Run: `cd /c/jarvis_worktrees/vizir-task-delivery && /c/jarvis/.venv/Scripts/python.exe -m pytest tests/ -k "vizir or hermes or task or loop or coordinator or acceptance or animate_batch or swap_sentinel or bot_" -q`
Expected: PASS без регрессий (≈251 vizir/hermes/task + бот-тул тесты зелёные). Известный докер-флак `test_docker_exec_path_still_kills_child_on_cap_breach` — НЕ регрессия (пере-прогнать одиночно при красном).

- [ ] **Step 2: core diff пуст**

Run:
```bash
cd /c/jarvis_worktrees/vizir-task-delivery
git diff --stat d4fd98c -- app/services/vizir/loop.py app/services/vizir/coordinator.py app/services/vizir/handlers.py app/services/vizir/handlers_hermes.py app/services/vizir/hermes_acceptance.py app/services/vizir/task_acceptance.py
```
Expected: ПУСТО (core и приёмка не тронуты).

- [ ] **Step 3: Диффстат арки + лог**

Run: `git diff --stat d4fd98c..HEAD && git log --oneline d4fd98c..HEAD`
Expected: только `vizir_task_handler.py` + `jarvis_smart_telegram_control.py` + новый тест + docs; 3 feat-коммита.

---

## ✅ ЧЕК-ПОИНТ 2 (финал) — ОК Daniil перед живым
Показать: полная сюита зелёная, core diff пуст, диффстат аддитивен. **СТОП. Живой тест
(крестики → `.html` → ОТКРЫТЬ С ТЕЛЕФОНА → игра работает) — ОТДЕЛЬНЫМ ОК + рестарт бота
через гардиан.** Не мерджить/не рестартить без ОК. Если Hermes упрётся/доставка сбоит →
сразу обсудить B (file-тулсет + sandbox).

---

## Self-Review (при написании плана)
**1. Покрытие спеки:** §4 преамбула → Task 1 (зуб 1) + goal-чистота (зуб 4a). §5 извлечение/
формат → Task 2 (extract-юниты + зубы 2/3/4b/5). §6 MIME → Task 3 (зубы 6a/6b). §7 приёмка-
страховка → зуб 5 (эскалация без файла). §8 не сломать → зуб 4 («что умеешь») + зуб 6b (zip)
+ Task 4 (core пуст). §9 все зубы 1–6 покрыты. §10 критерий → чек-поинт 2 живой.
**2. Плейсхолдеры:** нет; весь код показан.
**3. Консистентность типов:** `_extract_artifact(final_response)->(kind,content)`,
`_compose_hermes_prompt(base_prompt)->str`, `_send_local_document(chat_id,path,caption,mime)`,
`VizirTaskReply(text,document_path,escalated)` — единообразно во всех задачах.
