from __future__ import annotations

import time
from collections.abc import AsyncIterator

import httpx
import pytest
from telethon import errors

from tests.conftest import FakeClient, audio_doc, audio_message
from tmusic_common.stream_ticket import StreamTicket, sign
from tmusic_indexer.account import Account, ResolverAccount
from tmusic_indexer.config import Settings
from tmusic_indexer.stream import CHUNK, Sources, create_app

SIZE = 2 * CHUNK + 1000
DATA = bytes(i % 251 for i in range(SIZE))


def mt_ticket(**over: object) -> StreamTicket:
    base: dict[str, object] = {
        "track_id": 9, "user_id": 1, "exp": int(time.time()) + 300, "size": SIZE,
        "mime": "audio/mpeg", "channel_id": 777, "channel_username": "music", "message_id": 1,
    }  # fmt: skip
    return StreamTicket(**(base | over))  # type: ignore[arg-type]


def bot_api_transport(
    calls: list[httpx.Request], ignore_range: bool = False
) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.url.path.endswith("/getFile"):
            return httpx.Response(200, json={"ok": True, "result": {"file_path": "music/f.mp3"}})
        rng = request.headers.get("Range", "")
        if ignore_range or not rng:
            return httpx.Response(200, content=DATA[:1500])
        start, end = (int(x) for x in rng.removeprefix("bytes=").split("-"))
        return httpx.Response(206, content=DATA[start : end + 1])

    return httpx.MockTransport(handler)


@pytest.fixture
def client_fake() -> FakeClient:
    fake = FakeClient([audio_message(1, audio_doc(10, size=SIZE))])
    fake.file_bytes = DATA
    return fake


async def make_http(
    settings: Settings, fake: FakeClient, tg: httpx.AsyncClient
) -> httpx.AsyncClient:
    resolver = ResolverAccount(settings)
    resolver.account = Account(key="acc1", client=fake, id=1)
    app = create_app(settings, Sources(settings, resolver, tg))
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://edge")


@pytest.fixture
async def edge(settings: Settings, client_fake: FakeClient) -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient(transport=bot_api_transport([])) as tg:
        async with await make_http(settings, client_fake, tg) as http:
            yield http


def url(ticket: StreamTicket, key: bytes = b"k1", kind: str = "s") -> str:
    return f"/{kind}/{ticket.track_id}?t={sign(ticket, key)}"


async def test_full_stream(edge: httpx.AsyncClient) -> None:
    resp = await edge.get(url(mt_ticket()))
    assert resp.status_code == 200
    assert resp.content == DATA
    assert resp.headers["accept-ranges"] == "bytes"
    assert resp.headers["content-length"] == str(SIZE)


async def test_range_across_chunk_boundary(
    edge: httpx.AsyncClient, client_fake: FakeClient
) -> None:
    start, end = CHUNK - 10, CHUNK + 20
    resp = await edge.get(url(mt_ticket()), headers={"Range": f"bytes={start}-{end}"})
    assert resp.status_code == 206
    assert resp.headers["content-range"] == f"bytes {start}-{end}/{SIZE}"
    assert resp.content == DATA[start : end + 1]
    assert ("download", 0) in client_fake.calls  # aligned to the chunk start


async def test_suffix_range_and_head(edge: httpx.AsyncClient) -> None:
    resp = await edge.get(url(mt_ticket()), headers={"Range": "bytes=-100"})
    assert resp.content == DATA[-100:]
    head = await edge.head(url(mt_ticket()), headers={"Range": "bytes=0-9"})
    assert head.status_code == 206
    assert head.headers["content-length"] == "10"


async def test_unsatisfiable_range(edge: httpx.AsyncClient) -> None:
    resp = await edge.get(url(mt_ticket()), headers={"Range": f"bytes={SIZE}-"})
    assert resp.status_code == 416
    assert resp.headers["content-range"] == f"bytes */{SIZE}"


async def test_rejects_bad_tickets(edge: httpx.AsyncClient) -> None:
    assert (await edge.get(url(mt_ticket(), key=b"wrong"))).status_code == 403
    expired = mt_ticket(exp=int(time.time()) - 1)
    assert (await edge.get(url(expired))).status_code == 403
    ticket = mt_ticket()
    assert (await edge.get(f"/s/10?t={sign(ticket, b'k1')}")).status_code == 403
    assert (await edge.get("/s/9")).status_code == 403


async def test_old_key_still_verifies(edge: httpx.AsyncClient) -> None:
    assert (
        await edge.get(url(mt_ticket(), key=b"k0"), headers={"Range": "bytes=0-0"})
    ).status_code == 206


