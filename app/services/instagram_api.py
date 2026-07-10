"""Instagram Graph API client — Content Publishing (two-step container flow).

Docs: developers.facebook.com/docs/instagram-platform/content-publishing/

Flow (photo):
    1. create_media_container(image_url, caption) -> container_id
    2. publish_container(container_id)            -> published media id

Money / safety note (Этап 3, кирпич 3b):
    Publishing is FREE but IRREVERSIBLE outward. publish_container() below is
    fully implemented and unit-tested ON MOCKS ONLY — it is NOT fired live in
    this task. A separate task with an explicit confirm-gate performs the first
    real post. Media generation (the costly part) happens upstream, only after
    a content plan is approved.

Config comes from .env:
    IG_ACCESS_TOKEN  — long-lived user/page token (see exchange_to_long_lived)
    IG_USER_ID       — Instagram Business account id (optional; discoverable)
    IG_APP_ID        — Meta app id     (Settings -> Basic) — for token exchange
    IG_APP_SECRET    — Meta app secret (Settings -> Basic) — for token exchange
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Dict, Optional

GRAPH_API_BASE = "https://graph.facebook.com/v21.0"
_TIMEOUT = 30


class InstagramAPIError(RuntimeError):
    """Honest error for any Instagram Graph API failure.

    Carries the Graph API error envelope fields when available so callers can
    branch on rate limits (code 4), token issues (code 190), etc.
    """

    def __init__(self, message, *, code=None, subcode=None, fbtrace_id=None):
        super().__init__(message)
        self.code = code
        self.subcode = subcode
        self.fbtrace_id = fbtrace_id


def _error_from_http(exc: urllib.error.HTTPError) -> InstagramAPIError:
    try:
        payload = json.loads(exc.read().decode("utf-8"))
        err = payload.get("error", {})
    except (ValueError, AttributeError, OSError):
        return InstagramAPIError(f"HTTP {exc.code}: {getattr(exc, 'reason', '')}")
    return InstagramAPIError(
        err.get("message") or f"HTTP {exc.code}",
        code=err.get("code"),
        subcode=err.get("error_subcode"),
        fbtrace_id=err.get("fbtrace_id"),
    )


def _graph_request(path: str, params: Dict[str, str], method: str = "GET") -> Dict:
    """Perform a Graph API request and return parsed JSON.

    Raises InstagramAPIError on Graph error envelopes or network failures.
    """
    url = f"{GRAPH_API_BASE}/{path.lstrip('/')}"
    encoded = urllib.parse.urlencode(params)
    if method.upper() == "GET":
        full = f"{url}?{encoded}" if encoded else url
        req = urllib.request.Request(full, method="GET")
    else:
        req = urllib.request.Request(
            url, data=encoded.encode("utf-8"), method=method.upper()
        )
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            body = resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        raise _error_from_http(exc)
    except urllib.error.URLError as exc:
        raise InstagramAPIError(f"Network error contacting Graph API: {exc.reason}")
    try:
        return json.loads(body)
    except ValueError:
        raise InstagramAPIError(f"Non-JSON response from Graph API: {body[:200]!r}")


class InstagramAPI:
    """Two-step IG Content Publishing client (photo posts).

    Args override .env; omit to read IG_ACCESS_TOKEN / IG_USER_ID.
    """

    def __init__(self, access_token: Optional[str] = None,
                 ig_user_id: Optional[str] = None):
        self.access_token = (
            access_token if access_token is not None
            else os.getenv("IG_ACCESS_TOKEN", "")
        ).strip()
        self.ig_user_id = (
            ig_user_id if ig_user_id is not None
            else os.getenv("IG_USER_ID", "")
        ).strip()

    def _require_token(self) -> None:
        if not self.access_token:
            raise InstagramAPIError(
                "IG_ACCESS_TOKEN not configured. Obtain a short-lived token via "
                "Graph API Explorer, exchange it with exchange_to_long_lived(), "
                "then add to .env: IG_ACCESS_TOKEN=..."
            )

    def get_ig_user_id(self) -> str:
        """Return the Instagram Business account id.

        Uses IG_USER_ID from config if set (no network); otherwise discovers it
        by scanning the token's Facebook Pages for a linked IG Business account.
        """
        self._require_token()
        if self.ig_user_id:
            return self.ig_user_id
        data = _graph_request("me/accounts", {
            "fields": "instagram_business_account",
            "access_token": self.access_token,
        })
        for page in data.get("data", []):
            iba = page.get("instagram_business_account")
            if iba and iba.get("id"):
                self.ig_user_id = str(iba["id"])
                return self.ig_user_id
        raise InstagramAPIError(
            "No Instagram Business account linked to any Facebook Page for this "
            "token. Convert the IG account to Professional and link it to a Page."
        )

    def create_media_container(self, image_url: str, caption: str = "") -> str:
        """Step 1: create a media container for a photo. Returns container_id."""
        self._require_token()
        ig_id = self.get_ig_user_id()
        data = _graph_request(f"{ig_id}/media", {
            "image_url": image_url,
            "caption": caption,
            "access_token": self.access_token,
        }, method="POST")
        container_id = data.get("id")
        if not container_id:
            raise InstagramAPIError(f"No container id in response: {data!r}")
        return str(container_id)

    def publish_container(self, container_id: str) -> str:
        """Step 2: publish a previously created container. Returns media id.

        IRREVERSIBLE outward. Not fired live in this task (mock-tested only);
        the first real publish runs in a separate confirm-gated task.
        """
        self._require_token()
        ig_id = self.get_ig_user_id()
        data = _graph_request(f"{ig_id}/media_publish", {
            "creation_id": container_id,
            "access_token": self.access_token,
        }, method="POST")
        media_id = data.get("id")
        if not media_id:
            raise InstagramAPIError(f"No media id in publish response: {data!r}")
        return str(media_id)


def exchange_to_long_lived(short_token: str, app_id: Optional[str] = None,
                           app_secret: Optional[str] = None) -> str:
    """Exchange a short-lived token for a long-lived (~60 day) one.

    app_id / app_secret override .env (IG_APP_ID/IG_APP_SECRET, falling back to
    FB_APP_ID/FB_APP_SECRET). Returns the long-lived access token string.
    """
    app_id = (
        app_id if app_id is not None
        else os.getenv("IG_APP_ID") or os.getenv("FB_APP_ID", "")
    ).strip()
    app_secret = (
        app_secret if app_secret is not None
        else os.getenv("IG_APP_SECRET") or os.getenv("FB_APP_SECRET", "")
    ).strip()
    if not app_id or not app_secret:
        raise InstagramAPIError(
            "IG_APP_ID / IG_APP_SECRET not configured. Copy them from the Meta "
            "app dashboard (Settings -> Basic) into .env."
        )
    if not short_token:
        raise InstagramAPIError("short_token is required for token exchange.")
    data = _graph_request("oauth/access_token", {
        "grant_type": "fb_exchange_token",
        "client_id": app_id,
        "client_secret": app_secret,
        "fb_exchange_token": short_token,
    })
    token = data.get("access_token")
    if not token:
        raise InstagramAPIError(f"No access_token in exchange response: {data!r}")
    return str(token)
