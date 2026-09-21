"""Send a track to the user's own Telegram chat.

The core never touches audio bytes: it asks the edge (which already has the file in
its cache) to upload the track with the Bot API. The edge returns the resulting
``file_id``, which we store so later playback can use the cheaper Bot API path.
"""

from __future__ import annotations

import httpx
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.errors import Unavailable
from app.models import Track
from app.security.tokens import AccessClaims
from app.services import stream
from tmusic_common.logging import get_logger
from tmusic_common.stream_ticket import sign

log = get_logger(__name__)
SEND_TIMEOUT_S = 180.0


async def send_to_chat(
    session: AsyncSession,
    http: httpx.AsyncClient,
    settings: Settings,
    claims: AccessClaims,
    track_id: int,
    title: str,
    performer: str,
) -> None:
    if not settings.edge_internal_url:
        raise Unavailable("sending is not configured", reason="no_edge")
    source = await stream.pick_source(
        session, track_id, settings.bot_id, settings.bot_api_max_download
    )
    ticket = stream.build_ticket(source, claims.user_id, settings.stream_ticket_ttl_s * 4)
    payload = {
        "ticket": sign(ticket, settings.signing_keys[0]),
        "chat_id": claims.tg_id,
        "title": title[:64],
        "performer": performer[:64],
        "duration": 0,
    }
    try:
        response = await http.post(
            f"{settings.edge_internal_url.rstrip('/')}/internal/send",
            json=payload,
            headers={"Authorization": f"Bearer {settings.internal_api_token.get_secret_value()}"},
            timeout=SEND_TIMEOUT_S,
        )
    except httpx.HTTPError as exc:
        log.warning("send.edge_unreachable", track_id=track_id, error=str(exc))
        raise Unavailable("could not send the track", reason="edge_unreachable") from exc
    if response.status_code != 200:
        log.warning("send.failed", track_id=track_id, status=response.status_code)
        raise Unavailable("could not send the track", reason="send_failed")

    body = response.json()
    file_id = body.get("file_id")
    if file_id:
        # Future plays of this track can use the Bot API path (no user-account quota).
        await session.execute(
            update(Track)
            .where(Track.id == source.track_id, Track.bot_file_id.is_(None))
            .values(bot_file_id=file_id, bot_id=settings.bot_id)
        )
