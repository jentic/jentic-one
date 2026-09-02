"""Unit tests verifying XSS protection in the consent page template."""

from __future__ import annotations

import html as html_mod

from jentic_one.auth.web.routers.authorize import (
    _CHECK_SVG,
    _CONSENT_PAGE_TEMPLATE,
    _FONTS_URL,
)


def _render_consent(
    app_name: str = "TestApp",
    app_description: str = "A test app",
    user_email: str = "user@example.com",
) -> str:
    return _CONSENT_PAGE_TEMPLATE.format(
        app_name=html_mod.escape(app_name),
        app_description=html_mod.escape(app_description),
        user_email=html_mod.escape(user_email),
        permission_items="<li>View your agents</li>",
        consent_token=html_mod.escape("token123"),
        fonts_url=_FONTS_URL,
        check_svg=_CHECK_SVG,
    )


def test_app_name_xss_escaped() -> None:
    xss = "<script>alert('xss')</script>"
    result = _render_consent(app_name=xss)
    assert "<script>" not in result
    assert "&lt;script&gt;" in result


def test_app_description_xss_escaped() -> None:
    xss = '<img src=x onerror="alert(1)">'
    result = _render_consent(app_description=xss)
    assert 'onerror="alert(1)"' not in result
    assert "&lt;img" in result


def test_user_email_xss_escaped() -> None:
    xss = '"><script>steal(document.cookie)</script>'
    result = _render_consent(user_email=xss)
    assert "<script>steal" not in result
    assert "&lt;script&gt;" in result


def test_safe_values_render_normally() -> None:
    result = _render_consent(
        app_name="My App",
        app_description="Does things",
        user_email="test@example.com",
    )
    assert "My App" in result
    assert "Does things" in result
    assert "test@example.com" in result
