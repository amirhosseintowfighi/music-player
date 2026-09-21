"""Business metrics.

HTTP metrics live in the middleware; these are the numbers an operator is paged
about: money, indexing health and how much the recommender actually gets used.

Counters are incremented where the event happens. Gauges are refreshed by a worker
job (``export_business_metrics``) because they are aggregate questions the API path
should never pay for.
"""

from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram

PAYMENTS = Counter("payments_total", "Payment settlements", ["provider", "status"])
REVENUE = Counter(
    "payment_amount_total",
    "Settled payment amount (smallest currency unit)",
    ["provider", "currency"],
)
SUBSCRIPTIONS = Counter(
    "subscription_events_total", "Subscription lifecycle events", ["event", "source"]
)
PLAYS = Counter("plays_total", "Tracks played", ["source"])
SEARCHES = Counter("searches_total", "Search queries", ["scope", "degraded"])
INDEX_ITEMS = Counter("indexed_items_total", "Audio items ingested", ["result"])
# FloodWait is counted by the edge (tmusic_indexer.pool), which is where it happens;
# defining it here too would collide when both packages load in one process.
BROADCAST_MESSAGES = Counter("broadcast_messages_total", "Broadcast sends", ["outcome"])

ACTIVE_SUBSCRIPTIONS = Gauge("active_subscriptions", "Live subscriptions", ["plan"])
USERS_TOTAL = Gauge("users_total", "Registered users")
DAU = Gauge("users_active_24h", "Users seen in the last 24 hours")
TRACKS_TOTAL = Gauge("tracks_total", "Canonical, visible tracks")
CHANNELS_INDEXING = Gauge("channels_indexing", "Channels currently indexing")
PAYMENTS_AWAITING_REVIEW = Gauge("payments_awaiting_review", "Card transfers waiting for an admin")
OPEN_REPORTS = Gauge("reports_open", "Unhandled copyright/abuse reports")
QUEUE_DEPTH = Gauge("worker_queue_depth", "Jobs waiting in the arq queue")

# ── playback health (ADR-003 phase 10) ────────────────────────────────────────
#
# Four numbers answer "is playback good right now?". The fourth is the one that
# breaks first at scale: with no CDN fallback (ADR-002 §8) every first play of a
# track waits for MTProto, so the share of plays that had to resolve inline says
# directly whether pre-warm is keeping up.

PLAYBACK_TICKETS = Counter("playback_tickets_total", "Stream tickets issued", ["kind"])
PLAYBACK_INLINE_RESOLVE = Counter(
    "playback_inline_resolve_total", "Plays that had to resolve a file first", ["result"]
)
# Reported by the client, because only the client can see them.
PLAYBACK_START = Histogram(
    "playback_start_seconds",
    "From the user's tap to the first sound",
    buckets=(0.25, 0.5, 1.0, 2.0, 4.0, 8.0, 15.0),
)
PLAYBACK_UNDERRUNS = Counter("playback_underruns_total", "Playback stalls to rebuffer")
PLAYBACK_ERRORS = Counter("playback_errors_total", "Playback failures seen by clients", ["kind"])
