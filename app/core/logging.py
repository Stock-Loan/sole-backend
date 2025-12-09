import json
import logging
import logging.config
from datetime import datetime, timezone
from typing import Optional

from app.core.context import get_request_id, get_tenant_id
from app.core.settings import settings


class RequestContextFilter(logging.Filter):
    """Inject tenant/request ids into log records."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.tenant_id = get_tenant_id()
        record.request_id = get_request_id()
        record.stream = getattr(record, "stream", "transactional")
        return True


class JsonFormatter(logging.Formatter):
    """Lightweight JSON formatter to keep logs structured."""

    def __init__(self, stream_label: str = "transactional") -> None:
        super().__init__()
        self.stream_label = stream_label

    def format(self, record: logging.LogRecord) -> str:  # noqa: D401 - concise
        payload = {
            "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "stream": self.stream_label,
            "tenant_id": getattr(record, "tenant_id", "-"),
            "request_id": getattr(record, "request_id", "-"),
        }
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_logging(level: Optional[str] = None) -> None:
    log_level = (level or settings.log_level).upper()
    logging.config.dictConfig(
        {
            "version": 1,
            "disable_existing_loggers": False,
            "filters": {
                "request_context": {"()": RequestContextFilter},
            },
            "formatters": {
                "json": {"()": JsonFormatter, "stream_label": "transactional"},
                "audit_json": {"()": JsonFormatter, "stream_label": "audit"},
            },
            "handlers": {
                "default": {
                    "class": "logging.StreamHandler",
                    "level": log_level,
                    "formatter": "json",
                    "filters": ["request_context"],
                    "stream": "ext://sys.stdout",
                },
                "audit": {
                    "class": "logging.StreamHandler",
                    "level": log_level,
                    "formatter": "audit_json",
                    "filters": ["request_context"],
                    "stream": "ext://sys.stdout",
                },
            },
            "loggers": {
                "": {"handlers": ["default"], "level": log_level, "propagate": False},
                "app.audit": {"handlers": ["audit"], "level": log_level, "propagate": False},
                "uvicorn": {"handlers": ["default"], "level": log_level, "propagate": False},
                "uvicorn.error": {"handlers": ["default"], "level": log_level, "propagate": False},
                "uvicorn.access": {"handlers": ["default"], "level": log_level, "propagate": False},
            },
        }
    )
    logging.getLogger(__name__).info(
        "Logging configured for environment=%s tenancy_mode=%s",
        settings.environment,
        settings.tenancy_mode,
    )


def get_audit_logger() -> logging.Logger:
    return logging.getLogger("app.audit")
