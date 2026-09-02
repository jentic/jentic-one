"""Enforce that secret-like config fields are typed ``SecretStr``.

Configuration fields holding sensitive values (secrets, passwords, peppers,
tokens, keys) must be typed as ``pydantic.SecretStr`` so they are redacted in
logs and ``repr`` output rather than leaking plaintext.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from .conftest import SRC_ROOT

CONFIG_FILE = SRC_ROOT / "shared" / "config.py"

SECRET_NAME_PARTS = ("secret", "password", "pepper", "token", "key")

# Fields that match the name heuristic but hold a REFERENCE to a secret's
# location, not the secret itself. `material_keychain` is the macOS Keychain
# service NAME the key material lives under (shared/crypto/key_material.py);
# the material never enters the config object.
NON_SECRET_FIELDS = frozenset({"material_keychain"})


def _is_secret_field(name: str) -> bool:
    """Return True if a field name looks like it holds a secret value."""
    if name in NON_SECRET_FIELDS:
        return False
    lowered = name.lower()
    return any(part in lowered for part in SECRET_NAME_PARTS)


def _annotation_str(node: ast.expr) -> str:
    """Best-effort rendering of an annotation node to source text."""
    return ast.unparse(node)


def _check_file(filepath: Path) -> list[str]:
    """Return violations for secret-like fields not typed ``SecretStr``."""
    source = filepath.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(filepath))
    violations: list[str] = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.AnnAssign):
            continue
        target = node.target
        if not isinstance(target, ast.Name):
            continue
        if not _is_secret_field(target.id):
            continue
        annotation = _annotation_str(node.annotation)
        if "SecretStr" not in annotation:
            violations.append(
                f"{filepath}:{node.lineno} — config field '{target.id}' is typed "
                f"'{annotation}' but holds a secret; use 'SecretStr'"
            )

    return violations


@pytest.mark.arch
def test_secrets_are_secretstr() -> None:
    """Secret-like config fields must be typed ``SecretStr``."""
    violations = _check_file(CONFIG_FILE)
    assert not violations, "Secret config fields must use pydantic.SecretStr:\n" + "\n".join(
        violations
    )
