from __future__ import annotations

"""Phase 13: File processing tests.

Verifies:
1. parse_pdf / parse_docx / parse_xlsx / parse_csv / parse_text — return correct structure
2. parse_file auto-detects by extension
3. summarize_parse_result produces human-readable string
4. Bot routing: document/photo message → _handle_file_message
5. File-aware intents: summarize_file, extract_from_file, accounting
6. run_intent routes file intents correctly
"""

import csv
import io
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.services.file_parsers import (
    parse_csv_file,
    parse_text_file,
    parse_file,
    summarize_parse_result,
)


# ---------------------------------------------------------------------------
# parse_text_file
# ---------------------------------------------------------------------------

def test_parse_text_file_returns_text(tmp_path):
    f = tmp_path / "hello.txt"
    f.write_text("Hello world\nLine 2", encoding="utf-8")
    result = parse_text_file(str(f))
    assert result["ok"] is True
    assert "Hello world" in result["text"]
    assert result["metadata"]["line_count"] == 2


def test_parse_text_file_error_on_missing():
    result = parse_text_file("/nonexistent/file.txt")
    assert result["ok"] is False
    assert result["error"]


def test_parse_text_result_has_required_fields(tmp_path):
    f = tmp_path / "x.txt"
    f.write_text("test", encoding="utf-8")
    result = parse_text_file(str(f))
    assert set(result.keys()) >= {"ok", "text", "tables", "metadata", "error"}


# ---------------------------------------------------------------------------
# parse_csv_file
# ---------------------------------------------------------------------------

def test_parse_csv_returns_rows(tmp_path):
    f = tmp_path / "data.csv"
    f.write_text("name,price\nOpenAI,$20\nAnthropic,$20\n", encoding="utf-8")
    result = parse_csv_file(str(f))
    assert result["ok"] is True
    assert result["metadata"]["row_count"] == 3  # header + 2 rows
    assert result["tables"][0][0] == ["name", "price"]


def test_parse_csv_text_preview(tmp_path):
    f = tmp_path / "data.csv"
    f.write_text("a,b\n1,2\n", encoding="utf-8")
    result = parse_csv_file(str(f))
    assert "a" in result["text"]
    assert "1" in result["text"]


# ---------------------------------------------------------------------------
# parse_file — auto-detection by extension
# ---------------------------------------------------------------------------

def test_parse_file_dispatches_txt(tmp_path):
    f = tmp_path / "note.txt"
    f.write_text("test content", encoding="utf-8")
    result = parse_file(str(f))
    assert result["ok"] is True
    assert "test content" in result["text"]


def test_parse_file_dispatches_csv(tmp_path):
    f = tmp_path / "data.csv"
    f.write_text("col1,col2\nval1,val2\n", encoding="utf-8")
    result = parse_file(str(f))
    assert result["ok"] is True


def test_parse_file_dispatches_md(tmp_path):
    f = tmp_path / "readme.md"
    f.write_text("# Title\nContent", encoding="utf-8")
    result = parse_file(str(f))
    assert result["ok"] is True
    assert "Title" in result["text"]


def test_parse_file_unsupported_extension():
    result = parse_file("/some/file.xyz")
    assert result["ok"] is False
    assert "Unsupported" in result["error"]


def test_parse_file_dispatches_by_mime(tmp_path):
    f = tmp_path / "noext"
    f.write_text("plain text content", encoding="utf-8")
    result = parse_file(str(f), mime_type="text/plain")
    assert result["ok"] is True


# ---------------------------------------------------------------------------
# summarize_parse_result
# ---------------------------------------------------------------------------

def test_summarize_ok_result():
    result = {"ok": True, "text": "x" * 100, "tables": [], "metadata": {"line_count": 5}}
    summary = summarize_parse_result(result, "test.txt")
    assert "test.txt" in summary
    assert "📄" in summary


def test_summarize_error_result():
    result = {"ok": False, "text": "", "tables": [], "metadata": {}, "error": "File not found"}
    summary = summarize_parse_result(result)
    assert "⚠️" in summary
    assert "File not found" in summary


def test_summarize_pdf_metadata():
    result = {"ok": True, "text": "hello", "tables": [], "metadata": {"page_count": 5, "table_count": 2}}
    summary = summarize_parse_result(result, "report.pdf")
    assert "5 стр" in summary


def test_summarize_csv_metadata():
    result = {"ok": True, "text": "data", "tables": [[]], "metadata": {"row_count": 100, "col_count": 5}}
    summary = summarize_parse_result(result, "data.csv")
    assert "100 строк" in summary


