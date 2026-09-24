"""Edge streaming server (ADR-0004).

``GET /s/{track_id}?t=<ticket>``   audio bytes, HTTP Range → 206
``GET /t/{track_id}?t=<ticket>``   thumbnail
``GET /_auth``                     nginx ``auth_request`` target (guards cached responses)

nginx in front uses the ``slice`` module, so upstream requests are 1 MiB aligned ranges;
they map 1:1 onto MTProto ``upload.getFile`` chunks.
"""

from __future__ import annotations

import hmac
import re
import time
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse
from starlette.routing import Route
from telethon import errors

from tmusic_common.indexer_contract import AudioItem
from tmusic_common.logging import get_logger
from tmusic_common.stream_ticket import StreamTicket, TicketError, verify
from tmusic_indexer import id3
from tmusic_indexer.account import Account, ResolverAccount
from tmusic_indexer.config import Settings
from tmusic_indexer.extract import to_audio_item

log = get_logger(__name__)

CHUNK = 1024 * 1024
# Enough of the file to hold an ID3 tag with a cover in it.
COVER_HEAD_BYTES = 512 * 1024
_RANGE = re.compile(r"^bytes=(\d*)-(\d*)$")
_PATH = re.compile(r"^/[st]/(\d+)$")

STREAM_BYTES = Counter("edge_stream_bytes_total", "Bytes sent to clients", ["source"])
STREAM_ERRORS = Counter("edge_stream_errors_total", "Streaming failures", ["reason"])
# Time to first byte: how long the client waited before any audio arrived. Measured
# here because this is where the wait actually happens (ADR-003 phase 10).
STREAM_TTFB = Histogram(
    "edge_stream_ttfb_seconds",
    "From request to the first byte sent",
    ["source"],
    buckets=(0.05, 0.1, 0.25, 0.5, 1.0, 2.0, 5.0, 10.0),
)


class RangeNotSatisfiable(Exception):
    pass


def parse_range(header: str | None, size: int) -> tuple[int, int] | None:
    """Inclusive (start, end) for a single ``bytes=`` range, or None for the whole file.

    Multi-range requests are answered with the whole file (allowed by RFC 9110).
    """
    if not header or "," in header:
        return None
    m = _RANGE.match(header.strip())
    if not m:
        return None
    if size <= 0:
        raise RangeNotSatisfiable
    first, last = m.groups()
    if first == "" and last == "":
        return None
    if first == "":  # suffix: last N bytes
        length = int(last)
        if length == 0:
            raise RangeNotSatisfiable
        return max(size - length, 0), size - 1
    start = int(first)
    end = int(last) if last else size - 1
    if start >= size or end < start:
        raise RangeNotSatisfiable
    return start, min(end, size - 1)


def ticket_for(request_path: str, token: str | None, keys: list[bytes]) -> StreamTicket:
    m = _PATH.match(request_path)
    if not m or not token:
        raise TicketError("malformed")
    ticket = verify(token, keys)
    if ticket.track_id != int(m.group(1)):
        raise TicketError("track mismatch")
    return ticket


ByteSource = Callable[[int, int], AsyncIterator[bytes]]


@dataclass
class _CachedPath:
    path: str
    expires: float