async def test_nginx_auth_endpoint(edge: httpx.AsyncClient) -> None:
    ok = await edge.get("/_auth", headers={"X-Original-URI": url(mt_ticket())})
    assert ok.status_code == 204
    bad = await edge.get("/_auth", headers={"X-Original-URI": url(mt_ticket(), key=b"x")})
    assert bad.status_code == 403
    assert (await edge.get("/_auth")).status_code == 403


async def test_file_reference_refresh_resumes(
    edge: httpx.AsyncClient, client_fake: FakeClient
) -> None:
    client_fake.download_errors = [errors.FileReferenceExpiredError(request=None)]
    resp = await edge.get(url(mt_ticket()), headers={"Range": "bytes=5-15"})
    assert resp.content == DATA[5:16]
    assert [c for c in client_fake.calls if c[0] == "get_message"] == [("get_message", 1)] * 2


async def test_flood_wait_returns_503(edge: httpx.AsyncClient, client_fake: FakeClient) -> None:
    client_fake.download_errors = [errors.FloodWaitError(request=None, capture=30)]
    resp = await edge.get(url(mt_ticket()))
    assert resp.status_code == 503
    assert resp.headers["retry-after"] == "30"


async def test_missing_message_is_502(edge: httpx.AsyncClient) -> None:
    assert (await edge.get(url(mt_ticket(message_id=404)))).status_code == 502
    assert (await edge.get(url(mt_ticket(channel_username=None)))).status_code == 502


async def test_bot_api_source_uses_range(settings: Settings, client_fake: FakeClient) -> None:
    calls: list[httpx.Request] = []
    async with httpx.AsyncClient(transport=bot_api_transport(calls)) as tg:
        async with await make_http(settings, client_fake, tg) as http:
            ticket = mt_ticket(bot_file_id="BQAC", message_id=None, size=1500)
            resp = await http.get(url(ticket), headers={"Range": "bytes=100-199"})
            again = await http.get(url(ticket), headers={"Range": "bytes=0-9"})
    assert resp.status_code == 206
    assert resp.content == DATA[100:200]
    assert again.content == DATA[:10]
    assert sum(1 for r in calls if r.url.path.endswith("/getFile")) == 1  # file_path cached
    assert "123456:TEST" in str(calls[0].url)  # token only ever sent to Telegram


async def test_bot_api_source_trims_when_range_ignored(
    settings: Settings, client_fake: FakeClient
) -> None:
    async with httpx.AsyncClient(transport=bot_api_transport([], ignore_range=True)) as tg:
        async with await make_http(settings, client_fake, tg) as http:
            ticket = mt_ticket(bot_file_id="BQAC", message_id=None, size=1500)
            resp = await http.get(url(ticket), headers={"Range": "bytes=100-199"})
    assert resp.content == DATA[100:200]


async def test_thumbnail_and_health(edge: httpx.AsyncClient) -> None:
    thumb = await edge.get(url(mt_ticket(), kind="t"))
    assert thumb.status_code == 200
    assert thumb.headers["content-type"] == "image/jpeg"
    assert (await edge.get(url(mt_ticket(message_id=None), kind="t"))).status_code == 404
    health = await edge.get("/healthz")
    assert health.json() == {"status": "ok", "resolver_account": 1}
    assert "edge_stream_bytes_total" in (await edge.get("/metrics")).text


async def test_send_to_chat_uploads_via_bot_api(
    settings: Settings, client_fake: FakeClient
) -> None:
    uploads: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        uploads.append(request)
        if request.url.path.endswith("/sendAudio"):
            return httpx.Response(
                200,
                json={
                    "ok": True,
                    "result": {"audio": {"file_id": "CQACnew", "file_unique_id": "AgADnew"}},
                },
            )
        return httpx.Response(404, json={"ok": False, "description": "nope"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as tg:
        async with await make_http(settings, client_fake, tg) as http:
            ticket = mt_ticket()
            body = {
                "ticket": sign(ticket, b"k1"),
                "chat_id": 4242,
                "title": "Shabe Barooni",
                "performer": "Moein",
            }
            ok = await http.post(
                "/internal/send", json=body, headers={"Authorization": "Bearer internal-token"}
            )
            unauthorised = await http.post("/internal/send", json=body)
            bad_ticket = await http.post(
                "/internal/send",
                json={**body, "ticket": "garbage"},
                headers={"Authorization": "Bearer internal-token"},
            )

    assert ok.status_code == 200
    assert ok.json() == {"file_id": "CQACnew", "file_unique_id": "AgADnew"}
    assert unauthorised.status_code == 401
    assert bad_ticket.status_code == 403
    upload = uploads[-1]
    assert upload.url.path.endswith("/sendAudio")
    assert b"Shabe Barooni" in upload.content  # multipart body carries the metadata


async def test_send_rejects_files_too_large_for_the_bot_api(
    settings: Settings, client_fake: FakeClient
) -> None:
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda r: httpx.Response(200, json={"ok": True, "result": {}})
        )
    ) as tg:
        async with await make_http(settings, client_fake, tg) as http:
            ticket = mt_ticket(size=60 * 1024 * 1024)
            resp = await http.post(
                "/internal/send",
                json={"ticket": sign(ticket, b"k1"), "chat_id": 1, "title": "t", "performer": "p"},
                headers={"Authorization": "Bearer internal-token"},
            )
    assert resp.status_code == 502
    assert "too large" in resp.json()["error"]


