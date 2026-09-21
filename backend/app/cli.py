"""Developer/ops helpers.

python -m app.cli gen-keys            # Ed25519 JWT key pair + random secrets for .env
python -m app.cli dev-login <tg_id>   # print a signed initData for local API testing
python -m app.cli add-edge <host> [weight]
python -m app.cli seed-demo           # demo channel + tracks for UI work (dev only)
python -m app.cli reindex-search
python -m app.cli fake-initdata <n>   # signed initData for k6 (dev/staging only)
python -m app.cli add-admin <tg_id>   # prints the SQL for the first owner
python -m app.cli seed-channels <file>   # batch import channels to crawl (- for stdin)
"""

from __future__ import annotations

import asyncio
import json
import secrets
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from sqlalchemy.dialects.postgresql import insert

from app.config import get_settings
from app.db import make_engine, make_sessionmaker, session_scope
from app.models import Channel, EdgeNode
from app.security.initdata import sign_init_data
from tmusic_common.indexer_contract import AudioItem


def gen_keys() -> None:
    key = Ed25519PrivateKey.generate()
    private = key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    ).decode()
    public = (
        key.public_key()
        .public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
        .decode()
    )

    def env_value(pem: str) -> str:
        return '"' + pem.strip().replace("\n", "\\n") + '"'

    print(f"JWT_PRIVATE_KEY={env_value(private)}")
    print(f"JWT_PUBLIC_KEY={env_value(public)}")
    print(f"WEBHOOK_SECRET={secrets.token_urlsafe(32)}")
    print(f"STREAM_SIGNING_KEYS={secrets.token_urlsafe(48)}")
    print(f"INTERNAL_API_TOKEN={secrets.token_urlsafe(48)}")
    print(f"SESSION_ENC_KEY={secrets.token_urlsafe(32)}")


def dev_login(tg_id: int) -> None:
    settings = get_settings()
    if settings.env == "prod":
        raise SystemExit("dev-login is disabled in prod")
    user = {"id": tg_id, "first_name": "Dev", "language_code": "fa"}
    fields = {"auth_date": str(int(time.time())), "user": json.dumps(user)}
    print(sign_init_data(fields, settings.bot_token.get_secret_value()))


def fake_initdata(count: int) -> None:
    """Signed initData for N synthetic load-test users, one per line.

    Disabled in prod: these are real, valid credentials for whoever holds them.
    """
    settings = get_settings()
    if settings.env == "prod":
        raise SystemExit("fake-initdata is disabled in prod")
    token = settings.bot_token.get_secret_value()
    now = str(int(time.time()))
    for index in range(count):
        user = {"id": 900_000_000 + index, "first_name": f"Load{index}", "language_code": "fa"}
        print(sign_init_data({"auth_date": now, "user": json.dumps(user)}, token))


def add_admin_sql(tg_id: int) -> None:
    """Prints the SQL that makes the first owner; deliberately not an API endpoint."""
    values = f"VALUES ({tg_id}, 'owner', ARRAY['*'], true)"
    print(
        "INSERT INTO admin_users (tg_id, role, permissions, is_active) "
        + values
        + " ON CONFLICT (tg_id) DO UPDATE SET role = 'owner', is_active = true;"
    )


async def add_edge(host: str, weight: int) -> None:
    engine = make_engine(get_settings())
    async with session_scope(make_sessionmaker(engine)) as session:
        await session.execute(
            insert(EdgeNode)
            .values(host=host, weight=weight)
            .on_conflict_do_update(index_elements=[EdgeNode.host], set_={"weight": weight})
        )
    await engine.dispose()
    print("edge registered:", host)


async def seed_demo() -> None:
    from app.services.ingest import ingest_items

    settings = get_settings()
    if settings.env == "prod":
        raise SystemExit("seed-demo is disabled in prod")
    engine = make_engine(settings)
    demo = [
        ("Moein - Shabe Barooni", None, 245),
        ("Googoosh - Pol", "Googoosh", 272),
        ("Hayedeh | Gole Sangam @demo_channel", None, 301),
        ("Dariush - Nooneh Paneer", None, 233),
        ("Shadmehr Aghili - Taghdir", None, 256),
        ("Queen - Bohemian Rhapsody", None, 355),
    ]
    async with session_scope(make_sessionmaker(engine)) as session:
        channel = (
            await session.scalars(
                insert(Channel)
                .values(
                    username="demo_channel",
                    tg_channel_id=1,
                    title="Demo Channel",
                    status="active",
                    progress_pct=100,
                    is_featured=True,
                    category_id=1,
                )
                .on_conflict_do_update(index_elements=[Channel.username], set_={"status": "active"})
                .returning(Channel)
            )
        ).one()
        now = datetime.now(UTC)
        items = [
            AudioItem(
                message_id=i + 1,
                posted_at=now - timedelta(hours=i),
                file_unique_id=f"AgADdemo{i:04d}",
                duration=duration,
                file_size=duration * 16_000,
                mime_type="audio/mpeg",
                title=title,
                performer=performer,
            )
            for i, (title, performer, duration) in enumerate(demo)
        ]
        stats = await ingest_items(session, channel, items)
        print("demo tracks:", stats)
    await engine.dispose()


async def seed_channels(raw: str) -> None:
    """Batch seed for the catalogue (ADR-002 §3): one username, link or CSV row per line.

    The panel has the same thing as a paste box; this exists because the first thirty
    channels are imported before anybody has an admin account.
    """
    from app.services.discovery import import_usernames

    engine = make_engine(get_settings())
    async with session_scope(make_sessionmaker(engine)) as session:
        result = await import_usernames(session, raw)
    await engine.dispose()
    print(
        f"created={result.created} existing={result.existing} "
        f"blocked={result.blocked} invalid={len(result.invalid)}"
    )
    for entry in result.invalid[:20]:
        print("  ? ", entry)


async def reindex() -> None:
    import httpx
    from redis.asyncio import Redis

    from app.services.meili import MeiliClient
    from app.services.search import full_reindex

    settings = get_settings()
    engine = make_engine(settings)
    async with httpx.AsyncClient() as http:
        meili = MeiliClient(
            http,
            settings.meili_url,
            settings.meili_api_key.get_secret_value(),
            settings.meili_index,
        )
        redis = Redis.from_url(settings.redis_url, decode_responses=True)
        async with session_scope(make_sessionmaker(engine)) as session:
            print("reindexed:", await full_reindex(session, meili, redis))
        await redis.aclose()
    await engine.dispose()


def main(argv: list[str]) -> None:
    match argv:
        case ["gen-keys"]:
            gen_keys()
        case ["dev-login", tg_id]:
            dev_login(int(tg_id))
        case ["add-edge", host]:
            asyncio.run(add_edge(host, 100))
        case ["add-edge", host, weight]:
            asyncio.run(add_edge(host, int(weight)))
        case ["fake-initdata", count]:
            fake_initdata(int(count))
        case ["add-admin", tg_id]:
            add_admin_sql(int(tg_id))
        case ["seed-demo"]:
            asyncio.run(seed_demo())
        case ["seed-channels", path]:
            raw = sys.stdin.read() if path == "-" else Path(path).read_text("utf-8")
            asyncio.run(seed_channels(raw))
        case ["reindex-search"]:
            asyncio.run(reindex())
        case _:
            print(__doc__)
            raise SystemExit(2)


if __name__ == "__main__":
    main(sys.argv[1:])
