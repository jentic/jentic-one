"""Serving the onboarding skill over HTTP against the real app factories.

Boots the production admin app factories — standalone ``create_app`` and
``create_combined_app(..., ["admin", ...])`` — and asserts issue #651's
contract: an agent can fetch the onboarding skill at ``GET /skills/jentic.md``
(and the ``/SKILL.md`` alias) with a markdown content-type, unauthenticated, in
both deploy modes; the served bytes are the single source of truth shared with
the CLI embed and so cannot drift.

No live database is required: ``Context`` is lazy and the app factories don't
touch the DB at construction; the lifespan (which would connect) is never
entered (no ``with client:``). This mirrors ``test_static_spa_real_app.py``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from jentic_one.admin.web.app import create_app as create_admin_app
from jentic_one.shared.config import AppConfig
from jentic_one.shared.context import Context
from jentic_one.shared.web.app_factory import create_combined_app
from jentic_one.shared.web.skill_router import (
    SKILL_ALIAS_PATH,
    SKILL_PATH,
    load_skill,
)

# The single committed source of truth the Go CLI embeds via `//go:embed`.
_CLI_SKILL_FILE = Path("cli/internal/skillgen/content/jentic.md")


@pytest.fixture()
def ctx(sample_config_dict: dict[str, Any]) -> Context:
    return Context(AppConfig.model_validate(sample_config_dict))


def _client(app: FastAPI) -> TestClient:
    # Never enter the lifespan: no DB connection is made.
    return TestClient(app, raise_server_exceptions=False)


def _repo_root() -> Path:
    """Walk up from this test to the repo root holding the CLI skill file."""
    for parent in Path(__file__).resolve().parents:
        if (parent / _CLI_SKILL_FILE).is_file():
            return parent
    raise AssertionError(f"could not locate {_CLI_SKILL_FILE} above {__file__}")


@pytest.fixture()
def cli_skill_source() -> str:
    """The exact bytes the Go CLI embeds — the drift-guard reference."""
    return (_repo_root() / _CLI_SKILL_FILE).read_text(encoding="utf-8")


def test_standalone_admin_serves_skill(ctx: Context, cli_skill_source: str) -> None:
    app = create_admin_app(ctx)
    client = _client(app)

    resp = client.get(SKILL_PATH)
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "text/markdown; charset=utf-8"
    assert resp.text == cli_skill_source
    # It is a real skill, not an empty file or an HTML shell.
    assert "name: jentic" in resp.text
    assert "# Using Jentic from the CLI" in resp.text


def test_standalone_admin_serves_skill_alias(ctx: Context, cli_skill_source: str) -> None:
    app = create_admin_app(ctx)
    client = _client(app)

    resp = client.get(SKILL_ALIAS_PATH)
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "text/markdown; charset=utf-8"
    assert resp.text == cli_skill_source


def test_skill_is_unauthenticated(ctx: Context) -> None:
    """Onboarding content must be fetchable before an agent holds any token."""
    app = create_admin_app(ctx)
    client = _client(app)

    # No Authorization header at all — must still succeed (not 401/403).
    resp = client.get(SKILL_PATH)
    assert resp.status_code == 200


def test_combined_app_serves_skill(ctx: Context, cli_skill_source: str) -> None:
    app = create_combined_app(ctx, ["admin", "control", "registry"])
    client = _client(app)

    for path in (SKILL_PATH, SKILL_ALIAS_PATH):
        resp = client.get(path)
        assert resp.status_code == 200, path
        assert resp.headers["content-type"] == "text/markdown; charset=utf-8", path
        assert resp.text == cli_skill_source, path


def test_skill_excluded_from_openapi_schema(ctx: Context) -> None:
    """The skill is tooling/onboarding content, not a product API surface."""
    app = create_combined_app(ctx, ["admin"])
    client = _client(app)

    spec = client.get("/openapi.json").json()
    paths = spec.get("paths", {})
    assert SKILL_PATH not in paths
    assert SKILL_ALIAS_PATH not in paths


def test_served_skill_matches_cli_embedded_source(cli_skill_source: str) -> None:
    """Drift guard: the served skill is byte-for-byte the CLI-embedded source.

    Both the Go CLI embed and the backend route read the *same* committed file
    (``cli/internal/skillgen/content/jentic.md``) — packaged into the wheel via
    ``pyproject.toml`` force-include — so they cannot diverge. This asserts that
    invariant directly so a future refactor that breaks the wiring fails loudly.
    """
    assert load_skill() == cli_skill_source
