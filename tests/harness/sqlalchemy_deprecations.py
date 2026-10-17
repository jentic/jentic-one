"""Suite-wide guard: a SQLAlchemy deprecation warning fails its test, even when caught.

The ``error::sqlalchemy.exc.SADeprecationWarning`` entry in ``pyproject.toml``
turns the warning into an exception at the call site, and a broad
``except Exception`` on the path (``emit_event_best_effort`` and similar
best-effort handlers, or a server error swallowed into a 500) can eat that
exception. Each deprecated call walks the stack on every invocation, so a
swallowed one is a silent per-request CPU cost (#1820).

``_no_sqlalchemy_deprecations`` therefore *records* the warning for the duration
of each test instead of raising it — a recorded warning cannot be caught by
``except`` — and fails the test at teardown, naming the call site. Warnings of
any other category are re-emitted so pytest's own capture and summary keep
them. Session- and module-scoped fixture setup runs outside the recorder, where
the ini ``error`` filter still applies.

``tests/conftest.py`` registers this module as a plugin.
"""

from __future__ import annotations

import warnings
from collections import Counter
from collections.abc import Iterator
from contextlib import contextmanager

import pytest
from sqlalchemy.exc import SADeprecationWarning


@contextmanager
def fail_on_sqlalchemy_deprecations() -> Iterator[None]:
    """Record SQLAlchemy deprecation warnings in the block and fail if any occurred.

    Raises ``AssertionError`` on exit listing each distinct ``file:line: message``
    once, in first-seen order, with a ``(x N)`` count when it fired more than once
    (a deprecated call in a loop records one warning per iteration).
    """
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always", SADeprecationWarning)
        yield
    hits: Counter[str] = Counter()
    for record in caught:
        if issubclass(record.category, SADeprecationWarning):
            hits[f"{record.filename}:{record.lineno}: {record.message}"] += 1
        else:
            warnings.warn_explicit(
                record.message,
                record.category,
                record.filename,
                record.lineno,
                source=record.source,
            )
    if hits:
        raise AssertionError(
            "SQLAlchemy deprecation warning(s) raised during the test:\n"
            + "\n".join(hit if n == 1 else f"{hit} (x {n})" for hit, n in hits.items())
        )


@pytest.fixture(autouse=True)
def _no_sqlalchemy_deprecations() -> Iterator[None]:
    """Fail the test if it triggered a SQLAlchemy deprecation, swallowed or not."""
    with fail_on_sqlalchemy_deprecations():
        yield
