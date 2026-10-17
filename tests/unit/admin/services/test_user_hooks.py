"""Unit tests for the user-deleted hook registry."""

from __future__ import annotations

from typing import cast

import pytest

from jentic_one.admin.services import user_hooks
from jentic_one.admin.services.user_hooks import (
    register_user_deleted_hook,
    run_user_deleted_hooks,
)
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.context import Context

_CTX = cast(Context, object())
_IDENTITY = Identity(sub="usr_admin", permissions=["org:admin"])


@pytest.fixture(autouse=True)
def _empty_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(user_hooks, "_USER_DELETED_HOOKS", [])


async def test_hooks_run_in_registration_order_with_the_delete_arguments() -> None:
    calls: list[tuple[str, Context, str, Identity]] = []

    async def _first(ctx: Context, user_id: str, identity: Identity) -> None:
        calls.append(("first", ctx, user_id, identity))

    async def _second(ctx: Context, user_id: str, identity: Identity) -> None:
        calls.append(("second", ctx, user_id, identity))

    register_user_deleted_hook(_first)
    register_user_deleted_hook(_second)

    await run_user_deleted_hooks(_CTX, "usr_gone", identity=_IDENTITY)

    assert calls == [
        ("first", _CTX, "usr_gone", _IDENTITY),
        ("second", _CTX, "usr_gone", _IDENTITY),
    ]


async def test_registering_the_same_hook_twice_runs_it_once() -> None:
    calls: list[str] = []

    async def _hook(_ctx: Context, user_id: str, _identity: Identity) -> None:
        calls.append(user_id)

    register_user_deleted_hook(_hook)
    register_user_deleted_hook(_hook)

    await run_user_deleted_hooks(_CTX, "usr_gone", identity=_IDENTITY)

    assert calls == ["usr_gone"]


async def test_a_failing_hook_is_skipped_and_later_hooks_still_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    counted: list[tuple[int, dict[str, str]]] = []
    monkeypatch.setattr(
        user_hooks.user_deleted_hook_failures_counter,
        "add",
        lambda amount, attributes: counted.append((amount, attributes)),
    )

    async def _failing(_ctx: Context, _user_id: str, _identity: Identity) -> None:
        raise RuntimeError("boom")

    async def _after(_ctx: Context, user_id: str, _identity: Identity) -> None:
        calls.append(user_id)

    register_user_deleted_hook(_failing)
    register_user_deleted_hook(_after)

    await run_user_deleted_hooks(_CTX, "usr_gone", identity=_IDENTITY)

    assert calls == ["usr_gone"]
    # Only the failing hook is counted, named by module and qualname.
    assert counted == [(1, {"hook": f"{__name__}.{_failing.__qualname__}"})]


async def test_no_hooks_registered_is_a_no_op() -> None:
    await run_user_deleted_hooks(_CTX, "usr_gone", identity=_IDENTITY)
