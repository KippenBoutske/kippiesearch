"""Gestructureerde logs met redaction van secrets."""

from __future__ import annotations

import logging
import re
import sys
from typing import Iterable

import structlog


_SECRET_KEYS = ("password", "passwd", "token", "secret", "authorization", "api_token")
_REDACT = re.compile(
    r"(?i)(password|passwd|token|secret|authorization|api[_-]?token)\s*[:=]\s*\S+"
)


class _RedactFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = _REDACT.sub(r"\1=***", record.msg)
        if record.args and isinstance(record.args, dict):
            record.args = {
                k: ("***" if any(s in str(k).lower() for s in _SECRET_KEYS) else v)
                for k, v in record.args.items()
            }
        return True


def _drop_secrets(_, __, event_dict: dict) -> dict:
    for key in list(event_dict):
        if any(s in str(key).lower() for s in _SECRET_KEYS):
            event_dict[key] = "***"
    return event_dict


def configure_logging(level: str = "info") -> None:
    numeric = getattr(logging, level.upper(), logging.INFO)
    handler = logging.StreamHandler(sys.stdout)
    handler.addFilter(_RedactFilter())
    logging.basicConfig(level=numeric, handlers=[handler], format="%(message)s")
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.stdlib.add_log_level,
            structlog.stdlib.add_logger_name,
            structlog.processors.TimeStamper(fmt="iso"),
            _drop_secrets,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(numeric),
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str = "kippiesearch"):
    return structlog.get_logger(name)


def secret_values(settings) -> Iterable[str]:
    for value in (
        settings.api_token,
        settings.nas_smb_password,
        settings.itslearning_password,
    ):
        if value:
            yield value
