"""Unit tests for broker job polling link projection and actor-type resolution (#1807)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml
from starlette.requests import Request

from jentic_one.broker.core.errors import (
    JobNotCompletedError,
    JobNotFoundError,
    JobResultExpiredError,
)
from jentic_one.broker.core.problem import broker_error_problem, status_for_broker_error
from jentic_one.broker.services.jobs.schemas import BrokerJobView
from jentic_one.broker.services.jobs.service import _job_actor_type
from jentic_one.broker.web.routers.jobs import _job_response
from jentic_one.shared.config import load_config
from jentic_one.shared.context import Context


def _ctx(tmp_path: Path, base: dict[str, Any], **sections: dict[str, Any]) -> Context:
    path = tmp_path / "cfg.yaml"
    path.write_text(yaml.dump({**base, **sections}))
    return Context(load_config(path))


def _broker_request(path: str = "/jobs/job_1807") -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "scheme": "https",
            "server": ("broker.example.com", 443),
            "path": path,
            "root_path": "",
            "query_string": b"",
            "headers": [(b"host", b"broker.example.com")],
        }
    )


def _view(
    *, result_available: bool = True, execution_id: str | None = "exec_1807"
) -> BrokerJobView:
    now = datetime(2026, 11, 13, 12, 0, tzinfo=UTC)
    return BrokerJobView(
        id="job_1807",
        kind="execution",
        status="completed",
        execution_id=execution_id,
        error=None,
        result_available=result_available,
        created_at=now,
        updated_at=now,
        actor_id="agnt_1807",
        actor_type="agent",
    )


def test_broker_job_response_roots_self_and_result_on_broker_even_when_jobs_api_base_url_is_set(
    tmp_path: Path, sample_config_dict: dict[str, Any]
) -> None:
    """Polling and result links stay on the broker; execution roots on jobs_api_base_url."""
    ctx = _ctx(
        tmp_path,
        sample_config_dict,
        broker={"jobs_api_base_url": "https://control.example.com/"},
        server={"public_base_url": "https://fallback.example.com"},
    )
    projected = _job_response(_view(result_available=True), _broker_request(), ctx)
    dumped = projected.model_dump(by_alias=True)

    assert dumped["_links"] == {
        "self": "https://broker.example.com/jobs/job_1807",
        "result": "https://broker.example.com/jobs/job_1807/result",
        "execution": "https://control.example.com/executions/exec_1807",
    }


def test_broker_job_response_omits_result_and_execution_links_when_absent(
    tmp_path: Path, sample_config_dict: dict[str, Any]
) -> None:
    ctx = _ctx(tmp_path, sample_config_dict)
    projected = _job_response(
        _view(result_available=False, execution_id=None), _broker_request(), ctx
    )
    dumped = projected.model_dump(by_alias=True)

    assert dumped["_links"] == {
        "self": "https://broker.example.com/jobs/job_1807",
        "result": None,
        "execution": None,
    }


def test_job_actor_type_prefers_recognized_ksuid_prefix_over_stored_column() -> None:
    assert _job_actor_type("agnt_abc123", "user") == "agent"
    assert _job_actor_type("usr_abc123", "agent") == "user"
    assert _job_actor_type("custom-actor-id", "agent") == "agent"
    assert _job_actor_type(None, "user") == "user"


def test_broker_job_errors_map_to_expected_statuses_and_types() -> None:
    not_found = JobNotFoundError("job_1", instance="/jobs/job_1")
    not_done = JobNotCompletedError("job_1", instance="/jobs/job_1/result")
    expired = JobResultExpiredError("job_1", instance="/jobs/job_1/result")

    assert status_for_broker_error(not_found) == 404
    assert broker_error_problem(not_found)["type"] == "job_not_found"
    assert status_for_broker_error(not_done) == 409
    assert broker_error_problem(not_done)["type"] == "job_not_completed"
    assert status_for_broker_error(expired) == 410
    assert broker_error_problem(expired)["type"] == "job_result_expired"