# ---------------------------------------------------------------------------
# Bot file routing — classify_message with file intents
# ---------------------------------------------------------------------------

def test_summarize_file_intent_when_file_in_state():
    from tools.jarvis_smart_telegram_control import classify_message
    state = {"last_uploaded_file": {"path": "/tmp/f.pdf", "filename": "f.pdf"}}
    result = classify_message("суммируй файл", state)
    assert result["intent"] == "summarize_file"


def test_extract_from_file_intent_when_file_in_state():
    from tools.jarvis_smart_telegram_control import classify_message
    state = {"last_uploaded_file": {"path": "/tmp/f.pdf", "filename": "f.pdf"}}
    result = classify_message("извлеки данные", state)
    assert result["intent"] == "extract_from_file"


def test_accounting_intent_when_file_in_state():
    from tools.jarvis_smart_telegram_control import classify_message
    state = {"last_uploaded_file": {"path": "/tmp/f.pdf", "filename": "f.pdf"}}
    result = classify_message("бухгалтерия", state)
    assert result["intent"] == "accounting"


def test_file_intent_not_triggered_without_uploaded_file():
    from tools.jarvis_smart_telegram_control import classify_message
    # Without last_uploaded_file, "суммируй файл" should NOT route to summarize_file
    result = classify_message("суммируй файл", {})
    assert result["intent"] != "summarize_file"


# ---------------------------------------------------------------------------
# _handle_file_intent — no file in state sends helpful message
# ---------------------------------------------------------------------------

def test_handle_file_intent_no_file_sends_prompt(monkeypatch):
    import tools.jarvis_smart_telegram_control as mod
    sent = []
    monkeypatch.setattr(mod, "send", lambda cid, txt, **kw: sent.append(txt))
    state = mod.default_state()
    mod._handle_file_intent("123", "summarize_file", "", state)
    assert len(sent) == 1
    assert "файл" in sent[0].lower() or "отправь" in sent[0].lower()


# ---------------------------------------------------------------------------
# run_intent routes file intents
# ---------------------------------------------------------------------------

def test_run_intent_summarize_calls_handle_file(monkeypatch):
    import tools.jarvis_smart_telegram_control as mod
    called = []
    monkeypatch.setattr(mod, "_handle_file_intent", lambda cid, intent, q, s: called.append(intent))
    mod.run_intent("123", {"intent": "summarize_file", "query": "суммируй"}, {})
    assert called == ["summarize_file"]


def test_run_intent_accounting_calls_handle_file(monkeypatch):
    import tools.jarvis_smart_telegram_control as mod
    called = []
    monkeypatch.setattr(mod, "_handle_file_intent", lambda cid, intent, q, s: called.append(intent))
    mod.run_intent("123", {"intent": "accounting", "query": "счета"}, {})
    assert called == ["accounting"]


# ---------------------------------------------------------------------------
# Phase 13 fix: classify_file_caption tests
# ---------------------------------------------------------------------------

def test_caption_prosmoтри_routes_to_summarize():
    from tools.jarvis_smart_telegram_control import classify_file_caption
    result = classify_file_caption("Привет, просмотри что это за файлы и расскажи про них")
    assert result["intent"] == "summarize_file"


def test_caption_chto_eto_routes_to_summarize():
    from tools.jarvis_smart_telegram_control import classify_file_caption
    result = classify_file_caption("что это такое?")
    assert result["intent"] == "summarize_file"


def test_caption_rasskazhi_pro_routes_to_summarize():
    from tools.jarvis_smart_telegram_control import classify_file_caption
    result = classify_file_caption("расскажи про эти файлы")
    assert result["intent"] == "summarize_file"


def test_caption_izvleki_routes_to_extract():
    from tools.jarvis_smart_telegram_control import classify_file_caption
    result = classify_file_caption("извлеки данные из этого файла")
    assert result["intent"] == "extract_from_file"


def test_caption_vytashchi_routes_to_extract():
    from tools.jarvis_smart_telegram_control import classify_file_caption
    result = classify_file_caption("вытащи суммы")
    assert result["intent"] == "extract_from_file"


def test_caption_buhgalteriya_routes_to_accounting():
    from tools.jarvis_smart_telegram_control import classify_file_caption
    result = classify_file_caption("бухгалтерия за март")
    assert result["intent"] == "accounting"


