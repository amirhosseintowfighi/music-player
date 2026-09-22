"""The one MTProto session the edge still needs (ADR-002 §6).

The pool is gone. The catalogue no longer comes from user accounts, so there is
nothing to round-robin across and nothing to keep warm: what is left is a single
session used for two narrow jobs — resolving a track's file the first time somebody
plays it, and streaming its bytes.

What survives from the old pool, because it still matters with one account:

- FloodWait is never slept through by Telethon (``flood_sleep_threshold=0``); it
  cools the account down and the core's circuit breaker takes over meanwhile.
- Resolved channel peers are cached on disk, because username resolution is the
  most rate-limited call Telegram has and access hashes are per account.
"""

from __future__ import annotations

import asyncio
import json
import random
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from prometheus_client import Counter, Gauge
from telethon import TelegramClient, types
from telethon.sessions import StringSession

from tmusic_common.indexer_contract import AccountIn, AccountReportIn
from tmusic_common.logging import get_logger
from tmusic_indexer.config import Settings
from tmusic_indexer.crypto import load_sessions

log = get_logger(__name__)

FLOODWAITS = Counter("floodwaits_total", "FloodWait responses from Telegram", ["kind"])
ACCOUNT_READY = Gauge("indexer_account_ready", "1 when the resolver account can be used")

# Sessions whose name starts with this belong to the crawler, everything else to
# the resolver. One account must never do both: a crawling account can be limited
# or banned, and playback may not go down with it.
CRAWL_PREFIX = "crawl"

ClientFactory = Callable[[str], Any]


@dataclass
class Account:
    """One Telegram session: its client, its peer cache, and how hot it is."""

    key: str
    client: Any
    id: int | None = None
    status: str = "healthy"
    cooling_until: float = 0.0
    phone_hint: str | None = None
    downloads: asyncio.Semaphore = field(default_factory=lambda: asyncio.Semaphore(8))
    peers: dict[str, list[int]] = field(default_factory=dict)

    def available(self, now: float | None = None) -> bool:
        return self.status in ("healthy", "cooling", "limited") and self.cooling_until <= (
            time.time() if now is None else now
        )


class ResolverAccount:
    """Owns the session: connect, resolve peers, absorb FloodWait."""

    def __init__(
        self,
        settings: Settings,
        client_factory: ClientFactory | None = None,
        role: str = "resolver",
    ) -> None:
        self.settings = settings
        self._factory = client_factory or self._telethon_client
        self.role = role
        self.account: Account | None = None

    def _mine(self, key: str) -> bool:
        crawler = key.lower().startswith(CRAWL_PREFIX)
        return crawler if self.role == "crawler" else not crawler

    def _ready(self, value: int) -> None:
        # One gauge, one meaning: whether playback can resolve files. A crawling
        # account cooling down must not read as "the resolver is dead".
        if self.role == "resolver":
            ACCOUNT_READY.set(value)

    def _telethon_client(self, session_string: str) -> Any:
        return TelegramClient(
            StringSession(session_string),
            self.settings.tg_api_id,
            self.settings.tg_api_hash.get_secret_value(),
            device_model=self.settings.device_model,
            flood_sleep_threshold=0,
            request_retries=3,
            connection_retries=5,
            auto_reconnect=True,
        )

    def _peer_cache_path(self, key: str) -> Path:
        return self.settings.sessions_dir / f"{key}.peers.json"

    async def start(self) -> None:
        """Loads this role's session from ``sessions_dir``; extras are ignored, not pooled."""
        sessions = {
            key: value
            for key, value in load_sessions(
                self.settings.sessions_dir, self.settings.session_enc_key.get_secret_value()
            ).items()
            if self._mine(key)
        }
        if not sessions:
            # The web crawler needs no account at all, and the MTProto fallback simply
            # stays unavailable until someone logs one in.
            log.warning("account.none_configured", role=self.role)
            self._ready(0)
            return
        key, session_string = next(iter(sorted(sessions.items())))
        if len(sessions) > 1:
            log.warning(
                "account.extra_sessions_ignored", role=self.role, using=key, found=len(sessions)
            )

        account = Account(key=key, client=self._factory(session_string))
        account.downloads = asyncio.Semaphore(self.settings.downloads_max)
        cache = self._peer_cache_path(key)
        if cache.exists():
            account.peers = json.loads(cache.read_text("utf-8"))
        try:
            await account.client.connect()
            if not await account.client.is_user_authorized():
                account.status = "disabled"
                log.error("account.session_not_authorized", account=key)
            else:
                me = await account.client.get_me()
                phone = getattr(me, "phone", None) or ""
                account.phone_hint = phone[-4:] or None
        except Exception:
            account.status = "disabled"
            log.exception("account.connect_failed", account=key)
        self.account = account
        self._ready(1 if account.available() else 0)
        log.info("account.started", role=self.role, account=key, status=account.status)

    async def stop(self) -> None:
        if self.account is None:
            return
        try:
            await self.account.client.disconnect()
        except Exception:  # noqa: BLE001 — shutting down anyway
            log.warning("account.disconnect_failed", account=self.account.key)

    def registration(self) -> list[AccountIn]:
        if self.account is None:
            return []
        return [AccountIn(session_key=self.account.key, phone_hint=self.account.phone_hint)]

    def assign_ids(self, ids: dict[str, int]) -> None:
        if self.account is not None and self.account.key in ids:
            self.account.id = ids[self.account.key]

    def ready(self) -> Account | None:
        """The account when it can be used right now, else None (never raises)."""
        account = self.account
        usable = account if account is not None and account.available() else None
        self._ready(1 if usable else 0)
        return usable

    def on_flood(self, seconds: int) -> AccountReportIn:
        """Cools the account down. Playback of resolved tracks is unaffected."""
        account = self.account
        assert account is not None
        kind = "limited" if seconds >= self.settings.limited_after_s else "cooling"
        FLOODWAITS.labels(kind).inc()
        wait = seconds + random.uniform(1, max(2.0, seconds * 0.1))  # noqa: S311 — jitter only
        account.cooling_until = time.time() + wait
        account.status = kind
        self._ready(0)
        log.warning("account.flood_wait", role=self.role, seconds=seconds, status=kind)
        return AccountReportIn(
            status=kind,
            cooling_until=datetime.fromtimestamp(account.cooling_until, UTC),
            floodwait_s=seconds,
            error=f"FloodWait {seconds}s",
        )

    async def resolve(self, username: str | None, tg_channel_id: int | None) -> Any:
        account = self.account
        if account is None:
            raise LookupError("no resolver account configured")
        keys = [
            k for k in (username and username.lower(), tg_channel_id and str(tg_channel_id)) if k
        ]
        for key in keys:
            if key in account.peers:
                channel_id, access_hash = account.peers[key]
                return types.InputPeerChannel(channel_id, access_hash)
        if not username:
            raise LookupError("channel has no username and no cached access hash")
        peer = await account.client.get_input_entity(username)
        if not isinstance(peer, types.InputPeerChannel):
            raise TypeError("not a channel")
        pair = [peer.channel_id, peer.access_hash]
        account.peers[username.lower()] = pair
        account.peers[str(peer.channel_id)] = pair
        path = self._peer_cache_path(account.key)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(account.peers), "utf-8")
        tmp.chmod(0o600)
        tmp.replace(path)
        return peer
