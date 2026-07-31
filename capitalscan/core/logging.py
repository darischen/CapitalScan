"""Structured logging with JSON lines output."""

import json
import logging
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path


def setup_logging(
    log_dir: str = "logs",
    job_name: str = "default",
    level: int = logging.INFO,
) -> logging.Logger:
    """Configure rotating JSON lines logger."""
    Path(log_dir).mkdir(exist_ok=True)

    logger = logging.getLogger(job_name)
    logger.setLevel(level)
    logger.handlers.clear()

    log_file = Path(log_dir) / f"{job_name}.jsonl"

    class JSONFormatter(logging.Formatter):
        def format(self, record: logging.LogRecord) -> str:
            log_data = {
                "timestamp": datetime.utcnow().isoformat(),
                "level": record.levelname,
                "message": record.getMessage(),
                "job": job_name,
            }
            if record.exc_info:
                log_data["exception"] = self.formatException(record.exc_info)
            return json.dumps(log_data)

    file_handler = RotatingFileHandler(
        log_file,
        maxBytes=100 * 1024 * 1024,
        backupCount=10,
    )
    file_handler.setFormatter(JSONFormatter())
    logger.addHandler(file_handler)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(
        logging.Formatter("[%(levelname)s] %(message)s")
    )
    logger.addHandler(console_handler)

    return logger
