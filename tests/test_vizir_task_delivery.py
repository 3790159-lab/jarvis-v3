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


def _mock_text(final_response, cost=0.10):
    async def handler(step, ctx):
        rc = ctx.get("report_cost")
        if rc and cost:
            rc(cost)
        return HandlerResult(ok=True, cost_usd=cost, result={
            "final_response": final_response, "stopped_reason": "completed"})
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
    assert "крестики" in cap["prompt"]


# --- Зуб 4a (goal-чистота): текст-задача "что умеешь" НЕ ложно-reject как build-task ---
def test_tooth4_text_task_accepted_not_false_build(tmp_path):
    answer = "Я — Claude Code. Умею: писать код, отвечать на вопросы, работать с файлами."
    hermes = _mock_text(answer)
    h = _mk(tmp_path, hermes)
    rep = _run(h.run_task_phase(chat_id=1, base_prompt="что ты умеешь?",
                                progress_cb=lambda s, p: None, user_id=1, username="d"))
    assert rep.escalated is False, rep.text


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


def test_extract_prose_mentioning_tag_is_text():
    # a text answer that merely MENTIONS a tag (no fence, no full document) -> text, not html
    fr = "Чтобы обернуть контент, используй тег <div>. Это блочный элемент."
    kind, content = _extract_artifact(fr)
    assert kind == "text", (kind, content)
    assert content == fr


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
