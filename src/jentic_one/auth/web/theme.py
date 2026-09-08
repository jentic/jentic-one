"""Shared visual theme for the server-rendered auth pages.

The interactive OAuth flow's browser pages (``/login``, ``/oauth/consent``,
the no-agents empty state, and — in a follow-up — the approval-pending page)
are hand-rolled inline HTML templates, deliberately free of any templating
library (see the module docstrings in ``routers/authorize.py`` and
``routers/local_login.py`` for the JSON-seam security pattern). This module
gives them one platform-consistent look without touching that posture:

- :data:`AUTH_PAGE_CSS` — a single stylesheet, packaged as
  ``assets/auth.css``, inlined into each page's ``<style>`` block. Its token
  values are hand-derived from the SPA palette (``ui/src/index.css``) and
  drift-guarded by ``tests/unit/auth/web/test_theme_tokens.py``. Dark/light
  follows ``prefers-color-scheme`` using the same palette values.
- :data:`JENTIC_LOGO_SVG` — the Jentic logo as inline SVG. The path
  geometry is copied verbatim from ``ui/src/shared/ui/Logo.tsx``
  (``LOGO_ICON_PATHS`` — the single source of truth for the glyph — plus
  ``LOGO_TEXT_PATHS`` for the wordmark), same drift guard.

SECURITY INVARIANTS — both exports are **static, build-time constants**:

- No request-, row-, or config-derived data ever enters them, so they add
  no injection surface: the templates ``.format`` them in exactly like any
  other trusted literal, and every *dynamic* value keeps going through
  ``html.escape`` / the JSON config seam as before.
- They reference no external assets (no fonts, images, or ``@import``), so
  the pages gain no new network fetches.
- Being static, the inlined ``<style>`` block stays hashable — a future
  strict CSP can allow it with a stable ``style-src 'sha256-…'`` source
  instead of per-response nonces.
"""

from __future__ import annotations

import importlib.resources


def _load_css() -> str:
    """Read the packaged stylesheet once at import time.

    ``assets/auth.css`` ships inside the wheel via the normal package
    include (``packages = ["src/jentic_one"]``), so this works identically
    in a source checkout and a wheel install — unlike the SPA bundle, whose
    absence (API-only deployments) must not affect these pages.
    """
    resource = importlib.resources.files("jentic_one.auth.web") / "assets" / "auth.css"
    return resource.read_text(encoding="utf-8")


#: The shared stylesheet, inlined into every auth page's ``<style>`` block.
AUTH_PAGE_CSS: str = _load_css()

#: Glyph geometry — byte-identical to ``LOGO_ICON_PATHS`` in
#: ``ui/src/shared/ui/Logo.tsx`` (which is itself copied from
#: ``@jentic/frontend-ui``'s ``SvgLogo`` so the mark is pixel-identical to
#: the webapp). Drift-guarded; do not edit by hand.
_LOGO_ICON_PATHS: tuple[str, ...] = (
    "M94.04,26.63c-2.28-.32-4.91,2.9-7.17,8.67-2.61,6.68-5.26,13.38-8.06,19.87l-.03.06-1.57,3.69h0s-.05.13-.05.13c-1.16,2.74-5,11.78-6.24,14.68-.82,1.94-1.7,3.8-2.65,5.59,0,0,0,0,0,.01h0s-.01.02-.02.03c-.65,1.24-1.37,2.39-2.15,3.4-5.36,6.93-12.54,8.78-17.79,9.07-7.89.13-18.36-3.07-22.73-14.86-2.07-5.36-2.58-11.83-2.41-18.05h36.34s-1.47,3.37-1.47,3.37l-.3.69-3.06.08c-2.66.07-5.24.04-7.93.16-2.38.11-4.62-.08-6.49,2.04-.74.84-1.23,1.86-1.49,2.95-.35,1.51-.15,2.63.59,4.45,1.52,3.38,4.63,5.4,8.33,5.4.11,0,.23,0,.34,0,2.09-.07,4.28-.8,6.08-2.14,1.59-1.19,2.78-3.02,3.71-5.05l2.33-5.85,14.87-35.07h0s0-.01,0-.01c.38-.96.76-1.92,1.14-2.88,0-.01,0-.02.01-.03.68-1.66,1.45-1.67,1.46-1.67,0,0,2.45-.61,7.67-.61,4.68,0,7.92.42,8.06.51.13.09.66.16.65,1.39Z",
    "M104.8,58.91h-.03l-1.96-6.2-.02-.06-2.06-6.51h0s-3.83-12.16-3.83-12.16c-.72-2.15-1.34-4.39-3.11-4.01-3.1.68-4.9,5.86-6.39,9.94-.36.97-.73,2.03-.93,3.16l.47.58,12.4,15.26.14.17h-16.88l-2.38-.02-.02.05-1.41,3.24-.3.7h27.65l-1.33-4.14ZM100.71,61.78h0s.01,0,.01,0h-.01Z",
)

