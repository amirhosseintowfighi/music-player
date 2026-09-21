"""Bot API ``file_unique_id`` for MTProto documents.

Tracks are identified by ``file_unique_id`` (ADR-0005). The bot receives it from the
Bot API; the indexer only sees raw MTProto documents, so it derives the same value:
``base64url(rle(pack("<iq", UNIQUE_TYPE_DOCUMENT, document.id)))`` without padding.
Audio, voice, video and plain documents all share the "document" unique type (2), which
is why Bot API ids for audio start with ``AgAD``.
"""

from __future__ import annotations

import base64
import struct

UNIQUE_TYPE_DOCUMENT = 2


def rle_encode(data: bytes) -> bytes:
    """Telegram's zero-run-length encoding: runs of 0x00 become ``00 <count>``."""
    out = bytearray()
    zeros = 0
    for byte in data:
        if byte == 0:
            zeros += 1
            if zeros == 255:
                out += bytes((0, zeros))
                zeros = 0
            continue
        if zeros:
            out += bytes((0, zeros))
            zeros = 0
        out.append(byte)
    if zeros:
        out += bytes((0, zeros))
    return bytes(out)


def rle_decode(data: bytes) -> bytes:
    out = bytearray()
    it = iter(data)
    for byte in it:
        if byte == 0:
            out += bytes(next(it))
        else:
            out.append(byte)
    return bytes(out)


def document_unique_id(document_id: int) -> str:
    raw = struct.pack("<iq", UNIQUE_TYPE_DOCUMENT, document_id)
    return base64.urlsafe_b64encode(rle_encode(raw)).decode().rstrip("=")


def decode_unique_id(unique_id: str) -> tuple[int, int]:
    raw = rle_decode(base64.urlsafe_b64decode(unique_id + "=" * (-len(unique_id) % 4)))
    kind, media_id = struct.unpack("<iq", raw[:12])
    return int(kind), int(media_id)
