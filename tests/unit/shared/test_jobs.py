"""Tests for JobStatus and JobKind enums."""

import pytest

from jentic_one.shared.models.jobs import JobKind, JobStatus


@pytest.mark.parametrize(
    ("member", "expected"),
    [
        (JobStatus.QUEUED, "queued"),
        (JobStatus.RUNNING, "running"),
        (JobStatus.COMPLETED, "completed"),
        (JobStatus.FAILED, "failed"),
        (JobStatus.CANCELLED, "cancelled"),
        (JobStatus.DEAD_LETTER, "dead_letter"),
        (JobStatus.HELD, "held"),
    ],
)
def test_job_status_member_value(member: JobStatus, expected: str) -> None:
    assert member == expected
    assert isinstance(member, str)


def test_job_status_member_count() -> None:
    assert len(JobStatus) == 7


@pytest.mark.parametrize(
    ("member", "expected"),
    [
        (JobKind.IMPORT, "import"),
        (JobKind.EXECUTION, "execution"),
    ],
)
def test_job_kind_member_value(member: JobKind, expected: str) -> None:
    assert member == expected
    assert isinstance(member, str)


def test_job_kind_member_count() -> None:
    assert len(JobKind) == 2
