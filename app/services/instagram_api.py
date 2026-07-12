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

Credentials (multi-account, see app.services.ig_accounts):
    InstagramAPI(account_key=...) resolves access_token/ig_user_id from
    state/ig_accounts.json when it exists; if it doesn't yet, falls back to
    the legacy .env vars below (unchanged behaviour, auto-migrates on first
    resolve). IG_APP_ID/IG_APP_SECRET stay .env-only (token exchange, rare).
    IG_ACCESS_TOKEN  — long-lived user/page token (see exchange_to_long_lived)
    IG_USER_ID       — Instagram Business account id (optional; discoverable)
    IG_APP_ID        — Meta app id     (Settings -> Basic) — for token exchange
    IG_APP_SECRET    — Meta app secret (Settings -> Basic) — for token exchange
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Dict, Optional

GRAPH_API_BASE = "https://graph.facebook.com/v21.0"
# Instagram API with Instagram Login (Path B) talks to this host instead.
INSTAGRAM_GRAPH_BASE = "https://graph.instagram.com"
_TIMEOUT = 30


def _resolve_base(base_url: Optional[str], login_type: Optional[str]) -> str:
    """Pick the Graph host.

    Precedence: explicit ``base_url`` arg > ``IG_GRAPH_BASE`` env >
    Instagram-Login (``login_type``/``IG_LOGIN_TYPE`` == "instagram") >
    the default Facebook Graph host (back-compat).
    """
    if base_url:
        return base_url.rstrip("/")
    env_base = os.getenv("IG_GRAPH_BASE", "").strip()
    if env_base:
        return env_base.rstrip("/")
    lt = (login_type if login_type is not None
          else os.getenv("IG_LOGIN_TYPE", "")).strip().lower()
    if lt == "instagram":
        return INSTAGRAM_GRAPH_BASE
    return GRAPH_API_BASE


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


