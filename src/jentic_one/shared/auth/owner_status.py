"""Shared logging for agents refused because their owner is not active.

An owned agent follows its owner's account status: while the owner is disabled
or deleted, every agent credential channel refuses it. Each channel logs the
refusal through :func:`log_agent_owner_inactive` so the event and its guidance
read the same everywhere.
"""

from __future__ import annotations

import structlog

logger = structlog.get_logger(__name__)

_ACTIONABLE_STEP = (
    "This agent's owner is disabled or deleted, so the agent cannot "
    "authenticate. Re-enable the owner, or transfer the agent to an "
    "active owner."
)


def log_agent_owner_inactive(agent_id: str, owner_id: str | None) -> None:
    """Log the refusal of an active agent whose owner is disabled or deleted.

    INFO, not WARNING: a client still presenting a credential for such an
    agent lands here on every call, an expected client-side 401 rather than a
    server fault (the same level as ``retired_key_unresolved``).
    """
    logger.info(
        "agent_owner_inactive",
        agent_id=agent_id,
        owner_id=owner_id,
        actionable_step=_ACTIONABLE_STEP,
    )
