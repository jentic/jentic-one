"""The org's defaults and maximums for grants (step 8)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from jentic_one.shared.auth.grant_limits import (
    CLOCK_GRACE,
    GrantPolicyLimitError,
    check_grant_limits,
    default_grant_policy,
    duration_label,
    new_grant_policy,
    renewal_expiry,
    renewal_idle_period,
)
from jentic_one.shared.auth.grant_policy import UNSET
from jentic_one.shared.config import GrantExpiryConfig

NOW = datetime(2026, 11, 23, 12, 0, tzinfo=UTC)
DAY = 86_400


def test_open_source_defaults_change_nothing() -> None:
    policy = new_grant_policy(GrantExpiryConfig(), now=NOW)
    assert (policy.expires_at, policy.max_idle_period, policy.max_quota) == (None, None, None)
    check_grant_limits(GrantExpiryConfig(), now=NOW, expires_at=None, max_idle_period=None)


def test_a_creation_that_sets_no_policy_gets_the_default() -> None:
    config = GrantExpiryConfig.model_validate(
        {"default_expires_in": "P7D", "default_idle_period": "P14D", "single_use_by_default": True}
    )
    policy = new_grant_policy(config, now=NOW)
    assert policy.expires_at == NOW + timedelta(days=7)
    assert policy.max_idle_period == 14 * DAY
    assert policy.max_quota == 1


def test_a_creation_that_sets_any_limit_is_taken_as_it_is() -> None:
    config = GrantExpiryConfig.model_validate(
        {"default_expires_in": "P7D", "default_idle_period": "P14D"}
    )
    policy = new_grant_policy(config, now=NOW, max_quota=1)
    assert (policy.expires_at, policy.max_idle_period, policy.max_quota) == (None, None, 1)


def test_a_maximum_alone_is_the_default_and_every_grant_must_end() -> None:
    config = GrantExpiryConfig.model_validate({"max_expires_in": "P30D"})
    assert new_grant_policy(config, now=NOW).expires_at == NOW + timedelta(days=30)
    with pytest.raises(GrantPolicyLimitError) as none:
        new_grant_policy(config, now=NOW, expires_at=None)
    assert none.value.field == "expires_at"
    assert none.value.status_code == 422
    assert "within 30 days" in none.value.message
    with pytest.raises(GrantPolicyLimitError, match="at most 30 days"):
        check_grant_limits(config, now=NOW, expires_at=NOW + timedelta(days=31))
    # The maximum itself passes, with grace for a browser's clock.
    check_grant_limits(config, now=NOW, expires_at=NOW + timedelta(days=30) + CLOCK_GRACE)


def test_defaults_alone_never_enforce_the_maximum() -> None:
    """Whoever binds enforces the maximum; a confirm that binds no agent has no grant to limit."""
    config = GrantExpiryConfig.model_validate({"max_expires_in": "P30D"})
    policy = default_grant_policy(config, now=NOW, expires_at=None)
    assert policy.expires_at is None  # "No expiry", taken as sent, not refused here
    with pytest.raises(GrantPolicyLimitError):
        new_grant_policy(config, now=NOW, expires_at=None)  # the checked form still refuses


def test_a_maximum_only_checks_what_a_renewal_sets() -> None:
    config = GrantExpiryConfig.model_validate({"max_expires_in": "P30D", "max_idle_period": "P2W"})
    # Turning on the idle warning alone leaves an older grant's lack of an end date alone.
    check_grant_limits(config, now=NOW)
    with pytest.raises(GrantPolicyLimitError, match="idle limit of at most 14 days"):
        check_grant_limits(config, now=NOW, max_idle_period=None)
    with pytest.raises(GrantPolicyLimitError, match="at most 14 days"):
        check_grant_limits(config, now=NOW, max_idle_period=30 * DAY)


def test_a_default_above_its_maximum_is_a_config_error() -> None:
    with pytest.raises(ValueError, match="must not exceed"):
        GrantExpiryConfig.model_validate({"default_expires_in": "P31D", "max_expires_in": "P30D"})


@pytest.mark.parametrize(
    ("seconds", "label"),
    [
        (DAY, "1 day"),
        (30 * DAY, "30 days"),
        (7_200, "2 hours"),
        (60, "1 minute"),
        (90, "90 seconds"),
    ],
)
def test_duration_label(seconds: int, label: str) -> None:
    assert duration_label(seconds) == label


def test_a_renewal_reruns_the_grant_length_capped_at_the_maximum() -> None:
    now = datetime(2026, 6, 1, tzinfo=UTC)
    granted = now - timedelta(days=40)
    ninety_days = granted + timedelta(days=90)
    open_config = GrantExpiryConfig()
    capped = GrantExpiryConfig.model_validate({"max_expires_in": "P30D"})

    assert renewal_expiry(
        open_config, now=now, expires_at=ninety_days, granted_at=granted
    ) == now + timedelta(days=90)
    assert renewal_expiry(
        capped, now=now, expires_at=ninety_days, granted_at=granted
    ) == now + timedelta(days=30)
    # No end date: the maximum, or still none without one.
    assert renewal_expiry(capped, now=now, expires_at=None, granted_at=granted) == now + timedelta(
        days=30
    )
    assert renewal_expiry(open_config, now=now, expires_at=None, granted_at=granted) is UNSET


def test_org_limits_are_iso_8601_durations() -> None:
    config = GrantExpiryConfig.model_validate({"max_expires_in": "P2W", "max_idle_period": "PT36H"})
    assert (config.max_expires_in, config.max_idle_period) == (14 * DAY, 36 * 3_600)
    assert config.model_dump()["max_expires_in"] == "P14D"
    for bad in (
        {"max_expires_in": 30 * DAY},
        {"max_expires_in": "30d"},
        {"max_idle_period": "PT1H"},
    ):
        with pytest.raises(ValueError):
            GrantExpiryConfig.model_validate(bad)


def test_a_renewal_keeps_its_idle_limit_within_the_maximum() -> None:
    assert renewal_idle_period(GrantExpiryConfig(), current=90 * DAY) is UNSET
    capped = GrantExpiryConfig.model_validate({"max_idle_period": "P14D"})
    assert renewal_idle_period(capped, current=90 * DAY) == 14 * DAY
    assert renewal_idle_period(capped, current=7 * DAY) == 7 * DAY
    assert renewal_idle_period(capped, current=None) == 14 * DAY
    with_default = GrantExpiryConfig.model_validate(
        {"max_idle_period": "P14D", "default_idle_period": "P10D"}
    )
    assert renewal_idle_period(with_default, current=None) == 10 * DAY
