"""Logging setup for Chronicle Keeper sync server."""

from __future__ import annotations

import logging
import os
import sys

_LOG_FMT = "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s"
_DATE_FMT = "%H:%M:%S"


def is_debug() -> bool:
    return os.environ.get("CHRONICLE_DEBUG", "").lower() in ("1", "true", "yes")


def setup_logging() -> None:
    debug = is_debug()
    level = logging.DEBUG if debug else logging.WARNING

    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter(_LOG_FMT, datefmt=_DATE_FMT))

    root = logging.getLogger("ck")
    root.setLevel(level)
    root.handlers = [handler]
    root.propagate = False

    logging.getLogger("uvicorn.access").setLevel(logging.INFO if debug else logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"ck.{name}")
