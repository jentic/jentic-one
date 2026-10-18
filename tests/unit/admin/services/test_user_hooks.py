"""Unit tests for the user-deleted hook registry."""

from __future__ import annotations

import asyncio
import functools
from typing import cast

import pytest
from structlog.testing import capture_logs

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
    assert counted == [(1, {"hook": f"{__name__}.{_failing.__qualname__}", "reason": "error"})]


async def test_a_hook_that_overruns_its_budget_is_cut_off_and_counted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    counted: list[tuple[int, dict[str, str]]] = []
    monkeypatch.setattr(user_hooks, "USER_DELETED_HOOK_TIMEOUT_S", 0.01)
    monkeypatch.setattr(
        user_hooks.user_deleted_hook_failures_counter,
        "add",
        lambda amount, attributes: counted.append((amount, attributes)),
    )

    async def _hanging(_ctx: Context, _user_id: str, _identity: Identity) -> None:
        await asyncio.Event().wait()

    async def _after(_ctx: Context, user_id: str, _identity: Identity) -> None:
        calls.append(user_id)

    register_user_deleted_hook(_hanging)
    register_user_deleted_hook(_after)

    with capture_logs() as logs:
        await asyncio.wait_for(run_user_deleted_hooks(_CTX, "usr_gone", identity=_IDENTITY), 5)

    assert calls == ["usr_gone"]
    assert counted == [(1, {"hook": f"{__name__}.{_hanging.__qualname__}", "reason": "timeout"})]
    [failure] = [e for e in logs if e["event"] == "user_deleted_hook_failed"]
    assert failure["reason"] == "timeout"
    assert failure["timeout_s"] == 0.01
    # An expired budget has no hook traceback worth keeping.
    assert "exc_info" not in failure


async def test_a_timeout_error_raised_by_the_hook_itself_is_an_error_with_traceback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    counted: list[tuple[int, dict[str, str]]] = []
    monkeypatch.setattr(
        user_hooks.user_deleted_hook_failures_counter,
        "add",
        lambda amount, attributes: counted.append((amount, attributes)),
    )

    async def _driver_timeout(_ctx: Context, _user_id: str, _identity: Identity) -> None:
        raise TimeoutError("driver timed out")

    register_user_deleted_hook(_driver_timeout)

    with capture_logs() as logs:
        await run_user_deleted_hooks(_CTX, "usr_gone", identity=_IDENTITY)

    # The budget did not expire, so this is the hook's own failure, not ours.
    assert counted == [
        (1, {"hook": f"{__name__}.{_driver_timeout.__qualname__}", "reason": "error"})
    ]
    [failure] = [e for e in logs if e["event"] == "user_deleted_hook_failed"]
    assert failure["reason"] == "error"
    assert failure["exc_info"] is True
    assert "timeout_s" not in failure


async def _named_hook(_ctx: Context, _user_id: str, _identity: Identity, *, tag: str) -> None:
    raise RuntimeError(tag)


class _CallableHook:
    async def __call__(self, _ctx: Context, _user_id: str, _identity: Identity) -> None:
        raise RuntimeError("boom")


@pytest.mark.parametrize(
    ("hook", "expected"),
    [
        (functools.partial(_named_hook, tag="x"), f"{__name__}._named_hook"),
        (_CallableHook(), f"{__name__}._CallableHook"),
    ],
    ids=["partial", "callable-instance"],
)
async def test_failure_metric_names_a_hook_without_a_qualname_stably(
    monkeypatch: pytest.MonkeyPatch, hook: user_hooks.UserDeletedHook, expected: str
) -> None:
    counted: list[tuple[int, dict[str, str]]] = []
    monkeypatch.setattr(
        user_hooks.user_deleted_hook_failures_counter,
        "add",
        lambda amount, attributes: counted.append((amount, attributes)),
    )
    register_user_deleted_hook(hook)

    await run_user_deleted_hooks(_CTX, "usr_gone", identity=_IDENTITY)

    # No memory address: the name is the same in every process.
    assert counted == [(1, {"hook": expected, "reason": "error"})]


async def test_no_hooks_registered_is_a_no_op() -> None:
    await run_user_deleted_hooks(_CTX, "usr_gone", identity=_IDENTITY)
