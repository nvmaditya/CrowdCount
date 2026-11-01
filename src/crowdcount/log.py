"""Logging setup shared by every command."""

from __future__ import annotations

import logging
import os
import sys

_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"
_DATEFMT = "%H:%M:%S"

# Third-party loggers that are chatty at INFO and drown out our own messages.
_QUIET = {
    "uvicorn.access": logging.WARNING,
    "uvicorn.error": logging.INFO,
    "ultralytics": logging.WARNING,
    "matplotlib": logging.WARNING,
    "PIL": logging.WARNING,
}


def setup_logging(verbose: bool = False) -> None:
    """Configure root logging. ``$CROWDCOUNT_LOG_LEVEL`` overrides the level."""
    level_name = os.environ.get("CROWDCOUNT_LOG_LEVEL", "DEBUG" if verbose else "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter(_FORMAT, _DATEFMT))
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)
    for name, quiet_level in _QUIET.items():
        logging.getLogger(name).setLevel(logging.DEBUG if verbose else quiet_level)