def _graph_request(path: str, params: Dict[str, str], method: str = "GET",
                   base: Optional[str] = None) -> Dict:
    """Perform a Graph API request and return parsed JSON.

    ``base`` selects the Graph host (defaults to the Facebook Graph host for
    back-compat). Raises InstagramAPIError on Graph error envelopes or network
    failures.
    """
    url = f"{(base or GRAPH_API_BASE).rstrip('/')}/{path.lstrip('/')}"
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

    Args override .env; omit to read from the multi-account store
    (``app.services.ig_accounts``) — ``account_key`` selects which account
    (default: env ``IG_DEFAULT_ACCOUNT``, falls back to ``jtest_lab_``). If no
    account json exists yet, this transparently falls back to the legacy
    ``IG_ACCESS_TOKEN``/``IG_USER_ID`` env vars — same behaviour as before
    multi-account support existed.
    """

    def __init__(self, access_token: Optional[str] = None,
                 ig_user_id: Optional[str] = None,
                 base_url: Optional[str] = None,
                 login_type: Optional[str] = None,
                 account_key: Optional[str] = None):
        if access_token is None or ig_user_id is None:
            from app.services.ig_accounts import resolve_credentials
            _resolved_token, _resolved_uid = resolve_credentials(account_key)
        else:
            _resolved_token, _resolved_uid = "", ""
        self.account_key = account_key
        self.access_token = (
            access_token if access_token is not None else _resolved_token
        ).strip()
        self.ig_user_id = (
            ig_user_id if ig_user_id is not None else _resolved_uid
        ).strip()
        self.login_type = (
            login_type if login_type is not None
            else os.getenv("IG_LOGIN_TYPE", "")
        ).strip().lower()
        self.base_url = _resolve_base(base_url, self.login_type)

    def _require_token(self) -> None:
        if not self.access_token:
            raise InstagramAPIError(
                "IG_ACCESS_TOKEN not configured. Obtain a short-lived token via "
                "Graph API Explorer, exchange it with exchange_to_long_lived(), "
                "then add to .env: IG_ACCESS_TOKEN=..."
            )

    def get_ig_user_id(self) -> str:
        """Return the Instagram Business account id.

        Uses IG_USER_ID from config if set (no network). Otherwise discovers it:
        Instagram Login (Path B) reads ``me?fields=user_id`` directly; Facebook
        Login (Path A) scans the token's Facebook Pages for a linked IG account.
        """
        self._require_token()
        if self.ig_user_id:
            return self.ig_user_id
        if self.login_type == "instagram":
            data = _graph_request("me", {
                "fields": "user_id",
                "access_token": self.access_token,
            }, base=self.base_url)
            uid = data.get("user_id")
            if uid:
                self.ig_user_id = str(uid)
                return self.ig_user_id
            raise InstagramAPIError(
                f"No user_id returned by graph.instagram.com/me: {data!r}"
            )
        data = _graph_request("me/accounts", {
            "fields": "instagram_business_account",
            "access_token": self.access_token,
        }, base=self.base_url)
        for page in data.get("data", []):
            iba = page.get("instagram_business_account")
            if iba and iba.get("id"):
                self.ig_user_id = str(iba["id"])
                return self.ig_user_id
        raise InstagramAPIError(
            "No Instagram Business account linked to any Facebook Page for this "
            "token. Convert the IG account to Professional and link it to a Page."
        )

    def get_profile(self, fields: str = "user_id,username,account_type,media_count") -> Dict:
        """Read-only profile fetch (verification / health).

        Hits ``me`` on the configured Graph host. For Instagram Login this
        returns ``user_id``/``username``; extra fields (account_type,
        media_count) are best-effort and may be omitted by the API.
        """
        self._require_token()
        return _graph_request("me", {
            "fields": fields,
            "access_token": self.access_token,
        }, base=self.base_url)

    def create_media_container(self, image_url: str, caption: str = "") -> str:
        """Step 1: create a media container for a photo. Returns container_id."""
        self._require_token()
        ig_id = self.get_ig_user_id()
        data = _graph_request(f"{ig_id}/media", {
            "image_url": image_url,
            "caption": caption,
            "access_token": self.access_token,
        }, method="POST", base=self.base_url)
        container_id = data.get("id")
        if not container_id:
            raise InstagramAPIError(f"No container id in response: {data!r}")
        return str(container_id)

    def publish_container(self, container_id: str) -> str:
        """Step 2: publish a previously created container. Returns media id.

        IRREVERSIBLE outward. Fired live ONLY behind an explicit confirm-tap
        ([📤 Опубликовать]) in the bot; mock-tested here.
        """
        self._require_token()
        ig_id = self.get_ig_user_id()
        data = _graph_request(f"{ig_id}/media_publish", {
            "creation_id": container_id,
            "access_token": self.access_token,
        }, method="POST", base=self.base_url)
        media_id = data.get("id")
        if not media_id:
            raise InstagramAPIError(f"No media id in publish response: {data!r}")
        return str(media_id)

    def get_container_status(self, container_id: str) -> str:
        """Return a media container's ``status_code`` (read-only, cheap).

        One of: FINISHED, IN_PROGRESS, ERROR, EXPIRED, PUBLISHED (or "" if the
        field is absent). Same host as the rest of the client (graph.instagram.com
        under Instagram Login).
        """
        self._require_token()
        data = _graph_request(f"{container_id}", {
            "fields": "status_code",
            "access_token": self.access_token,
        }, method="GET", base=self.base_url)
        return str(data.get("status_code") or "")

    def wait_until_ready(self, container_id: str, max_status_checks: int = 8,
                         delay_seconds: float = 2.0) -> None:
        """Poll the container until it is FINISHED, then return.

        Content-publishing containers are created asynchronously; calling
        media_publish before the container is FINISHED yields "Media ID is not
        available". Fail-closed: ERROR/EXPIRED or exceeding ``max_status_checks``
        (still IN_PROGRESS) raises — the caller must NOT publish.
        """
        for attempt in range(max_status_checks):
            status = self.get_container_status(container_id)
            if status == "FINISHED":
                return
            if status in ("ERROR", "EXPIRED"):
                raise InstagramAPIError(
                    f"Media container {status.lower()} (id={container_id}) — not published."
                )
            # IN_PROGRESS / PUBLISHED / unknown → wait and re-check (unless last)
            if attempt < max_status_checks - 1:
                time.sleep(delay_seconds)
        raise InstagramAPIError(
            f"Media container not ready after {max_status_checks} checks "
            f"(still processing) — not published."
        )

    def list_recent_media(self, limit: int = 5, fields: Optional[str] = None) -> list:
        """Read-only: last ``limit`` media items on the account (newest first).

        Hits ``{ig-user-id}/media``. ``fields`` defaults to metadata + engagement
        counts that live on the media node itself (``like_count``/``comments_count``
        need no insights permission, unlike reach/impressions below).
        """
        self._require_token()
        ig_id = self.get_ig_user_id()
        data = _graph_request(f"{ig_id}/media", {
            "fields": fields or (
                "id,caption,media_type,media_product_type,timestamp,"
                "permalink,like_count,comments_count"
            ),
            "limit": str(limit),
            "access_token": self.access_token,
        }, base=self.base_url)
        return list(data.get("data") or [])

    def get_media_insights(self, media_id: str, metrics: str = "reach,total_interactions") -> Dict[str, int]:
        """Read-only per-media insights (``reach``/``total_interactions`` by default).

        Requires ``instagram_business_manage_insights``. Raises
        :class:`InstagramAPIError` on failure (e.g. missing permission, or a
        metric unsupported for this media's type) — the caller decides whether
        to degrade a single card line or abort (fail-closed either way: never
        fabricate a number).
        """
        self._require_token()
        data = _graph_request(f"{media_id}/insights", {
            "metric": metrics,
            "access_token": self.access_token,
        }, base=self.base_url)
        result: Dict[str, int] = {}
        for item in data.get("data") or []:
            name = item.get("name")
            values = item.get("values") or []
            if name and values:
                result[str(name)] = values[0].get("value")
        return result

    def get_permalink(self, media_id: str) -> str:
        """Fetch the public permalink of a published media node.

        Read-only, cheap; used after publish to hand the poster the post URL.
        """
        self._require_token()
        data = _graph_request(f"{media_id}", {
            "fields": "permalink",
            "access_token": self.access_token,
        }, method="GET", base=self.base_url)
        permalink = data.get("permalink")
        if not permalink:
            raise InstagramAPIError(f"No permalink in response: {data!r}")
        return str(permalink)

    def publish_photo(self, image_url: str, caption: str = "",
                      max_status_checks: int = 8) -> Dict[str, Optional[str]]:
        """Full photo publish: container -> wait until FINISHED -> publish (-> permalink).

        Returns ``{"id", "container_id", "permalink"}``. IRREVERSIBLE outward:
        fired live ONLY behind the [📤] confirm-tap. Fail-closed — any error in
        the container/status/publish steps propagates as :class:`InstagramAPIError`
        (nothing gets published silently). The container-status poll prevents the
        "Media ID is not available" race (publishing before FINISHED). The
        permalink lookup is best-effort: once ``media_publish`` succeeds the post
        is live, so a permalink failure is NOT a publish failure — we return
        ``permalink=None`` honestly.
        """
        container_id = self.create_media_container(image_url, caption)
        self.wait_until_ready(container_id, max_status_checks=max_status_checks)
        media_id = self.publish_container(container_id)
        try:
            permalink: Optional[str] = self.get_permalink(media_id)
        except InstagramAPIError:
            permalink = None
        return {"id": media_id, "container_id": container_id, "permalink": permalink}


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


def refresh_long_lived_token(access_token: str, base: Optional[str] = None) -> str:
    """Refresh an Instagram-Login long-lived token (Path B), extending it ~60 days.

    Uses ``GET graph.instagram.com/refresh_access_token?grant_type=ig_refresh_token``.
    No app secret required — only the current (still-valid) long-lived token,
    which must be >24h old and <60 days. Fail-closed: raises if the response
    carries no ``access_token`` so callers never overwrite a good token with an
    empty one.
    """
    if not access_token:
        raise InstagramAPIError("access_token is required to refresh.")
    data = _graph_request("refresh_access_token", {
        "grant_type": "ig_refresh_token",
        "access_token": access_token,
    }, base=base or INSTAGRAM_GRAPH_BASE)
    token = data.get("access_token")
    if not token:
        raise InstagramAPIError(f"No access_token in refresh response: {data!r}")
    return str(token)