#: Wordmark geometry — byte-identical to ``LOGO_TEXT_PATHS`` in
#: ``ui/src/shared/ui/Logo.tsx``. Drift-guarded; do not edit by hand.
_LOGO_TEXT_PATHS: tuple[str, ...] = (
    "M249.95,49.65c-1.6,0-2.79-.42-3.55-1.26-.77-.84-1.15-1.9-1.15-3.18s.38-2.39,1.15-3.23c.77-.84,1.95-1.26,3.55-1.26s2.79.42,3.55,1.26c.77.84,1.15,1.91,1.15,3.23s-.38,2.34-1.15,3.18c-.77.84-1.95,1.26-3.55,1.26Z",
    "M177.07,53.63c-2.15-1.41-4.8-2.11-7.93-2.11-2.39,0-4.51.42-6.38,1.26-1.87.84-3.44,1.97-4.7,3.39-1.26,1.42-2.23,3.04-2.88,4.83-.66,1.8-.99,3.68-.99,5.63v1.07c0,1.89.33,3.73.99,5.53.66,1.8,1.63,3.43,2.91,4.89,1.28,1.46,2.87,2.62,4.78,3.47,1.9.85,4.1,1.28,6.6,1.28s4.63-.44,6.52-1.31c1.89-.87,3.45-2.07,4.7-3.61,1.25-1.53,2.08-3.28,2.51-5.23h-7.85c-.36.89-1.03,1.64-2.03,2.24-1,.61-2.28.91-3.85.91-1.71,0-3.1-.36-4.17-1.07-1.07-.71-1.85-1.72-2.35-3.02-.28-.72-.47-1.52-.6-2.38h21.37v-2.88c0-2.67-.57-5.14-1.71-7.4-1.14-2.26-2.79-4.09-4.94-5.5ZM162.99,62.44c.53-1.3,1.32-2.28,2.35-2.94,1.03-.66,2.3-.99,3.79-.99s2.68.33,3.66.99c.98.66,1.71,1.6,2.19,2.83.26.66.44,1.4.56,2.22h-13.14c.13-.77.33-1.48.59-2.11Z",
    "M205.34,51.62h-.37c-2.24,0-4.15.5-5.71,1.5-1.57,1-2.73,2.49-3.5,4.49-.27.71-.49,1.48-.67,2.31v-7.39h-6.84v29.27h8.6v-17.04c0-1.64.49-2.95,1.47-3.95.98-1,2.27-1.5,3.87-1.5s2.8.49,3.71,1.47c.91.98,1.36,2.25,1.36,3.82v17.2h8.6v-16.72c0-4.52-.9-7.9-2.7-10.12-1.8-2.22-4.41-3.34-7.82-3.34Z",
    "M230.93,44.63h-7.96l-.02,7.9h-4.47v6.3h4.46l-.02,11.54c0,2.96.43,5.3,1.28,7.02.85,1.73,2.19,2.96,4.01,3.71,1.82.75,4.2,1.12,7.16,1.12h4.11v-7.26h-4.33c-1.35,0-2.4-.36-3.12-1.09-.73-.73-1.09-1.79-1.09-3.18v-11.86h8.55v-6.3h-8.55v-7.9Z",
    "M255.18,52.53 246.58,52.53 246.58,52.53 244.75,52.53 242.72,58.86 246.58,58.86 246.58,81.8 255.18,81.8 255.18,58.89 255.18,58.89 255.18,52.53 255.18,52.53 255.18,52.53",  # noqa: E501
    "M279.79,70.64c-.14.96-.45,1.81-.91,2.54-.46.73-1.09,1.3-1.9,1.71-.8.41-1.77.61-2.91.61-1.53,0-2.77-.35-3.71-1.04-.94-.69-1.63-1.67-2.06-2.94-.43-1.26-.64-2.68-.64-4.25,0-1.67.23-3.14.69-4.41.46-1.26,1.17-2.25,2.11-2.96.94-.71,2.13-1.07,3.55-1.07,1.67,0,2.97.45,3.9,1.36.93.91,1.46,2.04,1.6,3.39h8.38c-.14-2.38-.82-4.49-2.03-6.3-1.21-1.82-2.83-3.23-4.86-4.25-2.03-1.01-4.36-1.52-7-1.52-2.42,0-4.57.41-6.44,1.23-1.87.82-3.44,1.94-4.7,3.36-1.26,1.42-2.22,3.05-2.86,4.89-.64,1.83-.96,3.75-.96,5.74v1.01c0,1.92.31,3.78.93,5.58.62,1.8,1.56,3.41,2.8,4.83,1.25,1.42,2.79,2.56,4.65,3.42,1.85.85,4.06,1.28,6.62,1.28s4.98-.52,7.05-1.55c2.06-1.03,3.72-2.47,4.97-4.3,1.25-1.83,1.92-3.96,2.03-6.38h-8.33Z",
    "M131.94,42.2l-2.54,7.93h11.36l.18.13c.32.23.43.45.43.83,0,2.45-.03,4.94-.06,7.14-.04,3.04-.09,6.48-.03,9.76v.08c-.42,4.55-4.35,7-7.83,7-2.79,0-5.14-1.52-6.28-4.08-1.23-2.75-.34-5.18.58-6.99l.4-.78h-8.57l-.14.34c-1.35,3.39-1.32,7.17.1,10.64,1.53,3.76,4.56,6.73,8.31,8.15,1.82.69,3.71,1.04,5.61,1.04,4.1,0,8.07-1.61,11.18-4.52,3.14-2.94,5.04-6.9,5.34-11.15v-.02s0-25.5,0-25.5h-18.01Z",
)

