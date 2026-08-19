import json
import logging
import re
import sys
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any

correlation_id_context: ContextVar[str | None] = ContextVar("correlation_id", default=None)
workflow_id_context: ContextVar[str | None] = ContextVar("workflow_id", default=None)
request_id_context: ContextVar[str | None] = ContextVar("request_id", default=None)

_SENSITIVE = re.compile(
    r"(?i)(authorization|api[_-]?key|access[_-]?token|password|secret|customer[_-]?(?:email|name|phone))"
    r"\s*[=:]\s*([^\s,;]+)"
)
_EMAIL = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)
_BEARER = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+")


def redact(value: str) -> str:
    value = _SENSITIVE.sub(lambda match: f"{match.group(1)}=[REDACTED]", value)
    value = _EMAIL.sub("[REDACTED_EMAIL]", value)
    return _BEARER.sub("Bearer [REDACTED]", value)


class JsonFormatter(logging.Formatter):
    """Small JSON formatter with stable fields for log aggregation."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": redact(record.getMessage()),
        }
        correlation_id = correlation_id_context.get()
        if correlation_id:
            payload["correlation_id"] = correlation_id
        if request_id := request_id_context.get():
            payload["request_id"] = request_id
        if workflow_id := workflow_id_context.get():
            payload["workflow_id"] = workflow_id
        if record.exc_info:
            payload["exception"] = redact(self.formatException(record.exc_info))
        return json.dumps(payload, default=str)


def configure_logging(level: str) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)

    logging.getLogger("uvicorn.access").propagate = True
