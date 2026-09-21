-- 0001_initial — full schema from docs/DB.md, in dependency order.
-- Tables not used before later phases are created now so the data model is stable.

CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS citext;
CREATE EXTENSION IF NOT EXISTS btree_gin;

CREATE OR REPLACE FUNCTION touch_updated_at() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    NEW.updated_at := now();
    RETURN NEW;
END $$;

-- Creates monthly range partitions [start of month, next month) from this month
-- up to `months_ahead` months ahead. Idempotent; run by the daily maintenance job.
-- SECURITY DEFINER: the job connects as the DML-only app role, which cannot create tables.
CREATE OR REPLACE FUNCTION ensure_month_partitions(parent text, months_ahead int)
RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
DECLARE
    m date := date_trunc('month', now())::date;
    i int;
    part text;
BEGIN
    IF parent NOT IN ('play_history', 'audit_log') THEN
        RAISE EXCEPTION 'unsupported partitioned table %', parent;
    END IF;
    FOR i IN 0..months_ahead LOOP
        part := format('%s_%s', parent, to_char(m + make_interval(months => i), 'YYYYMM'));
        EXECUTE format(
            'CREATE TABLE IF NOT EXISTS %I PARTITION OF %I FOR VALUES FROM (%L) TO (%L)',
            part, parent, m + make_interval(months => i), m + make_interval(months => i + 1)
        );
        -- Partitions can be written directly, so the append-only rule must cover them too.
        IF parent = 'audit_log' AND EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'tmusic_app') THEN
            EXECUTE format('REVOKE UPDATE, DELETE, TRUNCATE ON %I FROM tmusic_app', part);
        END IF;
    END LOOP;
END $$;

-- ─── reference / configuration ──────────────────────────────────────────────

