"""User-deleted hooks (extension seam).

An out-of-tree extension may keep its own per-user state (in its own tables,
possibly in another database) that should be cleaned up when an account is
deleted, without this package importing the extension. A hook is
``async fn(ctx, user_id, identity) -> None``: ``user_id`` is the deleted account
and ``identity`` is the caller who deleted it (for the extension's own audit
records).

Hooks run after the delete has committed, in registration order, inside the
``DELETE /users/{id}`` request: the response waits for them. Each hook gets
``USER_DELETED_HOOK_TIMEOUT_S`` seconds; one that runs longer is cancelled. The
delete is already final by then, so a hook that raises an ``Exception`` or times
out is logged at ``error``, counted on ``admin.user_deleted_hook.failures``
(``hook`` and ``reason`` attributes) and skipped: it never turns a completed
delete into an error, and the remaining hooks still run. A refused delete runs
no hooks. OSS registers no hooks, so with no extension present a delete behaves
exactly as without this seam. Mirrors ``register_access_filter_provider``.

Hooks must be idempotent. Deleting an account that is already deleted succeeds
again and runs every hook again, which is also how an operator re-runs a hook
that failed.

Delivery is best effort, not durable: hooks run once, in-process, after the
commit. Nothing records or automatically retries a hook that fails, and a cancellation (a
``BaseException`` such as ``asyncio.CancelledError``) or a process exit during
the run skips the hooks still to come. A hook whose cleanup must eventually
happen needs its own reconciliation (e.g. a periodic or manual sweep).
"""

from __future__ import annotations

import asyncio
import functools
from collections.abc import Awaitable, Callable

import structlog

from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.context import Context
from jentic_one.shared.metrics import get_meter

logger = structlog.get_logger(__name__)

_meter = get_meter("admin")

user_deleted_hook_failures_counter = _meter.create_counter(
    "admin.user_deleted_hook.failures",
    description="User-deleted hooks that raised or timed out, by hook and reason",
)

UserDeletedHook = Callable[[Context, str, Identity], Awaitable[None]]

# Per-hook budget. The delete has committed before any hook runs, so cutting a
# slow hook off costs the delete nothing and keeps the request from hanging on
# an unreachable dependency.
USER_DELETED_HOOK_TIMEOUT_S = 10.0

_USER_DELETED_HOOKS: list[UserDeletedHook] = []


def _hook_name(hook: object) -> str:
    """A name for ``hook`` that is stable across restarts (it is a metric attribute).

    A ``functools.partial`` is named after the function it wraps; any other
    callable without a ``__qualname__`` (a callable instance) after its type.
    ``repr`` is never used because it embeds a memory address.
    """
    if isinstance(hook, functools.partial):
        return _hook_name(hook.func)
    qualname = getattr(hook, "__qualname__", None)
    if not isinstance(qualname, str):
        return f"{type(hook).__module__}.{type(hook).__qualname__}"
    module = getattr(hook, "__module__", None)
    return f"{module}.{qualname}" if module else qualname


def register_user_deleted_hook(hook: UserDeletedHook) -> None:
    """Register a hook to run after a user is deleted (idempotent).

    Call at import time from a registering package.
    """
    if hook not in _USER_DELETED_HOOKS:
        _USER_DELETED_HOOKS.append(hook)


async def run_user_deleted_hooks(ctx: Context, user_id: str, *, identity: Identity) -> None:
    """Run every registered hook for a committed user delete; log, count and skip failures.

    Each hook is bounded by ``USER_DELETED_HOOK_TIMEOUT_S``; a timeout is a
    failure like any other ``Exception``. Only an expired budget counts as
    ``reason="timeout"``: a ``TimeoutError`` the hook raises itself (e.g. from
    its own driver or HTTP client) is an ``error`` and keeps its traceback.
    """
    for hook in tuple(_USER_DELETED_HOOKS):
        budget = asyncio.timeout(USER_DELETED_HOOK_TIMEOUT_S)
        try:
            async with budget:
                await hook(ctx, user_id, identity)
        except TimeoutError:
            if not budget.expired():
                _log_hook_error(hook, user_id)
                continue
            name = _hook_name(hook)
            user_deleted_hook_failures_counter.add(1, {"hook": name, "reason": "timeout"})
            logger.error(
                "user_deleted_hook_failed",
                user_id=user_id,
                hook=name,
                reason="timeout",
                timeout_s=USER_DELETED_HOOK_TIMEOUT_S,
            )
        except Exception:
            _log_hook_error(hook, user_id)


def _log_hook_error(hook: UserDeletedHook, user_id: str) -> None:
    """Count and log the exception being handled as a hook ``error``, with traceback."""
    name = _hook_name(hook)
    user_deleted_hook_failures_counter.add(1, {"hook": name, "reason": "error"})
    logger.error(
        "user_deleted_hook_failed",
        user_id=user_id,
        hook=name,
        reason="error",
        exc_info=True,
    )
