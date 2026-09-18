"""Shared logging helper (standard library only).

Q6 operational minimum: every service should emit structured, queryable logs so
that "something broke" is diagnosable after delivery, not a black box.
"""

from __future__ import annotations

import logging


def get_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
    return logger
