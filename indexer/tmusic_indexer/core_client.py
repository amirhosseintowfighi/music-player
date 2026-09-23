"""Client for the core internal API.

Everything here is a crawl-side call. There is no outbox any more: a crawled page is
cheap to fetch again, and replaying a stale page after the lease moved to another
worker would be worse than dropping it (ADR-002 §6).
"""

from __future__ import annotations

import contextlib
from typing import TypeVar

import httpx
from pydantic import BaseModel

from tmusic_common.indexer_contract import (
    AccountIn,
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
from tmusic_common.logging import get_logger

log = get_logger(__name__)
M = TypeVar("M", bound=BaseModel)


class CoreUnavailable(Exception):
    pass


class CoreClient:
    def __init__(self, http: httpx.AsyncClient, base_url: str, token: str) -> None:
        self._http = http
        self._base = base_url.rstrip("/") + "/internal/indexer"
        self._headers = {"Authorization": f"Bearer {token}"}

    async def _post(self, path: str, body: BaseModel, out: type[M] | None = None) -> M | None:
        try:
            resp = await self._http.post(
                f"{self._base}{path}",
                content=body.model_dump_json(),
                headers={**self._headers, "Content-Type": "application/json"},
                timeout=30.0,
            )
        except httpx.HTTPError as exc:
            raise CoreUnavailable(str(exc)) from exc
        if resp.status_code >= 500:
            raise CoreUnavailable(f"core returned {resp.status_code}")
        resp.raise_for_status()
        return out.model_validate_json(resp.content) if out is not None else None

    async def register(self, worker_id: str, accounts: list[AccountIn]) -> dict[str, int]:
        result = await self._post(
            "/accounts",
            RegisterAccountsIn(worker_id=worker_id, accounts=accounts),
            RegisterAccountsOut,
        )
        return result.ids if result else {}

    async def report_account(self, account_id: int, report: AccountReportIn) -> None:
        try:
            await self._post(f"/accounts/{account_id}/report", report)
        except CoreUnavailable:
            log.warning("core.report_dropped", account_id=account_id, status=report.status)

    async def crawl_claim(self, body: CrawlClaimIn) -> list[CrawlTaskOut]:
        result = await self._post("/crawl/claim", body, CrawlClaimOut)
        return result.tasks if result else []

    async def crawl_batch(self, batch: CrawlBatchIn) -> CrawlBatchOut | None:
        """A crawled page. Unlike MTProto batches this is not buffered on outage.

        Re-crawling a page is cheap and safe, while a stale buffered page could be
        replayed after the lease moved to another worker — so a failed post simply
        ends this channel's run and the scheduler picks it up again.
        """
        try:
            return await self._post("/crawl/batch", batch, CrawlBatchOut)
        except CoreUnavailable:
            log.warning("core.crawl_batch_dropped", channel_id=batch.channel_id)
            return None

    async def crawl_release(self, channel_id: int, body: ReleaseIn) -> None:
        with contextlib.suppress(CoreUnavailable):  # the lease expires by itself
            await self._post(f"/crawl/channels/{channel_id}/release", body)

    async def crawl_failure(self, channel_id: int, body: CrawlFailureIn) -> None:
        with contextlib.suppress(CoreUnavailable):
            await self._post(f"/crawl/channels/{channel_id}/failure", body)

    async def candidate_claim(self, body: CrawlClaimIn) -> list[CandidateTaskOut]:
        result = await self._post("/crawl/candidates/claim", body, CandidateClaimOut)
        return result.tasks if result else []

    async def search_terms(self) -> list[str]:
        """What to search Telegram for. Empty when the core has the feature off."""
        try:
            resp = await self._http.get(
                f"{self._base}/discover/terms", headers=self._headers, timeout=30.0
            )
        except httpx.HTTPError as exc:
            raise CoreUnavailable(str(exc)) from exc
        if resp.status_code >= 500:
            raise CoreUnavailable(f"core returned {resp.status_code}")
        resp.raise_for_status()
        return SearchTermsOut.model_validate_json(resp.content).terms

    async def search_found(self, term: str, usernames: list[str]) -> int:
        result = await self._post(
            "/discover/search",
            SearchFoundIn(term=term, usernames=usernames),
            SearchFoundOut,
        )
        return result.added if result else 0

    async def candidate_stats(self, candidate_id: int, body: CandidateStatsIn) -> None:
        with contextlib.suppress(CoreUnavailable):  # re-probed on the next pass
            await self._post(f"/crawl/candidates/{candidate_id}/stats", body)
