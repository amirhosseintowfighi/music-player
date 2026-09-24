"""Runtime configuration. Every value comes from the environment; see /.env.example."""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    env: Literal["dev", "test", "prod"] = "dev"
    log_level: str = "INFO"
    log_json: bool = True

    # ── storage ──
    database_url: str = Field(description="postgresql+asyncpg://… (through PgBouncer in prod)")
    migration_database_url: str | None = None
    db_pool_size: int = 10
    db_max_overflow: int = 10
    db_behind_pgbouncer: bool = True
    redis_url: str = "redis://localhost:6379/0"
    meili_url: str = "http://localhost:7700"
    meili_api_key: SecretStr = SecretStr("")
    meili_index: str = "tracks"

    # ── telegram ──
    bot_token: SecretStr
    bot_username: str
    webapp_url: str
    # Bot API base. In the hybrid topology this is the edge egress proxy (ADR-0002).
    tg_api_base: str = "https://api.telegram.org"
    webhook_secret: SecretStr
    webhook_url: str | None = None

    # ── auth ──
    jwt_private_key: SecretStr = Field(description="Ed25519 private key, PEM")
    jwt_public_key: str = Field(description="Ed25519 public key, PEM")
    jwt_issuer: str = "tmusic"
    access_ttl_s: int = 15 * 60
    refresh_ttl_s: int = 30 * 86400
    auth_max_age_s: int = 86400

    # ── streaming ──
    # Comma-separated; the first key signs, all keys verify (rotation).
    stream_signing_keys: SecretStr
    stream_ticket_ttl_s: int = 300
    bot_api_max_download: int = 20 * 1024 * 1024

    # ── internal (core <-> edge) ──
    internal_api_token: SecretStr
    # Edge app address over WireGuard, used to ask the edge to upload a track (phase 5).
    edge_internal_url: str = ""
    # Public base URL of this API; payment gateways call back to it.
    public_api_url: str = ""

    # ── artist pages ──
    # Spotify, for artist photos only (client-credentials, no user data involved).
    # Empty means the enrichment job does nothing and artists simply have no photo.
    spotify_client_id: str = ""
    spotify_client_secret: SecretStr = SecretStr("")

    # ── payment gateway credentials (phase 6) ──
    zarinpal_merchant_id: SecretStr = SecretStr("")
    idpay_api_key: SecretStr = SecretStr("")
    nextpay_api_key: SecretStr = SecretStr("")
    # Group (or channel) where card-to-card receipts are reviewed. 0 disables the flow.
    payments_admin_chat_id: int = 0
    # How long one worker owns a channel it is crawling, and how many it may hold.
    indexer_lease_s: int = 300
    indexer_claim_limit: int = 20

    # ── http ──
    cors_origins: list[str] = []
    rate_limit_user_per_min: int = 240
    rate_limit_ip_per_min: int = 600

    @field_validator("database_url", "migration_database_url")
    @classmethod
    def _asyncpg_only(cls, v: str | None) -> str | None:
        if not v:
            return None
        if not v.startswith("postgresql+asyncpg://"):
            raise ValueError("must use the postgresql+asyncpg:// driver")
        return v

    @field_validator("jwt_public_key", "jwt_private_key", mode="before")
    @classmethod
    def _pem_newlines(cls, v: object) -> object:
        # .env files keep PEM on one line with literal "\n" separators.
        if isinstance(v, str):
            return v.replace("\\n", "\n")
        return v

    @property
    def signing_keys(self) -> list[bytes]:
        keys = [k.strip().encode() for k in self.stream_signing_keys.get_secret_value().split(",")]
        return [k for k in keys if k]

    def provider_secret(self, provider_code: str) -> str:
        """Gateway credential for a provider; secrets never live in the database."""
        mapping = {
            "zarinpal": self.zarinpal_merchant_id,
            "idpay": self.idpay_api_key,
            "nextpay": self.nextpay_api_key,
        }
        secret = mapping.get(provider_code)
        return secret.get_secret_value() if secret else ""

    @property
    def bot_id(self) -> int:
        return int(self.bot_token.get_secret_value().split(":", 1)[0])


@lru_cache
def get_settings() -> Settings:
    return Settings()
