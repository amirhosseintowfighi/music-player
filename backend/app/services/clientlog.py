"""What the Mini App saw on the listener's device: boots, crashes, script errors.

A Telegram web view that dies ("Webview crashed") takes its own logs with it, and a
listener who reports it rarely knows their GPU or their Telegram version. So the app
reports three things, and this keeps the latest of them for the admin panel:

- ``boot``: the app started, with the device's user agent and Telegram platform — the
  one event sent before anything heavy happens, so it arrives even when what follows
  crashes;
- ``crash``: on the next start, the previous session never reached ``ready``: the web
  view died on the way, and ``stage`` says how far it got;
- ``error``: an uncaught script error or promise rejection, with its stack.

Redis only, a capped list: diagnostics, not a record anyone depends on.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass
from typing import Any

from redis.asyncio import Redis

from app.redis_util import resolve

KEY = "clientlog"
MAX_EVENTS = 2000
KEEP_S = 14 * 86400


@dataclass(frozen=True, slots=True)
class ClientEvent:
    kind: str
    session: str
    platform: str
    tg_version: str
    ua: str
    app_version: str
    stage: str
    message: str
    stack: str
    path: str
    user_id: int | None
    at: float


async def record(redis: Redis, event: ClientEvent) -> None:
    async with redis.pipeline(transaction=True) as pipe:
        pipe.lpush(KEY, json.dumps(asdict(event), ensure_ascii=False))
        pipe.ltrim(KEY, 0, MAX_EVENTS - 1)
        pipe.expire(KEY, KEEP_S)
        await pipe.execute()


async def recent(
    redis: Redis,
    *,
    kind: str | None = None,
    platform: str | None = None,
    limit: int = 200,
) -> list[dict[str, Any]]:
    raw: list[str] = await resolve(redis.lrange(KEY, 0, MAX_EVENTS - 1))
    out: list[dict[str, Any]] = []
    for item in raw:
        try:
            event: dict[str, Any] = json.loads(item)
        except ValueError:
            continue
        if kind and event.get("kind") != kind:
            continue
        if platform and event.get("platform") != platform:
            continue
        out.append(event)
        if len(out) >= limit:
            break
    return out


async def summary(
    redis: Redis, since_s: float = 86400, now: float | None = None
) -> dict[str, dict[str, int]]:
    """{platform: {boot: n, crash: n, error: n}} over the last day."""
    cutoff = (time.time() if now is None else now) - since_s
    counts: dict[str, dict[str, int]] = {}
    for event in await recent(redis, limit=MAX_EVENTS):
        if float(event.get("at", 0)) < cutoff:
            continue
        platform = str(event.get("platform") or "unknown")
        per = counts.setdefault(platform, {"boot": 0, "crash": 0, "error": 0})
        kind = str(event.get("kind"))
        if kind in per:
            per[kind] += 1
    return counts
