"""``__version__`` is the single source of truth, sourced from package metadata.

This guards the drift class the release procedure fixed: ``__version__`` (what
``/health`` and the OpenAPI metadata serve) must equal the installed
distribution version, not a hand-edited literal that can fall out of sync with
``pyproject.toml`` / the Helm charts / the git tags.
"""

from importlib.metadata import version

from jentic_one import __version__


def test_version_matches_package_metadata() -> None:
    assert __version__ == version("jentic-one")


def test_version_is_not_the_uninstalled_fallback() -> None:
    # If this fails, jentic-one isn't installed in the test env (editable install
    # expected) — __version__ would be the raw-source-tree fallback.
    assert __version__ != "0.0.0+unknown"
