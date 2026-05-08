from __future__ import annotations

import base64
import mimetypes
import os
import traceback
from dataclasses import dataclass
from datetime import datetime, timezone
from email.message import EmailMessage
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

try:
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials as UserCredentials
    from google.oauth2.service_account import Credentials as ServiceAccountCredentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build
    from googleapiclient.errors import HttpError
except Exception as import_error:
    Request = None
    UserCredentials = None
    ServiceAccountCredentials = None
    InstalledAppFlow = None
    build = None
    HttpError = Exception
    _IMPORT_ERROR = import_error
else:
    _IMPORT_ERROR = None


def _load_local_env() -> None:
    """
    Lightweight .env loader for standalone tests.
    Does not overwrite variables that are already present.
    """
    try:
        candidate_paths = [
            Path.cwd() / ".env",
            Path(__file__).resolve().parents[2] / ".env",
        ]

        seen = set()
        for env_path in candidate_paths:
            try:
                env_path = env_path.resolve()
            except Exception:
                continue

            if env_path in seen:
                continue
            seen.add(env_path)

            if not env_path.exists() or not env_path.is_file():
                continue

            for raw_line in env_path.read_text(encoding="utf-8").splitlines():
                line = raw_line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" not in line:
                    continue

                key, value = line.split("=", 1)
                key = key.strip()
                value = value.strip().strip('"').strip("'")

                if key and key not in os.environ:
                    os.environ[key] = value
    except Exception:
        pass


_load_local_env()


GMAIL_SCOPES = [
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.readonly",
]

CALENDAR_SCOPES = [
    "https://www.googleapis.com/auth/calendar",
]

DEFAULT_CALENDAR_TIMEZONE = os.getenv("GOOGLE_CALENDAR_TIMEZONE", "Europe/Kyiv")


@dataclass
class ResolvedAuth:
    auth_mode: str
    credentials: Any


class GoogleWorkspaceError(RuntimeError):
    pass


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_env_path(name: str) -> Optional[Path]:
    value = os.getenv(name, "").strip()
    if not value:
        return None
    try:
        return Path(value).expanduser().resolve()
    except Exception:
        return None


def _path_exists(path: Optional[Path]) -> bool:
    return bool(path and path.exists() and path.is_file())


def _require_google_deps() -> None:
    if _IMPORT_ERROR is not None:
        raise GoogleWorkspaceError(
            "Google packages are not installed. "
            "Install: pip install google-api-python-client google-auth google-auth-oauthlib"
        ) from _IMPORT_ERROR


def _safe_error_payload(action: str, exc: Exception) -> Dict[str, Any]:
    return {
        "ok": False,
        "action": action,
        "error_type": type(exc).__name__,
        "error": str(exc),
        "timestamp": _utc_now_iso(),
    }


