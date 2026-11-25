"""``GrantRenewalRequest.expires_at`` is validated like the direct binding endpoint's.

A naive datetime is read as UTC, so a re-grant decision can never store an end
date that has already passed.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from jentic_one.admin.web.schemas.held_executions import GrantRenewalRequest


def test_a_naive_end_date_in_the_past_is_refused() -> None:
    past = (datetime.now(UTC) - timedelta(minutes=5)).replace(tzinfo=None)
    with pytest.raises(ValidationError, match="expires_at must be in the future"):
        GrantRenewalRequest(expires_at=past)


def test_an_aware_end_date_in_the_past_is_refused() -> None:
    with pytest.raises(ValidationError, match="expires_at must be in the future"):
        GrantRenewalRequest(expires_at=datetime.now(UTC) - timedelta(minutes=5))


def test_a_naive_end_date_in_the_future_is_accepted() -> None:
    future = (datetime.now(UTC) + timedelta(days=1)).replace(tzinfo=None)
    assert GrantRenewalRequest(expires_at=future).expires_at == future


def test_no_end_date_is_accepted() -> None:
    assert GrantRenewalRequest(expires_at=None).expires_at is None
    assert GrantRenewalRequest().expires_at is None
