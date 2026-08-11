"""Unit tests for the IdP provisioning-decision seam (WI-1).

Covers only the pure decision logic + the injectable registry. The end-to-end
callback behaviour (link/existing/reject + audit) is exercised by the web-layer
integration test in ``tests/web/auth/test_oidc_login_flow.py``.
"""

from __future__ import annotations

import pytest

from jentic_one.auth.core.idp import (
    AdmissionDecision,
    IdpClaims,
    get_admission_policy,
    open_admission_policy,
    set_admission_policy,
)


def _claims(*, email_verified: bool) -> IdpClaims:
    return IdpClaims(
        external_subject="ext-1",
        email="user@example.com",
        first_name="Jane",
        last_name="Doe",
        email_verified=email_verified,
    )


def test_open_policy_admits_verified() -> None:
    assert open_admission_policy(_claims(email_verified=True)) is (
        AdmissionDecision.ADMIT_AND_CREATE
    )


def test_open_policy_admits_unverified_preserving_legacy_behaviour() -> None:
    # The open default admits every brand-new email (today's behaviour). The
    # unverified+collision takeover case is fail-closed upstream, not here.
    assert open_admission_policy(_claims(email_verified=False)) is (
        AdmissionDecision.ADMIT_AND_CREATE
    )


def test_default_installed_policy_is_open() -> None:
    assert get_admission_policy() is open_admission_policy


def test_set_admission_policy_overrides_and_restores() -> None:
    original = get_admission_policy()
    try:

        def _reject_all(claims: IdpClaims) -> AdmissionDecision:
            return AdmissionDecision.REJECT

        set_admission_policy(_reject_all)
        assert get_admission_policy() is _reject_all
        assert get_admission_policy()(_claims(email_verified=True)) is AdmissionDecision.REJECT
    finally:
        set_admission_policy(original)
        assert get_admission_policy() is open_admission_policy


@pytest.fixture(autouse=True)
def _restore_policy() -> object:
    """Guard against a test leaking a non-default policy into the module global."""
    original = get_admission_policy()
    yield
    set_admission_policy(original)
