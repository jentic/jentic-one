"""Canonical access-recovery guidance shared across broker and control.

An API is only "served" once a credential covering it is provisioned; only
then can an agent be bound to it. When nothing serves an API yet, the broker's
missing-binding recovery directive and the control approval-denial reason must
recommend the *same* first step — provision a credential — instead of
contradicting each other (see issue #683).

This module holds the single wording both layers reference so the two messages
can never drift. It lives under ``shared`` because both the public broker
(``broker/core/exceptions.py``) and control (``control/services/access_requests``)
may import ``shared`` but not each other.
"""

from __future__ import annotations


def no_credential_serves_api_reason(api: str) -> str:
    """The canonical denial reason when no credential serves ``api`` yet.

    ``api`` is a ``vendor[/name][@version]`` label. A ``credential:bind`` filed
    by API reference can only resolve once a credential covering that API
    exists and is visible to the approver. The recommended first step —
    provision a credential — is the same one the broker's missing-binding
    directives name, so the two layers never contradict each other (see issue
    #683). Phrased as a statement of the condition plus the recommended first
    step, matching the broker directive.
    """
    return (
        f"No credential covers API {api}; provision a credential for it first "
        "(POST /credentials), then approve the credential binding"
    )
