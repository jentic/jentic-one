"""Held executions and the credentials keyset: broker and worker share one predicate.

A held call's payload is encrypted with the credentials keyset, so the broker
files a hold only when ``Context.has_encryption_keyset`` is true — the same
condition the worker's execution handler is given its ``EncryptionService``
under. Without a keyset the broker refuses the call with a typed 503 before
anything is stored, and a worker that meets an encrypted payload it cannot
read fails the job with a readable problem instead of crashing or running it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml
from starlette.requests import Request

from jentic_one.broker.core.exceptions import ApprovalHoldUnavailableError
from jentic_one.broker.core.problem import status_for_broker_error
from jentic_one.broker.core.schemas import ExecuteRequestContext
from jentic_one.broker.services.credentials.resolver import ResolvedCredential
from jentic_one.broker.services.execution.authorization import ExecutionAuthorization
from jentic_one.broker.web.routers.execute import _handle_hold
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.broker.protocols import RuleVerdict
from jentic_one.shared.config import load_config
from jentic_one.shared.context import Context
from jentic_one.shared.jobs.execution_handler import ExecutionHandler
from jentic_one.shared.jobs.hold import ENCRYPTED_PAYLOAD_KEY
from jentic_one.shared.jobs.protocols import UpstreamExecRequest, UpstreamExecResult
from jentic_one.shared.models import ActorType, CredentialType, StoredCredentialType

_KEY = "vF7VWq2NJLr+uGDBFy9boIXfSJJIzqnTSF7iDDMKR5U="  # pragma: allowlist secret


def _ctx(tmp_path: Path, base: dict[str, Any], *, keyset: bool) -> Context:
    cfg = dict(base)
    entries = [{"id": "v1", "material": _KEY}] if keyset else []
    cfg["credentials"] = {
        **cfg.get("credentials", {}),
        "encryption": {"active_id": "v1", "entries": entries},
    }
    path = tmp_path / "cfg.yaml"
    path.write_text(yaml.dump(cfg))
    return Context(load_config(path))


def test_the_keyset_predicate_follows_the_config(
    tmp_path: Path, sample_config_dict: dict[str, Any]
) -> None:
    assert _ctx(tmp_path, sample_config_dict, keyset=True).has_encryption_keyset is True
    assert _ctx(tmp_path, sample_config_dict, keyset=False).has_encryption_keyset is False


def _request() -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "scheme": "http",
            "server": ("127.0.0.1", 8100),
            "path": "/api.example.com/v1/charges",
            "root_path": "",
            "query_string": b"",
            "headers": [(b"host", b"127.0.0.1:8100")],
        }
    )


async def test_the_broker_refuses_to_hold_without_a_keyset(
    tmp_path: Path, sample_config_dict: dict[str, Any]
) -> None:
    ctx = _ctx(tmp_path, sample_config_dict, keyset=False)
    authorization = ExecutionAuthorization(
        allowed_credential_ids=["cred_1"],
        selected_credential=ResolvedCredential(
            credential_id="cred_1",
            name="key",
            wire_type=CredentialType.API_KEY,
            stored_type=StoredCredentialType.API_KEY,
            provider="api.example.com",
        ),
        verdict=RuleVerdict.REQUIRE_APPROVAL,
    )
    agent = Identity(sub="agnt_1", actor_type=ActorType.AGENT, permissions=[])
    ctx_req = ExecuteRequestContext(
        upstream_url="https://api.example.com/v1/charges", method="POST", trace_id="c" * 32
    )

    with pytest.raises(ApprovalHoldUnavailableError) as exc:
        await _handle_hold(_request(), ctx_req, ctx, agent, authorization)
    assert status_for_broker_error(exc.value) == 503
    assert exc.value.type == "approval_hold_unavailable"


class _NeverCalledExecutor:
    called = False

    async def execute(self, request: UpstreamExecRequest, *, session: Any) -> UpstreamExecResult:
        self.called = True
        raise AssertionError("an undecryptable held payload must not run")


async def test_a_worker_without_a_keyset_fails_a_held_job_instead_of_running_it() -> None:
    executor = _NeverCalledExecutor()
    handler = ExecutionHandler(executor=executor, encryption=None)

    result = await handler.execute(
        "job_held",
        object(),
        payload={ENCRYPTED_PAYLOAD_KEY: "v1:opaque"},
        created_by="agnt_1",
        actor_type="agent",
    )

    assert executor.called is False
    assert result.body["status"] == "failed"
    assert result.body["problem"]["type"] == "approval_hold_unavailable"