class Sources:
    """Fetches bytes from Telegram for a ticket."""

    def __init__(
        self, settings: Settings, account: ResolverAccount, http: httpx.AsyncClient
    ) -> None:
        self.settings = settings
        self.account = account
        self.http = http
        self._paths: dict[str, _CachedPath] = {}
        self._messages: dict[tuple[str, int, int], tuple[float, Any]] = {}

    # ── Bot API ──

    def _bot_url(self, suffix: str) -> str:
        token = self.settings.bot_token.get_secret_value()
        return f"{self.settings.tg_api_base.rstrip('/')}/{suffix.format(token=token)}"

    async def _file_path(self, file_id: str, refresh: bool = False) -> str:
        cached = self._paths.get(file_id)
        if cached and cached.expires > time.time() and not refresh:
            return cached.path
        resp = await self.http.get(self._bot_url("bot{token}/getFile"), params={"file_id": file_id})
        data = resp.json()
        if not data.get("ok"):
            raise LookupError(f"getFile failed: {data.get('description')}")
        path = str(data["result"]["file_path"])
        # file_path links stay valid for at least an hour.
        self._paths[file_id] = _CachedPath(path, time.time() + 50 * 60)
        return path

    async def bot_api(self, file_id: str, start: int, end: int) -> AsyncIterator[bytes]:
        for attempt in (0, 1):
            path = await self._file_path(file_id, refresh=attempt == 1)
            url = self._bot_url("file/bot{token}/" + path)
            async with self.http.stream("GET", url, headers={"Range": f"bytes={start}-{end}"}) as r:
                if r.status_code == 404 and attempt == 0:
                    continue  # stale file_path; fetch a fresh one
                if r.status_code not in (200, 206):
                    raise LookupError(f"bot file download returned {r.status_code}")
                # Some servers ignore Range: skip/trim ourselves.
                skip = start if r.status_code == 200 else 0
                remaining = end - start + 1
                async for chunk in r.aiter_bytes(64 * 1024):
                    if skip:
                        if len(chunk) <= skip:
                            skip -= len(chunk)
                            continue
                        chunk, skip = chunk[skip:], 0
                    if len(chunk) >= remaining:
                        yield chunk[:remaining]
                        return
                    remaining -= len(chunk)
                    yield chunk
                return

    # ── MTProto ──

    async def _message(self, account: Account, ticket: StreamTicket, refresh: bool) -> Any:
        assert ticket.message_id is not None
        key = (account.key, ticket.channel_id or 0, ticket.message_id)
        cached = self._messages.get(key)
        if cached and cached[0] > time.time() and not refresh:
            return cached[1]
        peer = await self.account.resolve(ticket.channel_username, ticket.channel_id)
        message = await account.client.get_messages(peer, ids=ticket.message_id)
        document = getattr(getattr(message, "media", None), "document", None)
        if document is None:
            raise LookupError("message has no document")
        if len(self._messages) > 50_000:
            self._messages.clear()
        self._messages[key] = (time.time() + self.settings.message_cache_s, message)
        return message

    async def mtproto(self, ticket: StreamTicket, start: int, end: int) -> AsyncIterator[bytes]:
        account = await self.account.ready_or_reload()
        if account is None:
            raise LookupError("no healthy account")
        pos = start  # next byte owed to the client; a retry resumes from here
        async with account.downloads:
            for attempt in (0, 1):
                message = await self._message(account, ticket, refresh=attempt == 1)
                aligned = pos - pos % CHUNK
                skip = pos - aligned
                try:
                    async for chunk in account.client.iter_download(
                        message.media.document,
                        offset=aligned,
                        request_size=CHUNK,
                        chunk_size=CHUNK,
                        limit=(end - aligned) // CHUNK + 1,
                        file_size=ticket.size,
                    ):
                        data = bytes(chunk)[skip:]
                        skip = 0
                        data = data[: end - pos + 1]
                        if data:
                            pos += len(data)
                            yield data
                        if pos > end:
                            return
                    return
                except errors.FileReferenceExpiredError:
                    if attempt == 1:
                        raise
                    log.info("stream.file_reference_refresh", track_id=ticket.track_id)
                except errors.FloodWaitError as exc:
                    self.account.on_flood(int(exc.seconds))
                    raise

    async def resolve_message(
        self, username: str, channel_id: int | None, message_id: int
    ) -> AudioItem:
        """The lazy resolver (ADR-002 layer C): one message, read once, cached forever.

        The web preview gives a catalogue but no file identity, duration or size; this
        is where they come from. Deliberately the same extraction the MTProto indexer
        uses, so a resolved track is indistinguishable from an indexed one.
        """
        account = await self.account.ready_or_reload()
        if account is None:
            raise LookupError("no healthy account")
        peer = await self.account.resolve(username, channel_id)
        message = await account.client.get_messages(peer, ids=message_id)
        item = to_audio_item(message) if message is not None else None
        if item is None:
            raise LookupError("message carries no audio")
        return item

    async def thumbnail(self, ticket: StreamTicket) -> tuple[bytes, str] | None:
        """Telegram's thumbnail, or the cover embedded in the file itself.

        Many crawled tracks have no Telegram thumbnail but do carry an ID3 APIC
        frame, which lives in the same head-of-file bytes we can already read. It is
        streamed through like any other artwork — nothing is stored (ADR-003 §2-6).
        """
        if ticket.message_id is None:
            return None
        account = await self.account.ready_or_reload()
        if account is not None:
            message = await self._message(account, ticket, refresh=False)
            data = await account.client.download_media(message, file=bytes, thumb=-1)
            if isinstance(data, bytes) and data:
                return data, "image/jpeg"
        return await self._embedded_cover(ticket)

    async def _embedded_cover(self, ticket: StreamTicket) -> tuple[bytes, str] | None:
        if ticket.size <= 0:
            return None
        try:
            _, source = self.open(ticket)
            head = b""
            async for chunk in source(0, min(COVER_HEAD_BYTES, ticket.size - 1)):
                head += chunk
                if len(head) >= COVER_HEAD_BYTES:
                    break
        except (LookupError, StopAsyncIteration, errors.RPCError):
            return None
        art = id3.parse_artwork(head)
        return (art.data, art.mime) if art else None

    def open(self, ticket: StreamTicket) -> tuple[str, ByteSource]:
        if ticket.bot_file_id and ticket.size <= 20 * 1024 * 1024:
            file_id = ticket.bot_file_id
            return "bot_api", lambda s, e: self.bot_api(file_id, s, e)
        if ticket.message_id is not None and ticket.channel_username:
            return "mtproto", lambda s, e: self.mtproto(ticket, s, e)
        raise LookupError("ticket has no usable source")


