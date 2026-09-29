"""Diagnostics from the Mini App: boots, suspected web-view crashes, script errors.

No login required: the event that matters most — "the app started on this device" —
is sent before the app has logged in, with ``navigator.sendBeacon`` so it leaves even
if the web view dies a moment later. A beacon cannot set headers, so the body arrives
as ``text/plain`` and is parsed here. A token, when there is one, only adds who it was.
"""

from __future__ import annotations

import json
import time

from fastapi import APIRouter, Depends, Request, Response
from pydantic import ValidationError

from app import metrics
from app.api.deps import RedisDep, SettingsDep, rate_limit_ip
from app.errors import InvalidInput
from app.schemas import ClientEventIn
from app.security.tokens import TokenError, decode_access
from app.services import clientlog

router = APIRouter(prefix="/v1", tags=["telemetry"])

MAX_BODY = 8 * 1024
KNOWN_PLATFORMS = {
    "android",
    "android_x",
    "ios",
    "tdesktop",
    "macos",
    "weba",
    "webk",
    "web",
    "unigram",
    "unknown",
}


@router.post("/telemetry/client", status_code=204, dependencies=[Depends(rate_limit_ip)])
async def client_event(request: Request, redis: RedisDep, settings: SettingsDep) -> Response:
    raw = await request.body()
    if len(raw) > MAX_BODY:
        raise InvalidInput("too large")
    try:
        body = ClientEventIn.model_validate(json.loads(raw or b"{}"))
    except (ValueError, ValidationError) as exc:
        raise InvalidInput("malformed event") from exc

    user_id: int | None = None
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        try:
            user_id = decode_access(auth[7:], settings.jwt_public_key, settings.jwt_issuer).user_id
        except TokenError:
            user_id = None

    platform = body.platform if body.platform in KNOWN_PLATFORMS else "unknown"
    # Only an app route is kept. Telegram's launch fragment carries initData, a login
    # credential; an older build sent it along, and nothing like it is ever stored.
    path = body.path if body.path.startswith("#/") and "tgWebApp" not in body.path else ""
    message = "" if "tgWebApp" in body.message else body.message
    metrics.CLIENT_EVENTS.labels(body.kind, platform).inc()
    await clientlog.record(
        redis,
        clientlog.ClientEvent(
            kind=body.kind,
            session=body.session,
            platform=platform,
            tg_version=body.tg_version,
            ua=body.ua,
            app_version=body.app_version,
            stage=body.stage,
            message=message,
            stack=body.stack,
            path=path,
            user_id=user_id,
            at=time.time(),
        ),
    )
    return Response(status_code=204)
