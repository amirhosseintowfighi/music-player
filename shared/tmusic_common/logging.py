"""Structured JSON logging with a trace id on every line.

stdlib loggers (uvicorn, aiogram, telethon) are routed through the same processors so
every line in production is one JSON object.
"""

from __future__ import annotations

import logging
import sys
import uuid
from contextvars import ContextVar

import structlog
from structlog.typing import EventDict, Processor, WrappedLogger

trace_id_var: ContextVar[str] = ContextVar("trace_id", default="-")

TRACE_HEADER = "X-Request-ID"


def new_trace_id() -> str:
    return uuid.uuid4().hex


def configure_logging(
    service: str, level: str = "INFO", json_logs: bool = True
) -> None:
    def add_static(_: WrappedLogger, __: str, event: EventDict) -> EventDict:
        event.setdefault("service", service)
        event.setdefault("trace_id", trace_id_var.get())
        return event

    shared: list[Processor] = [
        structlog.contextvars.merge_contextvars,
        add_static,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.format_exc_info,
    ]
    renderer: Processor = (
        structlog.processors.JSONRenderer(ensure_ascii=False)
        if json_logs
        else structlog.dev.ConsoleRenderer()
    )

    structlog.configure(
        processors=[*shared, structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        structlog.stdlib.ProcessorFormatter(
            foreign_pre_chain=shared,
            processors=[
                structlog.stdlib.ProcessorFormatter.remove_processors_meta,
                renderer,
            ],
        )
    )
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level.upper())
    for noisy in ("uvicorn.access", "telethon.network"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    logger: structlog.stdlib.BoundLogger = structlog.get_logger(name)
    return logger
