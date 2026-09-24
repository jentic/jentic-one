"""Unit tests for the app-release semver comparison.

These mirror the CLI's ``update.NewerAvailable`` / ``parseSemver`` rules in
``cli/internal/update/version.go`` so the backend's ``update_available`` verdict
matches ``jenticctl update``.
"""

from __future__ import annotations

import pytest

from jentic_one.shared.version_compare import is_update_available, normalize_release


@pytest.mark.parametrize(
    ("installed", "latest", "expected"),
    [
        ("0.25.0", "0.26.0", True),
        ("0.25.0", "v0.26.0", True),  # leading v tolerated
        ("v0.25.0", "0.26.0", True),
        ("0.26.0", "0.26.0", False),  # equal -> no update
        ("0.26.1", "0.26.0", False),  # installed newer -> no update
        ("1.0.0", "0.999.999", False),  # major dominates
        ("0.9.0", "0.10.0", True),  # numeric, not lexical, minor compare
        # Unparseable installed (dev/branch/sha) -> offer the latest release.
        ("dev", "0.26.0", True),
        ("main", "v1.2.3", True),
        ("", "0.1.0", True),
        # Unparseable / missing latest -> nothing sensible to update to.
        ("0.26.0", None, False),
        ("0.26.0", "", False),
        ("0.26.0", "v1.0.0-rc1", False),  # pre-release is not a clean release
        ("0.26.0", "not-a-version", False),
        # Both unparseable -> latest unparseable dominates (False).
        ("dev", "dev", False),
    ],
)
def test_is_update_available(installed: str, latest: str | None, expected: bool) -> None:
    assert is_update_available(installed, latest) is expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("v0.26.0", "0.26.0"),
        ("0.26.0", "0.26.0"),
        ("  v1.2.3  ", "1.2.3"),
        ("1.2.3", "1.2.3"),
        ("v1.0.0-rc1", None),
        ("1.2", None),
        ("1.2.3.4", None),
        ("main", None),
        ("", None),
        ("v1.2.-3", None),
        # Unicode "digit" chars satisfy str.isdigit() but crash int(); the
        # ASCII guard must reject them cleanly (regression: was a 500 on write).
        ("1.2.\u00b2", None),  # superscript two
        ("\uff11.2.3", None),  # fullwidth one (Go's strconv.Atoi rejects it too)
    ],
)
def test_normalize_release(raw: str, expected: str | None) -> None:
    assert normalize_release(raw) == expected
