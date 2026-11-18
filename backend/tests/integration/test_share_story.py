"""Story images: uploaded by the Mini App, fetched by Telegram, gone within the hour."""

from __future__ import annotations

import base64

import httpx

from app.api.routers import share
from tests.conftest import bearer, login

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 200


async def test_a_story_card_gets_a_public_short_lived_url(client: httpx.AsyncClient) -> None:
    auth = bearer(await login(client, 9301))
    resp = await client.post(
        "/v1/share/story", json={"image": base64.b64encode(PNG).decode()}, headers=auth
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["expires_in"] == share.STORY_TTL_S
    path = body["url"].split("http://test", 1)[1]
    # Telegram fetches it without our token.
    image = await client.get(path)
    assert image.status_code == 200
    assert image.headers["content-type"] == "image/png"
    assert image.content == PNG
    assert (await client.get("/v1/share/story/nothingsavedhere1234")).status_code == 404


async def test_only_images_and_only_so_many(client: httpx.AsyncClient) -> None:
    auth = bearer(await login(client, 9302))
    not_image = base64.b64encode(b"ID3" + b"\x00" * 200).decode()
    resp = await client.post("/v1/share/story", json={"image": not_image}, headers=auth)
    assert resp.status_code in (400, 422)
    image = base64.b64encode(PNG).decode()
    for _ in range(share.STORIES_PER_HOUR):
        ok = await client.post("/v1/share/story", json={"image": image}, headers=auth)
        assert ok.status_code == 200
    over = await client.post("/v1/share/story", json={"image": image}, headers=auth)
    assert over.status_code in (402, 403, 429)
