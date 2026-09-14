"""Drift guards for the shared auth-page theme (``auth/web/theme.py``).

The auth pages' stylesheet (``auth/web/assets/auth.css``) hand-derives its
palette from the SPA token source (``ui/src/index.css``), and the inline
logo hand-copies its path geometry from ``ui/src/shared/ui/Logo.tsx``. No
build step links them, so these tests are the sync mechanism: they fail the
moment either side moves.

They also pin the security properties that make the inlined ``<style>``
block safe and future-strict-CSP-ready: the CSS is a static constant with
no external fetches and no byte capable of closing the ``<style>`` element.

The palette/logo comparisons need the ``ui/`` sources, which exist in a
source checkout but not in a wheel install — they skip (never pass
vacuously) when the files are absent. CI always runs from the checkout.
"""

from __future__ import annotations

import importlib.resources
import re
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from jentic_one.auth.web import theme

_REPO_ROOT = Path(__file__).resolve().parents[4]
_UI_INDEX_CSS = _REPO_ROOT / "ui" / "src" / "index.css"
_UI_LOGO_TSX = _REPO_ROOT / "ui" / "src" / "shared" / "ui" / "Logo.tsx"

#: SPA tokens the auth theme mirrors (the subset the dark-only mappings
#: consume). Every name listed here must carry the exact same HSL triplet in
#: both files.
_SHARED_TOKENS = (
    "--primary-100",
    "--primary-300",
    "--primary-700",
    "--primary-850",
    "--primary-900",
    "--primary-950",
    "--accent-green",
)

_TRIPLET_RE = r"^\s*{name}:\s*([\d.]+ [\d.]+% [\d.]+%);"


def _extract_triplet(css: str, name: str, *, source: str) -> str:
    matches: list[str] = re.findall(
        _TRIPLET_RE.format(name=re.escape(name)), css, flags=re.MULTILINE
    )
    assert matches, f"token {name} not found as an HSL triplet in {source}"
    return matches[0]


@pytest.mark.skipif(not _UI_INDEX_CSS.is_file(), reason="ui/ sources absent (wheel install)")
def test_palette_matches_spa_token_source() -> None:
    """Every shared token carries the SPA's exact HSL triplet."""
    spa_css = _UI_INDEX_CSS.read_text(encoding="utf-8")
    for name in _SHARED_TOKENS:
        spa_value = _extract_triplet(spa_css, name, source="ui/src/index.css")
        auth_value = _extract_triplet(theme.AUTH_PAGE_CSS, name, source="auth.css")
        assert auth_value == spa_value, (
            f"{name} drifted: auth.css has '{auth_value}', ui/src/index.css has "
            f"'{spa_value}' — ui/src/index.css is the source of truth, update "
            "src/jentic_one/auth/web/assets/auth.css to match"
        )


@pytest.mark.skipif(not _UI_LOGO_TSX.is_file(), reason="ui/ sources absent (wheel install)")
def test_logo_geometry_matches_spa_component() -> None:
    """The inline SVG uses the exact path data of the SPA's ``JenticLogo``.

    ``LOGO_ICON_PATHS`` in ``Logo.tsx`` is the single source of truth for
    the glyph; the wordmark paths and viewBox ride along. Comparison is
    verbatim string equality, per path.
    """
    tsx = _UI_LOGO_TSX.read_text(encoding="utf-8")
    tsx_paths = re.findall(r"'(M[^']+)'", tsx)
    assert len(tsx_paths) >= 9, "expected icon + wordmark paths in Logo.tsx"

    theme_paths = (*theme._LOGO_ICON_PATHS, *theme._LOGO_TEXT_PATHS)
    # Exact multiset equality, both directions: a path edited in Logo.tsx
    # fails because the stale copy here no longer appears there, and a path
    # ADDED to Logo.tsx fails because it is missing here — a subset check
    # would let the logo gain an element the auth pages never render.
    assert sorted(theme_paths) == sorted(tsx_paths), (
        "logo geometry drifted between theme.py and ui/src/shared/ui/Logo.tsx "
        "— Logo.tsx is the source of truth"
    )
    for d in theme_paths:
        assert d in theme.JENTIC_LOGO_SVG

    full_viewbox = re.search(r"FULL_VIEWBOX = '([^']+)'", tsx)
    assert full_viewbox is not None
    assert f'viewBox="{full_viewbox.group(1)}"' in theme.JENTIC_LOGO_SVG