MAX_BOT_UPLOAD = 50 * 1024 * 1024


class Sender:
    """Uploads a track to a user's chat with the Bot API (phase 5).

    Runs on the edge because that is where the bytes are; the core only passes a
    signed ticket. The resulting file_id goes back so the core can reuse it.
    """

    def __init__(self, settings: Settings, sources: Sources) -> None:
        self.settings = settings
        self.sources = sources

    async def send(
        self, ticket: StreamTicket, chat_id: int, title: str, performer: str
    ) -> dict[str, Any]:
        if ticket.size > MAX_BOT_UPLOAD:
            raise ValueError("file is too large for the Bot API")
        _, source = self.sources.open(ticket)
        chunks = [chunk async for chunk in source(0, max(ticket.size - 1, 0))]
        data = b"".join(chunks)
        token = self.settings.bot_token.get_secret_value()
        url = f"{self.settings.tg_api_base.rstrip('/')}/bot{token}/sendAudio"
        response = await self.sources.http.post(
            url,
            data={
                "chat_id": str(chat_id),
                "title": title,
                "performer": performer,
                "duration": "0",
            },
            files={"audio": (f"{title or 'track'}.mp3", data, ticket.mime or "audio/mpeg")},
            timeout=180.0,
        )
        body = response.json()
        if not body.get("ok"):
            raise RuntimeError(str(body.get("description")))
        audio_result = body["result"].get("audio", {})
        return {
            "file_id": audio_result.get("file_id"),
            "file_unique_id": audio_result.get("file_unique_id"),
        }


# Enough for an ID3v2 header with cover art stripped out of the way; the frames we
# read sit at the very start, and anything bigger is album artwork we do not want.
PROBE_HEAD_BYTES = 256 * 1024


class Prober:
    """Reads a track's ID3 tags from the first bytes of the file.

    Telegram never sends album, year or genre, so this is the only way to get them.
    It is a deliberate one-off read per track, driven by a core job, not something on
    the streaming path: the point is to fill gaps cheaply, not to slow playback.
    """

    def __init__(self, sources: Sources) -> None:
        self.sources = sources

    async def probe(self, ticket: StreamTicket) -> tuple[id3.Tags, bool]:
        """Tags, and whether the file carries its own cover.

        The artwork answer comes free: it lives in the same head bytes already read
        for the tags. Without it the core has no way to know a track has a cover
        Telegram never made a thumbnail for, so the player shows a blank square for a
        file that has had artwork inside it all along.
        """
        _, source = self.sources.open(ticket)
        end = min(PROBE_HEAD_BYTES, max(ticket.size - 1, 0))
        head = b""
        async for chunk in source(0, end):
            head += chunk
            if len(head) >= PROBE_HEAD_BYTES:
                break
        artwork = id3.parse_artwork(head) is not None
        tags = id3.parse_id3v2(head)
        if not tags.empty or ticket.size <= id3.ID3V1_SIZE:
            return tags, artwork
        # No v2 tag: the old 128-byte block lives at the very end of the file.
        tail = b""
        async for chunk in source(ticket.size - id3.ID3V1_SIZE, ticket.size - 1):
            tail += chunk
        return id3.parse_id3v1(tail), artwork


