from __future__ import annotations

import socket
from functools import lru_cache
from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    env: str = "dev"
    log_level: str = "INFO"
    log_json: bool = True
    worker_id: str = socket.gethostname()

    # core internal API (over WireGuard)
    core_url: str = "http://core:8000"
    internal_api_token: SecretStr

    # MTProto — one session, used only by the lazy resolver and for streaming
    tg_api_id: int
    tg_api_hash: SecretStr
    sessions_dir: Path = Path("/data/sessions")
    session_enc_key: SecretStr
    device_model: str = "tmusic-indexer"

    # web-preview crawler (ADR-002)
    crawl_min_delay_s: float = 1.5
    crawl_max_delay_s: float = 4.0
    crawl_max_pages: int = 500
    crawl_claim_limit: int = 3
    # Comma-separated http(s) proxies. One burned IP is cheaper than one burned
    # account, but rotating is cheaper still.
    crawl_proxies: str = ""

    claim_interval_s: float = 10.0
    # A FloodWait longer than this marks the account "limited" (needs attention).
    limited_after_s: int = 900

    # streaming
    bot_token: SecretStr
    tg_api_base: str = "https://api.telegram.org"
    stream_signing_keys: SecretStr
    stream_host: str = "0.0.0.0"  # noqa: S104 — listens behind nginx inside the container network
    stream_port: int = 8080
    downloads_max: int = 8
    message_cache_s: int = 1800

    @property
    def proxy_pool(self) -> tuple[str, ...]:
        return tuple(p.strip() for p in self.crawl_proxies.split(",") if p.strip())

    @property
    def signing_keys(self) -> list[bytes]:
        keys = [k.strip().encode() for k in self.stream_signing_keys.get_secret_value().split(",")]
        return [k for k in keys if k]


@lru_cache
def get_settings() -> Settings:
    return Settings()
