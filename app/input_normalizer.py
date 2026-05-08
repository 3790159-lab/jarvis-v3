from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class MediaPayload:
    kind: str
    file_id: str = ""
    mime_type: str = ""
    bytes_data: bytes | None = None
    caption: str = ""


@dataclass
class NormalizedInput:
    chat_id: str
    text: str
    raw_text: str
    caption: str
    reply_text: str
    has_photo: bool
    media: MediaPayload | None = None
    message_id: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def combined_text(self) -> str:
        chunks = [self.text.strip(), self.caption.strip(), self.reply_text.strip()]
        return "\n".join([c for c in chunks if c]).strip()


def normalize_telegram_update(update: dict[str, Any], photo_bytes: bytes | None = None) -> NormalizedInput | None:
    message = update.get("message") or update.get("edited_message") or {}
    chat = message.get("chat") or {}
    chat_id = str(chat.get("id", "")).strip()
    if not chat_id:
        return None

    text = (message.get("text") or "").strip()
    caption = (message.get("caption") or "").strip()
    reply_to = message.get("reply_to_message") or {}
    reply_text = (reply_to.get("text") or reply_to.get("caption") or "").strip()
    photos = message.get("photo") or []
    has_photo = bool(photos)
    media = None

    if has_photo:
        best = photos[-1]
        media = MediaPayload(
            kind="photo",
            file_id=str(best.get("file_id", "")),
            mime_type="image/jpeg",
            bytes_data=photo_bytes,
            caption=caption,
        )

    return NormalizedInput(
        chat_id=chat_id,
        text=text,
        raw_text=text,
        caption=caption,
        reply_text=reply_text,
        has_photo=has_photo,
        media=media,
        message_id=message.get("message_id"),
        metadata={
            "from_user": (message.get("from") or {}).get("username") or "",
        },
    )
