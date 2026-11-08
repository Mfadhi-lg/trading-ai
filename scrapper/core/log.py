"""Structured operational logging shared by pipeline workers."""
import json
import logging
from datetime import datetime, timezone

_CONFIGURED = False


def get_logger(name: str = "scrapper") -> logging.Logger:
    global _CONFIGURED
    if not _CONFIGURED:
        logging.basicConfig(
            level=logging.INFO,
            format="%(message)s",
        )
        _CONFIGURED = True
    return logging.getLogger(name)


def log_event(logger: logging.Logger, event: str, /, **fields: object) -> None:
    """Emit one JSON object; callers must not put sensitive payloads in fields."""
    logger.info(json.dumps({
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "event": event,
        **fields,
    }, sort_keys=True, default=str))
