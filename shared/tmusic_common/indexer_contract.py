"""Wire contract between the edge indexer and the core internal API.

Both sides import these models, so a field change breaks type-checking on both ends
instead of silently at runtime.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

TaskMode = Literal["backfill", "incremental"]
# Which reader the edge uses. The web preview is the default and needs no account;
# "mtproto" is the fallback for channels that have no public preview at all.
TaskSource = Literal["web_preview", "mtproto"]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AccountIn(_Model):
    session_key: str = Field(min_length=1, max_length=64)
    phone_hint: str | None = Field(None, max_length=8)


class RegisterAccountsIn(_Model):
    worker_id: str = Field(min_length=1, max_length=64)
    accounts: list[AccountIn] = Field(max_length=100)


class RegisterAccountsOut(_Model):
    ids: dict[str, int]


class ChannelMeta(_Model):
    # The web preview never reveals the numeric channel id, so it is optional now and
    # gets filled in the first time MTProto touches the channel (ADR-002).
    tg_channel_id: int | None = None
    username: str | None = None
    title: str
    description: str | None = None
    music_total: int | None = None
    subscribers: int | None = None


class AudioItem(_Model):
    message_id: int
    posted_at: datetime
    views: int | None = None
    # Telegram's stable id and the dedup key. The web preview does not expose it, so a
    # crawled item arrives without one and gets it the first time somebody plays the
    # track (ADR-002 layer C). Identity until then is "<channel_id>:<message_id>".
    file_unique_id: str | None = Field(None, min_length=4, max_length=64)
    duration: int = Field(ge=0)
    file_size: int = Field(ge=0)
    mime_type: str | None = Field(None, max_length=100)
    title: str | None = Field(None, max_length=512)
    performer: str | None = Field(None, max_length=512)
    file_name: str | None = Field(None, max_length=512)
    caption: str | None = Field(None, max_length=4096)
    has_thumb: bool = False
    # Only for items received by our bot (Bot API file ids are per bot).
    bot_file_id: str | None = None
    # Only voice notes and bot-received files expose a direct link; music posts never
    # do (measured — ADR-002 §8), so for crawled tracks this stays empty.
    cdn_url: str | None = Field(None, max_length=1024)
    thumb_url: str | None = Field(None, max_length=1024)


class AccountReportIn(_Model):
    status: Literal["healthy", "cooling", "limited", "banned", "disabled"]
    cooling_until: datetime | None = None
    floodwait_s: int = Field(0, ge=0)
    error: str | None = Field(None, max_length=500)


class ReleaseIn(_Model):
    lease_token: str
    retry_after_s: int = Field(0, ge=0, le=86400)


class ProbeIn(_Model):
    """Ask the edge to read the ID3 tags of one track (phase: metadata backfill)."""

    ticket: str


class ProbeOut(_Model):
    track_id: int
    album: str | None = Field(None, max_length=512)
    year: int | None = Field(None, ge=1900, le=2100)
    genre: str | None = Field(None, max_length=100)
    title: str | None = Field(None, max_length=512)
    artist: str | None = Field(None, max_length=512)


# ── web-preview crawler (ADR-002) ────────────────────────────────────────────


class CrawlClaimIn(_Model):
    worker_id: str = Field(min_length=1, max_length=100)
    limit: int = Field(5, ge=1, le=50)


class CrawlTaskOut(_Model):
    channel_id: int
    username: str
    lease_token: str
    mode: TaskMode
    source: TaskSource = "web_preview"
    before: int | None = None
    stop_at: int | None = None
    needs_meta: bool = False


class CrawlClaimOut(_Model):
    tasks: list[CrawlTaskOut] = Field(default_factory=list)


class CrawlBatchIn(_Model):
    """One page of crawled items, posted as soon as it is parsed."""

    channel_id: int
    lease_token: str
    meta: ChannelMeta | None = None
    items: list[AudioItem] = Field(default_factory=list, max_length=200)
    oldest_msg_id: int | None = None
    newest_msg_id: int | None = None
    finished: bool = False
    extraction_rate: float | None = Field(None, ge=0, le=1)
    # Messages seen on this page, audio or not — the denominator of the parser health
    # check, which is why it is sent even when ``items`` is empty.
    page_messages: int = 0
    mentions: list[str] = Field(default_factory=list, max_length=200)


class CrawlBatchOut(_Model):
    lease_valid: bool
    inserted: int = 0
    updated: int = 0
    duplicates: int = 0


class CrawlFailureIn(_Model):
    lease_token: str
    reason: str = Field(max_length=100)
    detail: str = Field("", max_length=500)
    preview_disabled: bool = False
    # "Not ever", as opposed to "not today": a username nobody owns, or a name that
    # belongs to a user or a bot. Retrying those is pure waste.
    permanent: bool = False
    retry_after_s: int | None = Field(None, ge=0, le=86_400)


# ── candidate probing (ADR-002 §3) ───────────────────────────────────────────
#
# A candidate is scored from one preview page: how much of what it posts is music,
# and how often it posts. One page, no account, no lease — a bad guess costs nothing.


class CandidateTaskOut(_Model):
    candidate_id: int
    username: str


class CandidateClaimOut(_Model):
    tasks: list[CandidateTaskOut] = Field(default_factory=list)


class CandidateStatsIn(_Model):
    title: str | None = Field(None, max_length=300)
    subscribers: int | None = Field(None, ge=0)
    messages: int = Field(0, ge=0)
    audio: int = Field(0, ge=0)
    newest_msg_id: int | None = Field(None, ge=0)
    posts_per_day: float | None = Field(None, ge=0)
    unavailable: bool = False


class SearchTermsOut(_Model):
    """What to ask Telegram about. Empty means the feature is off — not "no ideas"."""

    terms: list[str] = Field(default_factory=list, max_length=100)


class SearchFoundIn(_Model):
    """Channels one search turned up, on their way to the candidate queue."""

    term: str = Field(min_length=1, max_length=100)
    usernames: list[str] = Field(default_factory=list, max_length=500)


class SearchFoundOut(_Model):
    added: int = 0
