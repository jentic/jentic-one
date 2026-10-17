"""User-deleted hooks (extension seam).

An out-of-tree extension may keep its own per-user state (in its own tables,
possibly in another database) that should be cleaned up when an account is
deleted, without this package importing the extension. A hook is
``async fn(ctx, user_id, identity) -> None``: ``user_id`` is the deleted account
and ``identity`` is the caller who deleted it (for the extension's own audit
records).

Hooks run after the delete has committed, in registration order. The delete is
already final by then, so a hook that raises is logged and skipped: it never
turns a completed delete into an error, and the remaining hooks still run. OSS
registers no hooks, so with no extension present a delete behaves exactly as
without this seam. Mirrors ``register_access_filter_provider``.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

import structlog

from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.context import Context

logger = structlog.get_logger(__name__)

UserDeletedHook = Callable[[Context, str, Identity], Awaitable[None]]

_USER_DELETED_HOOKS: list[UserDeletedHook] = []


def register_user_deleted_hook(hook: UserDeletedHook) -> None:
    """Register a hook to run after a user is deleted (idempotent).

    Call at import time from a registering package.
    """
    if hook not in _USER_DELETED_HOOKS:
        _USER_DELETED_HOOKS.append(hook)


async def run_user_deleted_hooks(ctx: Context, user_id: str, *, identity: Identity) -> None:
    """Run every registered hook for a committed user delete; log and skip failures."""
    for hook in tuple(_USER_DELETED_HOOKS):
        try:
            await hook(ctx, user_id, identity)
        except Exception:
            logger.warning(
                "user_deleted_hook_failed",
                user_id=user_id,
                hook=getattr(hook, "__qualname__", repr(hook)),
                exc_info=True,
            )
