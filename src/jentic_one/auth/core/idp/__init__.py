"""Pluggable Identity Provider adapters."""

from jentic_one.auth.core.idp.adapter import IdpAdapter, IdpClaims
from jentic_one.auth.core.idp.oidc import OidcAdapter
from jentic_one.auth.core.idp.provisioning import (
    AdmissionDecision,
    AdmissionPolicy,
    get_admission_policy,
    open_admission_policy,
    set_admission_policy,
)

__all__ = [
    "AdmissionDecision",
    "AdmissionPolicy",
    "IdpAdapter",
    "IdpClaims",
    "OidcAdapter",
    "get_admission_policy",
    "open_admission_policy",
    "set_admission_policy",
]
