"""Pydantic result models for access-request effect application."""

from __future__ import annotations

from pydantic import BaseModel


class CredentialBindEffect(BaseModel):
    """Result of applying a credential-bind (agent↔credential) effect.

    ``credential_id`` records the concrete credential the bind resolved to —
    meaningful when the item was filed by API reference, where the id is only
    known at decide time. ``rule_set_id`` is set when the binding's policy is a
    shared rule set rather than inline rules (then ``rules_applied`` is 0).
    """

    binding_id: str
    credential_id: str
    rules_applied: int
    rule_set_id: str | None = None
    already_bound: bool


class ScopeGrantEffect(BaseModel):
    """Result of applying a scope-grant effect."""

    scope: str
    already_granted: bool


class SkippedEffect(BaseModel):
    """Result recorded for a fulfilment-only intent (an audited no-op)."""

    skipped: bool = True
    reason: str
