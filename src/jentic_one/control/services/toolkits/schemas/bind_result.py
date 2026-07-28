"""Bind-time signals: BindingWarning + BindResult for the ``bind_credential`` service."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from jentic_one.control.core.schema.toolkit_credential_bindings import ToolkitCredentialBinding
from jentic_one.control.core.schema.toolkit_permission_rules import ToolkitPermissionRule

if TYPE_CHECKING:
    from jentic_one.control.core.schema.toolkits import Toolkit

BINDING_WARNING_NO_RULES = "no_permission_rules"


@dataclass(frozen=True, slots=True)
class BindingWarning:
    """A non-fatal signal about a bind (or create-time inline bind).

    The broker defaults to deny when a binding has zero permission rules,
    so a bind that produces a zero-rule binding is a "you probably meant
    something else" moment. Surface it structurally in the same response,
    so a caller cannot ship a broken toolkit without seeing it.
    """

    code: str
    message: str
    credential_id: str | None = None


@dataclass(frozen=True, slots=True)
class BindResult:
    """Return value of ``ToolkitService.bind_credential``.

    Carries the binding, the permission rules effective on it (so the
    router does not need a second read), and any bind-time warnings
    (issue #750).
    """

    binding: ToolkitCredentialBinding
    rules: list[ToolkitPermissionRule]
    warnings: tuple[BindingWarning, ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class ToolkitCreateResult:
    """Return value of ``ToolkitService.create``.

    Carries the toolkit + issued plaintext key, and any bind-time
    warnings emitted for inline-bound ``credential_ids`` (issue #750
    review — the same discoverability gap applies when a bind happens
    during create).
    """

    toolkit: Toolkit
    plaintext_key: str
    warnings: tuple[BindingWarning, ...] = field(default_factory=tuple)
