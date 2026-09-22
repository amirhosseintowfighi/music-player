from __future__ import annotations

import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from telethon import types

os.environ.update(
    {
        "INTERNAL_API_TOKEN": "internal-token",
        "TG_API_ID": "1",
        "TG_API_HASH": "hash",
        "SESSION_ENC_KEY": "a-very-long-test-key",
        "BOT_TOKEN": "123456:TEST",
        "STREAM_SIGNING_KEYS": "k1,k0",
        "LOG_JSON": "false",
    }
)

from tmusic_indexer.config import Settings


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        sessions_dir=tmp_path / "sessions",
        outbox_path=tmp_path / "outbox.sqlite3",
        batch_size=3,
        batch_delay_ms=0,
        claim_interval_s=0.01,
    )  # type: ignore[call-arg]


def audio_doc(doc_id: int, *, title: str | None = "T", performer: str | None = "P",
              voice: bool = False, size: int = 5_000_000, thumbs: bool = True,
              file_name: str | None = "song.mp3") -> types.Document:  # fmt: skip
    attrs: list[Any] = [
        types.DocumentAttributeAudio(duration=200, voice=voice, title=title, performer=performer)
    ]
    if file_name:
        attrs.append(types.DocumentAttributeFilename(file_name=file_name))
    return types.Document(
        id=doc_id,
        access_hash=1,
        file_reference=b"ref",
        date=datetime(2026, 1, 1, tzinfo=UTC),
        mime_type="audio/mpeg",
        size=size,
        dc_id=2,
        attributes=attrs,
        thumbs=[types.PhotoSize(type="m", w=90, h=90, size=100)] if thumbs else None,
    )


def audio_message(msg_id: int, doc: types.Document | None = None, text: str = "") -> Any:
    media = types.MessageMediaDocument(document=doc or audio_doc(msg_id * 10))
    return SimpleNamespace(
        id=msg_id, media=media, date=datetime(2026, 1, 1, tzinfo=UTC), views=7, message=text
    )


class FakeClient:
    """Enough of TelegramClient for the worker and the streamer."""

    def __init__(self, messages: list[Any] | None = None) -> None:
        self.messages = sorted(messages or [], key=lambda m: m.id)
        self.calls: list[tuple[str, Any]] = []
        self.fail_with: dict[str, Exception] = {}
        self.file_bytes = b""
        self.download_errors: list[Exception] = []

    def _maybe_fail(self, name: str) -> None:
        if name in self.fail_with:
            raise self.fail_with.pop(name)

    async def connect(self) -> None:
        self._maybe_fail("connect")

    async def disconnect(self) -> None:
        pass

    async def is_user_authorized(self) -> bool:
        return True

    async def get_me(self) -> Any:
        return SimpleNamespace(id=1, phone="989121234567")

    async def get_input_entity(self, username: str) -> Any:
        self.calls.append(("resolve", username))
        self._maybe_fail("resolve")
        return types.InputPeerChannel(channel_id=777, access_hash=99)

    async def get_entity(self, peer: Any) -> Any:
        self._maybe_fail("entity")
        return types.Channel(
            id=777, title="Music", photo=types.ChatPhotoEmpty(), date=None,
            username="music", access_hash=99, broadcast=True,
        )  # fmt: skip

    async def __call__(self, request: Any) -> Any:
        return SimpleNamespace(full_chat=SimpleNamespace(about="about"))

    async def get_messages(self, peer: Any, limit: int | None = None, ids: int | None = None,
                           filter: Any = None) -> Any:  # fmt: skip
        if ids is not None:
            self.calls.append(("get_message", ids))
            return next((m for m in self.messages if m.id == ids), None)
        return SimpleNamespace(total=len(self.messages))

    async def iter_messages(self, peer: Any, limit: int, offset_id: int = 0, min_id: int = 0,
                            reverse: bool = False, filter: Any = None) -> AsyncIterator[Any]:  # fmt: skip
        self.calls.append(("iter", (offset_id, min_id, reverse)))
        self._maybe_fail("iter")
        if reverse:
            pool = [m for m in self.messages if m.id > min_id]
        else:
            pool = [
                m
                for m in reversed(self.messages)
                if (not offset_id or m.id < offset_id) and m.id > min_id
            ]
        for m in pool[:limit]:
            yield m

    async def iter_download(self, document: Any, offset: int, request_size: int,
                            chunk_size: int, limit: int, file_size: int) -> AsyncIterator[bytes]:  # fmt: skip
        self.calls.append(("download", offset))
        if self.download_errors:
            raise self.download_errors.pop(0)
        for i in range(limit):
            start = offset + i * chunk_size
            if start >= len(self.file_bytes):
                return
            yield self.file_bytes[start : start + chunk_size]

    #: Set to empty to act like a message Telegram gave no thumbnail for.
    thumb_bytes: bytes = bytes([0xFF, 0xD8]) + b"thumb"

    async def download_media(self, message: Any, file: Any, thumb: int) -> bytes:
        return self.thumb_bytes
