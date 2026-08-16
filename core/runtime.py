import logging
import os
from logging.handlers import RotatingFileHandler

from core.logging_config import RedactingFormatter


def configure_logging(service_name: str) -> logging.Logger:
    """Configure redacted, process-specific console and file logs."""

    os.makedirs("logs", exist_ok=True)
    formatter = RedactingFormatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    )

    file_handler = RotatingFileHandler(
        f"logs/{service_name}.log",
        maxBytes=10 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    file_handler.setLevel(logging.INFO)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    console_handler.setLevel(logging.INFO)

    logging.basicConfig(
        level=logging.INFO,
        handlers=[file_handler, console_handler],
        force=True,
    )
    return logging.getLogger(f"buyerly.{service_name}")
