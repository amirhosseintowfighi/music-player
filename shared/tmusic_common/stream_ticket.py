"""Signed, self-contained stream tickets.

Core issues a ticket, the edge verifies it offline with the shared key. Everything the
edge needs to fetch the bytes from Telegram is inside the ticket, so playback keeps
working while the core<->edge tunnel is down.

Format: ``base64url(json) "." base64url(hmac_sha256(json_b64))``. Verification accepts
any of several keys so the signing key can be rotated without breaking live tickets.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from collections.abc import Sequence
from dataclasses import asdict, dataclass


class TicketError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class StreamTicket:
    track_id: int
    user_id: int
    exp: int
    size: int
    mime: str
    # MTProto source: public channel + message holding the document.
    channel_id: int | None = None
    channel_username: str | None = None
    message_id: int | None = None
    # Bot API source: only set when our own bot has seen the file and it is <= 20 MB
    # (ADR-003 §2-2 — the resolver never produces a bot file id).
    bot_file_id: str | None = None
    # Prefetch tickets carry a byte ceiling so they cannot stand in for a real play:
    # the edge refuses to serve past it. 0 means "the whole file" (ADR-003 §2-3).
    max_bytes: int = 0


def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _b64d(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _mac(key: bytes, body: str) -> str:
    return _b64e(hmac.new(key, body.encode(), hashlib.sha256).digest())


def sign(ticket: StreamTicket, key: bytes) -> str:
    body = _b64e(json.dumps(asdict(ticket), separators=(",", ":")).encode())
    return f"{body}.{_mac(key, body)}"


def verify(token: str, keys: Sequence[bytes], now: float | None = None) -> StreamTicket:
    body, _, sig = token.partition(".")
    if not body or not sig:
        raise TicketError("malformed")
    if not any(hmac.compare_digest(_mac(k, body), sig) for k in keys):
        raise TicketError("bad signature")
    try:
        ticket = StreamTicket(**json.loads(_b64d(body)))
    except (ValueError, TypeError) as exc:
        raise TicketError("malformed") from exc
    if ticket.exp < (time.time() if now is None else now):
        raise TicketError("expired")
    return ticket
