"""Pin the OAuth-client none↔NULL-secret-hash coupling to its enforcement point.

Design §4.1: ``token_endpoint_auth_method='none'`` is allowed only when
``client_secret_hash IS NULL`` — a public client stores no secret, a
confidential client always stores one. The invariant is enforced at the
**service level** (``OAuthClientService.create`` derives the hash from the
auth method and never accepts one from callers; ``rotate_secret`` refuses
public clients), and ``authenticate_for_token_endpoint`` fails closed on any
row that violates it.

That enforcement only holds if the service stays the sole writer of the
secret hash. This test pins that: the repository write paths that can set or
change ``client_secret_hash`` (``OAuthClientRepository.create`` /
``update_secret_hash``) must not grow call sites outside
``admin/services/oauth_client_service.py``. A new writer (e.g. the 3a-2 DCR
front door) must either route through the service or replicate the invariant
and be added to the allowlist here — deliberately, in review.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from tests.arch.conftest import SRC_ROOT, python_files_in

_GUARDED_REPO_METHODS = frozenset({"create", "update_secret_hash"})
_ALLOWED_CALLERS = frozenset(
    {
        "admin/services/oauth_client_service.py",
        # The repository module itself (definitions, not call sites).
        "admin/repos/oauth_client_repo.py",
        # The 3a-2 anonymous DCR front door replicates the invariant by
        # construction: it hardcodes client_secret_hash=None together with
        # token_endpoint_auth_method='none' (public clients only — no caller
        # can supply either value), so the none↔NULL coupling cannot break.
        "auth/services/oauth_dcr_service.py",
    }
)


def _guarded_calls_in(path: Path) -> list[str]:
    """Return guarded ``OAuthClientRepository.<method>`` call sites in *path*."""
    try:
        tree = ast.parse(path.read_text())
    except SyntaxError:
        return []

    hits: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if (
            isinstance(func, ast.Attribute)
            and func.attr in _GUARDED_REPO_METHODS
            and isinstance(func.value, ast.Name)
            and func.value.id == "OAuthClientRepository"
        ):
            hits.append(f"{path.relative_to(SRC_ROOT)}:{node.lineno} ({func.attr})")
    return hits


@pytest.mark.arch
def test_oauth_client_secret_hash_writers_are_confined_to_the_service() -> None:
    violations: list[str] = []
    for path in python_files_in(SRC_ROOT):
        if str(path.relative_to(SRC_ROOT)) in _ALLOWED_CALLERS:
            continue
        violations.extend(_guarded_calls_in(path))

    assert not violations, (
        "OAuthClientRepository.create/update_secret_hash may only be called from "
        "OAuthClientService, which enforces the none↔NULL-hash coupling (§4.1). "
        "Route new writers through the service or extend the allowlist with the "
        "invariant replicated:\n" + "\n".join(f"  {v}" for v in violations)
    )
