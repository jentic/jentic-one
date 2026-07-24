"""Unit tests for handler-side toolkit derivation (``select_toolkit``)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, cast

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from jentic.problem_details import Forbidden, ProblemDetailException

from jentic_one.broker.core.exceptions import (
    ActionDeniedError,
    AmbiguousMatchError,
    no_toolkit_binding_directive,
)
from jentic_one.broker.web.errors import install_broker_error_handlers
from jentic_one.broker.web.routers.execute import select_toolkit
from jentic_one.shared.access_guidance import no_toolkit_serves_api_reason
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.models import ActorType
from jentic_one.shared.schemas import APIReference

_API = APIReference(vendor="acme", name="widgets", version="1.0.0")
_INSTANCE = "/acme.example.com/v1/widgets"


class _StubDeriver:
    def __init__(self, candidates: list[str], *, toolkit_serves_api: bool = True) -> None:
        self.candidates = candidates
        self._toolkit_serves_api = toolkit_serves_api
        self.calls: list[dict[str, str]] = []
        self.serves_calls: list[dict[str, str]] = []

    async def derive_toolkits(
        self, *, agent_id: str, vendor: str, name: str, version: str
    ) -> list[str]:
        self.calls.append(
            {"agent_id": agent_id, "vendor": vendor, "name": name, "version": version}
        )
        return self.candidates

    async def any_toolkit_serves_api(self, *, vendor: str, name: str, version: str) -> bool:
        self.serves_calls.append({"vendor": vendor, "name": name, "version": version})
        return self._toolkit_serves_api


def _identity(actor_type: str = "agent") -> Identity:
    return Identity(
        sub="agnt_1",
        actor_type=ActorType(actor_type),
        permissions=["capabilities:execute"],
        expires_at=datetime.now(UTC) + timedelta(hours=1),
        active=True,
    )


async def _select(deriver: _StubDeriver, *, header_toolkit: str | None) -> str:
    return await select_toolkit(
        deriver=deriver,
        identity=_identity(),
        api=_API,
        header_toolkit=header_toolkit,
        instance=_INSTANCE,
    )


def _toolkit_identity(toolkit_id: str = "tk_key1") -> Identity:
    return Identity(
        sub=toolkit_id,
        actor_type=ActorType.TOOLKIT,
        permissions=["capabilities:execute"],
        expires_at=datetime.now(UTC) + timedelta(hours=1),
        active=True,
    )


async def _select_toolkit_actor(
    deriver: _StubDeriver, *, header_toolkit: str | None, toolkit_id: str = "tk_key1"
) -> str:
    return await select_toolkit(
        deriver=deriver,
        identity=_toolkit_identity(toolkit_id),
        api=_API,
        header_toolkit=header_toolkit,
        instance=_INSTANCE,
    )


def _detail(exc: ProblemDetailException) -> dict[str, Any]:
    """The problem-detail body (a dict at runtime, though typed ``str`` on the exc)."""
    return cast(dict[str, Any], exc.detail)


async def test_single_candidate_no_header_uses_it() -> None:
    result = await _select(_StubDeriver(["tk_only"]), header_toolkit=None)
    assert result == "tk_only"


async def test_zero_candidates_no_header_toolkit_serves_recommends_binding() -> None:
    # A toolkit serves this API; the caller just isn't bound. The directive
    # recommends the (approvable) toolkit-binding request.
    with pytest.raises(ActionDeniedError) as exc:
        await _select(_StubDeriver([], toolkit_serves_api=True), header_toolkit=None)
    assert exc.value.type == "no_toolkit_binding"
    assert exc.value.instance == _INSTANCE
    assert exc.value.directive is not None
    assert exc.value.directive.strategy == "prompt_human"
    assert exc.value.directive.parameters["toolkit_serves_api"] is True
    assert exc.value.directive.parameters["suggested_command"] == (
        'jentic access request --toolkit acme/widgets --wait'
    )


async def test_zero_candidates_no_toolkit_serves_recommends_credential_first() -> None:
    # No toolkit serves this API yet (issue #683). A bare toolkit-binding request
    # would be denied, so the directive must point at provisioning a credential
    # first rather than filing an unapprovable binding request.
    deriver = _StubDeriver([], toolkit_serves_api=False)
    with pytest.raises(ActionDeniedError) as exc:
        await _select(deriver, header_toolkit=None)
    assert exc.value.type == "no_toolkit_binding"
    assert exc.value.directive is not None
    assert exc.value.directive.strategy == "prompt_human"
    assert exc.value.directive.parameters["toolkit_serves_api"] is False
    instruction = exc.value.directive.human_readable_instruction
    assert "credential" in instruction.lower()
    assert "provision" in instruction.lower()
    # The broker consulted the "any toolkit serves this API?" predicate.
    assert deriver.serves_calls == [{"vendor": "acme", "name": "widgets", "version": "1.0.0"}]


async def test_multiple_candidates_no_header_raises_409_with_candidates() -> None:
    with pytest.raises(AmbiguousMatchError) as exc:
        await _select(_StubDeriver(["tk_a", "tk_b"]), header_toolkit=None)
    assert exc.value.type == "ambiguous_toolkit"
    assert exc.value.instance == _INSTANCE
    codes = {e["code"] for e in exc.value.extra["errors"]}
    assert codes == {"tk_a", "tk_b"}
    assert exc.value.directive is not None
    assert exc.value.directive.strategy == "switch_toolkit"
    assert exc.value.directive.parameters["candidates"] == ["tk_a", "tk_b"]
    # The directive must hand the agent a copy-pasteable disambiguation command.
    assert "Jentic-Toolkit-Id=tk_a" in exc.value.directive.parameters["suggested_command"]


async def test_header_present_and_bound_uses_it() -> None:
    result = await _select(_StubDeriver(["tk_a", "tk_b"]), header_toolkit="tk_b")
    assert result == "tk_b"


async def test_header_present_but_not_bound_raises_403() -> None:
    # Recoverable denial: the agent named a toolkit it isn't bound to but *is*
    # bound to another (tk_a). It must get an ActionDeniedError carrying a
    # switch_toolkit directive pointing at its real binding — not a dead-end
    # Forbidden with no agent_directive.
    with pytest.raises(ActionDeniedError) as exc:
        await _select(_StubDeriver(["tk_a"]), header_toolkit="tk_unbound")
    assert exc.value.type == "toolkit_binding_required"
    assert exc.value.instance == _INSTANCE
    assert "tk_unbound" in exc.value.detail
    assert exc.value.directive is not None
    assert exc.value.directive.strategy == "switch_toolkit"
    assert exc.value.directive.parameters["candidates"] == ["tk_a"]


async def test_header_present_not_bound_and_no_candidates_prompts_human() -> None:
    # The agent named a toolkit but is bound to none at all. When a toolkit does
    # serve the API it should be told to file the binding request (prompt_human);
    # it should never be handed a dead-end 403.
    with pytest.raises(ActionDeniedError) as exc:
        await _select(_StubDeriver([], toolkit_serves_api=True), header_toolkit="tk_unbound")
    assert exc.value.type == "no_toolkit_binding"
    assert exc.value.directive is not None
    assert exc.value.directive.strategy == "prompt_human"
    assert exc.value.directive.parameters["toolkit_serves_api"] is True
    assert exc.value.directive.parameters["suggested_command"] == (
        'jentic access request --toolkit acme/widgets --wait'
    )


async def test_header_present_not_bound_and_no_toolkit_serves_recommends_credential_first() -> None:
    # The agent named a toolkit, is bound to none, and no toolkit serves the API:
    # recover by provisioning a credential first (issue #683), not a binding request.
    with pytest.raises(ActionDeniedError) as exc:
        await _select(_StubDeriver([], toolkit_serves_api=False), header_toolkit="tk_unbound")
    assert exc.value.type == "no_toolkit_binding"
    assert exc.value.directive is not None
    assert exc.value.directive.parameters["toolkit_serves_api"] is False
    assert "credential" in exc.value.directive.human_readable_instruction.lower()


async def test_derivation_uses_discovered_api_identity() -> None:
    deriver = _StubDeriver(["tk_only"])
    await _select(deriver, header_toolkit=None)
    assert deriver.calls == [
        {"agent_id": "agnt_1", "vendor": "acme", "name": "widgets", "version": "1.0.0"}
    ]


async def test_toolkit_actor_no_header_uses_key_toolkit() -> None:
    """A toolkit key names its toolkit directly — derivation is bypassed."""
    deriver = _StubDeriver(["tk_should_not_be_used"])
    result = await _select_toolkit_actor(deriver, header_toolkit=None)
    assert result == "tk_key1"
    assert deriver.calls == []


async def test_toolkit_actor_matching_header_uses_key_toolkit() -> None:
    deriver = _StubDeriver([])
    result = await _select_toolkit_actor(deriver, header_toolkit="tk_key1")
    assert result == "tk_key1"
    assert deriver.calls == []


async def test_toolkit_actor_mismatched_header_raises_403() -> None:
    deriver = _StubDeriver([])
    with pytest.raises(Forbidden) as exc:
        await _select_toolkit_actor(deriver, header_toolkit="tk_other")
    assert exc.value.status_code == 403
    assert _detail(exc.value)["type"] == "toolkit_binding_required"
    assert deriver.calls == []


def test_ambiguous_toolkit_body_lists_candidates() -> None:
    """End-to-end render check: the 409 problem+json carries candidates + directive."""
    app = FastAPI()
    install_broker_error_handlers(app)

    @app.get("/p")
    async def _p() -> None:
        await _select(_StubDeriver(["tk_a", "tk_b"]), header_toolkit=None)

    resp = TestClient(app, raise_server_exceptions=False).get("/p")
    assert resp.status_code == 409
    body = resp.json()
    assert body["type"] == "ambiguous_toolkit"
    codes = {e.get("code") for e in body.get("errors", [])}
    assert codes == {"tk_a", "tk_b"}
    assert body["agent_directive"]["strategy"] == "switch_toolkit"
    assert body["agent_directive"]["parameters"]["candidates"] == ["tk_a", "tk_b"]


def test_no_toolkit_binding_body_carries_prompt_human_directive() -> None:
    """End-to-end render check: the 403 problem+json carries the prompt_human directive."""
    app = FastAPI()
    install_broker_error_handlers(app)

    @app.get("/p")
    async def _p() -> None:
        await _select(_StubDeriver([], toolkit_serves_api=True), header_toolkit=None)

    resp = TestClient(app, raise_server_exceptions=False).get("/p")
    assert resp.status_code == 403
    body = resp.json()
    assert body["type"] == "no_toolkit_binding"
    assert body["agent_directive"]["strategy"] == "prompt_human"
    assert body["agent_directive"]["parameters"]["suggested_command"] == (
        'jentic access request --toolkit acme/widgets --wait'
    )


def test_no_toolkit_binding_credential_first_directive_and_denial_reason_agree() -> None:
    """The broker directive (no toolkit yet) and the control denial reason agree.

    Regression for issue #683: the ``no_toolkit_binding`` directive must recommend
    provisioning a credential first, matching the reason the toolkit:bind approval
    denies with when no toolkit serves the API — so the two never contradict.
    """
    directive = no_toolkit_binding_directive(
        vendor="acme", name="widgets", version="1.0.0", toolkit_serves_api=False
    )
    instruction = directive.human_readable_instruction.lower()
    assert directive.parameters["toolkit_serves_api"] is False
    assert "credential" in instruction
    assert "provision" in instruction

    denial_reason = no_toolkit_serves_api_reason("acme/widgets").lower()
    # Both recommend provisioning/binding a credential as the first step.
    assert "credential" in denial_reason
    assert "provision" in denial_reason
    assert "no toolkit serves" in denial_reason
