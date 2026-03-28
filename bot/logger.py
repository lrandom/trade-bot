"""
bot/logger.py
-------------
Loguru configuration for the gold trading bot.

Call ``setup_logger()`` once at startup (before any other imports that use
the logger).  After that, anywhere in the codebase:

    from loguru import logger
    logger.info("...")

Sinks
-----
* **stderr** — human-readable coloured output, level controlled by LOG_LEVEL env var.
* **logs/bot.log** — rotating file sink, always at DEBUG, 10 MB per file,
  7-day retention.  The ``logs/`` directory is created if it does not exist.
"""

import os
import sys

from loguru import logger

from bot.config import settings


def setup_logger() -> "logger":  # type: ignore[name-defined]  # loguru typing quirk
    """Configure loguru sinks and return the logger instance."""

    # Remove the default sink so we control everything ourselves.
    logger.remove()

    # -----------------------------------------------------------------
    # Stderr sink — coloured, level from env
    # -----------------------------------------------------------------
    logger.add(
        sys.stderr,
        level=settings.log_level.upper(),
        colorize=True,
        format=(
            "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
            "<level>{level: <8}</level> | "
            "<cyan>{name}</cyan>:<cyan>{line}</cyan> | "
            "{message}"
        ),
        backtrace=True,
        diagnose=False,  # set True locally for rich tracebacks; off in prod
    )

    # -----------------------------------------------------------------
    # Rotating file sink — always DEBUG, 10 MB per file, 7-day retention
    # -----------------------------------------------------------------
    log_dir = "logs"
    os.makedirs(log_dir, exist_ok=True)

    logger.add(
        os.path.join(log_dir, "bot.log"),
        level="DEBUG",
        rotation="10 MB",
        retention="7 days",
        compression="gz",
        encoding="utf-8",
        format=(
            "{time:YYYY-MM-DD HH:mm:ss} | "
            "{level: <8} | "
            "{name}:{line} | "
            "{message}"
        ),
        backtrace=True,
        diagnose=False,
        enqueue=True,   # thread-safe async-friendly writes
    )

    logger.debug("Logger initialised (level={}, log_dir={})", settings.log_level, log_dir)
    return logger
