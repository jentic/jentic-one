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

# URL/endpoint fields are addressable resources, not secret material — even when
# the resource itself deals in secrets (e.g. an OAuth ``token_endpoint`` URL).
# The URL string is logged, embedded in vendor discovery responses, and shown
# to operators for debugging; typing it ``SecretStr`` would mis-model it and
# suppress useful diagnostic output. Names ending in one of these suffixes are
# treated as URL-typed and skipped by the secret-field heuristic.
URL_LIKE_SUFFIXES = ("_url", "_endpoint", "_uri")


def _is_secret_field(name: str) -> bool:
    """Return True if a field name looks like it holds a secret value."""
    lowered = name.lower()
    if any(lowered.endswith(suffix) for suffix in URL_LIKE_SUFFIXES):
        return False
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