# Same aspect ratio as JenticLogo's FULL_VIEWBOX (265.8 x 66.8), sized a
# touch larger than the SPA navbar (77x24) for the page header.
_LOGO_VIEWBOX = "21.5 25.4 265.8 66.8"
_LOGO_WIDTH = 103
_LOGO_HEIGHT = 26


def _build_logo_svg() -> str:
    paths = "".join(
        f'<path d="{d}" fill="currentColor" stroke="none"/>'
        for d in (*_LOGO_ICON_PATHS, *_LOGO_TEXT_PATHS)
    )
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{_LOGO_WIDTH}"'
        f' height="{_LOGO_HEIGHT}" viewBox="{_LOGO_VIEWBOX}" fill="currentColor"'
        f' aria-hidden="true" focusable="false">{paths}</svg>'
    )


#: The Jentic wordmark as inline SVG (``fill="currentColor"`` — it follows
#: the surrounding ``.logo`` color token). Templates pair it with the
#: ``<span class="brand-badge">One</span>`` product badge, mirroring the
#: SPA's ``JenticLogo`` component.
JENTIC_LOGO_SVG: str = _build_logo_svg()

#: The full brand header block shared by every auth page.
LOGO_BLOCK_HTML: str = (
    f'<div class="logo">{JENTIC_LOGO_SVG}<span class="brand-badge">One</span></div>'
)
