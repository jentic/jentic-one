"""Integration tests for the toolkit permission-rule path matcher.

Covers the #751 write path end-to-end against a real database:

* the new ``match_mode`` column persists via ``replace_user_rules`` / ``patch_rules``
* the broker evaluator (raw ``text()`` SQL) reads it and enforces the mode
* the vendor-pooled dry-run service reflects the enforcer's decision
* invalid stored patterns fail closed (never match) instead of the pre-#751
  silent wildcard.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator

import pytest
from sqlalchemy import delete

from jentic_one.broker.repos.rule_evaluator import RuleEvaluator
from jentic_one.control.core.schema.credentials import Credential
from jentic_one.control.core.schema.toolkit_credential_bindings import ToolkitCredentialBinding
from jentic_one.control.core.schema.toolkit_permission_rules import ToolkitPermissionRule
from jentic_one.control.core.schema.toolkits import Toolkit
from jentic_one.control.repos.toolkit_permission_repo import ToolkitPermissionRepository
from jentic_one.control.services.toolkits.service import ToolkitService
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.context import Context
from jentic_one.shared.db.session import DatabaseSession
from jentic_one.shared.models import ActorType

pytestmark = pytest.mark.integration

_VENDOR = "acme751.com"
_IDENTITY = Identity(sub="usr_test751", actor_type=ActorType.USER, permissions=["org:admin"])


@pytest.fixture()
async def clean_tables(control_db: DatabaseSession) -> AsyncGenerator[None, None]:
    async def _truncate() -> None:
        async with control_db.session() as session:
            await session.execute(delete(ToolkitPermissionRule))
            await session.execute(delete(ToolkitCredentialBinding))
            await session.execute(delete(Credential).where(Credential.api_vendor == _VENDOR))
            await session.execute(delete(Toolkit).where(Toolkit.name.like("tk751-%")))
            await session.commit()

    await _truncate()
    yield
    await _truncate()


async def _seed(
    control_db: DatabaseSession,
    *,
    name: str,
    cred_id: str,
    api_name: str = "main",
) -> tuple[str, str]:
    toolkit = Toolkit(name=name)
    credential = Credential(
        id=cred_id,
        type="token_value",
        name=f"cred-{cred_id}",
        api_vendor=_VENDOR,
        api_name=api_name,
        api_version="1",
        active=True,
    )
    async with control_db.session() as session:
        session.add(toolkit)
        session.add(credential)
        await session.flush()
        tk_id = toolkit.id
        session.add(
            ToolkitCredentialBinding(toolkit_id=tk_id, credential_id=cred_id, created_by="test")
        )
        await session.commit()
    return tk_id, cred_id


# ---------------------------------------------------------------------------
# Storage: match_mode round-trip
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_replace_user_rules_persists_match_mode(
    control_db: DatabaseSession, clean_tables: None
) -> None:
    tk_id, cred_id = await _seed(control_db, name="tk751-round", cred_id="cred_751_round")

    async with control_db.session() as session:
        await ToolkitPermissionRepository.replace_user_rules(
            session,
            tk_id,
            cred_id,
            [
                {"effect": "allow", "path": "/v1/things", "match_mode": "prefix"},
                {"effect": "deny", "path": "/v1/things/delete", "match_mode": "exact"},
            ],
            created_by="test",
        )
        await session.commit()

        rules = await ToolkitPermissionRepository.list_rules(session, tk_id, cred_id)

    modes = [r.match_mode for r in rules]
    assert modes == ["prefix", "exact"]


@pytest.mark.asyncio
async def test_replace_user_rules_defaults_match_mode_to_regex(
    control_db: DatabaseSession, clean_tables: None
) -> None:
    # A rule dict that omits ``match_mode`` — the effect applicator for access
    # requests emits dicts this way — lands as ``regex`` for compatibility.
    tk_id, cred_id = await _seed(control_db, name="tk751-default", cred_id="cred_751_default")

    async with control_db.session() as session:
        await ToolkitPermissionRepository.replace_user_rules(
            session, tk_id, cred_id, [{"effect": "allow", "path": ".*"}], created_by="test"
        )
        await session.commit()
        rules = await ToolkitPermissionRepository.list_rules(session, tk_id, cred_id)

    assert [r.match_mode for r in rules] == ["regex"]


# ---------------------------------------------------------------------------
# Enforcement: broker reads match_mode and uses the shared matcher
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_broker_enforces_prefix_match_mode(
    control_db: DatabaseSession, clean_tables: None
) -> None:
    tk_id, cred_id = await _seed(control_db, name="tk751-prefix", cred_id="cred_751_prefix")

    async with control_db.session() as session:
        await ToolkitPermissionRepository.replace_user_rules(
            session,
            tk_id,
            cred_id,
            [{"effect": "allow", "path": "/v1/things", "match_mode": "prefix"}],
            created_by="test",
        )
        await session.commit()

    evaluator = RuleEvaluator(control_db, cache_ttl_seconds=0)
    # Prefix mode is literal: substring after the prefix is fine…
    result = await evaluator.evaluate(
        toolkit_id=tk_id,
        method="GET",
        path="/v1/things/42",
        operation_id=None,
        api_vendor=_VENDOR,
    )
    assert result.allowed is True
    # …but a different prefix does not match.
    result = await evaluator.evaluate(
        toolkit_id=tk_id,
        method="GET",
        path="/v2/things",
        operation_id=None,
        api_vendor=_VENDOR,
    )
    assert result.allowed is False


@pytest.mark.asyncio
async def test_broker_full_match_regex_rejects_trailing_content(
    control_db: DatabaseSession, clean_tables: None
) -> None:
    # #751 anchoring migration: ``.match()`` accepted ``/v1/users/42/extra`` for
    # ``/v1/users/\d+``; ``.fullmatch()`` (via the shared matcher) rejects it.
    tk_id, cred_id = await _seed(control_db, name="tk751-full", cred_id="cred_751_full")

    async with control_db.session() as session:
        await ToolkitPermissionRepository.replace_user_rules(
            session,
            tk_id,
            cred_id,
            [{"effect": "allow", "path": r"/v1/users/\d+", "match_mode": "regex"}],
            created_by="test",
        )
        await session.commit()

    evaluator = RuleEvaluator(control_db, cache_ttl_seconds=0)
    result = await evaluator.evaluate(
        toolkit_id=tk_id,
        method="GET",
        path="/v1/users/42",
        operation_id=None,
        api_vendor=_VENDOR,
    )
    assert result.allowed is True
    result = await evaluator.evaluate(
        toolkit_id=tk_id,
        method="GET",
        path="/v1/users/42/roles",
        operation_id=None,
        api_vendor=_VENDOR,
    )
    assert result.allowed is False


@pytest.mark.asyncio
async def test_broker_fail_closed_on_stored_invalid_pattern(
    control_db: DatabaseSession, clean_tables: None
) -> None:
    # A legacy row that predates ``validate_path`` (bypass the API by writing
    # directly with the ORM) must fail closed at enforcement — the opposite
    # of today's silent-wildcard.
    tk_id, cred_id = await _seed(control_db, name="tk751-legacy", cred_id="cred_751_legacy")

    async with control_db.session() as session:
        session.add(
            ToolkitPermissionRule(
                toolkit_id=tk_id,
                credential_id=cred_id,
                effect="allow",
                methods=None,
                path="[unterminated",
                match_mode="regex",
                operations=None,
                is_system=False,
                comment=None,
                sequence=0,
                created_by="test",
            )
        )
        await session.commit()

    evaluator = RuleEvaluator(control_db, cache_ttl_seconds=0)
    result = await evaluator.evaluate(
        toolkit_id=tk_id,
        method="GET",
        path="/any",
        operation_id=None,
        api_vendor=_VENDOR,
    )
    assert result.allowed is False


# ---------------------------------------------------------------------------
# Dry-run parity with the broker (vendor pooling)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dry_run_pools_rules_across_same_vendor_bindings(
    control_db: DatabaseSession, integration_context: Context, clean_tables: None
) -> None:
    # Two bindings for the same vendor: a rule attached to binding B allows
    # the request when queried under binding A. The broker sees the pooled
    # set, and so must the dry-run.
    toolkit = Toolkit(name="tk751-pool")
    cred_a = Credential(
        id="cred_751_pool_a",
        type="token_value",
        name="cred-a",
        api_vendor=_VENDOR,
        api_name="one",
        api_version="1",
        active=True,
    )
    cred_b = Credential(
        id="cred_751_pool_b",
        type="token_value",
        name="cred-b",
        api_vendor=_VENDOR,
        api_name="two",
        api_version="1",
        active=True,
    )
    async with control_db.session() as session:
        session.add(toolkit)
        session.add(cred_a)
        session.add(cred_b)
        await session.flush()
        tk_id = toolkit.id
        session.add(
            ToolkitCredentialBinding(toolkit_id=tk_id, credential_id=cred_a.id, created_by="test")
        )
        session.add(
            ToolkitCredentialBinding(toolkit_id=tk_id, credential_id=cred_b.id, created_by="test")
        )
        await session.commit()

    async with control_db.session() as session:
        await ToolkitPermissionRepository.replace_user_rules(
            session,
            tk_id,
            cred_b.id,
            [{"effect": "allow", "path": "/v1/thing", "match_mode": "exact"}],
            created_by="test",
        )
        await session.commit()

    svc = ToolkitService(integration_context)
    # Query the dry-run under binding A (which has zero rules of its own).
    result = await svc.test_permissions(
        tk_id,
        cred_a.id,
        method="GET",
        path="/v1/thing",
        operation_id=None,
        identity=_IDENTITY,
    )
    assert result.matched is True
    assert result.allowed is True
    # Vendor pooling means the winning rule lives on binding B — the
    # response names it explicitly so an operator understands what happened.
    assert result.credential_id == cred_b.id


@pytest.mark.asyncio
async def test_dry_run_reports_default_deny_when_no_rule_matches(
    control_db: DatabaseSession, integration_context: Context, clean_tables: None
) -> None:
    tk_id, cred_id = await _seed(control_db, name="tk751-noop", cred_id="cred_751_noop")

    async with control_db.session() as session:
        await ToolkitPermissionRepository.replace_user_rules(
            session,
            tk_id,
            cred_id,
            [{"effect": "allow", "path": "/only/this", "match_mode": "exact"}],
            created_by="test",
        )
        await session.commit()

    svc = ToolkitService(integration_context)
    result = await svc.test_permissions(
        tk_id,
        cred_id,
        method="GET",
        path="/something/else",
        operation_id=None,
        identity=_IDENTITY,
    )
    assert result.matched is False
    assert result.allowed is False
    assert result.credential_id is None


# ---------------------------------------------------------------------------
# #578: wildcard `.*` rule + two-variant deny diagnostic
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_wildcard_rule_allows_end_to_end(
    control_db: DatabaseSession, clean_tables: None
) -> None:
    # #578 regression: a single `{effect:allow, path:".*"}` rule must
    # allow every request under full-match regex semantics — this was
    # the exact user-visible complaint that closed as "denied despite
    # wildcard rule".
    tk_id, cred_id = await _seed(control_db, name="tk578-wild", cred_id="cred_578_wild")

    async with control_db.session() as session:
        await ToolkitPermissionRepository.replace_user_rules(
            session,
            tk_id,
            cred_id,
            [{"effect": "allow", "path": ".*", "match_mode": "regex"}],
            created_by="test",
        )
        await session.commit()

    evaluator = RuleEvaluator(control_db, cache_ttl_seconds=0)
    result = await evaluator.evaluate(
        toolkit_id=tk_id,
        method="POST",
        path="/deep/nested/resource/42",
        operation_id="anyOp",
        api_vendor=_VENDOR,
    )
    assert result.allowed is True
    assert result.rules_loaded == 1


@pytest.mark.asyncio
async def test_deny_variant_distinguishes_empty_pool_from_no_match(
    control_db: DatabaseSession, clean_tables: None
) -> None:
    # #578 diagnostic: the router keys its detail sentence on rules_loaded.
    # A wrong-vendor request produces a zero-length pool (the DB join
    # filters by api_vendor); a same-vendor request that misses every rule
    # produces a non-zero pool with allowed=False.
    tk_id, cred_id = await _seed(control_db, name="tk578-variants", cred_id="cred_578_variants")

    async with control_db.session() as session:
        await ToolkitPermissionRepository.replace_user_rules(
            session,
            tk_id,
            cred_id,
            [{"effect": "allow", "path": "/only/this", "match_mode": "exact"}],
            created_by="test",
        )
        await session.commit()

    evaluator = RuleEvaluator(control_db, cache_ttl_seconds=0)

    # Empty-pool branch: no rules for this vendor.
    empty_pool = await evaluator.evaluate(
        toolkit_id=tk_id,
        method="GET",
        path="/only/this",
        operation_id=None,
        api_vendor="different-vendor.com",
    )
    assert empty_pool.allowed is False
    assert empty_pool.rules_loaded == 0

    # Loaded-but-no-match branch: the pool exists, nothing matched.
    no_match = await evaluator.evaluate(
        toolkit_id=tk_id,
        method="GET",
        path="/somewhere/else",
        operation_id=None,
        api_vendor=_VENDOR,
    )
    assert no_match.allowed is False
    assert no_match.rules_loaded == 1