def _headers(ticket: StreamTicket, length: int) -> dict[str, str]:
    return {
        "Accept-Ranges": "bytes",
        "Content-Type": ticket.mime or "audio/mpeg",
        "Content-Length": str(length),
        # Client side: private; nginx caches upstream responses by its own rules.
        "Cache-Control": "private, max-age=3600",
        "X-Content-Type-Options": "nosniff",
    }


def create_app(
    settings: Settings, sources: Sources, background: Sources | None = None
) -> Starlette:
    """``background`` is where work nobody is waiting for reads from.

    Probing a file's tags and pre-warming a track are both downloads, and until now
    they came off the same account a listener's playback does — so a catalogue being
    backfilled competed with the person pressing play. When a crawling session
    exists, background reads go there instead and the two stop fighting.
    """
    keys = settings.signing_keys
    sender = Sender(settings, sources)
    prober = Prober(background or sources)

    def _ticket(request: Request) -> StreamTicket:
        return ticket_for(request.url.path, request.query_params.get("t"), keys)

    async def healthz(_: Request) -> Response:
        # The edge is healthy without an account: crawling and cached playback do not
        # need one. The account's absence is reported, not treated as an outage.
        ready = 1 if sources.account.ready() else 0
        return Response(
            f'{{"status":"ok","resolver_account":{ready}}}',
            status_code=200,
            media_type="application/json",
        )

    async def metrics(_: Request) -> Response:
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    async def auth(request: Request) -> Response:
        original = request.headers.get("X-Original-URI", "")
        parts = urlsplit(original)
        tokens = parse_qs(parts.query).get("t")
        token = tokens[0] if tokens else None
        try:
            ticket_for(parts.path, token, keys)
        except TicketError:
            return Response(status_code=403)
        return Response(status_code=204)

    async def stream(request: Request) -> Response:
        started = time.perf_counter()
        try:
            ticket = _ticket(request)
        except TicketError as exc:
            STREAM_ERRORS.labels("ticket").inc()
            return Response(str(exc), status_code=403)
        # A prefetch ticket may only read the head of the file; it can never stand in
        # for a real play, however the client asks (ADR-003 §2-3).
        limit = min(ticket.size, ticket.max_bytes) if ticket.max_bytes else ticket.size
        try:
            rng = parse_range(request.headers.get("Range"), limit)
        except RangeNotSatisfiable:
            return Response(status_code=416, headers={"Content-Range": f"bytes */{limit}"})
        start, end = rng or (0, limit - 1)
        end = min(end, limit - 1)
        if start > end:
            return Response(status_code=416, headers={"Content-Range": f"bytes */{limit}"})
        length = end - start + 1
        headers = _headers(ticket, length)
        status = 200
        if rng is not None:
            status = 206
            headers["Content-Range"] = f"bytes {start}-{end}/{ticket.size}"
        if request.method == "HEAD":
            return Response(status_code=status, headers=headers)
        try:
            # A cache-warming fetch is nobody's playback: it reads on the crawling
            # account so filling the cache never slows down the person listening.
            # nginx keys the cache on the track and the slice, so this header changes
            # who fetches the bytes without splitting the cache entry.
            reader = background or sources if request.headers.get("X-Warm") else sources
            source_name, source = reader.open(ticket)
            body = source(start, end)
            first = await anext(body)  # fail before headers are sent
        except (LookupError, StopAsyncIteration) as exc:
            STREAM_ERRORS.labels("source").inc()
            log.warning("stream.source_failed", track_id=ticket.track_id, error=str(exc))
            return Response("source unavailable", status_code=502)
        except errors.FloodWaitError:
            STREAM_ERRORS.labels("flood").inc()
            return Response("busy", status_code=503, headers={"Retry-After": "30"})
        except errors.RPCError as exc:
            STREAM_ERRORS.labels("rpc").inc()
            log.warning("stream.rpc_error", track_id=ticket.track_id, error=type(exc).__name__)
            return Response("source unavailable", status_code=502)

        STREAM_TTFB.labels(source_name).observe(time.perf_counter() - started)

        async def iterate() -> AsyncIterator[bytes]:
            sent = len(first)
            yield first
            try:
                async for chunk in body:
                    sent += len(chunk)
                    yield chunk
            except Exception:
                STREAM_ERRORS.labels("midstream").inc()
                log.warning("stream.midstream_failure", track_id=ticket.track_id, exc_info=True)
            finally:
                STREAM_BYTES.labels(source_name).inc(sent)

        return StreamingResponse(iterate(), status_code=status, headers=headers)

    async def send(request: Request) -> Response:
        expected = f"Bearer {settings.internal_api_token.get_secret_value()}"
        if not hmac.compare_digest(request.headers.get("authorization", ""), expected):
            return Response(status_code=401)
        body = await request.json()
        try:
            ticket = verify(str(body.get("ticket", "")), keys)
        except TicketError:
            return Response(status_code=403)
        try:
            result = await sender.send(
                ticket,
                int(body["chat_id"]),
                str(body.get("title", ""))[:64],
                str(body.get("performer", ""))[:64],
            )
        except (ValueError, LookupError, RuntimeError, errors.RPCError) as exc:
            log.warning("send.failed", track_id=ticket.track_id, error=str(exc))
            return JSONResponse({"error": str(exc)}, status_code=502)
        log.info("send.ok", track_id=ticket.track_id, chat_id=body["chat_id"])
        return JSONResponse(result)

    async def probe(request: Request) -> Response:
        expected = f"Bearer {settings.internal_api_token.get_secret_value()}"
        if not hmac.compare_digest(request.headers.get("authorization", ""), expected):
            return Response(status_code=401)
        body = await request.json()
        try:
            ticket = verify(str(body.get("ticket", "")), keys)
        except TicketError:
            return Response(status_code=403)
        try:
            tags, has_artwork = await prober.probe(ticket)
        except (ValueError, LookupError, RuntimeError, errors.RPCError) as exc:
            log.info("probe.failed", track_id=ticket.track_id, error=str(exc))
            return JSONResponse({"error": str(exc)}, status_code=502)
        return JSONResponse(
            {
                "track_id": ticket.track_id,
                "album": tags.album,
                "year": tags.year,
                "genre": tags.genre,
                "title": tags.title,
                "artist": tags.artist,
                "has_artwork": has_artwork,
            }
        )

    async def resolve_route(request: Request) -> Response:
        """Core asks: what is the file behind this message? (ADR-002 layer C)

        Called once per track, at the moment somebody first plays it. No ticket: the
        track has no size or mime yet, which is exactly what is being asked for.
        """
        expected = f"Bearer {settings.internal_api_token.get_secret_value()}"
        if not hmac.compare_digest(request.headers.get("authorization", ""), expected):
            return Response(status_code=401)
        body = await request.json()
        # The core says whether somebody is waiting: a play resolves on the account
        # that serves playback, a pre-warm resolves on the crawler's.
        reader = background or sources if body.get("background") else sources
        try:
            item = await reader.resolve_message(
                str(body["channel_username"]),
                int(body["channel_id"]) if body.get("channel_id") else None,
                int(body["message_id"]),
            )
        except (KeyError, ValueError, LookupError) as exc:
            log.info("resolve.failed", error=str(exc))
            return JSONResponse({"error": str(exc)}, status_code=502)
        except errors.FloodWaitError as exc:
            # The circuit breaker on the core side reads this.
            return JSONResponse(
                {"error": "flood_wait"}, status_code=503, headers={"Retry-After": str(exc.seconds)}
            )
        except errors.RPCError as exc:
            log.warning("resolve.rpc_error", error=type(exc).__name__)
            return JSONResponse({"error": type(exc).__name__}, status_code=502)
        return JSONResponse(item.model_dump(mode="json"))

    async def thumb(request: Request) -> Response:
        try:
            ticket = _ticket(request)
        except TicketError:
            return Response(status_code=403)
        try:
            found = await sources.thumbnail(ticket)
        except (LookupError, errors.RPCError):
            found = None
        if not found:
            return Response(status_code=404)
        data, mime = found
        return Response(data, media_type=mime, headers={"Cache-Control": "private, max-age=86400"})

    routes = [
        Route("/healthz", healthz),
        Route("/metrics", metrics),
        Route("/_auth", auth),
        Route("/s/{track_id:int}", stream, methods=["GET", "HEAD"]),
        Route("/t/{track_id:int}", thumb),
        Route("/internal/send", send, methods=["POST"]),
        Route("/internal/probe", probe, methods=["POST"]),
        Route("/internal/resolve", resolve_route, methods=["POST"]),
    ]
    return Starlette(routes=routes)
