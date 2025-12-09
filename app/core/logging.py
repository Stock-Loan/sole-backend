import logging
from typing import Optional

from app.core.settings import settings


def configure_logging(level: Optional[str] = None) -> None:
    log_level = (level or "INFO").upper()
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )
    logging.getLogger("uvicorn").setLevel(log_level)
    logging.getLogger("uvicorn.error").setLevel(log_level)
    logging.getLogger("uvicorn.access").setLevel(log_level)
    logging.getLogger(__name__).info("Logging configured for environment=%s", settings.environment)
