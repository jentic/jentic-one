"""Permission-rule dry-run result — control-side parity with the broker.

Used by ``CredentialService.test_agent_permissions`` to answer "what would
the broker do for this ``(method, path, operation_id)``?" on a direct
agent↔credential binding without issuing a real upstream call. The binding's
rules are a single ordered first-match-wins list (the attached shared rule
set when one is present, inline rules otherwise), so a dry-run cannot lie
about which rule wins.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class PermissionTestResult:
    """The outcome of a permission-rule dry-run for a single request shape.

    ``matched`` is True iff a rule in the binding's ordered list matched the
    request; when no rule matched, ``allowed`` is False (default-deny) and
    the remaining fields are ``None``. When a rule did match, ``allowed``
    reflects its effect, and ``credential_id`` echoes the binding whose rule
    list contributed the match.
    """

    allowed: bool
    matched: bool
    effect: str | None
    rule_index: int | None
    credential_id: str | None
    is_system: bool | None
