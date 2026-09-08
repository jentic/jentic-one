"""Provisioning-decision seam for external-IdP logins.

When a verified external-IdP login resolves to a **brand-new** email (not already
linked and not an existing local account), something has to decide whether to
create that user. The default is **open**: auto-provision any ``email_verified``
account — the historical behaviour, and the right default for a single-admin
self-host.

This module is the injectable seam for that one decision. It deliberately holds
**no** rules/modes/domains: a deployment that wants a stricter policy (invite-only,
domain-allowlist, hosted-domain gating, …) installs its own callable via
:func:`set_admission_policy`. The link/race/fail-closed logic around this decision
stays in ``AuthorizeService`` — the policy only answers "admit-and-create, or
reject this new email?".

A second, independent seam decides *what a brand-new user starts with*: the
**default-grants provider** (:func:`set_default_idp_grants`) returns the baseline
permissions a just-created IdP user receives (e.g. read-only). It defaults to
granting nothing (today's behaviour) and applies only to brand-new users.

Same process-global registry posture as ``register_config`` in
``shared/config.py``: a module-level default, replaceable at import time.
"""

from __future__ import annotations

from enum import Enum
from typing import Protocol

from jentic_one.auth.core.idp.adapter import IdpClaims


class AdmissionDecision(Enum):
    """Whether a brand-new external-IdP email may be provisioned."""

    ADMIT_AND_CREATE = "admit_and_create"
    REJECT = "reject"


class AdmissionPolicy(Protocol):
    """Decides whether a never-seen, verified IdP email is admitted."""

    def __call__(self, claims: IdpClaims) -> AdmissionDecision:
        """Return the decision for a brand-new email (already-linked and existing
        accounts are handled upstream and never reach the policy)."""
        ...


def open_admission_policy(claims: IdpClaims) -> AdmissionDecision:
    """Default policy: admit every brand-new email (open self-signup).

    This preserves the historical OSS behaviour exactly — the callback creates a
    user for any never-seen email at this step. Note the *collision* case for an
    unverified email (an unverified IdP email that matches an existing local
    account) is fail-closed upstream in ``_resolve_or_create_user`` before the
    policy is consulted, so this "admit all" default never enables account
    takeover. A deployment wanting to gate unverified emails installs a stricter
    policy via :func:`set_admission_policy`.
    """
    return AdmissionDecision.ADMIT_AND_CREATE


_admission_policy: AdmissionPolicy = open_admission_policy


def set_admission_policy(policy: AdmissionPolicy) -> None:
    """Install the process-wide admission policy for new external-IdP users.

    Called once at import/boot by a deployment that wants a stricter policy than
    the open default. Idempotent from the caller's perspective — last write wins.
    """
    global _admission_policy
    _admission_policy = policy


def get_admission_policy() -> AdmissionPolicy:
    """Return the currently-installed admission policy (open by default)."""
    return _admission_policy


class DefaultGrantsProvider(Protocol):
    """Yields the permission grants a brand-new IdP-provisioned user receives."""

    def __call__(self, claims: IdpClaims) -> list[str]:
        """Return the permission names to grant a just-created user (may be empty)."""
        ...


def no_default_grants(claims: IdpClaims) -> list[str]:
    """Default provider: grant nothing.

    Preserves the historical behaviour — a self-provisioned IdP user starts with
    no permissions until an admin grants them. A deployment that wants new users
    to start with, say, read-only access installs its own provider via
    :func:`set_default_idp_grants`.
    """
    return []


_default_grants_provider: DefaultGrantsProvider = no_default_grants


def set_default_idp_grants(provider: DefaultGrantsProvider) -> None:
    """Install the process-wide default-grants provider for new IdP users.

    Called once at import/boot by a deployment that wants newly-provisioned
    external-IdP users to start with a baseline permission set (e.g. read-only).
    The grants are applied ONLY to brand-new users at creation time; existing and
    already-linked accounts are never modified. Last write wins.
    """
    global _default_grants_provider
    _default_grants_provider = provider


def get_default_idp_grants() -> DefaultGrantsProvider:
    """Return the installed default-grants provider (grants nothing by default)."""
    return _default_grants_provider
