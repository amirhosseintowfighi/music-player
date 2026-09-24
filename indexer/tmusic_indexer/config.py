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
    # The pause between page requests, jittered. Telegram answers 429 when this is
    # too small for the current IP; the crawler then backs off and releases the
    # channel, so the cost of being slightly too fast is a pause, not a ban.
    crawl_min_delay_s: float = 0.8
    crawl_max_delay_s: float = 2.0
    crawl_max_pages: int = 500
    crawl_claim_limit: int = 3
    # How many channels one edge walks at the same time. Together with the delays
    # above this is the whole throughput knob: 3 channels at ~1.4s a page is roughly
    # 40 messages a second. Lower it (or raise the delays) the moment the log starts
    # showing crawl.blocked.
    crawl_parallel: int = 3
    # Comma-separated http(s) proxies. One burned IP is cheaper than one burned
    # account, but rotating is cheaper still.
    crawl_proxies: str = ""

    # MTProto fallback (ADR-002 §6): only used for channels with no web preview, and
    # only when the core's `mtproto_fallback` flag hands one out. 50 pages of 100 is a
    # long-but-finite run, so one big channel cannot hold the account all day.
    max_crawl_pages_mtproto: int = 50

    # One Telegram search per this many seconds, at most. Discovery is never urgent.
    search_interval_s: float = 300.0

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
