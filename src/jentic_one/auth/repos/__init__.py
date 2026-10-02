"""Auth-module repositories."""

from __future__ import annotations

from jentic_one.auth.repos.binding_rule_repo import BindingRuleRepository
from jentic_one.auth.repos.credential_ref_repo import CredentialRef, CredentialRefRepository

__all__ = [
    "BindingRuleRepository",
    "CredentialRef",
    "CredentialRefRepository",
]
