"""Closed denial-reason vocabulary for broker authorization observability.

Every authorization denial the broker emits (403/409 on the execute path)
carries one of these reasons — stamped into the ``PBAC_DENIED`` event ``data``
and onto the ``broker.authorization.denied`` counter — so operators can split
the denial rate by cause without parsing free-text summaries. The enum is
**closed**: a new denial path must add its reason here (and nowhere else), so
dashboards never meet an unknown label.
"""

from __future__ import annotations

from enum import StrEnum


class DenialReason(StrEnum):
    """Why the broker denied an execute request at the authorization layer."""

    # --- binding derivation (who may use what) ---------------------------
    # Historical note: stored PBAC_DENIED events may carry the retired
    # ``no_toolkit_binding`` reason from the pre-6b legacy toolkit path; no
    # live path emits it and nothing parses stored reasons back through this
    # enum, so it has no member here.
    NO_CREDENTIAL_BINDING = "no_credential_binding"
    """Direct path: no active credential binding covers the API (403)."""

    CREDENTIAL_IDENTITY_MISMATCH = "credential_identity_mismatch"
    """Bound, but no bound credential's stored identity covers the API (403)."""

    # --- rule evaluation (what the binding may do) ------------------------
    NO_RULES_LOADED = "no_rules_loaded"
    """The binding's rule list is empty — nothing to match (403, default deny)."""

    NO_RULE_MATCHED = "no_rule_matched"
    """Rules loaded but none allowed the request — includes an explicit deny
    match (the evaluator's first-match-wins outcome does not distinguish an
    exhausted list from a matched ``deny``; both are "not allowed") (403)."""
