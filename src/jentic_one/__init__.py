"""Jentic One.

``__version__`` is sourced from the installed package metadata (a single source
of truth: ``pyproject.toml``'s ``version``, stamped into the distribution at
build time). This kills the historical drift where ``__init__.py`` carried a
hand-edited literal that fell out of sync with ``pyproject.toml`` / the Helm
charts / the git tags — and it is what ``/health`` and the OpenAPI metadata
serve, so it must never lie.

The ``PackageNotFoundError`` fallback covers running from a source tree that was
never installed (e.g. some ad-hoc script invocations); editable installs and
built wheels both resolve via metadata.
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("jentic-one")
except PackageNotFoundError:  # pragma: no cover - not installed (raw source tree)
    __version__ = "0.0.0+unknown"

__all__ = ["__version__"]
