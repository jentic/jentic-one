"""Integration tests for the permission-rule path matcher (#751 / #578).

Covers the write path end-to-end against a real database, on the direct
agent↔credential binding axis (the only axis since theme-5 Phase 6b):

* the ``match_mode`` column persists via ``replace_user_rules`` / ``patch_rules``
* the broker evaluator (raw ``text()`` SQL) reads it and enforces the mode
* invalid stored patterns fail closed (never match) instead of the pre-#751
  silent wildcard.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator

import pytest
from sqlalchemy import delete

from jentic_one.broker.repos.agent_rule_evaluator import AgentRuleEvaluator
from jentic_one.control.core.schema.agent_permission_rules import AgentPermissionRule
from jentic_one.control.core.schema.credentials import Credential
from jentic_one.control.repos.agent_permission_rule_repo import AgentPermissionRuleRepository
from jentic_one.shared.broker.protocols import RuleEvaluation
from jentic_one.shared.db.session import DatabaseSession

pytestmark = pytest.mark.integration

_VENDOR = "acme751.com"
_AGENT_ID = "agnt_test751"


@pytest.fixture()
async def clean_tables(control_db: DatabaseSession) -> AsyncGenerator[None, None]:
    async def _truncate() -> None:
        async with control_db.session() as session:
            await session.execute(
                delete(AgentPermissionRule).where(AgentPermissionRule.agent_id == _AGENT_ID)
            )
            await session.execute(delete(Credential).where(Credential.api_vendor == _VENDOR))
            await session.commit()

    await _truncate()
    yield
    await _truncate()


async def _seed(
    control_db: DatabaseSession,
    *,
    cred_id: str,
    api_name: str = "main",
) -> str:
    """Create the credential the binding's rules hang off; return its id."""
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
        session.add(credential)
        await session.commit()
    return cred_id


async def _evaluate(
    control_db: DatabaseSession, *, credential_id: str, method: str, path: str
) -> RuleEvaluation:
    evaluator = AgentRuleEvaluator(control_db, cache_ttl_seconds=0)
    return await evaluator.evaluate(
        agent_id=_AGENT_ID,
        credential_id=credential_id,
        rule_set_id=None,
        method=method,
        path=path,
        operation_id=None,
    )


# ---------------------------------------------------------------------------
# Storage: match_mode round-trip
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_replace_user_rules_persists_match_mode(
    control_db: DatabaseSession, clean_tables: None
) -> None:
    cred_id = await _seed(control_db, cred_id="cred_751_round")

    async with control_db.session() as session:
        await AgentPermissionRuleRepository.replace_user_rules(
            session,
            _AGENT_ID,
            cred_id,
            [
                {"effect": "allow", "path": "/v1/things", "match_mode": "prefix"},
                {"effect": "deny", "path": "/v1/things/delete", "match_mode": "exact"},
            ],
            created_by="test",
        )
        await session.commit()

        rules = await AgentPermissionRuleRepository.list_rules(session, _AGENT_ID, cred_id)

    modes = [r.match_mode for r in rules]
    assert modes == ["prefix", "exact"]


@pytest.mark.asyncio
async def test_replace_user_rules_defaults_match_mode_to_regex(
    control_db: DatabaseSession, clean_tables: None
) -> None:
    # A rule dict that omits ``match_mode`` — the effect applicator for access
    # requests emits dicts this way — lands as ``regex`` for compatibility.
    cred_id = await _seed(control_db, cred_id="cred_751_default")

    async with control_db.session() as session:
        await AgentPermissionRuleRepository.replace_user_rules(
            session, _AGENT_ID, cred_id, [{"effect": "allow", "path": ".*"}], created_by="test"
        )
        await session.commit()
        rules = await AgentPermissionRuleRepository.list_rules(session, _AGENT_ID, cred_id)

    assert [r.match_mode for r in rules] == ["regex"]


