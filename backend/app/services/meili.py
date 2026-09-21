"""Thin Meilisearch client (the HTTP API is small; no SDK dependency needed)."""

from __future__ import annotations

from typing import Any

import httpx

from app.errors import Unavailable

INDEX_SETTINGS: dict[str, Any] = {
    "searchableAttributes": [
        "title_norm",
        "artists_norm",
        "aliases",
        "album_norm",
        "title_latin",
        "artists_latin",
        "title_skel",
        "artists_skel",
    ],
    "filterableAttributes": [
        "channel_ids",
        "artist_ids",
        "language",
        "year",
        "duration",
        "album_norm",
    ],
    "sortableAttributes": ["channels_count", "plays_7d", "created_at"],
    "rankingRules": [
        "words",
        "typo",
        "proximity",
        "attribute",
        "exactness",
        "channels_count:desc",
        "plays_7d:desc",
    ],
    "displayedAttributes": ["id"],
    "typoTolerance": {"minWordSizeForTypos": {"oneTypo": 4, "twoTypos": 8}},
    "pagination": {"maxTotalHits": 1000},
}

# Skeleton hits are fuzzier than direct hits, so they rank lower (ADR-0006).
PRIMARY_FIELDS = [
    "title_norm",
    "artists_norm",
    "aliases",
    "album_norm",
    "title_latin",
    "artists_latin",
]
SKELETON_FIELDS = ["title_skel", "artists_skel"]
SKELETON_WEIGHT = 0.6


class MeiliClient:
    def __init__(self, http: httpx.AsyncClient, base_url: str, api_key: str, index: str) -> None:
        self._http = http
        self._base = base_url.rstrip("/")
        self._headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self.index = index

    async def _request(self, method: str, path: str, json: Any = None) -> Any:
        try:
            resp = await self._http.request(
                method, f"{self._base}{path}", json=json, headers=self._headers, timeout=5.0
            )
        except httpx.HTTPError as exc:
            raise Unavailable("search backend unreachable") from exc
        if resp.status_code >= 500:
            raise Unavailable("search backend error", status=resp.status_code)
        if resp.status_code >= 400:
            raise RuntimeError(f"meilisearch {method} {path}: {resp.status_code} {resp.text}")
        return resp.json() if resp.content else None

    async def healthy(self) -> bool:
        try:
            data = await self._request("GET", "/health")
        except (Unavailable, RuntimeError):
            return False
        return isinstance(data, dict) and data.get("status") == "available"

    async def ensure_index(self) -> None:
        existing = await self._http.get(
            f"{self._base}/indexes/{self.index}", headers=self._headers, timeout=5.0
        )
        if existing.status_code == 404:
            await self.wait(
                await self._request("POST", "/indexes", {"uid": self.index, "primaryKey": "id"})
            )
        await self.wait(
            await self._request("PATCH", f"/indexes/{self.index}/settings", INDEX_SETTINGS)
        )

    async def add_documents(self, docs: list[dict[str, Any]]) -> Any:
        return await self._request("POST", f"/indexes/{self.index}/documents", docs)

    async def delete_documents(self, ids: list[int]) -> Any:
        return await self._request("POST", f"/indexes/{self.index}/documents/delete-batch", ids)

    async def delete_all(self) -> Any:
        return await self._request("DELETE", f"/indexes/{self.index}/documents")

    async def federated_search(
        self, queries: list[dict[str, Any]], limit: int, offset: int
    ) -> dict[str, Any]:
        body = {
            "federation": {"limit": limit, "offset": offset},
            "queries": [{"indexUid": self.index, **q} for q in queries],
        }
        result: dict[str, Any] = await self._request("POST", "/multi-search", body)
        return result

    async def wait(self, task: Any, timeout_s: float = 30.0) -> None:
        """Block until an async Meilisearch task finishes (used by setup and tests)."""
        import asyncio

        uid = task.get("taskUid") if isinstance(task, dict) else None
        if uid is None:
            return
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout_s
        while loop.time() < deadline:
            info = await self._request("GET", f"/tasks/{uid}")
            if info["status"] in ("succeeded", "failed", "canceled"):
                if info["status"] != "succeeded":
                    raise RuntimeError(f"meilisearch task {uid} {info['status']}: {info}")
                return
            await asyncio.sleep(0.05)
        raise TimeoutError(f"meilisearch task {uid} did not finish")
