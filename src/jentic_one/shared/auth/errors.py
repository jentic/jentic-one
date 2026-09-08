"""Typed errors for the shared auth edges."""

from __future__ import annotations


class TokenValidationError(Exception):
    """A presented credential failed validation at an auth edge.

    The message is a static snake_case reason (``unknown_token``,
    ``jwt_actor_type_missing``, …) used for server-side logging only. Web
    layers map this to a deliberately uniform 401 and MUST NOT echo the reason
    to the client — a distinguishable response is an oracle for forgers.
    """
