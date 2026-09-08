"""Shared link-builder utilities for consistent absolute URLs in API responses."""

from __future__ import annotations

from starlette.requests import Request

from jentic_one.shared.config import AuthConfig


def build_link(request: Request, path: str) -> str:
    """Return an absolute URL for the given path rooted at the request's base URL."""
    return str(request.base_url).rstrip("/") + "/" + path.lstrip("/")


def deployment_base_url(config: AuthConfig, request: Request) -> str:
    """Deployment base URL: the configured canonical URL, else the request's.

    Single home for the "``auth.canonical_base_url`` wins, request base URL is
    the fallback" rule, shared by the OAuth discovery document (issuer) and the
    agent-discovery documents (llms.txt links) so the two can never disagree.
    """
    if config.canonical_base_url:
        return config.canonical_base_url.rstrip("/")
    return str(request.base_url).rstrip("/")
