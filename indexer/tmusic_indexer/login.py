"""Interactive login for this service's Telegram sessions.

    docker compose -f infra/compose/edge.yml run --rm edge python -m tmusic_indexer.login acc1
    docker compose -f infra/compose/edge.yml run --rm edge python -m tmusic_indexer.login crawl1

The name decides the job. A session named crawl* is used by the MTProto crawler,
for channels with no public web preview; anything else is the resolver, which is what
makes playback work. Keep them on different accounts on purpose: crawling is what
gets an account limited, and playback must not go down with it.

Prompts for phone, code and 2FA password (Telethon), then stores the session encrypted
under SESSIONS_DIR. The plain session string is never printed or written.
"""

from __future__ import annotations

import asyncio
import re
import sys

from telethon import TelegramClient
from telethon.sessions import StringSession

from tmusic_indexer.config import get_settings
from tmusic_indexer.crypto import save_session

_NAME = re.compile(r"^[a-z0-9_-]{1,32}$")


async def login(name: str) -> None:
    if not _NAME.match(name):
        raise SystemExit("name must match [a-z0-9_-]{1,32}")
    settings = get_settings()
    # Telethon's own error for this is "Your API ID or Hash cannot be empty or None",
    # which says nothing about where they come from or which file to put them in.
    if not settings.tg_api_id or settings.tg_api_hash.get_secret_value() in ("", "replace-me"):
        raise SystemExit(
            "TG_API_ID / TG_API_HASH are not set.\n"
            "\n"
            "  1. open https://my.telegram.org -> API development tools\n"
            "  2. create an application (any name) and copy api_id and api_hash\n"
            "  3. put them in .env:\n"
            "       TG_API_ID=1234567\n"
            "       TG_API_HASH=0123456789abcdef0123456789abcdef\n"
            "  4. recreate the edge container so it reads the new values:\n"
            "       docker compose -f infra/compose/solo.yml --env-file .env up -d edge\n"
            "\n"
            "These are account API credentials, not the bot token: the bot cannot\n"
            "download a file it never received, which is what this session is for."
        )
    client = TelegramClient(
        StringSession(),
        settings.tg_api_id,
        settings.tg_api_hash.get_secret_value(),
        device_model=settings.device_model,
    )
    await client.start()  # interactive prompts
    try:
        me = await client.get_me()
        session_string = client.session.save()
        path = save_session(
            settings.sessions_dir, name, session_string, settings.session_enc_key.get_secret_value()
        )
        print(f"saved session for user id {me.id} to {path}")
    finally:
        await client.disconnect()


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    asyncio.run(login(sys.argv[1]))
