"""Core ↔ edge API. Reachable only over the WireGuard interface (nginx denies it
publicly) and additionally protected by a shared bearer token.

Everything here belongs to the crawler (ADR-002): claim a channel, post its pages,
measure a candidate. The only MTProto endpoints left are the two by which the edge's
single resolver account announces itself.
"""

from __future__ import annotations

import hmac
from typing import Annotated

from fastapi import APIRouter, Depends, Header

from app.api.deps import SessionDep, SettingsDep
from app.errors import Unauthorized
from app.services import crawling, discovery, resolving
from app.services.ingest import ingest_items
from tmusic_common.indexer_contract import (
    AccountReportIn,
    CandidateClaimOut,
    CandidateStatsIn,
    CandidateTaskOut,
    CrawlBatchIn,
    CrawlBatchOut,
    CrawlClaimIn,
    CrawlClaimOut,
    CrawlFailureIn,
    CrawlTaskOut,
    RegisterAccountsIn,
    RegisterAccountsOut,
    ReleaseIn,
    SearchFoundIn,
    SearchFoundOut,
    SearchTermsOut,
)


async def internal_auth(
    settings: SettingsDep, authorization: Annotated[str | None, Header()] = None
) -> None:
    expected = f"Bearer {settings.internal_api_token.get_secret_value()}"
    if not authorization or not hmac.compare_digest(authorization, expected):
        raise Unauthorized("internal token required")


router = APIRouter(
    prefix="/internal/indexer",
    tags=["internal"],
    dependencies=[Depends(internal_auth)],
    include_in_schema=False,
)


@router.post("/accounts", response_model=RegisterAccountsOut)
async def register_accounts(body: RegisterAccountsIn, session: SessionDep) -> RegisterAccountsOut:
    return RegisterAccountsOut(ids=await resolving.register_accounts(session, body))


@router.post("/accounts/{account_id}/report", status_code=204)
async def report_account(account_id: int, body: AccountReportIn, session: SessionDep) -> None:
    await resolving.report_account(session, account_id, body)


# ── web-preview crawler (ADR-002) ────────────────────────────────────────────
#
# The same lease contract as the MTProto path, minus the account pool: claim a
# channel, post a page at a time, then say how it ended.


@router.post("/crawl/claim", response_model=CrawlClaimOut)
async def crawl_claim(
    body: CrawlClaimIn, session: SessionDep, settings: SettingsDep
) -> CrawlClaimOut:
    """Hands out channels to crawl. The operator's limits win over the worker's ask."""
    tasks = await crawling.claim(
        session,
        body.worker_id,
        min(body.limit, settings.indexer_claim_limit),
        settings.indexer_lease_s,
    )
    return CrawlClaimOut(
        tasks=[
            CrawlTaskOut(
                channel_id=task.channel_id,
                username=task.username,
                lease_token=task.lease_token,
                mode=task.mode,
                source=task.source,
                before=task.before,
                stop_at=task.stop_at,
                needs_meta=task.needs_meta,
            )
            for task in tasks
        ]
    )


@router.post("/crawl/batch", response_model=CrawlBatchOut)
async def crawl_batch(body: CrawlBatchIn, session: SessionDep) -> CrawlBatchOut:
    """One page: ingest the items, then move the cursors under the same lease."""
    channel = await crawling.channel_for_batch(session, body.channel_id, body.lease_token)
    if channel is None:
        return CrawlBatchOut(lease_valid=False)

    if body.meta is not None:
        await crawling.apply_meta(session, channel, body.meta)
    stats = await ingest_items(session, channel, body.items)
    await crawling.record_page_health(
        session,
        channel,
        messages=body.page_messages,
        items=len(body.items),
        rate=body.extraction_rate,
    )
    if body.mentions:
        await discovery.record_mentions(session, body.mentions, discovered_from=body.channel_id)

    alive = await crawling.save_progress(
        session,
        body.channel_id,
        body.lease_token,
        oldest=body.oldest_msg_id,
        newest=body.newest_msg_id,
        finished=body.finished,
        extraction_rate=body.extraction_rate,
    )
    return CrawlBatchOut(
        lease_valid=alive,
        inserted=stats.inserted,
        updated=stats.updated,
        duplicates=stats.duplicates,
    )


@router.post("/crawl/channels/{channel_id}/release")
async def crawl_release(channel_id: int, body: ReleaseIn, session: SessionDep) -> dict[str, bool]:
    ok = await crawling.release(session, channel_id, body.lease_token, body.retry_after_s or 60)
    return {"released": ok}


@router.post("/crawl/channels/{channel_id}/failure")
async def crawl_failure(
    channel_id: int, body: CrawlFailureIn, session: SessionDep
) -> dict[str, bool]:
    ok = await crawling.report_failure(
        session,
        channel_id,
        body.lease_token,
        reason=body.reason,
        detail=body.detail,
        preview_disabled=body.preview_disabled,
    )
    return {"recorded": ok}


@router.post("/crawl/candidates/claim", response_model=CandidateClaimOut)
async def candidate_claim(body: CrawlClaimIn, session: SessionDep) -> CandidateClaimOut:
    """Candidates to measure. Scoring needs one page each; no lease, no account."""
    if not await crawling.enabled(session):
        return CandidateClaimOut()
    rows = await discovery.claim_probes(session, body.limit)
    return CandidateClaimOut(
        tasks=[CandidateTaskOut(candidate_id=r.id, username=r.username) for r in rows]
    )


@router.post("/crawl/candidates/{candidate_id}/stats")
async def candidate_stats(
    candidate_id: int, body: CandidateStatsIn, session: SessionDep
) -> dict[str, float]:
    candidate = await discovery.apply_probe(
        session,
        candidate_id,
        title=body.title,
        subscribers=body.subscribers,
        messages=body.messages,
        audio=body.audio,
        newest_msg_id=body.newest_msg_id,
        posts_per_day=body.posts_per_day,
        unavailable=body.unavailable,
    )
    return {"score": candidate.score if candidate else 0.0}


# ── discovery by search (ADR-002 §3) ─────────────────────────────────────────


@router.get("/discover/terms", response_model=SearchTermsOut)
async def discover_terms(session: SessionDep) -> SearchTermsOut:
    """What to search Telegram for. Empty list = the feature is off.

    The edge asks rather than being configured, so turning this on or aiming it
    somewhere else is one flag and one settings row, not a redeploy of every edge.
    """
    return SearchTermsOut(terms=await discovery.search_terms(session))


@router.post("/discover/search", response_model=SearchFoundOut)
async def discover_search(body: SearchFoundIn, session: SessionDep) -> SearchFoundOut:
    added = await discovery.record_search(session, body.usernames)
    return SearchFoundOut(added=added)
