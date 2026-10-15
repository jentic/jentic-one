"""Caller-IP derivation for pre-auth rate limiters.

Lives in ``shared`` so every surface that throttles anonymous traffic (the auth
surface's OAuth doors, the admin ``/auth/login``) derives the caller identity
the same way without a cross-surface import.
"""

from __future__ import annotations

from fastapi import Request


def client_ip(request: Request, trusted_proxies: frozenset[str]) -> str:
    """Extract the real client IP, honoring XFF only from trusted reverse proxies."""
    socket_ip = request.client.host if request.client else "unknown"
    if not trusted_proxies or socket_ip not in trusted_proxies:
        return socket_ip
    forwarded = request.headers.get("x-forwarded-for")
    if not forwarded:
        return socket_ip
    hops = [h.strip() for h in forwarded.split(",")]
    for hop in reversed(hops):
        if hop not in trusted_proxies:
            return hop
    return socket_ip


__all__ = ["client_ip"]
