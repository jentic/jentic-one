"""Semver comparison for the app-release update check.

A dependency-free port of the CLI's release-comparison rules in
``cli/internal/update/version.go`` (``parseSemver`` / ``compareSemver`` /
``NewerAvailable``), kept intentionally identical so the backend's
``update_available`` verdict matches ``jenticctl update``'s.

Only the clean ``[v]MAJOR.MINOR.PATCH`` shape the release tags use is parsed;
branch names, ``dev``, SHAs, and pre-releases (``v1.0.0-rc1``) are treated as
unparseable. As in the CLI: an unparseable *installed* version conservatively
reports an update is available (unreleased/source builds are offered the latest
release); an empty/unparseable *latest* reports no update (nothing to move to).
"""

from __future__ import annotations


def _parse_semver(value: str) -> tuple[int, int, int] | None:
    """Parse ``[v]MAJOR.MINOR.PATCH`` into a tuple, or ``None`` if not clean."""
    value = value.strip()
    if value.startswith("v"):
        value = value[1:]
    parts = value.split(".")
    if len(parts) != 3:
        return None
    nums: list[int] = []
    for part in parts:
        # ASCII-only guard: str.isdigit() is True for chars like "²" that then
        # raise in int(); this also matches Go's strconv.Atoi (no fullwidth digits).
        if not (part.isascii() and part.isdigit()):
            return None
        nums.append(int(part))
    return nums[0], nums[1], nums[2]


def is_update_available(installed: str, latest: str | None) -> bool:
    """Report whether ``latest`` is a newer release than ``installed``.

    Mirrors ``update.NewerAvailable`` in the Go CLI exactly:

    - ``latest`` empty/unparseable => ``False`` (nothing sensible to update to).
    - ``installed`` unparseable => ``True`` (offer the latest to source builds).
    - otherwise a strict major/minor/patch comparison.
    """
    if latest is None:
        return False
    latest_v = _parse_semver(latest)
    if latest_v is None:
        return False
    installed_v = _parse_semver(installed)
    if installed_v is None:
        return True
    return latest_v > installed_v


def normalize_release(raw: str) -> str | None:
    """Return a clean ``[v]X.Y.Z`` as bare ``X.Y.Z``, or ``None`` if not clean.

    Used to validate + canonicalise a reported release version before storing it.
    """
    parsed = _parse_semver(raw)
    if parsed is None:
        return None
    return f"{parsed[0]}.{parsed[1]}.{parsed[2]}"
