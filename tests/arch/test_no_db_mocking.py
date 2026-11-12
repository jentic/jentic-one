"""Enforce that test files never mock database internals, ORM repositories, or transactions.

All persistence interactions in tests must run against a real database
(in-memory/file SQLite or PostgreSQL).

Allowed vs. forbidden test seams:
- **Allowed (HTTP / protocol fakes):** Tests may fake outbound provider HTTP
  transport (e.g. ``httpx.MockTransport`` or a provider subclass overriding
  ``_post_token``) or inject a pure in-memory protocol implementation (e.g.
  ``ConnectableRegistrationSourceProtocol``) that never emulates ORM sessions,
  transactions, or repositories.
- **Forbidden (persistence fakes):** Tests must not patch ``DatabaseSession``,
  ``AsyncSession``, ``create_async_engine``, ``asyncpg``, ``sqlalchemy``,
  ``*Repository`` classes/methods, or ``.transaction`` context managers.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from .conftest import python_files_in

pytestmark = pytest.mark.arch

TESTS_ROOT = Path(__file__).resolve().parent.parent

DB_SYMBOLS = frozenset(
    {
        "DatabaseSession",
        "AsyncSession",
        "create_async_engine",
        "asyncpg",
        "sqlalchemy",
    }
)

# Pre-existing test files that still patch repository methods or transaction
# context managers; new test files and migrated suites (such as
# ``unit/broker/test_refresh.py``) are not permitted here.
_LEGACY_REPO_OR_TX_MOCK_ALLOWLIST: frozenset[str] = frozenset(
    {
        "integration/admin/services/test_event_stream_service.py",
        "integration/auth/test_oauth_grant_archive_sweep.py",
        "integration/auth/test_oauth_grant_transfer.py",
        "integration/auth/test_token_endpoints.py",
        "integration/control/test_connect_session_edits.py",
        "integration/control/test_credential_service.py",
        "integration/control/test_service_account_migration.py",
        "integration/registry/ingest/test_revision_delete_count_race.py",
        "integration/registry/services/test_import_failure_classification.py",
        "unit/admin/services/test_actor_job_cancel.py",
        "unit/admin/services/test_monitoring_service_usage.py",
        "unit/admin/services/test_oauth_client_service.py",
        "unit/admin/services/test_passwords_concurrency.py",
        "unit/admin/test_events_conformance.py",
        "unit/auth/conftest.py",
        "unit/auth/test_agent_auth_service.py",
        "unit/auth/test_agent_claim_service.py",
        "unit/auth/test_agent_jwks_access.py",
        "unit/auth/test_agent_permissions.py",
        "unit/auth/test_agent_update_service.py",
        "unit/auth/test_assertion_service.py",
        "unit/auth/test_registration_scopes.py",
        "unit/auth/test_registration_service.py",
        "unit/auth/test_token_service.py",
        "unit/broker/conftest.py",
        "unit/broker/test_auth_failure_event.py",
        "unit/broker/test_circuit_event.py",
        "unit/broker/test_credential_service.py",
        "unit/broker/test_execute_credential_name.py",
        "unit/broker/test_resolver.py",
        "unit/broker/test_resolver_credential_name.py",
        "unit/broker/test_streaming_execution_persistence.py",
        "unit/control/credentials/test_device_authorization_provider.py",
        "unit/control/credentials/test_direct_oauth2_provider.py",
        "unit/control/integrations/flow_handlers/test_auth_code_handler.py",
        "unit/control/integrations/flow_handlers/test_device_authorization_handler.py",
        "unit/registry/ingest/test_extract_api_self_rematerialize.py",
        "unit/registry/ingest/test_observability.py",
        "unit/registry/services/test_api_mutations.py",
        "unit/registry/services/test_api_service_security.py",
        "unit/registry/services/test_catalog_refresh_lock.py",
        "unit/registry/services/test_catalog_supersede_gate.py",
        "unit/registry/services/test_catalog_update_notify.py",
        "unit/registry/services/test_import_handler_lifecycle.py",
        "unit/registry/services/test_inspect_service.py",
        "unit/registry/services/test_inspect_url_lookup.py",
        "unit/registry/services/test_note_service.py",
        "unit/registry/services/test_operation_service.py",
        "unit/registry/services/test_overlay_service.py",
        "unit/registry/services/test_revision_lifecycle.py",
        "unit/registry/services/test_spec_download_service.py",
        "unit/registry/web/routers/test_notes.py",
        "unit/registry/web/routers/test_overlays.py",
        "unit/shared/events/test_emit_tags.py",
        "unit/shared/events/test_mcp_session.py",
        "unit/shared/events/test_summary_label.py",
        "web/auth/test_oidc_login_flow.py",
    }
)


def _is_repo_or_tx_symbol(text: str) -> bool:
    parts = text.replace(":", ".").split(".")
    return any(
        part.endswith("Repository") or part in ("transaction", "acquire_refresh_lock")
        for part in parts
    )


def _check_source(
    source: str,
    *,
    filepath: Path = Path("<memory>"),
    check_repo_and_tx: bool = True,
) -> list[str]:
    """Return violation messages if ``source`` mocks DB, repository, or transaction seams."""
    tree = ast.parse(source, filename=str(filepath))
    violations: list[str] = []

    for node in ast.walk(tree):
        if check_repo_and_tx and isinstance(node, ast.Assign):
            for target_node in node.targets:
                unparsed = ast.unparse(target_node)
                if unparsed.endswith(".transaction") or ".transaction." in unparsed:
                    violations.append(
                        f"{filepath}:{node.lineno} — mocks transaction boundary '{unparsed}' "
                        f"(DB mocking forbidden; use real database connections)"
                    )
            continue

        if not isinstance(node, ast.Call):
            continue

        target = _resolve_call_target(node)
        if target in ("patch", "mock.patch", "unittest.mock.patch"):
            if not node.args:
                continue
            first_arg = node.args[0]
            if isinstance(first_arg, ast.Constant) and isinstance(first_arg.value, str):
                patch_target = first_arg.value
                if any(symbol in patch_target for symbol in DB_SYMBOLS) or (
                    check_repo_and_tx and _is_repo_or_tx_symbol(patch_target)
                ):
                    violations.append(
                        f"{filepath}:{node.lineno} — mocks '{patch_target}' "
                        f"(DB mocking forbidden; use real database connections)"
                    )
        elif (
            check_repo_and_tx
            and target.endswith(("patch.object", "monkeypatch.setattr"))
            and len(node.args) >= 2
        ):
            first_arg = node.args[0]
            first_str = (
                first_arg.value
                if isinstance(first_arg, ast.Constant) and isinstance(first_arg.value, str)
                else ast.unparse(first_arg)
            )
            second_arg = node.args[1]
            second_str = (
                second_arg.value
                if isinstance(second_arg, ast.Constant) and isinstance(second_arg.value, str)
                else ""
            )
            combined = f"{first_str}.{second_str}" if second_str else first_str
            if any(symbol in combined for symbol in DB_SYMBOLS) or _is_repo_or_tx_symbol(combined):
                violations.append(
                    f"{filepath}:{node.lineno} — mocks '{combined}' "
                    f"(DB mocking forbidden; use real database connections)"
                )

        if target in ("MagicMock", "AsyncMock", "Mock"):
            for kw in node.keywords:
                if kw.arg == "spec":
                    spec_name = (
                        kw.value.attr
                        if isinstance(kw.value, ast.Attribute)
                        else kw.value.id
                        if isinstance(kw.value, ast.Name)
                        else ""
                    )
                    if spec_name and (
                        spec_name in DB_SYMBOLS
                        or (check_repo_and_tx and _is_repo_or_tx_symbol(spec_name))
                    ):
                        violations.append(
                            f"{filepath}:{node.lineno} — creates mock with spec={spec_name} "
                            f"(DB mocking forbidden; use real database connections)"
                        )

    return violations


def _check_file(filepath: Path) -> list[str]:
    """Return violation messages if the file mocks DB-related symbols."""
    source = filepath.read_text(encoding="utf-8")
    rel_path = (
        filepath.relative_to(TESTS_ROOT).as_posix()
        if filepath.is_relative_to(TESTS_ROOT)
        else filepath.as_posix()
    )
    check_repo_and_tx = rel_path not in _LEGACY_REPO_OR_TX_MOCK_ALLOWLIST
    return _check_source(source, filepath=filepath, check_repo_and_tx=check_repo_and_tx)


def _resolve_call_target(node: ast.Call) -> str:
    """Resolve a Call node to a dotted name string."""
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        parts: list[str] = []
        current: ast.expr = node.func
        while isinstance(current, ast.Attribute):
            parts.append(current.attr)
            current = current.value
        if isinstance(current, ast.Name):
            parts.append(current.id)
        return ".".join(reversed(parts))
    return ""


_FLAGGED_SOURCES: dict[str, str] = {
    "patch_repository_class": (
        "from unittest.mock import patch\n"
        'with patch("jentic_one.broker.services.credentials.refresh.OAuthTokenRepository"):\n'
        "    pass\n"
    ),
    "patch_object_repository_method": (
        "from unittest.mock import patch\n"
        'with patch.object(OAuthTokenRepository, "get_by_credential"):\n'
        "    pass\n"
    ),
    "monkeypatch_repository_target": (
        "def test_it(monkeypatch):\n"
        '    monkeypatch.setattr("pkg.OAuthTokenRepository.update_tokens", lambda *a: None)\n'
    ),
    "assign_transaction_context_manager": (
        "def test_it(ctx):\n    ctx.control_db.transaction = lambda: None\n"
    ),
    "assign_transaction_return_value": (
        "def test_it(ctx, mock_tx):\n    ctx.admin_db.transaction.return_value = mock_tx\n"
    ),
    "mock_spec_repository": (
        "from unittest.mock import MagicMock\nm = MagicMock(spec=OAuthTokenRepository)\n"
    ),
    "mock_spec_async_session": (
        "from unittest.mock import AsyncMock\nm = AsyncMock(spec=db.AsyncSession)\n"
    ),
}

_ALLOWED_SOURCES: dict[str, str] = {
    "provider_http_transport_fake": (
        'from unittest.mock import patch\nwith patch("httpx.AsyncClient"):\n    pass\n'
    ),
    "provider_registry_entry": (
        "from unittest.mock import AsyncMock\n"
        "provider = AsyncMock()\n"
        "provider.refresh.return_value = None\n"
    ),
}


@pytest.mark.arch
def test_no_db_mocking() -> None:
    """No test file should mock database internals, repositories, or transactions."""
    assert "unit/broker/test_refresh.py" not in _LEGACY_REPO_OR_TX_MOCK_ALLOWLIST
    violations: list[str] = []
    for py_file in python_files_in(TESTS_ROOT):
        if py_file.is_relative_to(TESTS_ROOT / "arch"):
            continue
        violations.extend(_check_file(py_file))
    assert not violations, (
        "Test files must not mock database internals — use real DB connections:\n"
        + "\n".join(violations)
    )


@pytest.mark.arch
@pytest.mark.parametrize("source", _FLAGGED_SOURCES.values(), ids=_FLAGGED_SOURCES.keys())
def test_check_source_rejects_db_repository_and_transaction_mocks(source: str) -> None:
    """Representative repository, session, and transaction mocks are rejected."""
    violations = _check_source(source)
    assert len(violations) == 1


@pytest.mark.arch
@pytest.mark.parametrize("source", _ALLOWED_SOURCES.values(), ids=_ALLOWED_SOURCES.keys())
def test_check_source_allows_http_and_protocol_fakes(source: str) -> None:
    """Provider HTTP transport fakes and non-persistence mocks remain allowed."""
    assert _check_source(source) == []
