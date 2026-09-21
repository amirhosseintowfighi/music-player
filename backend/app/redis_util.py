"""redis-py annotates commands as ``Awaitable[T] | T`` (one stub for sync and async
clients). ``resolve`` narrows that for the asyncio client without casts."""

from __future__ import annotations

import inspect
from collections.abc import Awaitable


async def resolve[T](value: Awaitable[T] | T) -> T:
    if inspect.isawaitable(value):
        return await value
    return value


# Users banned by an admin. The request path checks this instead of hitting the DB.
BANNED_SET = "banned_users"
