"""
Logging Setup

Configures Python's standard logging for the master data generator so every
record includes a timestamp, the log level, the originating module and the
message.

The format and level are driven by :class:`~master_data.config.LoggingConfig`,
keeping configuration centralized rather than embedding format strings in each
call site.
"""

from __future__ import annotations

import logging
import sys
from typing import TextIO

from .config import LoggingConfig

_ROOT_LOGGER_NAME = "keemeds.master_data"


def setup_logging(
    config: LoggingConfig,
    *,
    stream: TextIO = sys.stdout,
) -> logging.Logger:
    """
    Apply the logging configuration to the master data root logger.

    Parameters
    ----------
    config:
        The centralized logging configuration.
    stream:
        Output stream (defaults to stdout).

    Returns
    -------
    logging.Logger
        The configured root logger for the master data package.
    """

    root = logging.getLogger(_ROOT_LOGGER_NAME)
    root.setLevel(logging.getLevelName(config.level.upper()))

    handler = logging.StreamHandler(stream)
    handler.setFormatter(
        logging.Formatter(
            fmt=config.format,
            datefmt=config.date_format,
            style=config.style,
        )
    )
    root.handlers = [handler]
    root.propagate = False

    return root


def get_logger(name: str) -> logging.Logger:
    """
    Return a child logger of the master data root logger.
    """

    return logging.getLogger(f"{_ROOT_LOGGER_NAME}.{name}")
