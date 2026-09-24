"""Request/response models. Every input is validated here before reaching a service."""

from __future__ import annotations

from datetime import date, datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class ApiModel(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid")


class ErrorBody(ApiModel):
    code: str
    message: str
    details: dict[str, object] = {}


class ErrorResponse(ApiModel):
    error: ErrorBody


class Page[T](ApiModel):
    items: list[T]
    next_cursor: str | None = None


# ── auth ──


class TelegramLoginIn(ApiModel):
    init_data: str = Field(min_length=10, max_length=4096)


class RefreshIn(ApiModel):
    refresh_token: str = Field(min_length=20, max_length=200)


class MeOut(ApiModel):
    id: int
    tg_id: int
    username: str | None
    first_name: str
    lang: Literal["fa", "en"]
    plan: str
    premium_until: datetime | None
    features: list[str]
    limits: dict[str, object]
    referral_code: str
    public_profile: bool = True


class GateChannelOut(ApiModel):
    username: str
    url: str


class GateOut(ApiModel):
    """Channels this listener still has to join. Empty list = the app opens."""

    missing: list[GateChannelOut] = []


class TokenOut(ApiModel):
    access_token: str
    access_expires_at: int
    refresh_token: str
    refresh_expires_at: int
    token_type: Literal["bearer"] = "bearer"  # noqa: S105 — not a secret
    start_param: str | None = None
    me: MeOut


class LangIn(ApiModel):
    lang: Literal["fa", "en"]


# ── channels ──

ChannelStatus = Literal["pending", "indexing", "active", "paused", "failed", "blacklisted"]


class AddChannelIn(ApiModel):
    ref: str = Field(min_length=2, max_length=200, description="@username or t.me link")


class ChannelOut(ApiModel):
    id: int
    username: str | None
    title: str | None
    status: ChannelStatus
    status_reason: str | None
    progress_pct: int
    tracks_count: int
    subscribers_count: int
    is_featured: bool
    category_id: int | None
    source: Literal["mtproto", "bot_admin"]
    avatar_url: str | None = None


class UserChannelOut(ChannelOut):
    added_at: datetime


class AddChannelOut(ApiModel):
    channel: ChannelOut
    created: bool


class CategoryOut(ApiModel):
    id: int
    slug: str
    name_fa: str
    name_en: str


# ── tracks ──


class ArtistRef(ApiModel):
    id: int
    name: str
    role: Literal["primary", "feature", "composer", "lyricist"]


class TrackChannelRef(ApiModel):
    """The channel a track is credited to (ADR-003 §2-5).

    The point of showing it is to send members to the channel that published the
    music, so the one picked is the one the viewer has *not* joined yet.
    """

    id: int
    username: str | None
    title: str
    is_featured: bool
    subscribers_count: int
    # True when the viewer already has this channel in their library.
    joined: bool


class TrackOut(ApiModel):
    id: int
    title: str
    artists: list[ArtistRef]
    album: str | None
    duration: int
    language: str | None
    year: int | None
    has_thumb: bool
    channels_count: int
    playable: bool
    liked: bool = False
    channel: TrackChannelRef | None = None
    # Dominant cover colours, so the player can paint before the artwork loads.
    palette: str | None = None


class ArtistOut(ApiModel):
    id: int
    name: str
    latin_name: str | None
    tracks_count: int


class AlbumOut(ApiModel):
    album: str
    artist_id: int | None
    artist_name: str | None
    tracks_count: int


class SearchOut(ApiModel):
    items: list[TrackOut]
    total: int
    offset: int
    limit: int
    degraded: bool = Field(False, description="true when served by the Postgres fallback")


class SuggestOut(ApiModel):
    history: list[str]
    tracks: list[TrackOut]


class ThumbsIn(ApiModel):
    ids: list[int] = Field(min_length=1, max_length=100)


class ThumbsOut(ApiModel):
    items: dict[str, str]


class StreamOut(ApiModel):
    url: str
    thumb_url: str | None
    expires_at: int
    size: int
    mime: str


# ── playlists, likes, history (phase 5) ──


class PlaylistOut(ApiModel):
    id: int
    name: str
    description: str | None
    kind: Literal["manual", "smart_ai", "discover_weekly", "daily_mix", "radio"]
    is_public: bool
    is_collaborative: bool
    share_slug: str | None
    share_url: str | None = None
    tracks_count: int
    duration_total: int
    is_owner: bool = True
    can_edit: bool = True
    updated_at: datetime


class PlaylistDetailOut(PlaylistOut):
    items: list[TrackOut]


class CreatePlaylistIn(ApiModel):
    name: str = Field(min_length=1, max_length=100)
    description: str | None = Field(None, max_length=500)
    track_ids: list[int] = Field(default_factory=list, max_length=500)


class UpdatePlaylistIn(ApiModel):
    name: str | None = Field(None, min_length=1, max_length=100)
    description: str | None = Field(None, max_length=500)
    is_public: bool | None = None
    is_collaborative: bool | None = None


class AddTracksIn(ApiModel):
    track_ids: list[int] = Field(min_length=1, max_length=500)


class MoveTrackIn(ApiModel):
    track_id: int
    after_track_id: int | None = None


class LikeOut(ApiModel):
    liked: bool
    likes_count: int


class PlayEventIn(ApiModel):
    track_id: int
    duration_played: int = Field(ge=0, le=24 * 3600)
    completed: bool = False
    source: Literal[
        "library", "search", "playlist", "channel", "discover", "mix", "radio", "trending", "shared"
    ] = "library"
    source_id: int | None = None


class PlaybackStateIn(ApiModel):
    track_id: int | None = None
    position_s: int = Field(0, ge=0)
    queue: list[int] = Field(default_factory=list, max_length=200)
    queue_index: int = Field(0, ge=0)
    shuffle: bool = False
    repeat_mode: Literal["off", "one", "all"] = "off"
    speed: float = Field(1.0, ge=0.5, le=3.0)


class PlaybackStateOut(PlaybackStateIn):
    items: list[TrackOut] = Field(default_factory=list)
    updated_at: datetime | None = None


class PlanOut(ApiModel):
    code: str
    name: str
    period_days: int | None
    prices: dict[str, int]
    limits: dict[str, Any]
    features: list[str]
    is_current: bool = False


class ProviderOut(ApiModel):
    code: str
    currency: str
    kind: Literal["telegram_invoice", "redirect", "instructions"]


class PlansOut(ApiModel):
    plans: list[PlanOut]
    providers: list[ProviderOut]
    trial_available: bool
    trial_days: int


class CheckoutIn(ApiModel):
    plan_code: str = Field(min_length=1, max_length=40)
    provider: str = Field(min_length=1, max_length=40)
    discount_code: str | None = Field(None, max_length=40)


class CheckoutOut(ApiModel):
    payment_id: str
    provider: str
    amount: int
    currency: str
    kind: Literal["telegram_invoice", "redirect", "instructions"]
    url: str | None = None
    payload: dict[str, Any] | None = None


class SubscriptionOut(ApiModel):
    plan_code: str
    status: Literal["trialing", "active", "grace", "expired", "canceled", "refunded", "free"]
    source: str | None = None
    started_at: datetime | None = None
    expires_at: datetime | None = None
    grace_until: datetime | None = None
    days_left: int = 0
    auto_renew: bool = False


class PaymentOut(ApiModel):
    public_id: str
    provider: str
    plan_code: str
    amount: int
    currency: str
    status: str
    created_at: datetime
    paid_at: datetime | None = None


class DiscountPreviewOut(ApiModel):
    valid: bool
    amount: int
    original_amount: int
    discount_amount: int
    currency: str
    reason: str | None = None


class SectionOut(ApiModel):
    id: str
    kind: Literal["playlist", "tracks", "channels"]
    title: str
    playlist_id: int | None = None
    items: list[TrackOut] = Field(default_factory=list)


class DiscoverOut(ApiModel):
    sections: list[SectionOut]


# ── admin (phase 8) ───────────────────────────────────────────────────────────


class AdminLoginIn(ApiModel):
    """Raw Telegram Login Widget payload; every field is part of the signature."""

    id: int
    first_name: str
    auth_date: int
    hash: str
    last_name: str | None = None
    username: str | None = None
    photo_url: str | None = None


class AdminPasswordLoginIn(ApiModel):
    """Username + password, for signing in where the Telegram widget cannot run."""

    username: str = Field(min_length=3, max_length=64)
    password: str = Field(min_length=8, max_length=256)


class AdminMeOut(ApiModel):
    id: int
    tg_id: int
    role: str
    permissions: list[str]
    first_name: str = ""
    is_active: bool = True


class AdminTokenOut(ApiModel):
    access_token: str
    expires_at: int
    token_type: Literal["bearer"] = "bearer"  # noqa: S105 — not a secret
    me: AdminMeOut


class ImpersonateOut(ApiModel):
    access_token: str
    expires_at: int
    user_id: int


class RevenueRow(ApiModel):
    provider: str
    currency: str
    amount: int


class OverviewOut(ApiModel):
    dau: int
    wau: int
    mau: int
    new_users_today: int
    paying_users: int
    trials: int
    tracks: int
    channels: int
    plays_today: int
    revenue_30d: list[RevenueRow]
    conversion_pct: float
    churn_30d_pct: float


class UserRowOut(ApiModel):
    id: int
    tg_id: int
    username: str | None = None
    first_name: str
    lang: str
    plan_code: str
    premium_until: datetime | None = None
    is_banned: bool
    ban_reason: str | None = None
    bot_blocked: bool = False
    created_at: datetime
    last_seen_at: datetime


class UsersPageOut(ApiModel):
    items: list[UserRowOut]
    total: int


class UserDetailOut(ApiModel):
    user: UserRowOut
    stats: dict[str, int]
    subscription_status: str
    subscription_expires_at: datetime | None = None


class BanIn(ApiModel):
    banned: bool
    reason: str = Field("", max_length=500)


class PendingPaymentOut(ApiModel):
    id: int
    public_id: str
    amount: int
    currency: str
    plan_code: str
    receipt_file_id: str | None = None
    review_due_at: datetime | None = None
    created_at: datetime
    user_id: int
    tg_id: int
    first_name: str
    username: str | None = None


class ReportOut(ApiModel):
    id: int
    entity_type: str
    entity_id: int
    reason: str
    details: str | None = None
    status: str
    resolution: str | None = None
    due_at: datetime
    created_at: datetime


class ResolveReportIn(ApiModel):
    status: Literal["in_review", "actioned", "dismissed"]
    resolution: str = Field("", max_length=500)
    hide_entity: bool = False


class BroadcastVariantIn(ApiModel):
    text: str = Field(min_length=1, max_length=4000)
    photo_file_id: str | None = None
    button_text: str | None = Field(None, max_length=64)
    button_url: str | None = Field(None, max_length=500)
    weight: int = Field(1, ge=1, le=100)


class AdminBroadcastIn(ApiModel):
    target: dict[str, Any] = Field(default_factory=dict)
    variants: list[BroadcastVariantIn] = Field(min_length=1, max_length=4)
    scheduled_at: datetime | None = None


class AdminBroadcastOut(ApiModel):
    id: int
    status: str
    total: int
    sent: int
    failed: int
    blocked: int
    scheduled_at: datetime | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    created_at: datetime


class AuditEntryOut(ApiModel):
    id: int
    actor_type: str
    actor_id: int | None = None
    action: str
    entity: str
    entity_id: str
    payload: dict[str, Any]
    created_at: datetime


class HealthOut(ApiModel):
    indexer_accounts: dict[str, int]
    edges: dict[str, int]
    channels: dict[str, int]
    queues: dict[str, int]
    floodwait_24h_s: int


class PlanPatchIn(ApiModel):
    name_fa: str | None = None
    name_en: str | None = None
    period_days: int | None = None
    limits: dict[str, Any] | None = None
    features: list[str] | None = None
    prices: dict[str, int] | None = None
    is_active: bool | None = None
    position: int | None = None


class SettingIn(ApiModel):
    value: Any


class ProviderPatchIn(ApiModel):
    is_enabled: bool | None = None
    config: dict[str, Any] | None = None


class AdminUpsertIn(ApiModel):
    tg_id: int
    role: Literal["owner", "admin", "moderator", "support"]
    permissions: list[str] = Field(default_factory=list)
    is_active: bool = True


# ── social and notifications ──────────────────────────────────────────────────


class ArtistCountOut(ApiModel):
    id: int
    name: str
    plays: int


class PublicPlaylistOut(ApiModel):
    id: int
    name: str
    tracks_count: int
    share_slug: str | None = None


class ProfileOut(ApiModel):
    user_id: int
    first_name: str
    username: str | None = None
    is_pro: bool
    followers: int
    following: int
    playlists: int
    tracks_played: int
    joined_at: datetime
    is_me: bool
    is_following: bool
    top_artists: list[ArtistCountOut]
    top_tracks: list[TrackOut]
    public_playlists: list[PublicPlaylistOut]


class FollowOut(ApiModel):
    following: bool
    changed: bool


class ConnectionOut(ApiModel):
    user_id: int
    first_name: str
    username: str | None = None
    is_pro: bool
    is_following: bool


class FriendActivityOut(ApiModel):
    user_id: int
    first_name: str
    username: str | None = None
    played_at: datetime
    track: TrackOut


class WrappedTrackOut(ApiModel):
    id: int
    title: str
    plays: int


class BusiestDayOut(ApiModel):
    day: str
    plays: int


class WrappedOut(ApiModel):
    year: int
    plays: int
    minutes: int
    unique_tracks: int
    active_days: int
    top_artists: list[ArtistCountOut]
    top_tracks: list[WrappedTrackOut]
    busiest_day: BusiestDayOut | None = None
    tracks: list[TrackOut] = Field(default_factory=list)


class PublicProfileIn(ApiModel):
    public: bool


class NotificationPrefsOut(ApiModel):
    prefs: dict[str, bool]


class NotificationPrefsIn(ApiModel):
    prefs: dict[str, bool] = Field(default_factory=dict)
    tz_offset_minutes: int | None = Field(None, ge=-720, le=840)


# ── channel discovery and crawler health (ADR-002 §5) ─────────────────────────


class CandidateOut(ApiModel):
    id: int
    username: str
    title: str | None = None
    source: str
    status: str
    score: float
    tracks_estimate: int | None = None
    audio_ratio: float | None = None
    posts_per_day: float | None = None
    subscribers: int | None = None
    mention_count: int
    requested_by: int
    discovered_from_channel_id: int | None = None
    probed_at: datetime | None = None
    reject_reason: str | None = None
    created_at: datetime


class CandidatePageOut(ApiModel):
    items: list[CandidateOut]
    total: int


class RejectCandidatesIn(ApiModel):
    ids: list[int] = Field(min_length=1, max_length=200)
    reason: str = Field("", max_length=500)


class ImportChannelsIn(ApiModel):
    """A pasted list: usernames, @names, t.me links or a one-column CSV."""

    text: str = Field(min_length=1, max_length=64_000)


class ImportChannelsOut(ApiModel):
    created: int
    existing: int
    blocked: int
    invalid: list[str]


class CrawlChannelOut(ApiModel):
    id: int
    username: str | None = None
    title: str | None = None
    status: str
    crawl_status: str
    progress_pct: int
    oldest_crawled_msg_id: int | None = None
    newest_crawled_msg_id: int | None = None
    tracks_count: int
    last_crawl_at: datetime | None = None
    next_crawl_at: datetime | None = None
    crawl_interval_sec: int
    fail_count: int
    crawl_error: str | None = None
    extraction_rate: float | None = None
    preview_available: bool | None = None


class CrawlerHealthOut(ApiModel):
    channels: int
    running: int
    errored: int
    preview_disabled: int
    # Channels handed to the logged-in account because they have no preview at all.
    mtproto: int = 0
    completed: int
    due: int
    unresolved_tracks: int


class ParserDayOut(ApiModel):
    day: date
    pages: int
    messages: int
    audio_items: int
    empty_pages: int
    rate: float


class ParserHealthOut(ApiModel):
    days: list[ParserDayOut]
    today_rate: float | None = None
    expected_rate: float | None = None
    alert: bool


class ResolverStatusOut(ApiModel):
    unresolved: int
    pending: int
    failed: int
    resolved: int
    breaker_open: bool
    consecutive_failures: int
    accounts: int


class SuggestChannelOut(ApiModel):
    """Either we already index it (and you now have it), or your request is queued."""

    status: Literal["subscribed", "queued"]
    channel: ChannelOut | None = None
    candidate_id: int | None = None


class PlaybackEventIn(ApiModel):
    """One playback telemetry event from a client (ADR-003 phase 10)."""

    kind: Literal["start", "underrun", "error"]
    # Only for "start": milliseconds from the user's tap to the first sound.
    ms: int | None = Field(None, ge=0, le=600_000)
    # Only for "error": the client's own classification.
    reason: Literal["network", "unavailable", "plan_limit", "decode", "unknown"] | None = None


class ReportTrackIn(ApiModel):
    """A listener telling us something is wrong with a track."""

    reason: Literal["wrong_metadata", "copyright", "inappropriate", "broken"]
    details: str = Field("", max_length=2000)


class ReportTrackOut(ApiModel):
    id: int
    status: str


HEX_COLOR = r"^#[0-9a-fA-F]{6}$"


class PaletteIn(ApiModel):
    """Three dominant colours of a track's artwork, as ``#rrggbb``."""

    colors: list[Annotated[str, Field(pattern=HEX_COLOR)]] = Field(min_length=3, max_length=3)


class MetadataReviewOut(ApiModel):
    """A track the parser was unsure about, or a listener reported."""

    id: int
    title: str
    artists: str
    metadata_confidence: int
    file_name: str | None = None
    album: str | None = None
    reports: int
    channel_title: str | None = None


class FixMetadataIn(ApiModel):
    title: str | None = Field(None, max_length=512)
    artist: str | None = Field(None, max_length=200)
    # Apply the artist correction to every track that currently shares this one's
    # (wrong) artist — the same mangled name is usually posted hundreds of times.
    apply_to_artist: bool = False
