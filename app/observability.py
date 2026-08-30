from __future__ import annotations

import json
import logging
from datetime import datetime, timezone


class JsonEventFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "event": getattr(record, "event", record.getMessage()),
        }
        for key in ("request_id", "task_id", "status", "method", "path", "elapsed_ms", "detail"):
            value = getattr(record, key, None)
            if value is not None and value != "":
                payload[key] = value
        return json.dumps(payload, ensure_ascii=False)


def configure_event_logger() -> logging.Logger:
    logger = logging.getLogger("docflow.events")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(JsonEventFormatter())
        logger.addHandler(handler)
    return logger


def log_event(logger: logging.Logger, event: str, level: int = logging.INFO, **fields) -> None:
    logger.log(level, event, extra={"event": event, **fields})
