"""Edge process: catalogue crawler + lazy resolver + streaming server (ADR-002).

The catalogue comes from public preview pages, so this process no longer owns a pool
of Telegram accounts — only one session, used when somebody plays a track we have
never resolved and to stream its bytes. If that session is missing or flooded, the
crawler keeps working and already-resolved tracks keep playing.
"""

from __future__ import annotations

import asyncio
import contextlib
import signal

import httpx
import uvicorn

from tmusic_common.logging import configure_logging, get_logger
from tmusic_indexer.account import ResolverAccount
from tmusic_indexer.config import get_settings
from tmusic_indexer.core_client import CoreClient, CoreUnavailable
from tmusic_indexer.stream import Sources, create_app
from tmusic_indexer.webpreview.worker import CrawlWorker

log = get_logger(__name__)


async def main() -> None:
    settings = get_settings()
    configure_logging("edge", settings.log_level, settings.log_json)
    resolver = ResolverAccount(settings)
    await resolver.start()
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):  # Windows dev machines
            loop.add_signal_handler(sig, stop.set)

    async with (
        httpx.AsyncClient(timeout=30.0) as core_http,
        httpx.AsyncClient(timeout=httpx.Timeout(30.0, read=60.0)) as tg_http,
    ):
        core = CoreClient(
            core_http, settings.core_url, settings.internal_api_token.get_secret_value()
        )
        with contextlib.suppress(CoreUnavailable):  # the panel can wait; work cannot
            resolver.assign_ids(await core.register(settings.worker_id, resolver.registration()))
        crawler = CrawlWorker(settings, core)
        app = create_app(settings, Sources(settings, resolver, tg_http))
        server = uvicorn.Server(
            uvicorn.Config(
                app,
                host=settings.stream_host,
                port=settings.stream_port,
                proxy_headers=True,
                forwarded_allow_ips="*",
                log_config=None,
                server_header=False,
            )
        )
        server_task = asyncio.create_task(server.serve())
        crawl_task = asyncio.create_task(crawler.run(stop))
        await stop.wait()
        log.info("edge.stopping")
        server.should_exit = True
        await asyncio.gather(server_task, crawl_task, return_exceptions=True)
        await crawler.aclose()
    await resolver.stop()


if __name__ == "__main__":
    asyncio.run(main())
