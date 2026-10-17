"""User-deleted hooks (extension seam).

An out-of-tree extension may keep its own per-user state (in its own tables,
possibly in another database) that should be cleaned up when an account is
deleted, without this package importing the extension. A hook is
``async fn(ctx, user_id, identity) -> None``: ``user_id`` is the deleted account
and ``identity`` is the caller who deleted it (for the extension's own audit
records).

Hooks run after the delete has committed, in registration order. The delete is
already final by then, so a hook that raises an ``Exception`` is logged at
``error``, counted on ``admin.user_deleted_hook.failures`` (``hook``
attribute) and skipped: it never turns a completed delete into an error, and the
remaining hooks still run. A refused delete runs no hooks. OSS registers no
hooks, so with no extension present a delete behaves exactly as without this
seam. Mirrors ``register_access_filter_provider``.

Delivery is best effort, not durable: hooks run once, in-process, after the
commit. Nothing records or retries a hook that fails, and a cancellation (a
``BaseException`` such as ``asyncio.CancelledError``) or a process exit during
the run skips the hooks still to come. A hook whose cleanup must eventually
happen needs its own reconciliation (e.g. a periodic or manual sweep).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

import structlog

from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.context import Context
from jentic_one.shared.metrics import get_meter

logger = structlog.get_logger(__name__)

_meter = get_meter("admin")

user_deleted_hook_failures_counter = _meter.create_counter(
    "admin.user_deleted_hook.failures",
    description="User-deleted hooks that raised, by hook",
)

UserDeletedHook = Callable[[Context, str, Identity], Awaitable[None]]

_USER_DELETED_HOOKS: list[UserDeletedHook] = []


def _hook_name(hook: UserDeletedHook) -> str:
    module = getattr(hook, "__module__", None)
    qualname = getattr(hook, "__qualname__", None)
    if qualname is None:
        return repr(hook)
    return f"{module}.{qualname}" if module else qualname


def register_user_deleted_hook(hook: UserDeletedHook) -> None:
    """Register a hook to run after a user is deleted (idempotent).

    Call at import time from a registering package.
    """
    if hook not in _USER_DELETED_HOOKS:
        _USER_DELETED_HOOKS.append(hook)


async def run_user_deleted_hooks(ctx: Context, user_id: str, *, identity: Identity) -> None:
    """Run every registered hook for a committed user delete; log, count and skip failures."""
    for hook in tuple(_USER_DELETED_HOOKS):
        try:
            await hook(ctx, user_id, identity)
        except Exception:
            name = _hook_name(hook)
            user_deleted_hook_failures_counter.add(1, {"hook": name})
            logger.error(
                "user_deleted_hook_failed",
                user_id=user_id,
                hook=name,
                exc_info=True,
            )
