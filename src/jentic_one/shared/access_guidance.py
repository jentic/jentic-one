"""Canonical access-recovery guidance for the broker's missing-binding path.

An API is only "served" once a credential covering it is provisioned; only
then can an agent be bound to it. When nothing serves an API yet, the broker's
missing-binding recovery directive must recommend provisioning a credential as
the first step (see issue #683).

Historically this wording was shared with the control access-request approval
flow so a denial reason could never contradict the broker directive; theme 7
removed that flow, leaving the broker as the sole consumer. It stays under
``shared`` as the single wording module pending a possible fold into
``broker/core/exceptions.py`` (theme-7 open question)."""

from __future__ import annotations


def no_credential_serves_api_reason(api: str) -> str:
    """The canonical reason when no credential serves ``api`` yet.

    ``api`` is a ``vendor[/name][@version]`` label. The recommended first
    step — provision a credential — matches the broker's missing-binding
    directives (see issue #683). Phrased as a statement of the condition plus
    the recommended first step, matching the broker directive.
    """
    return (
        f"No credential covers API {api}; provision a credential for it first "
        "(POST /credentials), then approve the credential binding"
    )