def test_css_is_inline_safe_and_fetch_free() -> None:
    """The stylesheet must stay a safe, self-contained ``<style>`` payload.

    - no ``<`` byte: nothing in it can close the ``<style>`` element, so
      inlining it via ``str.format`` adds no markup-injection surface;
    - no external fetches (``@import`` / ``url(http…)``) — the only
      ``url(…)`` allowed is the inline data-URI check mark;
    - non-empty and brace-balanced as a cheap parse sanity check.
    """
    css = theme.AUTH_PAGE_CSS
    assert css.strip()
    assert "<" not in css
    assert "@import" not in css
    for m in re.finditer(r"url\(\s*[\"']?([^\"')]+)", css):
        assert m.group(1).startswith("data:image/svg+xml"), f"external fetch in CSS: {m.group(0)}"
    assert css.count("{") == css.count("}")


def test_missing_packaged_css_degrades_to_unstyled_not_import_error() -> None:
    """A broken install must not take the auth surface down over styling.

    ``_load_css`` runs at import time; if it raised on a missing/unreadable
    ``assets/auth.css``, importing ``theme`` — and with it every auth
    router — would fail, turning a lost visual asset into a platform-wide
    startup failure. It must degrade to an empty stylesheet instead (the
    pages render unstyled but every flow keeps working), mirroring the
    missing-SPA-bundle posture of ``shared/web/static.py``.
    """
    real = importlib.resources.files
    missing = MagicMock()
    missing.__truediv__ = MagicMock(return_value=missing)
    missing.read_text = MagicMock(side_effect=FileNotFoundError("auth.css gone"))

    def _files(anchor: str) -> object:
        if anchor == "jentic_one.auth.web":
            return missing
        return real(anchor)

    with patch.object(importlib.resources, "files", side_effect=_files):
        assert theme._load_css() == ""


def test_theme_is_dark_only_like_the_spa() -> None:
    """The theme mirrors the SPA's dark-only posture (Manuel, 2026-09-08).

    The SPA (``ui/src/index.css``) ships exactly one palette — dark — with
    no ``prefers-color-scheme`` or ``data-theme`` switch, and the auth pages
    must match: one unconditional token block, page surface on
    ``--primary-950``, card on ``--primary-900``.
    """
    css = theme.AUTH_PAGE_CSS
    assert "prefers-color-scheme: dark" not in css
    assert "prefers-color-scheme: light" not in css
    assert "color-scheme: dark;" in css
    assert re.search(r"^\s*--page-bg:\s*var\(--primary-950\);", css, flags=re.MULTILINE)
    assert re.search(r"^\s*--card-bg:\s*var\(--primary-900\);", css, flags=re.MULTILINE)


def test_logo_svg_is_static_and_self_contained() -> None:
    """The logo block is a pure-SVG constant: no scripts, handlers, or refs."""
    svg = theme.JENTIC_LOGO_SVG
    assert svg.startswith("<svg ") and svg.endswith("</svg>")
    lowered = svg.lower()
    for forbidden in ("<script", "href", "xlink", "onload", "onerror", "http"):
        # "http" only allowed inside the xmlns namespace declaration.
        occurrences = lowered.count(forbidden)
        if forbidden == "http":
            assert occurrences == lowered.count("http://www.w3.org/2000/svg")
        else:
            assert occurrences == 0, f"unexpected '{forbidden}' in logo SVG"
    assert 'aria-hidden="true"' in svg
    assert theme.JENTIC_LOGO_SVG in theme.LOGO_BLOCK_HTML
    assert ">One</span>" in theme.LOGO_BLOCK_HTML
