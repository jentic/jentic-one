"""Permission-rule dry-run result — control-side parity with the broker.

Used by ``ToolkitService.test_permissions`` to answer "what would the
broker do for this ``(method, path, operation_id)``?" without issuing a
real upstream call. The service pools rules with the same query shape as
the broker's ``_RULES_QUERY`` (see ``ToolkitPermissionRepository.
list_rules_for_vendor``), so a dry-run cannot lie about which rule wins.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class PermissionTestResult:
    """The outcome of a permission-rule dry-run for a single request shape.

    ``matched`` is True iff a rule in the pooled set matched the request;
    when no rule matched, ``allowed`` is False (default-deny) and the
    remaining fields are ``None``. When a rule did match, ``allowed``
    reflects its effect, and ``credential_id`` names *which* binding
    contributed the matching rule — with vendor pooling that is not
    obvious from the toolkit id alone.
    """

    allowed: bool
    matched: bool
    effect: str | None
    rule_index: int | None
    credential_id: str | None
    is_system: bool | None