# ---------------------------------------------------------------------------
# Enforcement: broker reads match_mode and uses the shared matcher
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_broker_enforces_prefix_match_mode(
    control_db: DatabaseSession, clean_tables: None
) -> None:
    cred_id = await _seed(control_db, cred_id="cred_751_prefix")

    async with control_db.session() as session:
        await AgentPermissionRuleRepository.replace_user_rules(
            session,
            _AGENT_ID,
            cred_id,
            [{"effect": "allow", "path": "/v1/things", "match_mode": "prefix"}],
            created_by="test",
        )
        await session.commit()

    # Prefix mode is literal: substring after the prefix is fine…
    result = await _evaluate(control_db, credential_id=cred_id, method="GET", path="/v1/things/42")
    assert result.allowed is True
    # …but a different prefix does not match.
    result = await _evaluate(control_db, credential_id=cred_id, method="GET", path="/v2/things")
    assert result.allowed is False


@pytest.mark.asyncio
async def test_broker_full_match_regex_rejects_trailing_content(
    control_db: DatabaseSession, clean_tables: None
) -> None:
    # #751 anchoring migration: ``.match()`` accepted ``/v1/users/42/extra`` for
    # ``/v1/users/\d+``; ``.fullmatch()`` (via the shared matcher) rejects it.
    cred_id = await _seed(control_db, cred_id="cred_751_full")

    async with control_db.session() as session:
        await AgentPermissionRuleRepository.replace_user_rules(
            session,
            _AGENT_ID,
            cred_id,
            [{"effect": "allow", "path": r"/v1/users/\d+", "match_mode": "regex"}],
            created_by="test",
        )
        await session.commit()

    result = await _evaluate(control_db, credential_id=cred_id, method="GET", path="/v1/users/42")
    assert result.allowed is True
    result = await _evaluate(
        control_db, credential_id=cred_id, method="GET", path="/v1/users/42/roles"
    )
    assert result.allowed is False


@pytest.mark.asyncio
async def test_broker_fail_closed_on_stored_invalid_pattern(
    control_db: DatabaseSession, clean_tables: None
) -> None:
    # A legacy row that predates ``validate_path`` (bypass the API by writing
    # directly with the ORM) must fail closed at enforcement — the opposite
    # of the pre-#751 silent-wildcard.
    cred_id = await _seed(control_db, cred_id="cred_751_legacy")

    async with control_db.session() as session:
        session.add(
            AgentPermissionRule(
                agent_id=_AGENT_ID,
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

    result = await _evaluate(control_db, credential_id=cred_id, method="GET", path="/any")
    assert result.allowed is False


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
    cred_id = await _seed(control_db, cred_id="cred_578_wild")

    async with control_db.session() as session:
        await AgentPermissionRuleRepository.replace_user_rules(
            session,
            _AGENT_ID,
            cred_id,
            [{"effect": "allow", "path": ".*", "match_mode": "regex"}],
            created_by="test",
        )
        await session.commit()

    result = await _evaluate(
        control_db, credential_id=cred_id, method="POST", path="/deep/nested/resource/42"
    )
    assert result.allowed is True
    assert result.rules_loaded == 1


@pytest.mark.asyncio
async def test_deny_variant_distinguishes_empty_binding_from_no_match(
    control_db: DatabaseSession, clean_tables: None
) -> None:
    # #578 diagnostic: the router keys its detail sentence on rules_loaded.
    # A binding with no rules yields a zero-length list (rules are keyed
    # strictly per (agent, credential) — no vendor pooling); a binding whose
    # rules all miss yields a non-zero count with allowed=False.
    cred_id = await _seed(control_db, cred_id="cred_578_variants")

    async with control_db.session() as session:
        await AgentPermissionRuleRepository.replace_user_rules(
            session,
            _AGENT_ID,
            cred_id,
            [{"effect": "allow", "path": "/only/this", "match_mode": "exact"}],
            created_by="test",
        )
        await session.commit()

    # Empty-binding branch: a different credential's binding has no rules.
    empty = await _evaluate(
        control_db, credential_id="cred_578_other", method="GET", path="/only/this"
    )
    assert empty.allowed is False
    assert empty.rules_loaded == 0

    # Loaded-but-no-match branch: the rules exist, nothing matched.
    no_match = await _evaluate(
        control_db, credential_id=cred_id, method="GET", path="/somewhere/else"
    )
    assert no_match.allowed is False
    assert no_match.rules_loaded == 1
