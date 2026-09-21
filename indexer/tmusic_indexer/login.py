"""Interactive login for the resolver account (the only session this service has).

    docker compose -f infra/compose/edge.yml run --rm edge python -m tmusic_indexer.login acc1

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
