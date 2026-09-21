from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest

from tests.conftest import FakeClient
from tmusic_common.indexer_contract import (
    AccountIn,
    AccountReportIn,
    CandidateStatsIn,
    CrawlBatchIn,
    CrawlClaimIn,
)
from tmusic_indexer import crypto
from tmusic_indexer.account import Account, ResolverAccount
from tmusic_indexer.config import Settings
from tmusic_indexer.core_client import CoreClient, CoreUnavailable


class FakeCoreServer:
    def __init__(self) -> None:
        self.down = False
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if self.down:
            raise httpx.ConnectError("tunnel down")
        self.requests.append(request)
        assert request.headers["authorization"] == "Bearer secret"
        path = request.url.path
        if path.endswith("/accounts"):
            return httpx.Response(200, json={"ids": {"acc1": 7}})
        if path.endswith("/crawl/claim"):
            return httpx.Response(500)
        if path.endswith("/crawl/batch"):
            return httpx.Response(200, json={"lease_valid": True})
        return httpx.Response(204)


@pytest.fixture
def server() -> FakeCoreServer:
    return FakeCoreServer()


@pytest.fixture
async def core(server: FakeCoreServer):  # type: ignore[no-untyped-def]
    async with httpx.AsyncClient(transport=httpx.MockTransport(server)) as http:
        yield CoreClient(http, "http://core/", "secret")


async def test_a_core_outage_is_raised_not_swallowed(core, server) -> None:  # type: ignore[no-untyped-def]
    """The crawl worker decides what to do about it; the client does not guess."""
    with pytest.raises(CoreUnavailable):
        await core.crawl_claim(CrawlClaimIn(worker_id="w1"))  # 500 from the core
    server.down = True
    with pytest.raises(CoreUnavailable):
        await core.crawl_claim(CrawlClaimIn(worker_id="w1"))


async def test_a_dropped_page_is_not_buffered(core, server) -> None:  # type: ignore[no-untyped-def]
    """ADR-002 §6: replaying a stale page is worse than re-crawling it."""
    batch = CrawlBatchIn(channel_id=1, lease_token="t", items=[])
    assert await core.crawl_batch(batch) is not None
    server.down = True
    assert await core.crawl_batch(batch) is None  # dropped, not queued


async def test_best_effort_calls_survive_an_outage(core, server) -> None:  # type: ignore[no-untyped-def]
    assert await core.register("w1", [AccountIn(session_key="acc1")]) == {"acc1": 7}
    await core.report_account(7, AccountReportIn(status="healthy"))
    assert "/internal/indexer/accounts/7/report" in [r.url.path for r in server.requests]

    server.down = True
    await core.report_account(7, AccountReportIn(status="healthy"))
    await core.candidate_stats(1, CandidateStatsIn())


async def test_the_resolver_account_starts_and_caches_peers(settings: Settings) -> None:
    key = settings.session_enc_key.get_secret_value()
    crypto.save_session(settings.sessions_dir, "acc1", "s1", key)
    # A second session file is ignored rather than pooled (ADR-002 §6).
    crypto.save_session(settings.sessions_dir, "acc2", "s2", key)
    clients = {"s1": FakeClient(), "s2": FakeClient()}
    resolver = ResolverAccount(settings, client_factory=clients.__getitem__)
    await resolver.start()

    assert resolver.account is not None
    assert resolver.account.key == "acc1"
    assert resolver.account.phone_hint == "4567"
    assert [a.session_key for a in resolver.registration()] == ["acc1"]
    resolver.assign_ids({"acc1": 1, "ghost": 3})
    assert resolver.account.id == 1
    assert resolver.ready() is resolver.account

    peer = await resolver.resolve("Music", None)
    assert peer.channel_id == 777
    again = await resolver.resolve(None, 777)  # cached by id, no network call
    assert again.access_hash == 99
    assert [c for c in clients["s1"].calls if c[0] == "resolve"] == [("resolve", "Music")]
    with pytest.raises(LookupError):
        await resolver.resolve(None, 1)

    # Peer cache survives a restart.
    restarted = ResolverAccount(settings, client_factory=lambda s: FakeClient())
    await restarted.start()
    assert restarted.account is not None and "music" in restarted.account.peers
    await resolver.stop()


async def test_a_broken_session_disables_the_account_without_crashing(
    settings: Settings,
) -> None:
    crypto.save_session(
        settings.sessions_dir, "acc1", "s1", settings.session_enc_key.get_secret_value()
    )
    broken = FakeClient()
    broken.fail_with["connect"] = OSError("no network")
    resolver = ResolverAccount(settings, client_factory=lambda s: broken)
    await resolver.start()

    assert resolver.account is not None and resolver.account.status == "disabled"
    assert resolver.ready() is None  # playback of resolved tracks is unaffected


async def test_no_session_at_all_is_not_an_error(settings: Settings) -> None:
    """The crawler is the catalogue; an edge without an account still does its job."""
    resolver = ResolverAccount(settings)
    await resolver.start()
    assert resolver.account is None
    assert resolver.ready() is None
    assert resolver.registration() == []
    with pytest.raises(LookupError):
        await resolver.resolve("x", None)


async def test_a_floodwait_cools_the_account_down(settings: Settings) -> None:
    resolver = ResolverAccount(settings)
    resolver.account = Account(key="a", client=FakeClient(), id=1)

    report = resolver.on_flood(10)
    assert report.status == "cooling"
    assert report.cooling_until is not None and report.cooling_until > datetime.now(UTC)
    assert resolver.ready() is None

    resolver.account.cooling_until = 0
    assert resolver.ready() is resolver.account  # it recovers on its own

    long_wait = resolver.on_flood(settings.limited_after_s + 1)
    assert long_wait.status == "limited"  # a human should look at this one
