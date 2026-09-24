"""Enforce that no config secret ships a static default value.

Shipped images must not contain default credentials: AWS Marketplace's
container policy rejects hardcoded/default passwords found in image layers.
Their scanner flags the secret-shaped literal itself, so a boot-time
production guard does not satisfy the policy — the literal must not exist.

The rule enforced here: every ``SecretStr``-typed field reachable from
``AppConfig`` must default to empty/None/required. Dev ergonomics are provided
at load time by ``_require_or_generate_secret`` (random per-process value),
never by a literal default. The companion ``test_secrets_are_secretstr``
enforces the typing side; this test enforces the defaults side.
"""

from __future__ import annotations

import typing

import pytest
from pydantic import BaseModel, SecretStr
from pydantic_core import PydanticUndefined

from jentic_one.shared.config import AppConfig


def _models_reachable_from(root: type[BaseModel]) -> set[type[BaseModel]]:
    """All BaseModel classes reachable from *root* via field annotations."""
    seen: set[type[BaseModel]] = set()
    stack: list[type[BaseModel]] = [root]
    while stack:
        model = stack.pop()
        if model in seen:
            continue
        seen.add(model)
        for field in model.model_fields.values():
            stack.extend(_nested_models(field.annotation))
    return seen


def _nested_models(annotation: object) -> list[type[BaseModel]]:
    """BaseModel classes mentioned anywhere in a (possibly nested) annotation."""
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return [annotation]
    found: list[type[BaseModel]] = []
    for arg in typing.get_args(annotation):
        found.extend(_nested_models(arg))
    return found


def _is_secretstr(annotation: object) -> bool:
    """True if the annotation is SecretStr or contains it (e.g. ``SecretStr | None``)."""
    if annotation is SecretStr:
        return True
    return any(_is_secretstr(arg) for arg in typing.get_args(annotation))


@pytest.mark.arch
def test_no_static_secret_defaults() -> None:
    """No SecretStr field in the config tree may default to a non-empty value."""
    violations: list[str] = []
    for model in sorted(_models_reachable_from(AppConfig), key=lambda m: m.__name__):
        for name, field in model.model_fields.items():
            if not _is_secretstr(field.annotation):
                continue
            default = field.default
            if field.default_factory is not None:
                default = field.default_factory()  # type: ignore[call-arg]
            if default is PydanticUndefined or default is None:
                continue
            if isinstance(default, SecretStr) and not default.get_secret_value():
                continue
            violations.append(
                f"{model.__name__}.{name} ships a static secret default "
                f"({default!r}); default to SecretStr('') and wire the field "
                "through _require_or_generate_secret instead"
            )
    assert not violations, "\n".join(violations)
