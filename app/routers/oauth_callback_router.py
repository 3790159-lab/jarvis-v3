from __future__ import annotations

"""OAuth manual-capture helper endpoint.

Two flows land here:

* Implicit (`response_type=token`) returns the access token in the URL
  *fragment* (`#access_token=...`). Fragments never reach the server, so the
  client-side JS reads `location.hash` and renders it with a copy button.

* Authorization-code (`response_type=code`, e.g. Instagram Business Login)
  returns `?code=...` in the *query string*, which the server DOES receive.
  We log it and write it to ``state/oauth_callback_last.json`` so the code can
  be picked up automatically and exchanged immediately (codes are one-time and
  expire within minutes). The client page still shows it as a fallback.
"""

import json
import logging
import time
from pathlib import Path
from typing import Optional

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

logger = logging.getLogger("jarvis.oauth")

# Written by the server when an authorization ?code= arrives; read out-of-band
# to perform the token exchange. Lives under the project state/ dir.
_PICKUP_FILE = Path(__file__).resolve().parents[2] / "state" / "oauth_callback_last.json"

router = APIRouter(tags=["oauth"])


_CALLBACK_PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Jarvis OAuth — Access Token</title>
<style>
  :root { color-scheme: light dark; }
  body {
    font-family: system-ui, -apple-system, Segoe UI, Roboto, sans-serif;
    margin: 0; padding: 2rem 1rem; background: #0b0f1a; color: #e6edf3;
    display: flex; justify-content: center;
  }
  .wrap { width: 100%; max-width: 760px; }
  h1 { font-size: 1.3rem; margin: 0 0 1.25rem; }
  .token-box {
    background: #111827; border: 1px solid #263043; border-radius: 10px;
    padding: 1rem; font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
    font-size: 1.05rem; line-height: 1.5; word-break: break-all;
    user-select: all; -webkit-user-select: all;
  }
  button {
    margin-top: 1rem; padding: 0.75rem 1.5rem; font-size: 1rem; cursor: pointer;
    background: #2563eb; color: #fff; border: 0; border-radius: 8px; font-weight: 600;
  }
  button:active { background: #1d4ed8; }
  .meta { margin-top: 1rem; color: #9aa7b8; font-size: 0.9rem; }
  .meta code { color: #cbd5e1; }
  .err {
    background: #2a1215; border: 1px solid #5b2530; color: #ffb4b4;
    border-radius: 10px; padding: 1rem; line-height: 1.5;
  }
  .ok { color: #4ade80; font-weight: 600; }
</style>
</head>
<body>
<div class="wrap">
  <h1>Jarvis OAuth — Access Token</h1>
  <div id="content">Reading token from URL fragment…</div>
</div>
<script>
(function () {
  var content = document.getElementById("content");

  function parseParams(str) {
    var out = {};
    (str || "").replace(/^[#?]/, "").split("&").forEach(function (pair) {
      if (!pair) return;
      var i = pair.indexOf("=");
      var k = i < 0 ? pair : pair.slice(0, i);
      var v = i < 0 ? "" : pair.slice(i + 1);
      out[decodeURIComponent(k)] = decodeURIComponent(v.replace(/\\+/g, " "));
    });
    return out;
  }

  // Implicit flow: token in the hash. Errors may arrive in hash OR query.
  var hash = parseParams(window.location.hash);
  var query = parseParams(window.location.search);
  var token = hash.access_token;
  var error = hash.error || query.error;
  var errorDesc = hash.error_description || query.error_description;

  if (error) {
    content.innerHTML =
      '<div class="err"><strong>Authorization error:</strong> ' +
      escapeHtml(error) +
      (errorDesc ? '<br>' + escapeHtml(errorDesc) : '') +
      '</div>';
    return;
  }

  // Authorization-code flow (Business Login configs may return ?code=... in the query).
  var code = hash.code || query.code;
  if (!token && code) {
    content.innerHTML =
      '<p class="ok">Authorization code captured ✓</p>' +
      '<div class="token-box" id="tok">' + escapeHtml(code) + '</div>' +
      '<button id="copyBtn" type="button">Copy code</button>' +
      '<div class="meta">This is an authorization <code>code</code>, not a token — ' +
      'it needs a server-side exchange (client_id + client_secret) to become an access token.</div>';
    wireCopy(code, "Copy code");
    return;
  }

  if (!token) {
    content.innerHTML =
      '<div class="err">No <code>access_token</code> or <code>code</code> found in the URL. ' +
      'Make sure you opened the full OAuth link with <code>response_type=token</code> ' +
      'and completed the login.</div>';
    return;
  }

  var expiresIn = hash.expires_in;
  var dataExp = hash.data_access_expiration_time;

  content.innerHTML =
    '<p class="ok">Short-lived User Token captured ✓</p>' +
    '<div class="token-box" id="tok">' + escapeHtml(token) + '</div>' +
    '<button id="copyBtn" type="button">Copy token</button>' +
    '<div class="meta">' +
      (expiresIn ? 'expires_in: <code>' + escapeHtml(expiresIn) + '</code> sec<br>' : '') +
      (dataExp ? 'data_access_expiration_time: <code>' + escapeHtml(dataExp) + '</code>' : '') +
    '</div>';

  wireCopy(token, "Copy token");

  function wireCopy(value, label) {
    document.getElementById("copyBtn").addEventListener("click", function () {
      var btn = this;
      var done = function () { btn.textContent = "Copied ✓"; setTimeout(function () { btn.textContent = label; }, 2000); };
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(value).then(done, selectFallback);
      } else {
        selectFallback();
      }
    });
  }

  function selectFallback() {
    var el = document.getElementById("tok");
    var range = document.createRange();
    range.selectNodeContents(el);
    var sel = window.getSelection();
    sel.removeAllRanges();
    sel.addRange(range);
    try { document.execCommand("copy"); } catch (e) {}
  }

  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }
})();
</script>
</body>
</html>"""


@router.get("/oauth/callback", response_class=HTMLResponse)
def oauth_callback(
    code: Optional[str] = None,
    error: Optional[str] = None,
    error_description: Optional[str] = None,
    state: Optional[str] = None,
) -> HTMLResponse:
    """Serve the capture page and, for the code flow, persist ?code= server-side.

    The implicit-flow token only exists in the fragment and is handled by the
    page's JS. The authorization ?code= arrives in the query string, so we log
    it and write it to the pickup file for immediate exchange.
    """
    if code or error:
        record = {
            "code": code,
            "error": error,
            "error_description": error_description,
            "state": state,
            "ts": time.time(),
        }
        try:
            _PICKUP_FILE.parent.mkdir(parents=True, exist_ok=True)
            _PICKUP_FILE.write_text(json.dumps(record), encoding="utf-8")
        except Exception as exc:  # never break the user-facing page on IO error
            logger.warning("oauth callback: failed to write pickup file: %s", exc)
        if code:
            # Log a prefix only — enough to correlate, not the full one-time code.
            logger.info("oauth callback: received code (len=%d, prefix=%s...)", len(code), code[:8])
        else:
            logger.warning("oauth callback: received error=%s desc=%s", error, error_description)

    return HTMLResponse(content=_CALLBACK_PAGE)
