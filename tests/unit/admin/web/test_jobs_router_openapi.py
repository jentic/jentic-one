"""The job routes declare every error status their service actually raises.

``JobResultService.get`` answers ``404`` for a job the caller cannot see,
``409`` while the job has not completed, and ``410`` once an approved
execution's result has expired; ``JobService.get_by_id`` answers ``404``.
Without the declarations the generated clients cannot type those outcomes.
"""

from __future__ import annotations

from fastapi import FastAPI

from jentic_one.admin.web.routers import jobs


def _operation(path: str) -> dict[str, object]:
    app = FastAPI()
    app.include_router(jobs.router)
    op: dict[str, object] = app.openapi()["paths"][path]["get"]
    return op


def test_get_job_result_declares_not_found_conflict_and_gone() -> None:
    responses = _operation("/jobs/{job_id}/result")["responses"]
    assert isinstance(responses, dict)
    assert {"404", "409", "410"} <= responses.keys()


def test_get_job_declares_not_found() -> None:
    responses = _operation("/jobs/{job_id}")["responses"]
    assert isinstance(responses, dict)
    assert "404" in responses
