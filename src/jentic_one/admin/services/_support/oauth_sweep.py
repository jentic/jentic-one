"""Sweep a user's opaque OAuth credentials inside the caller's transaction.

Shared by ``AuthService`` (password change and operator password reset) and
``UserService`` (disable and delete) so every remediation or account-ending
transition revokes the user's opaque access tokens, refresh tokens, and
unredeemed authorization codes in one place. Agent-actor grant-channel tokens
are left untouched and stay governed by their grant and owner-active status.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import structlog

from jentic_one.admin.repos import (
    AuthorizationCodeRepository,
    TokenLineageRepository,
)
from jentic_one.shared.models import ActorType

logger = structlog.get_logger(__name__)


@dataclass(frozen=True, slots=True)
class OAuthSweepCounts:
    """Row counts swept across the three user OAuth credential tables."""

    access: int
    refresh: int
    codes: int


async def sweep_user_oauth_credentials(session: Any, user_id: str) -> OAuthSweepCounts:
    """Revoke every USER-actor access/refresh token and consume every unredeemed code."""
    sweep = await TokenLineageRepository.revoke_for_actor(
        session, user_id, actor_type=ActorType.USER.value
    )
    codes = await AuthorizationCodeRepository.consume_for_user(session, user_id)
    logger.info(
        "user_oauth_credentials_swept",
        user_id=user_id,
        swept_access=sweep.access,
        swept_refresh=sweep.refresh,
        swept_codes=codes,
    )
    return OAuthSweepCounts(access=sweep.access, refresh=sweep.refresh, codes=codes)