def test_caption_nakладная_routes_to_accounting():
    from tools.jarvis_smart_telegram_control import classify_file_caption
    result = classify_file_caption("накладная от поставщика")
    assert result["intent"] == "accounting"


def test_caption_naydi_v_routes_to_ask():
    from tools.jarvis_smart_telegram_control import classify_file_caption
    result = classify_file_caption("найди в файле упоминание Иванова")
    assert result["intent"] == "ask_about_file"


def test_caption_empty_defaults_to_summarize():
    from tools.jarvis_smart_telegram_control import classify_file_caption
    result = classify_file_caption("")
    assert result["intent"] == "summarize_file"


def test_caption_unknown_defaults_to_summarize():
    from tools.jarvis_smart_telegram_control import classify_file_caption
    result = classify_file_caption("какой-то непонятный текст")
    assert result["intent"] == "summarize_file"


def test_caption_prochitay_routes_to_summarize():
    from tools.jarvis_smart_telegram_control import classify_file_caption
    result = classify_file_caption("прочитай и объясни")
    assert result["intent"] == "summarize_file"


# ---------------------------------------------------------------------------
# Phase 13 fix: _handle_file_message uses classify_file_caption
# ---------------------------------------------------------------------------

def test_handle_file_message_document_calls_classify_file_caption(monkeypatch):
    import tools.jarvis_smart_telegram_control as mod
    captured_captions = []
    monkeypatch.setattr(mod, "_download_telegram_file", lambda fid, fn: "/tmp/test.pdf")
    monkeypatch.setattr(mod, "_parse_file_safe", lambda p, m: {"ok": True, "text": "content", "_summary": "PDF"})
    monkeypatch.setattr(mod, "save_state", lambda s: None)
    monkeypatch.setattr(mod, "classify_file_caption", lambda c: (captured_captions.append(c), {"intent": "summarize_file", "query": c})[1])
    monkeypatch.setattr(mod, "run_intent", lambda cid, pack, s: None)
    monkeypatch.setattr(mod, "send", lambda cid, txt, **kw: None)

    msg = {
        "document": {"file_id": "abc123", "file_name": "report.pdf", "mime_type": "application/pdf"},
        "caption": "просмотри что это",
        "chat": {"id": 123},
    }
    state = mod.default_state()
    mod._handle_file_message("123", msg, state)
    assert "просмотри что это" in captured_captions


def test_handle_file_message_photo_no_caption_sends_hint(monkeypatch):
    import tools.jarvis_smart_telegram_control as mod
    sent = []
    monkeypatch.setattr(mod, "_download_telegram_file", lambda fid, fn: "/tmp/photo.jpg")
    monkeypatch.setattr(mod, "_parse_file_safe", lambda p, m: {"ok": True, "text": "", "_summary": "Фото"})
    monkeypatch.setattr(mod, "save_state", lambda s: None)
    monkeypatch.setattr(mod, "send", lambda cid, txt, **kw: sent.append(txt))

    msg = {
        "photo": [{"file_id": "ph1", "file_size": 100}, {"file_id": "ph2", "file_size": 500}],
        "chat": {"id": 123},
    }
    state = mod.default_state()
    mod._handle_file_message("123", msg, state)
    assert any("суммируй" in s.lower() or "файл" in s.lower() for s in sent)


def test_handle_file_message_forward_with_document(monkeypatch):
    import tools.jarvis_smart_telegram_control as mod
    sent = []
    monkeypatch.setattr(mod, "_download_telegram_file", lambda fid, fn: "/tmp/fwd.pdf")
    monkeypatch.setattr(mod, "_parse_file_safe", lambda p, m: {"ok": True, "text": "fwd content", "_summary": "PDF"})
    monkeypatch.setattr(mod, "save_state", lambda s: None)
    monkeypatch.setattr(mod, "send", lambda cid, txt, **kw: sent.append(txt))

    msg = {
        "document": {"file_id": "fwd_id", "file_name": "fwd.pdf", "mime_type": "application/pdf"},
        "forward_from": {"first_name": "Alice", "id": 999},
        "chat": {"id": 123},
    }
    state = mod.default_state()
    mod._handle_file_message("123", msg, state)
    assert any("fwd.pdf" in s or "файл" in s.lower() for s in sent)
    assert state.get("last_uploaded_file") is not None


def test_caption_raskazhi_chto_routes_to_summarize():
    from tools.jarvis_smart_telegram_control import classify_file_caption
    result = classify_file_caption("расскажи что здесь написано")
    assert result["intent"] == "summarize_file"