CREATE TABLE plans (
    code         text PRIMARY KEY,
    name_fa      text    NOT NULL,
    name_en      text    NOT NULL,
    period_days  int     CHECK (period_days IS NULL OR period_days > 0),
    limits       jsonb   NOT NULL,
    features     text[]  NOT NULL DEFAULT '{}',
    prices       jsonb   NOT NULL DEFAULT '{}',
    is_active    boolean NOT NULL DEFAULT true,
    position     int     NOT NULL DEFAULT 0,
    updated_at   timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE admin_users (
    id           bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    tg_id        bigint      NOT NULL UNIQUE,
    role         text        NOT NULL CHECK (role IN ('owner','admin','moderator','support')),
    permissions  text[]      NOT NULL DEFAULT '{}',
    is_active    boolean     NOT NULL DEFAULT true,
    created_at   timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE feature_flags (
    key          text PRIMARY KEY,
    value        jsonb       NOT NULL,
    description  text,
    updated_by   bigint      REFERENCES admin_users(id),
    updated_at   timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE settings (
    key          text PRIMARY KEY,
    value        jsonb       NOT NULL,
    updated_by   bigint      REFERENCES admin_users(id),
    updated_at   timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE edge_nodes (
    id            int GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    host          text        NOT NULL UNIQUE,
    is_enabled    boolean     NOT NULL DEFAULT true,
    healthy       boolean     NOT NULL DEFAULT true,
    last_check_at timestamptz,
    last_rtt_ms   int,
    weight        int         NOT NULL DEFAULT 100 CHECK (weight >= 0)
);

-- ─── users & auth ───────────────────────────────────────────────────────────

CREATE TABLE users (
    id                    bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    tg_id                 bigint      NOT NULL UNIQUE,
    username              citext,
    first_name            text        NOT NULL,
    last_name             text,
    lang                  text        NOT NULL DEFAULT 'fa' CHECK (lang IN ('fa','en')),
    tg_is_premium         boolean     NOT NULL DEFAULT false,
    plan_code             text        NOT NULL DEFAULT 'free' REFERENCES plans(code),
    premium_until         timestamptz,
    trial_used            boolean     NOT NULL DEFAULT false,
    referred_by           bigint      REFERENCES users(id) ON DELETE SET NULL,
    referral_code         text        NOT NULL UNIQUE,
    country               text,
    tos_version_accepted  text,
    bot_blocked           boolean     NOT NULL DEFAULT false,
    is_banned             boolean     NOT NULL DEFAULT false,
    ban_reason            text,
    public_profile        boolean     NOT NULL DEFAULT true,
    created_at            timestamptz NOT NULL DEFAULT now(),
    last_seen_at          timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX users_username_trgm  ON users USING gin (username gin_trgm_ops);
CREATE INDEX users_created_keyset ON users (created_at, id);
CREATE INDEX users_last_seen      ON users (last_seen_at);
CREATE INDEX users_premium_expiry ON users (premium_until) WHERE premium_until IS NOT NULL;

CREATE TABLE refresh_tokens (
    id           bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id      bigint      NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    family_id    uuid        NOT NULL,
    token_hash   bytea       NOT NULL UNIQUE,
    device       text,
    used_at      timestamptz,
    revoked_at   timestamptz,
    expires_at   timestamptz NOT NULL,
    created_at   timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX refresh_tokens_family ON refresh_tokens (family_id);
CREATE INDEX refresh_tokens_expiry ON refresh_tokens (expires_at);

CREATE TABLE follows (
    follower_id  bigint NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    followee_id  bigint NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at   timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (follower_id, followee_id),
    CHECK (follower_id <> followee_id)
);
CREATE INDEX follows_followee ON follows (followee_id);

-- ─── channels & indexing ────────────────────────────────────────────────────

CREATE TABLE channel_categories (
    id        int GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    slug      text NOT NULL UNIQUE,
    name_fa   text NOT NULL,
    name_en   text NOT NULL,
    position  int  NOT NULL DEFAULT 0
);

CREATE TABLE indexer_accounts (
    id               int GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    session_key      text        NOT NULL UNIQUE,
    phone_hint       text,
    status           text        NOT NULL DEFAULT 'healthy'
                     CHECK (status IN ('healthy','cooling','limited','banned','disabled')),
    cooling_until    timestamptz,
    assigned_channels int        NOT NULL DEFAULT 0,
    floodwait_24h_s  int         NOT NULL DEFAULT 0,
    last_error       text,
    last_ok_at       timestamptz,
    last_seen_at     timestamptz,
    created_at       timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE channels (
    id                      bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    tg_channel_id           bigint      UNIQUE,
    username                citext      UNIQUE,
    title                   text,
    description             text,
    avatar_file_id          text,
    category_id             int         REFERENCES channel_categories(id) ON DELETE SET NULL,
    source                  text        NOT NULL DEFAULT 'mtproto' CHECK (source IN ('mtproto','bot_admin')),
    is_public               boolean     NOT NULL DEFAULT true,
    is_featured             boolean     NOT NULL DEFAULT false,
    status                  text        NOT NULL DEFAULT 'pending'
                            CHECK (status IN ('pending','indexing','active','paused','failed','blacklisted')),
    status_reason           text,
    indexer_account_id      int         REFERENCES indexer_accounts(id) ON DELETE SET NULL,
    lease_owner             text,
    lease_until             timestamptz,
    backfill_cursor_msg_id  bigint,
    backfill_total_estimate int,
    backfill_done_count     int         NOT NULL DEFAULT 0,
    progress_pct            smallint    NOT NULL DEFAULT 0 CHECK (progress_pct BETWEEN 0 AND 100),
    last_indexed_msg_id     bigint      NOT NULL DEFAULT 0,
    last_indexed_at         timestamptz,
    next_index_at           timestamptz NOT NULL DEFAULT now(),
    fail_count              int         NOT NULL DEFAULT 0,
    tracks_count            int         NOT NULL DEFAULT 0,
    subscribers_count       int         NOT NULL DEFAULT 0,
    tracks_per_day_30d      real        NOT NULL DEFAULT 0,
    added_by_user_id        bigint      REFERENCES users(id) ON DELETE SET NULL,
    created_at              timestamptz NOT NULL DEFAULT now(),
    CHECK (username IS NOT NULL OR tg_channel_id IS NOT NULL)
);
CREATE INDEX channels_due        ON channels (next_index_at) WHERE status IN ('pending','indexing','active');
CREATE INDEX channels_featured   ON channels (category_id, subscribers_count DESC) WHERE is_featured;
CREATE INDEX channels_title_trgm ON channels USING gin (title gin_trgm_ops);
CREATE INDEX channels_account    ON channels (indexer_account_id);

CREATE TABLE user_channels (
    user_id     bigint NOT NULL REFERENCES users(id)    ON DELETE CASCADE,
    channel_id  bigint NOT NULL REFERENCES channels(id) ON DELETE CASCADE,
    added_at    timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, channel_id)
);
CREATE INDEX user_channels_channel ON user_channels (channel_id);

-- ─── tracks & artists ───────────────────────────────────────────────────────

CREATE TABLE artists (
    id               bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name             text     NOT NULL,
    normalized_name  text     NOT NULL UNIQUE,
    latin_name       text,
    aliases          text[]   NOT NULL DEFAULT '{}',
    merged_into_id   bigint   REFERENCES artists(id),
    avatar_key       text,
    tracks_count     int      NOT NULL DEFAULT 0,
    hidden           boolean  NOT NULL DEFAULT false,
    created_at       timestamptz NOT NULL DEFAULT now(),
    CHECK (merged_into_id IS NULL OR merged_into_id <> id)
);
CREATE INDEX artists_aliases   ON artists USING gin (aliases);
CREATE INDEX artists_name_trgm ON artists USING gin (normalized_name gin_trgm_ops);

CREATE TABLE tracks (
    id                  bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    file_unique_id      text        NOT NULL UNIQUE,
    canonical_track_id  bigint      REFERENCES tracks(id) ON DELETE SET NULL,
    file_id             text,
    bot_id              bigint,
    file_id_updated_at  timestamptz,
    playable            boolean     NOT NULL DEFAULT true,
    title               text        NOT NULL DEFAULT '',
    performer           text,
    album               text,
    file_name           text,
    duration            int         NOT NULL CHECK (duration >= 0),
    file_size           bigint      NOT NULL CHECK (file_size >= 0),
    mime_type           text,
    has_thumb           boolean     NOT NULL DEFAULT false,
    thumb_file_id       text,
    cover_key           text,
    year                smallint    CHECK (year IS NULL OR year BETWEEN 1900 AND 2100),
    language            text        CHECK (language IN ('fa','en','ar','tr','ku','other')),
    genre               text,
    normalized_title    text        NOT NULL,
    normalized_album    text,
    normalized_artist   text        NOT NULL DEFAULT '',
    metadata_confidence smallint    NOT NULL DEFAULT 100 CHECK (metadata_confidence BETWEEN 0 AND 100),
    channels_count      int         NOT NULL DEFAULT 0,
    likes_count         int         NOT NULL DEFAULT 0,
    plays_total         bigint      NOT NULL DEFAULT 0,
    plays_7d            int         NOT NULL DEFAULT 0,
    hidden              boolean     NOT NULL DEFAULT false,
    hidden_reason       text,
    created_at          timestamptz NOT NULL DEFAULT now(),
    updated_at          timestamptz NOT NULL DEFAULT now(),
    CHECK (canonical_track_id IS NULL OR canonical_track_id <> id)
);
CREATE TRIGGER tracks_touch BEFORE UPDATE ON tracks FOR EACH ROW EXECUTE FUNCTION touch_updated_at();
CREATE INDEX tracks_title_trgm   ON tracks USING gin (normalized_title  gin_trgm_ops);
CREATE INDEX tracks_artist_trgm  ON tracks USING gin (normalized_artist gin_trgm_ops);
-- fuzzy dedup candidates: same artist key, duration within ±2s
CREATE INDEX tracks_dedup        ON tracks (normalized_artist, duration);
CREATE INDEX tracks_canonical    ON tracks (canonical_track_id) WHERE canonical_track_id IS NOT NULL;
CREATE INDEX tracks_most_added   ON tracks (channels_count DESC, id) WHERE canonical_track_id IS NULL AND NOT hidden;
CREATE INDEX tracks_review_queue ON tracks (metadata_confidence, id) WHERE metadata_confidence < 60;
CREATE INDEX tracks_updated      ON tracks (updated_at, id);

CREATE TABLE track_artists (
    track_id   bigint   NOT NULL REFERENCES tracks(id)  ON DELETE CASCADE,
    artist_id  bigint   NOT NULL REFERENCES artists(id) ON DELETE CASCADE,
    role       text     NOT NULL DEFAULT 'primary' CHECK (role IN ('primary','feature','composer','lyricist')),
    position   smallint NOT NULL DEFAULT 0,
    PRIMARY KEY (track_id, artist_id, role)
);
CREATE INDEX track_artists_artist ON track_artists (artist_id, track_id);

CREATE TABLE channel_tracks (
    channel_id  bigint      NOT NULL REFERENCES channels(id) ON DELETE CASCADE,
    track_id    bigint      NOT NULL REFERENCES tracks(id)   ON DELETE CASCADE,
    message_id  bigint      NOT NULL,
    posted_at   timestamptz NOT NULL,
    views       int,
    PRIMARY KEY (channel_id, message_id)
);
CREATE INDEX channel_tracks_track   ON channel_tracks (track_id);
CREATE INDEX channel_tracks_listing ON channel_tracks (channel_id, posted_at DESC, message_id DESC);

-- ─── playlists, likes, history ──────────────────────────────────────────────

CREATE TABLE playlists (
    id               bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id          bigint      NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name             text        NOT NULL CHECK (length(name) BETWEEN 1 AND 100),
    description      text        CHECK (length(description) <= 500),
    cover_key        text,
    kind             text        NOT NULL DEFAULT 'manual'
                     CHECK (kind IN ('manual','smart_ai','discover_weekly','daily_mix','radio')),
    is_public        boolean     NOT NULL DEFAULT false,
    is_collaborative boolean     NOT NULL DEFAULT false,
    share_slug       text        UNIQUE,
    tracks_count     int         NOT NULL DEFAULT 0,
    duration_total   int         NOT NULL DEFAULT 0,
    generated_for    date,
    created_at       timestamptz NOT NULL DEFAULT now(),
    updated_at       timestamptz NOT NULL DEFAULT now()
);
CREATE TRIGGER playlists_touch BEFORE UPDATE ON playlists FOR EACH ROW EXECUTE FUNCTION touch_updated_at();
CREATE INDEX playlists_user ON playlists (user_id, updated_at DESC, id);
CREATE UNIQUE INDEX playlists_one_mix_per_period
    ON playlists (user_id, kind, generated_for) WHERE kind IN ('discover_weekly','daily_mix');

CREATE TABLE playlist_tracks (
    playlist_id  bigint      NOT NULL REFERENCES playlists(id) ON DELETE CASCADE,
    track_id     bigint      NOT NULL REFERENCES tracks(id)    ON DELETE CASCADE,
    position     numeric     NOT NULL,
    added_by     bigint      REFERENCES users(id) ON DELETE SET NULL,
    added_at     timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (playlist_id, track_id)
);
CREATE INDEX playlist_tracks_order ON playlist_tracks (playlist_id, position);
CREATE INDEX playlist_tracks_track ON playlist_tracks (track_id);

CREATE TABLE playlist_collaborators (
    playlist_id  bigint NOT NULL REFERENCES playlists(id) ON DELETE CASCADE,
    user_id      bigint NOT NULL REFERENCES users(id)     ON DELETE CASCADE,
    role         text   NOT NULL DEFAULT 'editor' CHECK (role IN ('editor','viewer')),
    PRIMARY KEY (playlist_id, user_id)
);

CREATE TABLE likes (
    user_id     bigint      NOT NULL REFERENCES users(id)  ON DELETE CASCADE,
    track_id    bigint      NOT NULL REFERENCES tracks(id) ON DELETE CASCADE,
    created_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, track_id)
);
CREATE INDEX likes_user_keyset ON likes (user_id, created_at DESC, track_id);

CREATE TABLE play_history (
    id               bigint GENERATED ALWAYS AS IDENTITY,
    user_id          bigint      NOT NULL,
    track_id         bigint      NOT NULL,
    played_at        timestamptz NOT NULL DEFAULT now(),
    duration_played  int         NOT NULL CHECK (duration_played >= 0),
    completed        boolean     NOT NULL,
    skipped          boolean     NOT NULL DEFAULT false,
    source           text        NOT NULL
                     CHECK (source IN ('library','search','playlist','channel','discover','mix','radio','trending','shared')),
    source_id        bigint,
    device           text,
    PRIMARY KEY (played_at, id)
) PARTITION BY RANGE (played_at);
CREATE TABLE play_history_default PARTITION OF play_history DEFAULT;
CREATE INDEX play_history_user  ON play_history (user_id, played_at DESC);
CREATE INDEX play_history_track ON play_history (track_id, played_at);
SELECT ensure_month_partitions('play_history', 2);

CREATE TABLE playback_state (
    user_id      bigint      PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    track_id     bigint      REFERENCES tracks(id) ON DELETE SET NULL,
    position_s   int         NOT NULL DEFAULT 0,
    queue        bigint[]    NOT NULL DEFAULT '{}',
    queue_index  int         NOT NULL DEFAULT 0,
    shuffle      boolean     NOT NULL DEFAULT false,
    repeat_mode  text        NOT NULL DEFAULT 'off' CHECK (repeat_mode IN ('off','one','all')),
    speed        real        NOT NULL DEFAULT 1.0,
    updated_at   timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE search_history (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id     bigint      NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    query       text        NOT NULL CHECK (length(query) <= 200),
    results     int         NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX search_history_user ON search_history (user_id, created_at DESC);

-- ─── recommendations ────────────────────────────────────────────────────────

CREATE TABLE track_similarity (
    track_id    bigint NOT NULL,
    similar_id  bigint NOT NULL,
    score       real   NOT NULL,
    PRIMARY KEY (track_id, similar_id)
);
CREATE INDEX track_similarity_rank ON track_similarity (track_id, score DESC);

CREATE TABLE trending_snapshots (
    time_window text        NOT NULL CHECK (time_window IN ('24h','7d','30d')),
    kind        text        NOT NULL CHECK (kind IN ('plays','most_added','rising')),
    rank        smallint    NOT NULL,
    track_id    bigint      NOT NULL REFERENCES tracks(id) ON DELETE CASCADE,
    score       real        NOT NULL,
    computed_at timestamptz NOT NULL,
    PRIMARY KEY (time_window, kind, rank)
);

-- ─── subscriptions & payments ───────────────────────────────────────────────

CREATE TABLE discount_codes (
    id                bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    code              citext      NOT NULL UNIQUE,
    kind              text        NOT NULL CHECK (kind IN ('percent','fixed')),
    value             bigint      NOT NULL CHECK (value > 0),
    currency          text,
    plan_codes        text[],
    max_uses          int,
    max_uses_per_user int         NOT NULL DEFAULT 1,
    uses              int         NOT NULL DEFAULT 0,
    starts_at         timestamptz NOT NULL DEFAULT now(),
    expires_at        timestamptz,
    is_active         boolean     NOT NULL DEFAULT true,
    created_by        bigint      REFERENCES admin_users(id),
    created_at        timestamptz NOT NULL DEFAULT now(),
    CHECK (kind <> 'percent' OR value <= 100),
    CHECK (kind <> 'fixed'   OR currency IS NOT NULL),
    CHECK (max_uses IS NULL OR uses <= max_uses)
);

CREATE TABLE payment_providers (
    code        text PRIMARY KEY,
    is_enabled  boolean NOT NULL DEFAULT false,
    currency    text    NOT NULL,
    config      jsonb   NOT NULL DEFAULT '{}',
    position    int     NOT NULL DEFAULT 0
);

CREATE TABLE payments (
    id               bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    public_id        uuid        NOT NULL UNIQUE DEFAULT gen_random_uuid(),
    user_id          bigint      NOT NULL REFERENCES users(id),
    provider         text        NOT NULL REFERENCES payment_providers(code),
    plan_code        text        NOT NULL REFERENCES plans(code),
    amount           bigint      NOT NULL CHECK (amount > 0),
    currency         text        NOT NULL,
    discount_code_id bigint      REFERENCES discount_codes(id),
    discount_amount  bigint      NOT NULL DEFAULT 0 CHECK (discount_amount >= 0),
    status           text        NOT NULL DEFAULT 'created'
                     CHECK (status IN ('created','pending','pending_review','paid','failed','rejected','expired','refunded')),
    provider_ref     text,
    receipt_file_id  text,
    reviewed_by      bigint      REFERENCES admin_users(id),
    reviewed_at      timestamptz,
    review_due_at    timestamptz,
    note             text,
    raw_callback     jsonb,
    paid_at          timestamptz,
    created_at       timestamptz NOT NULL DEFAULT now(),
    updated_at       timestamptz NOT NULL DEFAULT now()
);
CREATE TRIGGER payments_touch BEFORE UPDATE ON payments FOR EACH ROW EXECUTE FUNCTION touch_updated_at();
CREATE UNIQUE INDEX payments_idempotency ON payments (provider, provider_ref) WHERE provider_ref IS NOT NULL;
CREATE INDEX payments_review_queue ON payments (created_at) WHERE status = 'pending_review';
CREATE INDEX payments_user         ON payments (user_id, created_at DESC);
CREATE INDEX payments_report       ON payments (paid_at, provider) WHERE status = 'paid';

CREATE TABLE subscriptions (
    id               bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id          bigint      NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    plan_code        text        NOT NULL REFERENCES plans(code),
    status           text        NOT NULL
                     CHECK (status IN ('trialing','active','grace','expired','canceled','refunded')),
    source           text        NOT NULL
                     CHECK (source IN ('payment','trial','referral','gift','admin','promo')),
    payment_id       bigint      REFERENCES payments(id),
    auto_renew       boolean     NOT NULL DEFAULT false,
    provider_sub_ref text,
    started_at       timestamptz NOT NULL,
    expires_at       timestamptz NOT NULL,
    grace_until      timestamptz,
    canceled_at      timestamptz,
    created_at       timestamptz NOT NULL DEFAULT now(),
    CHECK (expires_at > started_at)
);
CREATE INDEX subscriptions_user   ON subscriptions (user_id, expires_at DESC);
CREATE INDEX subscriptions_expiry ON subscriptions (expires_at) WHERE status IN ('active','trialing','grace');
CREATE UNIQUE INDEX subscriptions_one_live ON subscriptions (user_id) WHERE status IN ('active','trialing','grace');

CREATE TABLE discount_redemptions (
    discount_code_id bigint NOT NULL REFERENCES discount_codes(id),
    user_id          bigint NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    payment_id       bigint NOT NULL UNIQUE REFERENCES payments(id),
    created_at       timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX discount_redemptions_user ON discount_redemptions (discount_code_id, user_id);

CREATE TABLE referrals (
    referrer_id   bigint      NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    referee_id    bigint      NOT NULL UNIQUE REFERENCES users(id) ON DELETE CASCADE,
    status        text        NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','qualified','rewarded','rejected')),
    reward_days   int,
    qualified_at  timestamptz,
    created_at    timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (referrer_id, referee_id)
);

CREATE TABLE subscription_notices (
    subscription_id bigint NOT NULL REFERENCES subscriptions(id) ON DELETE CASCADE,
    kind            text   NOT NULL CHECK (kind IN ('d7','d3','d1','expired','grace_start')),
    sent_at         timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (subscription_id, kind)
);

-- ─── admin, audit, messaging ────────────────────────────────────────────────

CREATE TABLE audit_log (
    id          bigint GENERATED ALWAYS AS IDENTITY,
    actor_type  text        NOT NULL CHECK (actor_type IN ('admin','user','system','provider')),
    actor_id    bigint,
    action      text        NOT NULL,
    entity      text        NOT NULL,
    entity_id   text        NOT NULL,
    payload     jsonb       NOT NULL DEFAULT '{}',
    trace_id    text,
    ip          inet,
    created_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (created_at, id)
) PARTITION BY RANGE (created_at);
CREATE TABLE audit_log_default PARTITION OF audit_log DEFAULT;
CREATE INDEX audit_log_entity ON audit_log (entity, entity_id, created_at DESC);
CREATE INDEX audit_log_actor  ON audit_log (actor_type, actor_id, created_at DESC);
SELECT ensure_month_partitions('audit_log', 2);

CREATE TABLE broadcasts (
    id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    admin_id      bigint      NOT NULL REFERENCES admin_users(id),
    target_filter jsonb       NOT NULL,
    variants      jsonb       NOT NULL,
    status        text        NOT NULL DEFAULT 'draft'
                  CHECK (status IN ('draft','scheduled','running','paused','completed','canceled')),
    scheduled_at  timestamptz,
    recurrence    text,
    total         int         NOT NULL DEFAULT 0,
    sent          int         NOT NULL DEFAULT 0,
    failed        int         NOT NULL DEFAULT 0,
    blocked       int         NOT NULL DEFAULT 0,
    started_at    timestamptz,
    finished_at   timestamptz,
    created_at    timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE broadcast_recipients (
    broadcast_id  bigint      NOT NULL REFERENCES broadcasts(id) ON DELETE CASCADE,
    user_id       bigint      NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    variant       text        NOT NULL,
    status        text        NOT NULL DEFAULT 'queued' CHECK (status IN ('queued','sent','failed','blocked')),
    tg_message_id bigint,
    error         text,
    clicked_at    timestamptz,
    sent_at       timestamptz,
    PRIMARY KEY (broadcast_id, user_id)
);
CREATE INDEX broadcast_recipients_queue ON broadcast_recipients (broadcast_id) WHERE status = 'queued';

CREATE TABLE support_messages (
    id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id       bigint      NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    direction     text        NOT NULL CHECK (direction IN ('in','out')),
    admin_id      bigint      REFERENCES admin_users(id),
    text          text,
    media         jsonb,
    tg_message_id bigint,
    read_at       timestamptz,
    created_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX support_messages_thread ON support_messages (user_id, created_at DESC);
CREATE INDEX support_messages_unread ON support_messages (created_at) WHERE direction = 'in' AND read_at IS NULL;

CREATE TABLE notifications (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id     bigint      NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    kind        text        NOT NULL CHECK (kind IN ('new_tracks','digest','sub_expiry','discover_ready','payment','system')),
    payload     jsonb       NOT NULL,
    dedup_key   text,
    status      text        NOT NULL DEFAULT 'queued' CHECK (status IN ('queued','sent','failed','skipped')),
    send_after  timestamptz NOT NULL DEFAULT now(),
    sent_at     timestamptz,
    created_at  timestamptz NOT NULL DEFAULT now(),
    UNIQUE (user_id, dedup_key)
);
CREATE INDEX notifications_due ON notifications (send_after) WHERE status = 'queued';

-- ─── compliance ─────────────────────────────────────────────────────────────

CREATE TABLE reports (
    id               bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    reporter_user_id bigint      REFERENCES users(id) ON DELETE SET NULL,
    reporter_contact text,
    entity_type      text        NOT NULL CHECK (entity_type IN ('track','channel','artist','playlist','user')),
    entity_id        bigint      NOT NULL,
    reason           text        NOT NULL CHECK (reason IN ('copyright','wrong_metadata','offensive','spam','other')),
    details          text        CHECK (length(details) <= 2000),
    status           text        NOT NULL DEFAULT 'open' CHECK (status IN ('open','in_review','actioned','dismissed')),
    handled_by       bigint      REFERENCES admin_users(id),
    resolution       text,
    due_at           timestamptz NOT NULL DEFAULT now() + interval '48 hours',
    created_at       timestamptz NOT NULL DEFAULT now(),
    resolved_at      timestamptz
);
CREATE INDEX reports_queue ON reports (due_at) WHERE status IN ('open','in_review');

CREATE TABLE blacklist (
    id           bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    entity_type  text        NOT NULL CHECK (entity_type IN ('channel','artist','track','channel_username')),
    value        text        NOT NULL,
    reason       text        NOT NULL,
    report_id    bigint      REFERENCES reports(id),
    created_by   bigint      REFERENCES admin_users(id),
    created_at   timestamptz NOT NULL DEFAULT now(),
    UNIQUE (entity_type, value)
);

-- ─── audit_log is append-only for the application role ─────────────────────
-- (roles are created by infra/postgres/init; absent in dev/test databases)

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'tmusic_app') THEN
        REVOKE UPDATE, DELETE, TRUNCATE ON audit_log, audit_log_default FROM tmusic_app;
        PERFORM ensure_month_partitions('audit_log', 2);
    END IF;
END $$;
