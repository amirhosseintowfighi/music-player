"""Domain errors. The API maps them to HTTP responses with a stable ``code`` that the
clients translate (no user-facing text is produced here)."""

from __future__ import annotations

from typing import Any


class AppError(Exception):
    status = 400
    code = "bad_request"

    def __init__(self, message: str = "", **details: Any) -> None:
        super().__init__(message or self.code)
        self.message = message or self.code
        self.details = details


class NotFound(AppError):
    status = 404
    code = "not_found"


class Unauthorized(AppError):
    status = 401
    code = "unauthorized"


class Forbidden(AppError):
    status = 403
    code = "forbidden"


class Conflict(AppError):
    status = 409
    code = "conflict"


class InvalidInput(AppError):
    status = 422
    code = "invalid_input"


class LimitReached(AppError):
    """A plan limit was hit; ``details`` carries ``limit`` and ``kind`` for the upsell UI."""

    status = 402
    code = "plan_limit"


class RateLimited(AppError):
    status = 429
    code = "rate_limited"


class Unavailable(AppError):
    status = 503
    code = "unavailable"
