import logging
import sys
from packages.shared.config import get_settings


def setup_logging(level: str | None = None) -> None:
    settings = get_settings()
    log_level = level or settings.LOG_LEVEL

    logging.basicConfig(
        level=getattr(logging, log_level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] [%(name)s] %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
        force=True,
    )


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
