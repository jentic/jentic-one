"""Pluggable Identity Provider adapters."""

from jentic_one.auth.core.idp.adapter import IdpAdapter, IdpClaims, parse_email_verified
from jentic_one.auth.core.idp.factory import clear_idp_adapters, get_idp_adapter
from jentic_one.auth.core.idp.oidc import GoogleOidcAdapter, OidcAdapter
from jentic_one.auth.core.idp.provisioning import (
    AdmissionDecision,
    AdmissionPolicy,
    DefaultGrantsProvider,
    get_admission_policy,
    get_default_idp_grants,
    no_default_grants,
    open_admission_policy,
    set_admission_policy,
    set_default_idp_grants,
)

__all__ = [
    "AdmissionDecision",
    "AdmissionPolicy",
    "DefaultGrantsProvider",
    "GoogleOidcAdapter",
    "IdpAdapter",
    "IdpClaims",
    "OidcAdapter",
    "clear_idp_adapters",
    "get_admission_policy",
    "get_default_idp_grants",
    "get_idp_adapter",
    "no_default_grants",
    "open_admission_policy",
    "parse_email_verified",
    "set_admission_policy",
    "set_default_idp_grants",
]
