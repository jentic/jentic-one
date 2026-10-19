"""Suite-wide isolation of process-global logging state (#1778).

``configure_logging()`` and uvicorn's ``dictConfig`` swap the structlog
processor chain, point the root handler at the running test's captured stdout,
and set levels and filters on library loggers. Tests call both on purpose, so
every test starts from the logging state the previous one started with:

- ``_structlog_first_use_caching_off`` keeps structlog's first-use logger
  caching off for the whole session.
- ``_isolated_logging_config`` snapshots the structlog configuration and the
  stdlib logging tree before each test and restores whatever the test changed.

``tests/conftest.py`` registers this module as a plugin.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from typing import TYPE_CHECKING, Any, NamedTuple

import pytest
import structlog
from structlog import _config as structlog_config

if TYPE_CHECKING:
    from logging import _FilterType

# Structlog's global defaults, as set by ``configure()`` / ``reset_defaults()``.
_STRUCTLOG_FIELDS = (
    "is_configured",
    "default_processors",
    "default_context_class",
    "default_wrapper_class",
    "logger_factory",
)


class _LoggerState(NamedTuple):
    level: int
    handlers: list[logging.Handler]
    filters: list[_FilterType]
    propagate: bool
    disabled: bool


# A logger nobody has configured: what ``logging.getLogger()`` hands out.
_PRISTINE = _LoggerState(logging.NOTSET, [], [], True, False)


class _StructlogState(NamedTuple):
    fields: dict[str, Any]
    processors: list[Any]


class _LoggingState(NamedTuple):
    structlog: _StructlogState
    contextvars: dict[str, Any]
    loggers: dict[logging.Logger, _LoggerState]
    disable: int
    capture_warnings: bool


class _ConfigurationWithoutCaching(structlog_config._Configuration):
    """structlog's global defaults, with first-use logger caching pinned off.

    A module-level ``structlog.get_logger()`` proxy that caches on first use
    binds the processor chain current at that moment for the rest of the run;
    a later ``structlog.testing.capture_logs()`` patches the then-current chain
    and sees nothing. Reads always return ``False`` and writes are dropped, so
    every entry point (``configure`` positional or keyword, ``configure_once``,
    ``reset_defaults``, a ``from structlog import configure`` alias) is covered.
    """

    @property
    def cache_logger_on_first_use(self) -> bool:
        return False

    @cache_logger_on_first_use.setter
    def cache_logger_on_first_use(self, value: bool) -> None:
        pass


@pytest.fixture(autouse=True, scope="session")
def _structlog_first_use_caching_off() -> Iterator[None]:
    """Keep structlog's first-use logger caching off for the whole session.

    Production ``configure_logging()`` turns caching on; under test no teardown
    can undo a proxy that cached, so it never caches. A test that needs to see
    what ``configure_logging()`` asks for spies on ``structlog.configure``.
    """
    assert isinstance(structlog_config._CONFIG, structlog_config._Configuration), (
        "structlog moved its global config (structlog._config._CONFIG); "
        "update tests/harness/logging_isolation.py"
    )
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(structlog_config._CONFIG, "__class__", _ConfigurationWithoutCaching)
        yield


def _is_pytest_handler(handler: logging.Handler) -> bool:
    # pytest adds and removes its own capture handlers on the root logger around
    # every test phase; those are pytest's to manage, never part of a snapshot.
    return type(handler).__module__ == "_pytest.logging"


def _own_handlers(logger: logging.Logger) -> list[logging.Handler]:
    return [h for h in logger.handlers if not _is_pytest_handler(h)]


def _all_loggers() -> list[logging.Logger]:
    return [logging.getLogger()] + [
        lg for lg in logging.root.manager.loggerDict.values() if isinstance(lg, logging.Logger)
    ]


def _capturing_warnings() -> bool:
    # ``logging.captureWarnings(True)`` stashes the original ``showwarning``
    # here; it is ``None`` while warnings are not routed to ``py.warnings``.
    return getattr(logging, "_warnings_showwarning", None) is not None


def _snapshot() -> _LoggingState:
    config = structlog_config._CONFIG
    fields = {name: getattr(config, name) for name in _STRUCTLOG_FIELDS}
    return _LoggingState(
        structlog=_StructlogState(fields, list(config.default_processors)),
        contextvars=structlog.contextvars.get_contextvars(),
        loggers={
            lg: _LoggerState(lg.level, _own_handlers(lg), lg.filters[:], lg.propagate, lg.disabled)
            for lg in _all_loggers()
        },
        disable=logging.root.manager.disable,
        capture_warnings=_capturing_warnings(),
    )


def _restore_structlog(state: _StructlogState) -> None:
    # Reassign only what the test replaced, and refill the processor list in
    # place: loggers bound before the test hold that exact list object, and
    # ``capture_logs()`` patches whichever list is current.
    config = structlog_config._CONFIG
    for name, value in state.fields.items():
        if getattr(config, name) is not value:
            setattr(config, name, value)
    processors = state.fields["default_processors"]
    if list(processors) != state.processors:
        processors[:] = state.processors


def _restore_logger(
    logger: logging.Logger, state: _LoggerState
) -> tuple[bool, list[logging.Handler]]:
    """Restore one logger; return whether its level changed and the handlers it dropped."""
    level_changed = logger.level != state.level
    if level_changed:
        # Assigned directly: ``setLevel`` clears the manager's whole level cache
        # per call, so the caller clears it once for the whole tree instead.
        logger.level = state.level
    own = _own_handlers(logger)
    dropped = [h for h in own if h not in state.handlers]
    if own != state.handlers:
        logger.handlers[:] = [h for h in logger.handlers if _is_pytest_handler(h)] + state.handlers
    if logger.filters != state.filters:
        logger.filters[:] = state.filters
    if logger.propagate != state.propagate:
        logger.propagate = state.propagate
    if logger.disabled != state.disabled:
        logger.disabled = state.disabled
    return level_changed, dropped


def _restore_stdlib(state: _LoggingState) -> None:
    levels_changed = False
    dropped: list[logging.Handler] = []
    for lg in _all_loggers():
        # A logger first created during the test has no snapshot; it goes back
        # to the state ``logging.getLogger()`` creates it in.
        changed, gone = _restore_logger(lg, state.loggers.get(lg, _PRISTINE))
        levels_changed |= changed
        dropped += gone
    if logging.root.manager.disable != state.disable:
        logging.root.manager.disable = state.disable
        levels_changed = True
    if levels_changed:
        # The stdlib's own cache reset; it is not in the typeshed stubs.
        logging.root.manager._clear_cache()  # type: ignore[attr-defined]
    # Handlers the test attached (a file sink, a stream on the test's captured
    # stdout) are closed once nothing in the restored tree still uses them.
    if dropped:
        kept = {h for lg_state in state.loggers.values() for h in lg_state.handlers}
        for handler in dict.fromkeys(dropped):
            if handler not in kept:
                handler.close()
    if _capturing_warnings() != state.capture_warnings:
        logging.captureWarnings(state.capture_warnings)


@pytest.fixture(autouse=True)
def _isolated_logging_config() -> Iterator[None]:
    """Give every test the structlog and stdlib logging state it started with.

    Restored on teardown: structlog's configuration and bound contextvars;
    level, handlers, filters, ``propagate`` and ``disabled`` of every stdlib
    logger (including ones the test created); ``logging.disable``; and
    ``logging.captureWarnings``. Only what changed is written back, so an
    untouched test costs one snapshot and one comparison pass. pytest's own
    capture handlers on the root logger are left to pytest.
    """
    state = _snapshot()
    try:
        yield
    finally:
        _restore_stdlib(state)
        _restore_structlog(state.structlog)
        if structlog.contextvars.get_contextvars() != state.contextvars:
            structlog.contextvars.clear_contextvars()
            structlog.contextvars.bind_contextvars(**state.contextvars)
