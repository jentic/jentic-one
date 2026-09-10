"""Canonical access-recovery guidance shared across broker and control.

A toolkit only "serves" an API once a credential for it is provisioned and
bound; only then can an agent be bound to that toolkit. When no toolkit serves
an API yet, the broker's ``no_toolkit_binding`` recovery directive and the
control approval-denial reason must recommend the *same* first step — provision
a credential — instead of contradicting each other (see issue #683).

This module holds the single wording both layers reference so the two messages
can never drift. It lives under ``shared`` because both the public broker
(``broker/core/exceptions.py``) and control (``control/services/access_requests``)
may import ``shared`` but not each other.
"""

from __future__ import annotations


def no_toolkit_serves_api_reason(api: str) -> str:
    """The canonical denial reason when no toolkit serves ``api`` yet.

    ``api`` is a ``vendor[/name][@version]`` label. Phrased as a statement of the
    condition plus the recommended first step, matching the broker directive.
    """
    return (
        f"No toolkit serves API {api}; provision and bind a credential for it "
        "first, then request the toolkit binding"
    )


def no_credential_serves_api_reason(api: str) -> str:
    """The canonical denial reason when no credential serves ``api`` yet.

    Theme-5 Phase 3 successor of :func:`no_toolkit_serves_api_reason` for the
    direct agent↔credential model: a ``credential:bind`` filed by API reference
    can only resolve once a credential covering that API exists and is visible
    to the approver. The recommended first step — provision a credential — is
    the same one the broker's ``no_credential_binding`` directive names, so the
    two layers never contradict each other (see issue #683).
    """
    return (
        f"No credential covers API {api}; provision a credential for it first "
        "(POST /credentials), then approve the credential binding"
    )
