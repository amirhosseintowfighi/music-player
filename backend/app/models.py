"""ORM mappings for the tables the code touches so far.

The schema itself lives in migrations/sql (source of truth). tests/test_schema_drift.py
fails if a mapped column is missing from, or disagrees with, the migrated database.
Tables for later phases get mapped when their phase lands.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    LargeBinary,
    Numeric,
    SmallInteger,
    Text,
    Uuid,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, CITEXT, JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

TS = DateTime(timezone=True)


class Base(DeclarativeBase):
    pass


class Plan(Base):
    __tablename__ = "plans"
    code: Mapped[str] = mapped_column(Text, primary_key=True)
    name_fa: Mapped[str] = mapped_column(Text)
    name_en: Mapped[str] = mapped_column(Text)
    period_days: Mapped[int | None] = mapped_column(Integer)
    limits: Mapped[dict[str, Any]] = mapped_column(JSONB)
    features: Mapped[list[str]] = mapped_column(ARRAY(Text))
    prices: Mapped[dict[str, Any]] = mapped_column(JSONB)
    is_active: Mapped[bool] = mapped_column(Boolean)
    position: Mapped[int] = mapped_column(Integer)
    updated_at: Mapped[datetime] = mapped_column(TS, server_default=func.now())


class AdminUser(Base):
    __tablename__ = "admin_users"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    tg_id: Mapped[int] = mapped_column(BigInteger, unique=True)
    role: Mapped[str] = mapped_column(Text)
    permissions: Mapped[list[str]] = mapped_column(ARRAY(Text))
    is_active: Mapped[bool] = mapped_column(Boolean)
    created_at: Mapped[datetime] = mapped_column(TS, server_default=func.now())


class FeatureFlag(Base):
    __tablename__ = "feature_flags"
    key: Mapped[str] = mapped_column(Text, primary_key=True)
    value: Mapped[Any] = mapped_column(JSONB)
    description: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(TS, server_default=func.now())


class Setting(Base):
    __tablename__ = "settings"
    key: Mapped[str] = mapped_column(Text, primary_key=True)
    value: Mapped[Any] = mapped_column(JSONB)
    updated_at: Mapped[datetime] = mapped_column(TS, server_default=func.now())


class EdgeNode(Base):
    __tablename__ = "edge_nodes"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    host: Mapped[str] = mapped_column(Text, unique=True)
    is_enabled: Mapped[bool] = mapped_column(Boolean, server_default="true")
    healthy: Mapped[bool] = mapped_column(Boolean, server_default="true")
    last_check_at: Mapped[datetime | None] = mapped_column(TS)
    last_rtt_ms: Mapped[int | None] = mapped_column(Integer)
    weight: Mapped[int] = mapped_column(Integer, server_default="100")


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    tg_id: Mapped[int] = mapped_column(BigInteger, unique=True)
    username: Mapped[str | None] = mapped_column(CITEXT)
    first_name: Mapped[str] = mapped_column(Text)
    last_name: Mapped[str | None] = mapped_column(Text)
    lang: Mapped[str] = mapped_column(Text, server_default="fa")
    tg_is_premium: Mapped[bool] = mapped_column(Boolean, server_default="false")
    plan_code: Mapped[str] = mapped_column(Text, ForeignKey("plans.code"), server_default="free")
    premium_until: Mapped[datetime | None] = mapped_column(TS)
    trial_used: Mapped[bool] = mapped_column(Boolean, server_default="false")
    referred_by: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("users.id"))
    referral_code: Mapped[str] = mapped_column(Text, unique=True)
    bot_blocked: Mapped[bool] = mapped_column(Boolean, server_default="false")
    is_banned: Mapped[bool] = mapped_column(Boolean, server_default="false")
    ban_reason: Mapped[str | None] = mapped_column(Text)
    public_profile: Mapped[bool] = mapped_column(Boolean, server_default="true")
    notification_prefs: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default="{}")
    tz_offset_minutes: Mapped[int] = mapped_column(Integer, server_default="210")
    followers_count: Mapped[int] = mapped_column(Integer, server_default="0")
    following_count: Mapped[int] = mapped_column(Integer, server_default="0")
    created_at: Mapped[datetime] = mapped_column(TS, server_default=func.now())
    last_seen_at: Mapped[datetime] = mapped_column(TS, server_default=func.now())


class RefreshToken(Base):
    __tablename__ = "refresh_tokens"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="CASCADE"))
    family_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    token_hash: Mapped[bytes] = mapped_column(LargeBinary, unique=True)
    device: Mapped[str | None] = mapped_column(Text)
    used_at: Mapped[datetime | None] = mapped_column(TS)
    revoked_at: Mapped[datetime | None] = mapped_column(TS)
    expires_at: Mapped[datetime] = mapped_column(TS)
    created_at: Mapped[datetime] = mapped_column(TS, server_default=func.now())


class ChannelCategory(Base):
    __tablename__ = "channel_categories"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    slug: Mapped[str] = mapped_column(Text, unique=True)
    name_fa: Mapped[str] = mapped_column(Text)
    name_en: Mapped[str] = mapped_column(Text)
    position: Mapped[int] = mapped_column(Integer)


class IndexerAccount(Base):
    __tablename__ = "indexer_accounts"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    session_key: Mapped[str] = mapped_column(Text, unique=True)
    phone_hint: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, server_default="healthy")
    cooling_until: Mapped[datetime | None] = mapped_column(TS)
    assigned_channels: Mapped[int] = mapped_column(Integer, server_default="0")
    floodwait_24h_s: Mapped[int] = mapped_column(Integer, server_default="0")
    last_error: Mapped[str | None] = mapped_column(Text)
    last_ok_at: Mapped[datetime | None] = mapped_column(TS)
    last_seen_at: Mapped[datetime | None] = mapped_column(TS)


class Channel(Base):
    __tablename__ = "channels"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    tg_channel_id: Mapped[int | None] = mapped_column(BigInteger, unique=True)
    username: Mapped[str | None] = mapped_column(CITEXT, unique=True)
    title: Mapped[str | None] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    category_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("channel_categories.id"))
    source: Mapped[str] = mapped_column(Text, server_default="mtproto")
    source_type: Mapped[str] = mapped_column(Text, server_default="web_preview")
    preview_available: Mapped[bool | None] = mapped_column(Boolean)
    oldest_crawled_msg_id: Mapped[int | None] = mapped_column(BigInteger)
    newest_crawled_msg_id: Mapped[int | None] = mapped_column(BigInteger)
    last_crawl_at: Mapped[datetime | None] = mapped_column(TS)
    next_crawl_at: Mapped[datetime] = mapped_column(TS, server_default=func.now())
    crawl_interval_sec: Mapped[int] = mapped_column(Integer, server_default="3600")
    crawl_status: Mapped[str] = mapped_column(Text, server_default="idle")
    crawl_error: Mapped[str | None] = mapped_column(Text)
    extraction_rate: Mapped[float | None] = mapped_column(Float)
    is_public: Mapped[bool] = mapped_column(Boolean, server_default="true")
    is_featured: Mapped[bool] = mapped_column(Boolean, server_default="false")
    status: Mapped[str] = mapped_column(Text, server_default="pending")
    status_reason: Mapped[str | None] = mapped_column(Text)
    indexer_account_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("indexer_accounts.id")
    )
    lease_owner: Mapped[str | None] = mapped_column(Text)
    lease_until: Mapped[datetime | None] = mapped_column(TS)
    backfill_cursor_msg_id: Mapped[int | None] = mapped_column(BigInteger)
    backfill_total_estimate: Mapped[int | None] = mapped_column(Integer)
    backfill_done_count: Mapped[int] = mapped_column(Integer, server_default="0")
    progress_pct: Mapped[int] = mapped_column(SmallInteger, server_default="0")
    last_indexed_msg_id: Mapped[int] = mapped_column(BigInteger, server_default="0")
    last_indexed_at: Mapped[datetime | None] = mapped_column(TS)
    next_index_at: Mapped[datetime] = mapped_column(TS, server_default=func.now())
    fail_count: Mapped[int] = mapped_column(Integer, server_default="0")
    tracks_count: Mapped[int] = mapped_column(Integer, server_default="0")
    subscribers_count: Mapped[int] = mapped_column(Integer, server_default="0")
    tracks_per_day_30d: Mapped[float] = mapped_column(Float, server_default="0")
    added_by_user_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(TS, server_default=func.now())


class UserChannel(Base):
    __tablename__ = "user_channels"
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"), primary_key=True)
    channel_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("channels.id"), primary_key=True)
    added_at: Mapped[datetime] = mapped_column(TS, server_default=func.now())


class Artist(Base):
    __tablename__ = "artists"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    name: Mapped[str] = mapped_column(Text)
    normalized_name: Mapped[str] = mapped_column(Text, unique=True)
    latin_name: Mapped[str | None] = mapped_column(Text)
    aliases: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default="{}")
    merged_into_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("artists.id"))
    tracks_count: Mapped[int] = mapped_column(Integer, server_default="0")
    hidden: Mapped[bool] = mapped_column(Boolean, server_default="false")
    created_at: Mapped[datetime] = mapped_column(TS, server_default=func.now())


class Track(Base):
    __tablename__ = "tracks"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    file_unique_id: Mapped[str | None] = mapped_column(Text)
    source_key: Mapped[str | None] = mapped_column(Text)
    canonical_track_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("tracks.id"))
    # Bot API only: issued per bot, and only for files our own bot has seen. The
    # resolver never produces one (ADR-003 §2-2).
    bot_file_id: Mapped[str | None] = mapped_column(Text)
    bot_id: Mapped[int | None] = mapped_column(BigInteger)
    bot_file_id_updated_at: Mapped[datetime | None] = mapped_column(TS)
    playable: Mapped[bool] = mapped_column(Boolean, server_default="true")
    title: Mapped[str] = mapped_column(Text, server_default="")
    performer: Mapped[str | None] = mapped_column(Text)
    album: Mapped[str | None] = mapped_column(Text)
    file_name: Mapped[str | None] = mapped_column(Text)
    duration: Mapped[int] = mapped_column(Integer)
    file_size: Mapped[int] = mapped_column(BigInteger)
    mime_type: Mapped[str | None] = mapped_column(Text)
    has_thumb: Mapped[bool] = mapped_column(Boolean, server_default="false")
    year: Mapped[int | None] = mapped_column(SmallInteger)
    language: Mapped[str | None] = mapped_column(Text)
    genre: Mapped[str | None] = mapped_column(Text)
    normalized_title: Mapped[str] = mapped_column(Text)
    normalized_album: Mapped[str | None] = mapped_column(Text)
    normalized_artist: Mapped[str] = mapped_column(Text, server_default="")
    metadata_confidence: Mapped[int] = mapped_column(SmallInteger, server_default="100")
    channels_count: Mapped[int] = mapped_column(Integer, server_default="0")
    likes_count: Mapped[int] = mapped_column(Integer, server_default="0")
    plays_total: Mapped[int] = mapped_column(BigInteger, server_default="0")
    plays_7d: Mapped[int] = mapped_column(Integer, server_default="0")
    hidden: Mapped[bool] = mapped_column(Boolean, server_default="false")
    hidden_reason: Mapped[str | None] = mapped_column(Text)
    metadata_probed_at: Mapped[datetime | None] = mapped_column(TS)
    # "#rrggbb,#rrggbb,#rrggbb" — reported by the first client that rendered it.
    cover_palette: Mapped[str | None] = mapped_column(Text)
    cdn_url: Mapped[str | None] = mapped_column(Text)
    cdn_url_fetched_at: Mapped[datetime | None] = mapped_column(TS)
    resolve_status: Mapped[str] = mapped_column(Text, server_default="resolved")
    resolve_attempts: Mapped[int] = mapped_column(Integer, server_default="0")
    resolve_requests: Mapped[int] = mapped_column(Integer, server_default="0")
    resolved_at: Mapped[datetime | None] = mapped_column(TS)
    created_at: Mapped[datetime] = mapped_column(TS, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(TS, server_default=func.now())


class TrackArtist(Base):
    __tablename__ = "track_artists"
    track_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("tracks.id"), primary_key=True)
    artist_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("artists.id"), primary_key=True)
    role: Mapped[str] = mapped_column(Text, primary_key=True, server_default="primary")
    position: Mapped[int] = mapped_column(SmallInteger, server_default="0")


class ChannelTrack(Base):
    __tablename__ = "channel_tracks"
    channel_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("channels.id"), primary_key=True)
    message_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    track_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("tracks.id"))
    posted_at: Mapped[datetime] = mapped_column(TS)
    views: Mapped[int | None] = mapped_column(Integer)


class SearchHistory(Base):
    __tablename__ = "search_history"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"))
    query: Mapped[str] = mapped_column(Text)
    results: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(TS, server_default=func.now())


class Blacklist(Base):
    __tablename__ = "blacklist"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    entity_type: Mapped[str] = mapped_column(Text)
    value: Mapped[str] = mapped_column(Text)
    reason: Mapped[str] = mapped_column(Text)
    report_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("reports.id"))
    created_by: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("admin_users.id"))
    created_at: Mapped[datetime] = mapped_column(TS, server_default=func.now())


class Playlist(Base):
    __tablename__ = "playlists"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    cover_key: Mapped[str | None] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(Text, server_default="manual")
    is_public: Mapped[bool] = mapped_column(Boolean, server_default="false")
    is_collaborative: Mapped[bool] = mapped_column(Boolean, server_default="false")
    share_slug: Mapped[str | None] = mapped_column(Text, unique=True)
    tracks_count: Mapped[int] = mapped_column(Integer, server_default="0")
    duration_total: Mapped[int] = mapped_column(Integer, server_default="0")
    generated_for: Mapped[date | None] = mapped_column(Date)
    created_at: Mapped[datetime] = mapped_column(TS, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(TS, server_default=func.now())


class PlaylistTrack(Base):
    __tablename__ = "playlist_tracks"
    playlist_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("playlists.id", ondelete="CASCADE"), primary_key=True
    )
    track_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("tracks.id"), primary_key=True)
    position: Mapped[Decimal] = mapped_column(Numeric)
    added_by: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("users.id"))
    added_at: Mapped[datetime] = mapped_column(TS, server_default=func.now())


class PlaylistCollaborator(Base):
    __tablename__ = "playlist_collaborators"
    playlist_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("playlists.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"), primary_key=True)
    role: Mapped[str] = mapped_column(Text, server_default="editor")


class Like(Base):
    __tablename__ = "likes"
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"), primary_key=True)
    track_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("tracks.id"), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(TS, server_default=func.now())


class PlaybackState(Base):
    __tablename__ = "playback_state"
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"), primary_key=True)
    track_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("tracks.id"))
    position_s: Mapped[int] = mapped_column(Integer, server_default="0")
    queue: Mapped[list[int]] = mapped_column(ARRAY(BigInteger), server_default="{}")
    queue_index: Mapped[int] = mapped_column(Integer, server_default="0")
    shuffle: Mapped[bool] = mapped_column(Boolean, server_default="false")
    repeat_mode: Mapped[str] = mapped_column(Text, server_default="off")
    speed: Mapped[float] = mapped_column(Float, server_default="1.0")
    updated_at: Mapped[datetime] = mapped_column(TS, server_default=func.now())


class PaymentProviderRow(Base):
    __tablename__ = "payment_providers"
    code: Mapped[str] = mapped_column(Text, primary_key=True)
    is_enabled: Mapped[bool] = mapped_column(Boolean, server_default="false")
    currency: Mapped[str] = mapped_column(Text)
    config: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default="{}")
    position: Mapped[int] = mapped_column(Integer, server_default="0")


class Payment(Base):
    __tablename__ = "payments"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    public_id: Mapped[uuid.UUID] = mapped_column(Uuid, default=uuid.uuid4)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"))
    provider: Mapped[str] = mapped_column(Text, ForeignKey("payment_providers.code"))
    plan_code: Mapped[str] = mapped_column(Text, ForeignKey("plans.code"))
    amount: Mapped[int] = mapped_column(BigInteger)
    currency: Mapped[str] = mapped_column(Text)
    discount_code_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("discount_codes.id")
    )
    discount_amount: Mapped[int] = mapped_column(BigInteger, server_default="0")
    status: Mapped[str] = mapped_column(Text, server_default="created")
    provider_ref: Mapped[str | None] = mapped_column(Text)
    receipt_file_id: Mapped[str | None] = mapped_column(Text)
    reviewed_by: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("admin_users.id"))
    reviewed_at: Mapped[datetime | None] = mapped_column(TS)
    review_due_at: Mapped[datetime | None] = mapped_column(TS)
    note: Mapped[str | None] = mapped_column(Text)
    raw_callback: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    paid_at: Mapped[datetime | None] = mapped_column(TS)
    created_at: Mapped[datetime] = mapped_column(TS, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(TS, server_default=func.now())


class Subscription(Base):
    __tablename__ = "subscriptions"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="CASCADE"))
    plan_code: Mapped[str] = mapped_column(Text, ForeignKey("plans.code"))
    status: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(Text)
    payment_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("payments.id"))
    auto_renew: Mapped[bool] = mapped_column(Boolean, server_default="false")
    provider_sub_ref: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(TS)
    expires_at: Mapped[datetime] = mapped_column(TS)
    grace_until: Mapped[datetime | None] = mapped_column(TS)
    canceled_at: Mapped[datetime | None] = mapped_column(TS)
    created_at: Mapped[datetime] = mapped_column(TS, server_default=func.now())


class DiscountCode(Base):
    __tablename__ = "discount_codes"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    code: Mapped[str] = mapped_column(CITEXT, unique=True)
    kind: Mapped[str] = mapped_column(Text)
    value: Mapped[int] = mapped_column(BigInteger)
    currency: Mapped[str | None] = mapped_column(Text)
    plan_codes: Mapped[list[str] | None] = mapped_column(ARRAY(Text))
    max_uses: Mapped[int | None] = mapped_column(Integer)
    max_uses_per_user: Mapped[int] = mapped_column(Integer, server_default="1")
    uses: Mapped[int] = mapped_column(Integer, server_default="0")
    starts_at: Mapped[datetime] = mapped_column(TS, server_default=func.now())
    expires_at: Mapped[datetime | None] = mapped_column(TS)
    is_active: Mapped[bool] = mapped_column(Boolean, server_default="true")
    created_at: Mapped[datetime] = mapped_column(TS, server_default=func.now())


class DiscountRedemption(Base):
    __tablename__ = "discount_redemptions"
    discount_code_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("discount_codes.id"), primary_key=True
    )
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"), primary_key=True)
    payment_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("payments.id"), unique=True)
    created_at: Mapped[datetime] = mapped_column(TS, server_default=func.now())


class Referral(Base):
    __tablename__ = "referrals"
    referrer_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    referee_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    status: Mapped[str] = mapped_column(Text, server_default="pending")
    reward_days: Mapped[int | None] = mapped_column(Integer)
    qualified_at: Mapped[datetime | None] = mapped_column(TS)
    created_at: Mapped[datetime] = mapped_column(TS, server_default=func.now())


class SubscriptionNotice(Base):
    __tablename__ = "subscription_notices"
    subscription_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("subscriptions.id", ondelete="CASCADE"), primary_key=True
    )
    kind: Mapped[str] = mapped_column(Text, primary_key=True)
    sent_at: Mapped[datetime] = mapped_column(TS, server_default=func.now())


class Broadcast(Base):
    __tablename__ = "broadcasts"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    admin_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("admin_users.id"))
    target_filter: Mapped[dict[str, Any]] = mapped_column(JSONB)
    variants: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(Text, server_default="draft")
    scheduled_at: Mapped[datetime | None] = mapped_column(TS)
    recurrence: Mapped[str | None] = mapped_column(Text)
    total: Mapped[int] = mapped_column(Integer, server_default="0")
    sent: Mapped[int] = mapped_column(Integer, server_default="0")
    failed: Mapped[int] = mapped_column(Integer, server_default="0")
    blocked: Mapped[int] = mapped_column(Integer, server_default="0")
    started_at: Mapped[datetime | None] = mapped_column(TS)
    finished_at: Mapped[datetime | None] = mapped_column(TS)
    created_at: Mapped[datetime] = mapped_column(TS, server_default=func.now())


class Notification(Base):
    __tablename__ = "notifications"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(Text)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    dedup_key: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, server_default="queued")
    send_after: Mapped[datetime] = mapped_column(TS, server_default=func.now())
    sent_at: Mapped[datetime | None] = mapped_column(TS)
    created_at: Mapped[datetime] = mapped_column(TS, server_default=func.now())


class Report(Base):
    __tablename__ = "reports"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    reporter_user_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("users.id"))
    reporter_contact: Mapped[str | None] = mapped_column(Text)
    entity_type: Mapped[str] = mapped_column(Text)
    entity_id: Mapped[int] = mapped_column(BigInteger)
    reason: Mapped[str] = mapped_column(Text)
    details: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, server_default="open")
    handled_by: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("admin_users.id"))
    resolution: Mapped[str | None] = mapped_column(Text)
    due_at: Mapped[datetime] = mapped_column(TS)
    created_at: Mapped[datetime] = mapped_column(TS, server_default=func.now())
    resolved_at: Mapped[datetime | None] = mapped_column(TS)


class Follow(Base):
    __tablename__ = "follows"
    follower_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    followee_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    created_at: Mapped[datetime] = mapped_column(TS, server_default=func.now())


class WrappedReport(Base):
    __tablename__ = "wrapped_reports"
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    year: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(TS, server_default=func.now())


class ChannelCandidate(Base):
    __tablename__ = "channel_candidates"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    username: Mapped[str] = mapped_column(CITEXT, unique=True)
    source: Mapped[str] = mapped_column(Text)
    discovered_from_channel_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("channels.id")
    )
    requested_by_user_ids: Mapped[list[int]] = mapped_column(ARRAY(BigInteger), server_default="{}")
    title: Mapped[str | None] = mapped_column(Text)
    tracks_estimate: Mapped[int | None] = mapped_column(Integer)
    audio_ratio: Mapped[float | None] = mapped_column(Float)
    posts_per_day: Mapped[float | None] = mapped_column(Float)
    score: Mapped[float] = mapped_column(Float, server_default="0")
    status: Mapped[str] = mapped_column(Text, server_default="pending")
    reviewed_by: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("admin_users.id"))
    reviewed_at: Mapped[datetime | None] = mapped_column(TS)
    reject_reason: Mapped[str | None] = mapped_column(Text)
    subscribers: Mapped[int | None] = mapped_column(Integer)
    probed_at: Mapped[datetime | None] = mapped_column(TS)
    probe_attempts: Mapped[int] = mapped_column(Integer, server_default="0")
    mention_count: Mapped[int] = mapped_column(Integer, server_default="1")
    approved_channel_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("channels.id"))
    created_at: Mapped[datetime] = mapped_column(TS, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(TS, server_default=func.now())