def _write_text_file(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _oauth_token_from_file(token_path: Path, scopes: Sequence[str]) -> Optional[Any]:
    if not _path_exists(token_path):
        return None

    creds = UserCredentials.from_authorized_user_file(str(token_path), scopes=scopes)

    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
        _write_text_file(token_path, creds.to_json())

    if creds and creds.valid:
        return creds

    return None


def _oauth_interactive(client_secret_path: Path, token_path: Path, scopes: Sequence[str]) -> Any:
    if not _path_exists(client_secret_path):
        raise GoogleWorkspaceError(
            "OAuth client secret file not found. "
            "Set GOOGLE_OAUTH_CLIENT_SECRET_JSON to a valid file path."
        )

    if InstalledAppFlow is None:
        raise GoogleWorkspaceError(
            "google-auth-oauthlib is not installed. "
            "Install: pip install google-auth-oauthlib"
        )

    flow = InstalledAppFlow.from_client_secrets_file(str(client_secret_path), scopes=list(scopes))
    creds = flow.run_local_server(host="localhost", port=int(os.getenv("GOOGLE_OAUTH_REDIRECT_PORT", "8080")), authorization_prompt_message="Please visit this URL to authorize this application: {url}", success_message="The auth flow has completed. You may close this window.", open_browser=True)
    _write_text_file(token_path, creds.to_json())
    return creds


def resolve_google_credentials(
    scopes: Sequence[str],
    *,
    allow_service_account: bool = True,
    prefer_oauth: bool = True,
    interactive_oauth_fallback: bool = False,
) -> ResolvedAuth:
    _require_google_deps()

    token_path = _normalize_env_path("GOOGLE_OAUTH_TOKEN_JSON")
    client_secret_path = _normalize_env_path("GOOGLE_OAUTH_CLIENT_SECRET_JSON")
    service_account_path = _normalize_env_path("GOOGLE_SERVICE_ACCOUNT_JSON")

    if prefer_oauth and token_path and _path_exists(token_path):
        creds = _oauth_token_from_file(token_path, scopes)
        if creds:
            return ResolvedAuth(auth_mode="oauth_token", credentials=creds)

    if prefer_oauth and interactive_oauth_fallback:
        if client_secret_path:
            if token_path is None:
                default_token_path = Path.cwd() / "artifacts" / "google_oauth_token.json"
                token_path = default_token_path
            creds = _oauth_interactive(client_secret_path, token_path, scopes)
            return ResolvedAuth(auth_mode="oauth_interactive", credentials=creds)

    if allow_service_account and service_account_path and _path_exists(service_account_path):
        creds = ServiceAccountCredentials.from_service_account_file(
            str(service_account_path),
            scopes=list(scopes),
        )
        return ResolvedAuth(auth_mode="service_account", credentials=creds)

    hints: List[str] = []
    if prefer_oauth:
        hints.append("GOOGLE_OAUTH_TOKEN_JSON")
        hints.append("GOOGLE_OAUTH_CLIENT_SECRET_JSON")
    if allow_service_account:
        hints.append("GOOGLE_SERVICE_ACCOUNT_JSON")

    raise GoogleWorkspaceError(
        "No usable Google credentials found. Configure one of: "
        + ", ".join(hints)
    )


def _gmail_service(*, interactive_oauth_fallback: bool = False) -> ResolvedAuth:
    auth = resolve_google_credentials(
        GMAIL_SCOPES,
        allow_service_account=False,
        prefer_oauth=True,
        interactive_oauth_fallback=interactive_oauth_fallback,
    )
    service = build("gmail", "v1", credentials=auth.credentials, cache_discovery=False)
    return ResolvedAuth(auth_mode=auth.auth_mode, credentials=service)


def _calendar_service(*, interactive_oauth_fallback: bool = False) -> ResolvedAuth:
    auth = resolve_google_credentials(
        CALENDAR_SCOPES,
        allow_service_account=True,
        prefer_oauth=True,
        interactive_oauth_fallback=interactive_oauth_fallback,
    )
    service = build("calendar", "v3", credentials=auth.credentials, cache_discovery=False)
    return ResolvedAuth(auth_mode=auth.auth_mode, credentials=service)


def healthcheck() -> Dict[str, Any]:
    try:
        service_account_path = _normalize_env_path("GOOGLE_SERVICE_ACCOUNT_JSON")
        oauth_client_path = _normalize_env_path("GOOGLE_OAUTH_CLIENT_SECRET_JSON")
        oauth_token_path = _normalize_env_path("GOOGLE_OAUTH_TOKEN_JSON")

        return {
            "ok": True,
            "timestamp": _utc_now_iso(),
            "google_packages_ready": _IMPORT_ERROR is None,
            "env": {
                "GOOGLE_SERVICE_ACCOUNT_JSON": str(service_account_path or ""),
                "GOOGLE_OAUTH_CLIENT_SECRET_JSON": str(oauth_client_path or ""),
                "GOOGLE_OAUTH_TOKEN_JSON": str(oauth_token_path or ""),
                "GOOGLE_CALENDAR_ID": os.getenv("GOOGLE_CALENDAR_ID", "primary"),
                "GOOGLE_CALENDAR_TIMEZONE": os.getenv("GOOGLE_CALENDAR_TIMEZONE", DEFAULT_CALENDAR_TIMEZONE),
            },
            "files": {
                "service_account_exists": _path_exists(service_account_path),
                "oauth_client_secret_exists": _path_exists(oauth_client_path),
                "oauth_token_exists": _path_exists(oauth_token_path),
            },
        }
    except Exception as exc:
        return _safe_error_payload("healthcheck", exc)


def gmail_list_messages(
    *,
    query: str = "",
    max_results: int = 10,
    user_id: str = "me",
    interactive_oauth_fallback: bool = False,
) -> Dict[str, Any]:
    try:
        if max_results < 1:
            max_results = 1
        if max_results > 100:
            max_results = 100

        auth = _gmail_service(interactive_oauth_fallback=interactive_oauth_fallback)
        service = auth.credentials

        response = service.users().messages().list(
            userId=user_id,
            q=query,
            maxResults=max_results,
        ).execute()

        items: List[Dict[str, Any]] = []
        for item in response.get("messages", []):
            full = service.users().messages().get(
                userId=user_id,
                id=item["id"],
                format="metadata",
                metadataHeaders=["From", "To", "Subject", "Date"],
            ).execute()

            headers = {
                header["name"]: header["value"]
                for header in full.get("payload", {}).get("headers", [])
            }

            items.append(
                {
                    "id": full.get("id"),
                    "thread_id": full.get("threadId"),
                    "snippet": full.get("snippet", ""),
                    "from": headers.get("From", ""),
                    "to": headers.get("To", ""),
                    "subject": headers.get("Subject", ""),
                    "date": headers.get("Date", ""),
                }
            )

        return {
            "ok": True,
            "action": "gmail_list_messages",
            "auth_mode": auth.auth_mode,
            "query": query,
            "count": len(items),
            "messages": items,
            "timestamp": _utc_now_iso(),
        }

    except HttpError as exc:
        return _safe_error_payload("gmail_list_messages", exc)
    except Exception as exc:
        return _safe_error_payload("gmail_list_messages", exc)


def gmail_send_email(
    *,
    to: str,
    subject: str,
    body_text: str,
    cc: Optional[Iterable[str]] = None,
    bcc: Optional[Iterable[str]] = None,
    attachments: Optional[Iterable[str]] = None,
    user_id: str = "me",
    interactive_oauth_fallback: bool = False,
) -> Dict[str, Any]:
    try:
        if not str(to).strip():
            raise GoogleWorkspaceError("Parameter 'to' is required.")
        if not str(subject).strip():
            raise GoogleWorkspaceError("Parameter 'subject' is required.")
        if body_text is None:
            body_text = ""

        auth = _gmail_service(interactive_oauth_fallback=interactive_oauth_fallback)
        service = auth.credentials

        msg = EmailMessage()
        msg["To"] = str(to).strip()
        msg["Subject"] = str(subject).strip()

        cc_list = [str(x).strip() for x in (cc or []) if str(x).strip()]
        bcc_list = [str(x).strip() for x in (bcc or []) if str(x).strip()]

        if cc_list:
            msg["Cc"] = ", ".join(cc_list)
        if bcc_list:
            msg["Bcc"] = ", ".join(bcc_list)

        msg.set_content(str(body_text))

        attached_files: List[str] = []

        for attachment_path in attachments or []:
            raw_path = str(attachment_path).strip()
            if not raw_path:
                continue

            path = Path(raw_path).expanduser().resolve()
            if not path.exists() or not path.is_file():
                raise FileNotFoundError(f"Attachment not found: {path}")

            content_type, _ = mimetypes.guess_type(str(path))
            if content_type:
                maintype, subtype = content_type.split("/", 1)
            else:
                maintype, subtype = "application", "octet-stream"

            with path.open("rb") as file_handle:
                msg.add_attachment(
                    file_handle.read(),
                    maintype=maintype,
                    subtype=subtype,
                    filename=path.name,
                )

            attached_files.append(str(path))

        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("utf-8")
        sent = service.users().messages().send(
            userId=user_id,
            body={"raw": raw},
        ).execute()

        return {
            "ok": True,
            "action": "gmail_send_email",
            "auth_mode": auth.auth_mode,
            "message_id": sent.get("id"),
            "thread_id": sent.get("threadId"),
            "to": msg["To"],
            "subject": msg["Subject"],
            "attachments": attached_files,
            "timestamp": _utc_now_iso(),
        }

    except HttpError as exc:
        return _safe_error_payload("gmail_send_email", exc)
    except Exception as exc:
        return _safe_error_payload("gmail_send_email", exc)


def calendar_list_events(
    *,
    calendar_id: Optional[str] = None,
    time_min: Optional[str] = None,
    time_max: Optional[str] = None,
    max_results: int = 10,
    q: Optional[str] = None,
    interactive_oauth_fallback: bool = False,
) -> Dict[str, Any]:
    try:
        if max_results < 1:
            max_results = 1
        if max_results > 250:
            max_results = 250

        auth = _calendar_service(interactive_oauth_fallback=interactive_oauth_fallback)
        service = auth.credentials
        effective_calendar_id = (calendar_id or os.getenv("GOOGLE_CALENDAR_ID", "primary")).strip()

        kwargs: Dict[str, Any] = {
            "calendarId": effective_calendar_id,
            "maxResults": max_results,
            "singleEvents": True,
            "orderBy": "startTime",
        }

        if time_min:
            kwargs["timeMin"] = time_min
        if time_max:
            kwargs["timeMax"] = time_max
        if q:
            kwargs["q"] = q

        response = service.events().list(**kwargs).execute()

        items: List[Dict[str, Any]] = []
        for event in response.get("items", []):
            items.append(
                {
                    "id": event.get("id"),
                    "status": event.get("status"),
                    "summary": event.get("summary", ""),
                    "description": event.get("description", ""),
                    "location": event.get("location", ""),
                    "html_link": event.get("htmlLink", ""),
                    "start": event.get("start", {}),
                    "end": event.get("end", {}),
                    "creator": event.get("creator", {}),
                    "organizer": event.get("organizer", {}),
                    "attendees": [
                        {
                            "email": attendee.get("email", ""),
                            "response_status": attendee.get("responseStatus", ""),
                        }
                        for attendee in (event.get("attendees") or [])
                    ],
                }
            )

        return {
            "ok": True,
            "action": "calendar_list_events",
            "auth_mode": auth.auth_mode,
            "calendar_id": effective_calendar_id,
            "count": len(items),
            "events": items,
            "timestamp": _utc_now_iso(),
        }

    except HttpError as exc:
        return _safe_error_payload("calendar_list_events", exc)
    except Exception as exc:
        return _safe_error_payload("calendar_list_events", exc)


def calendar_create_event(
    *,
    summary: str,
    start_iso: str,
    end_iso: str,
    calendar_id: Optional[str] = None,
    timezone_name: Optional[str] = None,
    description: str = "",
    location: str = "",
    attendees: Optional[Iterable[str]] = None,
    interactive_oauth_fallback: bool = False,
) -> Dict[str, Any]:
    try:
        if not str(summary).strip():
            raise GoogleWorkspaceError("Parameter 'summary' is required.")
        if not str(start_iso).strip():
            raise GoogleWorkspaceError("Parameter 'start_iso' is required.")
        if not str(end_iso).strip():
            raise GoogleWorkspaceError("Parameter 'end_iso' is required.")

        auth = _calendar_service(interactive_oauth_fallback=interactive_oauth_fallback)
        service = auth.credentials
        effective_calendar_id = (calendar_id or os.getenv("GOOGLE_CALENDAR_ID", "primary")).strip()
        effective_timezone = (timezone_name or os.getenv("GOOGLE_CALENDAR_TIMEZONE", DEFAULT_CALENDAR_TIMEZONE)).strip()

        body: Dict[str, Any] = {
            "summary": str(summary).strip(),
            "description": str(description or ""),
            "location": str(location or ""),
            "start": {
                "dateTime": str(start_iso).strip(),
                "timeZone": effective_timezone,
            },
            "end": {
                "dateTime": str(end_iso).strip(),
                "timeZone": effective_timezone,
            },
        }

        attendee_items = [{"email": str(email).strip()} for email in (attendees or []) if str(email).strip()]
        if attendee_items:
            body["attendees"] = attendee_items

        created = service.events().insert(
            calendarId=effective_calendar_id,
            body=body,
        ).execute()

        return {
            "ok": True,
            "action": "calendar_create_event",
            "auth_mode": auth.auth_mode,
            "calendar_id": effective_calendar_id,
            "event_id": created.get("id"),
            "status": created.get("status"),
            "html_link": created.get("htmlLink", ""),
            "summary": created.get("summary", ""),
            "timestamp": _utc_now_iso(),
        }

    except HttpError as exc:
        return _safe_error_payload("calendar_create_event", exc)
    except Exception as exc:
        return _safe_error_payload("calendar_create_event", exc)


def run_self_test() -> Dict[str, Any]:
    results: Dict[str, Any] = {
        "ok": True,
        "timestamp": _utc_now_iso(),
        "checks": {},
    }

    try:
        results["checks"]["healthcheck"] = healthcheck()

        try:
            gmail_auth = resolve_google_credentials(
                GMAIL_SCOPES,
                allow_service_account=False,
                prefer_oauth=True,
                interactive_oauth_fallback=False,
            )
            results["checks"]["gmail_auth"] = {
                "ok": True,
                "auth_mode": gmail_auth.auth_mode,
            }
        except Exception as exc:
            results["ok"] = False
            results["checks"]["gmail_auth"] = _safe_error_payload("gmail_auth", exc)

        try:
            calendar_auth = resolve_google_credentials(
                CALENDAR_SCOPES,
                allow_service_account=True,
                prefer_oauth=True,
                interactive_oauth_fallback=False,
            )
            results["checks"]["calendar_auth"] = {
                "ok": True,
                "auth_mode": calendar_auth.auth_mode,
            }
        except Exception as exc:
            results["ok"] = False
            results["checks"]["calendar_auth"] = _safe_error_payload("calendar_auth", exc)

        return results

    except Exception as exc:
        return _safe_error_payload("run_self_test", exc)


def ensure_combined_google_oauth_token(*, interactive: bool = True) -> Dict[str, Any]:
    """
    Force-create a single OAuth token with both Gmail and Calendar scopes.
    This avoids partial tokens that work for Gmail but fail for Calendar writes.
    """
    try:
        combined_scopes = sorted(set(GMAIL_SCOPES + CALENDAR_SCOPES))
        auth = resolve_google_credentials(
            combined_scopes,
            allow_service_account=False,
            prefer_oauth=True,
            interactive_oauth_fallback=interactive,
        )
        return {
            "ok": True,
            "action": "ensure_combined_google_oauth_token",
            "auth_mode": auth.auth_mode,
            "scopes": combined_scopes,
            "timestamp": _utc_now_iso(),
        }
    except Exception as exc:
        return _safe_error_payload("ensure_combined_google_oauth_token", exc)

