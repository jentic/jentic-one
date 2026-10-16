"""Suite-wide fixtures."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from typing import Any

import pytest
import structlog

from jentic_one.shared.dns_cache import clear_resolution_cache

# Stdlib loggers that ``configure_logging()`` and uvicorn's ``dictConfig`` set
# levels, handlers, or filters on. They are created up front so the logging
# isolation fixture snapshots them even when a test is the first to touch them.
_APP_CONFIGURED_LOGGERS = (
    "aiosqlite",
    "hpack",
    "httpcore",
    "httpx",
    "uvicorn",
    "uvicorn.access",
    "uvicorn.error",
)

_LoggerState = tuple[int, list[logging.Handler], list[Any], bool, bool]


@pytest.fixture(autouse=True)
def _isolated_resolution_cache() -> Iterator[None]:
    """Start and end every test with an empty process-wide DNS answer cache.

    Tests swap ``socket.getaddrinfo`` for canned answers; a cached answer from
    one test must never satisfy a lookup in the next.
    """
    clear_resolution_cache()
    yield
    clear_resolution_cache()


def _is_pytest_handler(handler: logging.Handler) -> bool:
    # pytest adds and removes its own capture handlers on the root logger around
    # every test phase; those are pytest's to manage, never part of a snapshot.
    return type(handler).__module__ == "_pytest.logging"


def _snapshot_loggers() -> dict[logging.Logger, _LoggerState]:
    for name in _APP_CONFIGURED_LOGGERS:
        logging.getLogger(name)
    loggers = [logging.getLogger()] + [
        lg for lg in logging.root.manager.loggerDict.values() if isinstance(lg, logging.Logger)
    ]
    return {
        lg: (
            lg.level,
            [h for h in lg.handlers if not _is_pytest_handler(h)],
            lg.filters[:],
            lg.propagate,
            lg.disabled,
        )
        for lg in loggers
    }


def _restore_loggers(snapshot: dict[logging.Logger, _LoggerState]) -> None:
    for lg, (level, handlers, filters, propagate, disabled) in snapshot.items():
        lg.setLevel(level)
        lg.handlers[:] = [h for h in lg.handlers if _is_pytest_handler(h)] + handlers
        lg.filters[:] = filters
        lg.propagate = propagate
        lg.disabled = disabled


@pytest.fixture(autouse=True)
def _isolated_logging_config() -> Iterator[None]:
    """Give every test the structlog and stdlib logging state it started with.

    ``configure_logging()`` (and uvicorn's ``dictConfig``) swap the structlog
    processor chain, point the root handler at the test's captured stdout, and
    set levels and filters on library loggers. Without a restore, a later
    ``structlog.testing.capture_logs()`` patches a processor list that module
    loggers no longer use and sees nothing.

    First-use caching is also kept off inside tests: a module-level
    ``structlog.get_logger()`` proxy that caches while a test's chain is active
    keeps that chain for the rest of the run, which no teardown can undo.
    """
    real_configure = structlog.configure

    def _configure_without_caching(**kwargs: Any) -> None:
        if kwargs.get("cache_logger_on_first_use"):
            kwargs["cache_logger_on_first_use"] = False
        real_configure(**kwargs)

    was_configured = structlog.is_configured()
    structlog_config = structlog.get_config()
    loggers = _snapshot_loggers()
    structlog.configure = _configure_without_caching  # type: ignore[assignment]
    try:
        yield
    finally:
        structlog.configure = real_configure
        _restore_loggers(loggers)
        if was_configured:
            structlog.configure(**structlog_config)
        else:
            structlog.reset_defaults()
