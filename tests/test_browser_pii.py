# -*- coding: utf-8 -*-
"""PII scrubber + PII-safe TG report. $0, no browser, no network.

Monitoring reports must NEVER carry cookies/headers/tokens/secret values into
Telegram — only the extracted content + cost + steps. See plan §5bis.
"""
from app.services.browser.pii import scrub_pii, build_report
from app.services.browser.engine import BrowserResult


def test_scrub_masks_cookies_headers_tokens():
    raw = ("Set-Cookie: session=abc123DEF; Path=/\n"
           "Authorization: Bearer sk-ant-9f8e7d6c5b4a\n"
           "cookie: uid=deadbeef")
    out = scrub_pii(raw)
    assert "abc123DEF" not in out
    assert "sk-ant-9f8e7d6c5b4a" not in out
    assert "deadbeef" not in out
    assert "***" in out                       # something was masked


def test_scrub_masks_explicit_secret_values():
    out = scrub_pii("login ok, password was hunter2 and token QWERTY",
                    secrets=["hunter2", "QWERTY"])
    assert "hunter2" not in out and "QWERTY" not in out


def test_scrub_keeps_benign_extracted_text():
    out = scrub_pii("Цена товара: 1499 руб, в наличии")
    assert "1499" in out and "наличии" in out   # benign content survives


def test_build_report_excludes_forbidden_fields_carries_extract():
    res = BrowserResult(steps=6, cost_usd=0.12, extracted="Цена: 1499 руб",
                        stopped_reason="done",
                        raw_dom="<html>Set-Cookie: s=secret</html>",
                        headers={"Authorization": "Bearer sk-ant-xxx"})
    report = build_report(res)
    assert "1499" in report and "0.12" in report and "6" in report   # extract+cost+steps
    assert "Set-Cookie" not in report and "Authorization" not in report
    assert "sk-ant" not in report and "<html>" not in report          # no dom/headers
