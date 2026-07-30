"""Shared link-builder utilities for consistent absolute URLs in API responses."""

from __future__ import annotations

from starlette.requests import Request

from jentic_one.shared.config import AppConfig, effective_auth_base_url


def build_link(request: Request, path: str) -> str:
    """Return an absolute URL for the given path rooted at the request's base URL."""
    return str(request.base_url).rstrip("/") + "/" + path.lstrip("/")


def deployment_base_url(config: AppConfig, request: Request) -> str:
    """Deployment base URL for request-scoped links.

    Resolution order: ``auth.canonical_base_url`` → ``server.public_base_url``
    → the incoming request's origin. Single home for the rule shared by the
    OAuth discovery document (issuer) and the agent-discovery documents
    (llms.txt links) so the two can never disagree. The request-origin fallback
    means a deployment on any port works with zero configuration; behind a
    gateway the operator sets one of the two config knobs.
    """
    return effective_auth_base_url(config) or str(request.base_url).rstrip("/")
