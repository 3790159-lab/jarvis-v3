# -*- coding: utf-8 -*-
"""Vizir Hermes acceptance — Phase M (mocks, $0, TDD).

Acceptance ("did Hermes do it RIGHT?") is Vizir's job, kept separate from the
handler (which only runs Hermes + meters cost). For the FIRST knee (Path A — a
Jarvis-styled web chat as ONE self-contained HTML file) the check is fully
DETERMINISTIC (no paid LLM judge): the artifact must be an HTML doc with a chat
structure, a dark futuristic theme, an online/offline status, and JS that POSTs
to the backend at localhost:8010 via ``const API_URL``."""
from app.services.vizir.hermes_acceptance import (
    check_chat_acceptance, accept_hermes_chat,
)


GOOD_HTML = """<!doctype html>
<html><head><style>
  body { background:#0a0a12; color:#e0faff; }
  .accent { color: cyan; box-shadow: 0 0 8px #00ffff; }
  #messages { overflow-y: auto; }
</style></head>
<body>
  <header>JARVIS <span id="status">online</span></header>
  <div id="messages" class="chat"></div>
  <textarea id="input"></textarea>
  <button id="send">Send</button>
  <script>
    const API_URL = "http://localhost:8010";
    async function send(text){
      try { const r = await fetch(API_URL, {method:"POST", body: JSON.stringify({text})});
            /* render reply */ }
      catch(e){ document.getElementById("status").textContent = "offline"; }
    }
    document.getElementById("input").addEventListener("keydown", e => {});
  </script>
</body></html>"""


def test_good_chat_is_accepted():
    res = check_chat_acceptance(GOOD_HTML)
    assert res.accepted is True, res.reasons
    assert res.reasons == []


def test_missing_api_url_is_rejected():
    bad = GOOD_HTML.replace('const API_URL = "http://localhost:8010";', 'var x = 1;')
    res = check_chat_acceptance(bad)
    assert res.accepted is False
    assert any("API_URL" in r for r in res.reasons)
    assert any("8010" in r for r in res.reasons)


def test_light_theme_no_neon_is_rejected():
    bad = GOOD_HTML.replace("background:#0a0a12", "background:#ffffff") \
                   .replace("color: cyan", "color: black") \
                   .replace("#00ffff", "#333333")
    res = check_chat_acceptance(bad)
    assert res.accepted is False
    assert any("dark" in r.lower() for r in res.reasons)
    assert any("neon" in r.lower() or "accent" in r.lower() for r in res.reasons)


def test_not_html_is_rejected():
    res = check_chat_acceptance("here is your chat: just some prose, no markup")
    assert res.accepted is False
    assert any("html" in r.lower() for r in res.reasons)


def test_no_chat_structure_is_rejected():
    bare = "<!doctype html><html><body><p>hello</p></body></html>"
    res = check_chat_acceptance(bare)
    assert res.accepted is False
    assert any("input" in r.lower() for r in res.reasons)


def test_accept_hermes_chat_reads_handler_result_value():
    value = {"final_response": GOOD_HTML, "stopped_reason": "completed",
             "artifact_path": None}
    res = accept_hermes_chat(value)
    assert res.accepted is True, res.reasons


def test_max_iterations_run_is_not_accepted_even_with_good_html():
    # truncated agent: artifact looks fine, but it didn't finish -> not accepted
    value = {"final_response": GOOD_HTML, "stopped_reason": "max_iterations",
             "artifact_path": None}
    res = accept_hermes_chat(value)
    assert res.accepted is False
    assert any("did not complete" in r for r in res.reasons)


def test_cost_cap_run_is_not_accepted():
    value = {"final_response": GOOD_HTML, "stopped_reason": "cost_cap",
             "artifact_path": None}
    res = accept_hermes_chat(value)
    assert res.accepted is False
    assert any("did not complete" in r for r in res.reasons)
