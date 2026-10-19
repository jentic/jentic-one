"""Pins the suite-wide SQLAlchemy deprecation guard in ``tests/harness/sqlalchemy_deprecations.py``.

A deprecated SQLAlchemy call must fail its test even when a broad ``except``
on the path would swallow the exception the ini ``error`` filter raises (#1820),
while warnings of any other category keep reaching pytest's own capture.
"""

from __future__ import annotations

import contextlib
import warnings

import pytest
from sqlalchemy.exc import SADeprecationWarning

from tests.harness.sqlalchemy_deprecations import fail_on_sqlalchemy_deprecations


def _best_effort_deprecated_call() -> None:
    """A deprecated call behind the broad handler a best-effort helper uses."""
    with contextlib.suppress(Exception):
        warnings.warn("Result.tuples() is deprecated", SADeprecationWarning, stacklevel=1)


def test_swallowed_sqlalchemy_deprecation_fails_the_block() -> None:
    """A deprecation behind ``except Exception`` still fails, naming the call site."""
    with (
        pytest.raises(AssertionError, match=r"test_sqlalchemy_deprecations\.py:\d+: Result"),
        fail_on_sqlalchemy_deprecations(),
    ):
        _best_effort_deprecated_call()


def test_repeated_deprecation_is_listed_once_with_a_count() -> None:
    """A deprecated call in a loop lists its call site once, with the hit count."""
    with pytest.raises(AssertionError) as excinfo, fail_on_sqlalchemy_deprecations():
        for _ in range(3):
            _best_effort_deprecated_call()
    lines = str(excinfo.value).splitlines()[1:]
    assert len(lines) == 1
    assert lines[0].endswith("Result.tuples() is deprecated (x 3)")


def test_other_warnings_pass_through_without_failing() -> None:
    """Non-SQLAlchemy warnings are re-emitted to the outer capture, not treated as failures."""
    with pytest.warns(UserWarning, match="kept"), fail_on_sqlalchemy_deprecations():
        warnings.warn("kept", UserWarning, stacklevel=1)


def test_clean_block_passes() -> None:
    """A block that triggers no deprecation exits normally."""
    with fail_on_sqlalchemy_deprecations():
        pass
