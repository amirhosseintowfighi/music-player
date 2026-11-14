"""Share to a Telegram story: the image a story needs, at an address Telegram can fetch.

``WebApp.shareToStory`` takes a public HTTPS URL, not a file. The Mini App draws the
card on a canvas, posts it here, and gets back a short-lived URL for the story
editor to load. Only PNG or JPEG of a bounded size, kept for an hour, never audio.
"""

from __future__ import annotations

import base64
import binascii
import secrets
from typing import Annotated

from fastapi import APIRouter, Path, Request, Response

from app.api.deps import RedisDep, SettingsDep, WritableClaims
from app.errors import InvalidInput, LimitReached, NotFound
from app.schemas import StoryImageIn, StoryImageOut

router = APIRouter(prefix="/v1/share", tags=["share"])

STORY_TTL_S = 3600
STORY_MAX_BYTES = 1_500_000
STORIES_PER_HOUR = 30
_TYPES = {b"\x89PNG\r\n\x1a\n": "image/png", b"\xff\xd8\xff": "image/jpeg"}


def _image_type(data: bytes) -> str | None:
    return next((mime for magic, mime in _TYPES.items() if data.startswith(magic)), None)


@router.post("/story", response_model=StoryImageOut)
async def upload_story(
    body: StoryImageIn,
    request: Request,
    claims: WritableClaims,
    redis: RedisDep,
    settings: SettingsDep,
) -> StoryImageOut:
    try:
        data = base64.b64decode(body.image, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise InvalidInput("not base64", reason="bad_image") from exc
    mime = _image_type(data)
    if mime is None or len(data) > STORY_MAX_BYTES:
        raise InvalidInput("a PNG or JPEG up to 1.5 MB", reason="bad_image")
    quota = f"story:n:{claims.user_id}"
    sent = await redis.incr(quota)
    if sent == 1:
        await redis.expire(quota, 3600)
    if sent > STORIES_PER_HOUR:
        raise LimitReached("too many stories", kind="stories", limit=STORIES_PER_HOUR)
    token = secrets.token_urlsafe(18)
    await redis.set(f"story:{token}", f"{mime}|{body.image}", ex=STORY_TTL_S)
    base = (settings.public_api_url or str(request.base_url)).rstrip("/")
    return StoryImageOut(url=f"{base}/v1/share/story/{token}", expires_in=STORY_TTL_S)


@router.get("/story/{token}", include_in_schema=False)
async def story_image(
    token: Annotated[str, Path(pattern=r"^[A-Za-z0-9_-]{16,40}$")], redis: RedisDep
) -> Response:
    stored = await redis.get(f"story:{token}")
    if not stored:
        raise NotFound("story image expired")
    mime, encoded = str(stored).split("|", 1)
    return Response(
        content=base64.b64decode(encoded),
        media_type=mime,
        headers={"Cache-Control": f"public, max-age={STORY_TTL_S}"},
    )