# ── ID3 probe ─────────────────────────────────────────────────────────────────


async def test_probe_reads_tags_from_the_head_of_the_file(
    settings: Settings, client_fake: FakeClient
) -> None:
    """The edge reads the file's own ID3 tag; Telegram never sends album or year."""
    from tests.test_id3 import frame_v3, tag_v2

    tag = tag_v2(
        frame_v3(b"TALB", "بهترین‌ها") + frame_v3(b"TYER", "1998") + frame_v3(b"TCON", "Pop")
    )
    client_fake.file_bytes = tag + DATA[len(tag) :]

    async with httpx.AsyncClient(transport=bot_api_transport([])) as tg:
        async with await make_http(settings, client_fake, tg) as http:
            body = {"ticket": sign(mt_ticket(), b"k1")}
            ok = await http.post(
                "/internal/probe", json=body, headers={"Authorization": "Bearer internal-token"}
            )
            unauthorised = await http.post("/internal/probe", json=body)
            bad_ticket = await http.post(
                "/internal/probe",
                json={"ticket": "garbage"},
                headers={"Authorization": "Bearer internal-token"},
            )

    assert ok.status_code == 200
    assert ok.json() == {
        "track_id": 9,
        "album": "بهترین‌ها",
        "year": 1998,
        "genre": "Pop",
        "title": None,
        "artist": None,
        # No APIC frame in this fixture, so the core is told there is no cover.
        "has_artwork": False,
    }
    assert unauthorised.status_code == 401
    assert bad_ticket.status_code == 403


async def test_probe_on_an_untagged_file_returns_nulls(edge: httpx.AsyncClient) -> None:
    resp = await edge.post(
        "/internal/probe",
        json={"ticket": sign(mt_ticket(), b"k1")},
        headers={"Authorization": "Bearer internal-token"},
    )
    assert resp.status_code == 200
    assert resp.json()["album"] is None
    assert resp.json()["year"] is None


async def test_a_track_without_a_telegram_thumbnail_falls_back_to_its_id3_cover(
    settings: Settings, client_fake: FakeClient
) -> None:
    """Most crawled tracks have no Telegram thumbnail but do carry an APIC frame."""
    from tests.test_id3 import apic_tag

    cover = bytes([0x89]) + b"PNGcover"
    client_fake.thumb_bytes = b""  # Telegram has nothing
    client_fake.file_bytes = apic_tag(cover) + DATA  # but the file does

    async with httpx.AsyncClient(transport=bot_api_transport([])) as tg:
        async with await make_http(settings, client_fake, tg) as edge:
            resp = await edge.get(url(mt_ticket(), kind="t"))

    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("image/png")
    assert resp.content == cover


async def test_no_thumbnail_and_no_id3_cover_is_a_clean_404(
    settings: Settings, client_fake: FakeClient
) -> None:
    client_fake.thumb_bytes = b""
    client_fake.file_bytes = DATA  # no tag at all

    async with httpx.AsyncClient(transport=bot_api_transport([])) as tg:
        async with await make_http(settings, client_fake, tg) as edge:
            resp = await edge.get(url(mt_ticket(), kind="t"))

    assert resp.status_code == 404


async def test_probe_reports_a_cover_the_file_carries_itself(
    settings: Settings, client_fake: FakeClient
) -> None:
    """Telegram makes a thumbnail for some messages only; the file often has its own.

    Without this flag the core never learns the cover exists, so the API hands the
    player no thumbnail URL and a track with artwork inside it shows a blank square.
    """
    from tests.test_id3 import apic_tag

    tag = apic_tag(b"\x89PNG" + b"x" * 500)
    client_fake.file_bytes = tag + DATA[len(tag) :]

    async with httpx.AsyncClient(transport=bot_api_transport([])) as tg:
        async with await make_http(settings, client_fake, tg) as http:
            resp = await http.post(
                "/internal/probe",
                json={"ticket": sign(mt_ticket(), b"k1")},
                headers={"Authorization": "Bearer internal-token"},
            )

    assert resp.status_code == 200
    assert resp.json()["has_artwork"] is True
