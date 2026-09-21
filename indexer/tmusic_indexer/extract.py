"""Telethon message → wire ``AudioItem``."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from telethon import types

from tmusic_common.indexer_contract import AudioItem
from tmusic_indexer.file_ids import document_unique_id

MAX_TEXT = 512
MAX_CAPTION = 4096


def _clip(value: str | None, limit: int) -> str | None:
    if not value:
        return None
    return value[:limit]


def audio_document(message: Any) -> Any | None:
    """The document if ``message`` carries a music file (not a voice note)."""
    media = getattr(message, "media", None)
    document = getattr(media, "document", None)
    if not isinstance(document, types.Document):
        return None
    for attr in document.attributes:
        if isinstance(attr, types.DocumentAttributeAudio):
            return None if attr.voice else document
    return None


def to_audio_item(message: Any) -> AudioItem | None:
    document = audio_document(message)
    if document is None:
        return None
    audio = next(a for a in document.attributes if isinstance(a, types.DocumentAttributeAudio))
    file_name = next(
        (
            a.file_name
            for a in document.attributes
            if isinstance(a, types.DocumentAttributeFilename)
        ),
        None,
    )
    posted: datetime = message.date or datetime.now(UTC)
    if posted.tzinfo is None:
        posted = posted.replace(tzinfo=UTC)
    return AudioItem(
        message_id=message.id,
        posted_at=posted,
        views=getattr(message, "views", None),
        file_unique_id=document_unique_id(document.id),
        duration=max(int(audio.duration or 0), 0),
        file_size=int(document.size or 0),
        mime_type=_clip(document.mime_type, 100),
        title=_clip(audio.title, MAX_TEXT),
        performer=_clip(audio.performer, MAX_TEXT),
        file_name=_clip(file_name, MAX_TEXT),
        caption=_clip(getattr(message, "message", None), MAX_CAPTION),
        has_thumb=bool(document.thumbs),
    )
