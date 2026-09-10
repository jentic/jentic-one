"""End-to-end tests of the provisioning-plan access-request flow (Phase 3 shape).

Drives the real service layer against a real DB: an agent files a provisioning
plan — the 2-item ``credential:provision`` + ``credential:bind`` chain — the
"wizard" fulfils the provision step by creating a real credential and amending
its id onto the bind item, then the operator approves the whole request.
Asserts the approval actually wired the direct agent↔credential binding (+
control-DB rules) — i.e. the plan reaches an executable state, not a hollow
yes — and that the broker's execute-path resolvers can derive it.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any

import pytest
from sqlalchemy import delete, select, text

from jentic_one.broker.repos.credential_binding_resolver import CredentialBindingResolver
from jentic_one.broker.services.credentials.resolver import CredentialResolver
from jentic_one.control.core.schema.access_request_items import AccessRequestItem
from jentic_one.control.core.schema.access_requests import AccessRequest
from jentic_one.control.core.schema.credentials import Credential
from jentic_one.control.services.access_requests.service import AccessRequestService
from jentic_one.control.services.credentials.schemas.credentials import CredentialCreate
from jentic_one.control.services.credentials.schemas.provision import APIReference
from jentic_one.control.services.credentials.service import CredentialService
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.context import Context
from jentic_one.shared.db.session import DatabaseSession
from jentic_one.shared.models import ActorType
from jentic_one.shared.models.credentials import CredentialType

pytestmark = pytest.mark.integration

AGENT_SUB = "agnt_plan_e2e_001"
OWNER_SUB = "usr_plan_e2e_owner_001"


def _agent_identity() -> Identity:
    return Identity(
        sub=AGENT_SUB,
        email="agent@test.local",
        permissions=[],
        actor_type=ActorType.AGENT,
        parent_actor_id=OWNER_SUB,
    )


def _owner_identity() -> Identity:
    # The operator who owns the filing agent and can decide + create resources.
    return Identity(
        sub=OWNER_SUB,
        email="owner@test.local",
        permissions=["agents:write", "credentials:write"],
    )


@pytest.fixture()
async def clean(
    control_db: DatabaseSession, admin_db: DatabaseSession
) -> AsyncGenerator[None, None]:
    async def _wipe() -> None:
        async with control_db.session() as session:
            await session.execute(delete(AccessRequestItem))
            await session.execute(delete(AccessRequest))
            await session.execute(
                text("DELETE FROM agent_permission_rules WHERE agent_id = :a"), {"a": AGENT_SUB}
            )
            await session.execute(delete(Credential))
            await session.commit()
        async with admin_db.session() as session:
            await session.execute(
                text("DELETE FROM agent_credential_bindings WHERE agent_id = :a"),
                {"a": AGENT_SUB},
            )
            await session.execute(
                text("DELETE FROM actor_scope_grants WHERE actor_id = :a"), {"a": AGENT_SUB}
            )
            await session.execute(text("DELETE FROM agents WHERE id = :a"), {"a": AGENT_SUB})
            await session.commit()

    await _wipe()
    # The agent must exist for the credential:bind admin effect's FK.
    async with admin_db.session() as session:
        await session.execute(
            text(
                "INSERT INTO agents (id, name, registered_by, status) "
                "VALUES (:id, :name, :rb, 'active') ON CONFLICT DO NOTHING"
            ),
            {"id": AGENT_SUB, "name": "plan-e2e-agent", "rb": OWNER_SUB},
        )
        await session.commit()
    yield
    await _wipe()


async def _agent_binding_row(ctx: Context, credential_id: str) -> tuple[str, str | None] | None:
    """The agent's admin binding row for ``credential_id`` (id, rule_set_id)."""
    async with ctx.admin_db.session() as session:
        row = await session.execute(
            text(
                "SELECT id, rule_set_id FROM agent_credential_bindings "
                "WHERE agent_id = :a AND credential_id = :c"
            ),
            {"a": AGENT_SUB, "c": credential_id},
        )
        found = row.first()
    return (str(found[0]), found[1]) if found is not None else None


async def _agent_rules(ctx: Context, credential_id: str) -> list[Any]:
    async with ctx.control_db.session() as session:
        rows = await session.execute(
            text(
                "SELECT effect, path FROM agent_permission_rules "
                "WHERE agent_id = :a AND credential_id = :c"
            ),
            {"a": AGENT_SUB, "c": credential_id},
        )
        return list(rows.fetchall())


async def test_provisioning_plan_end_to_end(integration_context: Context, clean: None) -> None:
    ctx = integration_context
    access_svc = AccessRequestService(ctx)
    cred_svc = CredentialService(ctx)

    api = {"vendor": "httpbin.org", "name": "httpbin", "version": "1.0.0"}

    # 1. AGENT files the provisioning plan (as the CLI --provision builder does):
    #    the 2-item credential:provision + credential:bind chain.
    plan_items: list[dict[str, Any]] = [
        {
            "resource_type": "credential",
            "action": "provision",
            "resource_reference": {**api, "security_scheme": "bearer"},
        },
        {
            "resource_type": "credential",
            "action": "bind",
            "resource_reference": api,
            "rules": [{"effect": "allow", "methods": ["GET"], "path": ".*"}],
        },
    ]
    view = await access_svc.file(
        actor_id=AGENT_SUB,
        reason="Make httpbin executable",
        items=plan_items,
        identity=_agent_identity(),
    )
    assert view.status == "pending"
    assert len(view.items) == 2
    bind_item = next(
        i for i in view.items if i.resource_type == "credential" and i.action == "bind"
    )

    # 2. WIZARD (operator) fulfils the provision step with a real credential.
    created_cred = await cred_svc.create(
        CredentialCreate(
            type=CredentialType.BEARER_TOKEN,
            name="httpbin cred",
            api=APIReference(vendor="httpbin.org", name="httpbin", version="1.0.0"),
            token="secret-token-value-123",
        ),
        identity=_owner_identity(),
    )

    # 3. WIZARD amends the resolved credential id + confirmed rules onto the bind.
    await access_svc.amend(
        view.id,
        identity=_owner_identity(),
        item_amendments=[
            {
                "item_id": bind_item.id,
                "resource_id": created_cred.credential_id,
                "rules": [{"effect": "allow", "methods": ["GET"], "path": ".*"}],
            },
        ],
    )

    # 4. OPERATOR approves every pending item.
    refreshed = await access_svc.get(view.id, identity=_owner_identity())
    decisions = [
        {"item_id": i.id, "decision": "approved"} for i in refreshed.items if i.status == "pending"
    ]
    decided = await access_svc.decide(view.id, identity=_owner_identity(), item_decisions=decisions)

    # 5. ASSERT the plan reached an executable state (not a hollow yes):
    #    the direct agent↔credential binding + its control-DB rules both exist.
    assert decided.status == "approved", [
        (i.resource_type, i.action, i.status, i.decision_reason) for i in decided.items
    ]

    binding = await _agent_binding_row(ctx, created_cred.credential_id)
    assert binding is not None, "credential:bind did not create an agent_credential_binding"
    rules = await _agent_rules(ctx, created_cred.credential_id)
    assert any(effect == "allow" for effect, _path in rules), "no allow rule written on the bind"

    # The broker's execute-path derivation reports the binding, so an agent can
    # tell it already has access without a throwaway denied execute.
    resolver = CredentialBindingResolver(ctx.admin_db, ctx.control_db)
    derivation = await resolver.derive_credentials(
        agent_id=AGENT_SUB, vendor="httpbin-org", name="httpbin", version="1.0.0"
    )
    assert [c.credential_id for c in derivation.credentials] == [created_cred.credential_id]


async def test_provisioning_plan_fulfilled_with_existing_credential(
    integration_context: Context, clean: None
) -> None:
    """A plan can be fulfilled with a PRE-EXISTING credential — the reuse path (#897).

    The operator already holds a covering credential and wants the agent bound
    to IT, not a duplicate provisioned. The wizard's "use existing" choice
    amends the bind item at the existing credential id and — audit honesty —
    stamps the inert ``credential:provision`` placeholder with the id that
    fulfilled it, so the approved record reads "fulfilled by cred_…" instead of
    implying a provision that never happened. Must reach FULL approval (the
    agent's ``--wait`` exits 0) with no second credential anywhere.
    """
    ctx = integration_context
    access_svc = AccessRequestService(ctx)
    cred_svc = CredentialService(ctx)

    # The operator's pre-existing credential — created BEFORE the plan is filed.
    existing_cred = await cred_svc.create(
        CredentialCreate(
            type=CredentialType.BEARER_TOKEN,
            name="httpbin cred (pre-existing)",
            api=APIReference(vendor="httpbin.org", name="httpbin", version="1.0.0"),
            token="secret-token-value-456",
        ),
        identity=_owner_identity(),
    )

    api = {"vendor": "httpbin.org", "name": "httpbin", "version": "1.0.0"}
    view = await access_svc.file(
        actor_id=AGENT_SUB,
        reason="Bind to the existing credential",
        items=[
            {
                "resource_type": "credential",
                "action": "provision",
                "resource_reference": {**api, "security_scheme": "bearer"},
            },
            {
                "resource_type": "credential",
                "action": "bind",
                "resource_reference": api,
                "rules": [{"effect": "allow", "methods": ["GET"], "path": ".*"}],
            },
        ],
        identity=_agent_identity(),
    )
    by_key = {(i.resource_type, i.action): i for i in view.items}

    await access_svc.amend(
        view.id,
        identity=_owner_identity(),
        item_amendments=[
            {
                "item_id": by_key[("credential", "bind")].id,
                "resource_id": existing_cred.credential_id,
                "rules": [{"effect": "allow", "methods": ["GET"], "path": ".*"}],
            },
            # Placeholder stamping: resource_id on the fulfilment-only item must
            # be amendable so the record names the reused object.
            {
                "item_id": by_key[("credential", "provision")].id,
                "resource_id": existing_cred.credential_id,
            },
        ],
    )

    refreshed = await access_svc.get(view.id, identity=_owner_identity())
    decisions = [
        {"item_id": i.id, "decision": "approved"} for i in refreshed.items if i.status == "pending"
    ]
    decided = await access_svc.decide(view.id, identity=_owner_identity(), item_decisions=decisions)

    # FULL approval — a partial approval reads as failure to the waiting agent.
    assert decided.status == "approved", [
        (i.resource_type, i.action, i.status, i.decision_reason) for i in decided.items
    ]
    decided_provision = next(
        i for i in decided.items if i.resource_type == "credential" and i.action == "provision"
    )
    assert decided_provision.resource_id == existing_cred.credential_id

    # The wiring landed on the EXISTING credential, and no duplicate exists.
    binding = await _agent_binding_row(ctx, existing_cred.credential_id)
    assert binding is not None, "credential:bind did not attach to the existing credential"
    async with ctx.control_db.session() as session:
        cred_ids = (await session.execute(select(Credential.id))).scalars().all()
        assert cred_ids == [existing_cred.credential_id], (
            f"unexpected extra credentials: {cred_ids}"
        )


async def test_noauth_plan_is_executable_via_broker_resolvers(
    integration_context: Context, clean: None
) -> None:
    """A fulfilled NO-AUTH plan must be resolvable by BOTH broker resolvers at
    execute time — the binding deriver AND the credential resolver — when the
    operation resolves to a concrete version.

    This is the end-to-end guard for issue #775. A no-auth API's credential is
    versionless (api_version NULL = "covers all versions"), and the broker
    resolves the operation to a concrete version (e.g. "4.2.3"). Every resolver
    on the execute path must treat NULL as a wildcard, or a fully-approved plan
    still 403s / 424s (credential_not_provisioned) despite a valid binding. The
    provisioning-path test above stops at approval; this one drives the actual
    resolver logic the broker runs on `jentic execute`.
    """
    ctx = integration_context
    access_svc = AccessRequestService(ctx)
    cred_svc = CredentialService(ctx)

    # A no-auth API. The version the OPERATION resolves to at execute time.
    api = {"vendor": "country-is", "name": "country-is"}
    resolved_version = "4.2.3"

    # 1. AGENT files a no-auth plan (as `--provision … --auth none` builds it).
    view = await access_svc.file(
        actor_id=AGENT_SUB,
        reason="Look up the caller's country from their IP",
        items=[
            {
                "resource_type": "credential",
                "action": "provision",
                "resource_reference": {**api, "security_scheme": "no_auth"},
            },
            {
                "resource_type": "credential",
                "action": "bind",
                "resource_reference": api,
                "rules": [{"effect": "allow", "methods": ["GET"], "path": ".*"}],
            },
        ],
        identity=_agent_identity(),
    )
    bind_item = next(
        i for i in view.items if i.resource_type == "credential" and i.action == "bind"
    )

    # 2. WIZARD fulfils: create a NO_AUTH credential (no version → persisted
    #    NULL), amend its id onto the bind, then approve.
    created_cred = await cred_svc.create(
        CredentialCreate(
            type=CredentialType.NO_AUTH,
            name="country-is (no-auth)",
            api=APIReference(vendor="country-is", name="country-is", version=""),
        ),
        identity=_owner_identity(),
    )
    await access_svc.amend(
        view.id,
        identity=_owner_identity(),
        item_amendments=[
            {
                "item_id": bind_item.id,
                "resource_id": created_cred.credential_id,
                "rules": [{"effect": "allow", "methods": ["GET"], "path": ".*"}],
            },
        ],
    )
    refreshed = await access_svc.get(view.id, identity=_owner_identity())
    decided = await access_svc.decide(
        view.id,
        identity=_owner_identity(),
        item_decisions=[
            {"item_id": i.id, "decision": "approved"}
            for i in refreshed.items
            if i.status == "pending"
        ],
    )
    assert decided.status == "approved", [
        (i.resource_type, i.action, i.status, i.decision_reason) for i in decided.items
    ]

    # Sanity: the credential persisted a NULL version (the wildcard), not "".
    async with ctx.control_db.session() as session:
        cred_row = await session.get(Credential, created_cred.credential_id)
        assert cred_row is not None
        assert cred_row.api_version is None, "versionless credential must store NULL, not ''"

    # 3. EXECUTE-PATH RESOLVERS: both must resolve for the CONCRETE version.
    #    (a) binding deriver — which credentials the agent is directly bound to.
    binding_resolver = CredentialBindingResolver(ctx.admin_db, ctx.control_db)
    derivation = await binding_resolver.derive_credentials(
        agent_id=AGENT_SUB, vendor="country-is", name="country-is", version=resolved_version
    )
    bound_ids = [c.credential_id for c in derivation.credentials]
    assert bound_ids == [created_cred.credential_id], (
        "binding deriver must serve the no-auth API at a concrete version "
        f"(NULL-version credential wildcard); got {bound_ids}"
    )

    #    (b) credential resolver — the credential to inject (a no-op for NO_AUTH),
    #    confined to the derived injection boundary as the broker calls it.
    cred_resolver = CredentialResolver(ctx)
    resolved = await cred_resolver.resolve(
        api=APIReference(vendor="country-is", name="country-is", version=resolved_version),
        caller=AGENT_SUB,
        allowed_credential_ids=bound_ids,
    )
    assert resolved.credential_id == created_cred.credential_id
    assert resolved.wire_type == CredentialType.NO_AUTH


async def test_plain_approve_of_unfulfilled_plan_is_denied_legibly(
    integration_context: Context, clean: None
) -> None:
    """A plan approved WITHOUT the wizard's fulfilment must deny the bind with a
    plan-aware reason — not the cryptic 'no credential covers API'.

    Reproduces the real dogfooding failure: the operator approved the plan
    through the plain path, the inert credential:provision intent was skipped,
    and the bind item failed with a confusing error. The guard now denies it
    pointing at the setup wizard.
    """
    ctx = integration_context
    access_svc = AccessRequestService(ctx)
    api = {"vendor": "httpbin.org", "name": "httpbin", "version": "1.0.0"}

    view = await access_svc.file(
        actor_id=AGENT_SUB,
        reason="Make httpbin executable",
        items=[
            {
                "resource_type": "credential",
                "action": "provision",
                "resource_reference": {**api, "security_scheme": "bearer"},
            },
            {
                "resource_type": "credential",
                "action": "bind",
                "resource_reference": api,
                "rules": [{"effect": "allow", "methods": ["GET"], "path": ".*"}],
            },
        ],
        identity=_agent_identity(),
    )

    # Approve every item WITHOUT fulfilling (no wizard, no amend) — the plain path.
    decided = await access_svc.decide(
        view.id,
        identity=_owner_identity(),
        item_decisions=[{"item_id": i.id, "decision": "approved"} for i in view.items],
    )

    # The plan cannot complete: the intent approves (an inert no-op) but the
    # bind is denied with the plan-aware reason pointing at the wizard.
    by_key = {(i.resource_type, i.action): i for i in decided.items}
    intent = by_key[("credential", "provision")]
    cred_bind = by_key[("credential", "bind")]
    assert intent.status == "approved"
    assert intent.applied_effects is not None
    assert intent.applied_effects.get("skipped") is True
    assert cred_bind.status == "denied"
    assert "provisioning plan" in (cred_bind.decision_reason or "")
    assert intent.id in (cred_bind.decision_reason or "")
    # And no half-provisioned state leaked (no binding created).
    async with ctx.admin_db.session() as session:
        rows = await session.execute(
            text("SELECT count(*) FROM agent_credential_bindings WHERE agent_id = :a"),
            {"a": AGENT_SUB},
        )
        assert rows.scalar_one() == 0, "a denied plan must not create any agent binding"


def _chain_items(api: dict[str, str], scheme: str) -> list[dict[str, Any]]:
    """One provisioning chain exactly as the CLI --provision builder emits it,
    including the API reference stamped on the credential:bind (the chain
    marker: item order is not guaranteed, so the reference is what keeps a
    composite request's chains attributable)."""
    return [
        {
            "resource_type": "credential",
            "action": "provision",
            "resource_reference": {**api, "security_scheme": scheme},
        },
        {
            "resource_type": "credential",
            "action": "bind",
            "resource_reference": api,
            "rules": [{"effect": "allow", "methods": ["GET"], "path": ".*"}],
        },
    ]


def _chain_bind(view: Any, api: dict[str, str]) -> Any:
    """The credential:bind item of the chain for ``api``, matched by the
    stamped reference — never by position."""

    def _matches(item: Any) -> bool:
        ref = item.resource_reference or {}
        return ref.get("vendor") == api["vendor"] and ref.get("name") == api["name"]

    return next(
        i
        for i in view.items
        if i.resource_type == "credential" and i.action == "bind" and _matches(i)
    )


async def test_composite_request_two_chains_plus_plain_items_end_to_end(
    integration_context: Context, clean: None
) -> None:
    """One composite request — two provisioning chains + a plain reference
    credential:bind to a pre-existing credential + a scope:grant — fulfils and
    approves to a fully wired state (issue #844).

    Also guards the mixed-composite fix: the plain reference bind rides in a
    request that IS a provisioning plan (sibling chains carry fulfilment
    intents), and must resolve against the existing credential instead of
    being auto-denied with the plan-aware reason.
    """
    ctx = integration_context
    access_svc = AccessRequestService(ctx)
    cred_svc = CredentialService(ctx)

    api_a = {"vendor": "httpbin.org", "name": "httpbin"}
    api_b = {"vendor": "country-is", "name": "country-is"}
    api_existing = {"vendor": "postman-echo.com", "name": "echo"}

    # 0. A credential already covering api_existing, so the composite's plain
    #    credential:bind reference can resolve.
    existing_cred = await cred_svc.create(
        CredentialCreate(
            type=CredentialType.BEARER_TOKEN,
            name="echo cred",
            api=APIReference(vendor="postman-echo.com", name="echo", version=""),
            token="echo-secret-1",
        ),
        identity=_owner_identity(),
    )

    # 1. AGENT files ONE composite request, as the CLI composes it.
    items = [
        *_chain_items(api_a, "bearer"),
        *_chain_items(api_b, "no_auth"),
        {
            "resource_type": "credential",
            "action": "bind",
            "resource_reference": api_existing,
            "rules": [{"effect": "allow", "methods": ["GET"], "path": ".*"}],
        },
        {"resource_type": "scope", "action": "grant", "resource_id": "catalog:import"},
    ]
    view = await access_svc.file(
        actor_id=AGENT_SUB,
        reason="Set up the release-notes automation",
        items=items,
        identity=_agent_identity(),
    )
    assert view.status == "pending"
    assert len(view.items) == 6

    # 2. WIZARD fulfils BOTH chains, resolving each by its stamped reference.
    fulfilled: dict[str, str] = {}
    for api, cred_type, token in (
        (api_a, CredentialType.BEARER_TOKEN, "secret-a-1"),
        (api_b, CredentialType.NO_AUTH, None),
    ):
        created_cred = await cred_svc.create(
            CredentialCreate(
                type=cred_type,
                name=f"{api['name']} cred",
                api=APIReference(vendor=api["vendor"], name=api["name"], version=""),
                token=token,
            ),
            identity=_owner_identity(),
        )
        fulfilled[api["vendor"]] = created_cred.credential_id

        cred_bind = _chain_bind(view, api)
        await access_svc.amend(
            view.id,
            identity=_owner_identity(),
            item_amendments=[
                {
                    "item_id": cred_bind.id,
                    "resource_id": created_cred.credential_id,
                    "rules": [{"effect": "allow", "methods": ["GET"], "path": ".*"}],
                },
            ],
        )

    # 3. OPERATOR approves everything — including the UNAMENDED plain reference
    #    bind and the scope grant.
    refreshed = await access_svc.get(view.id, identity=_owner_identity())
    decided = await access_svc.decide(
        view.id,
        identity=_owner_identity(),
        item_decisions=[
            {"item_id": i.id, "decision": "approved"}
            for i in refreshed.items
            if i.status == "pending"
        ],
    )
    assert decided.status == "approved", [
        (i.resource_type, i.action, i.status, i.decision_reason) for i in decided.items
    ]

    # 4. ASSERT the full end-state: both chains wired, the plain bind bound the
    #    agent to the EXISTING credential, and the scope granted.
    async with ctx.admin_db.session() as session:
        bound = await session.execute(
            text("SELECT credential_id FROM agent_credential_bindings WHERE agent_id = :a"),
            {"a": AGENT_SUB},
        )
        bound_ids = {row[0] for row in bound.fetchall()}
        granted = await session.execute(
            text(
                "SELECT 1 FROM actor_scope_grants WHERE actor_id = :a AND scope = 'catalog:import'"
            ),
            {"a": AGENT_SUB},
        )
        assert granted.scalar_one_or_none() is not None, "scope was not granted"
    expected = set(fulfilled.values()) | {existing_cred.credential_id}
    assert bound_ids == expected, f"agent bindings {bound_ids} != expected {expected}"


async def test_composite_partial_fulfilment_is_partially_approved(
    integration_context: Context, clean: None
) -> None:
    """Fulfilling only one of a composite's chains and approving everything
    yields ``partially_approved``: the fulfilled chain wires, the unfulfilled
    chain's bind is auto-denied with the plan-aware reason — per chain, not
    per request."""
    ctx = integration_context
    access_svc = AccessRequestService(ctx)
    cred_svc = CredentialService(ctx)

    api_a = {"vendor": "httpbin.org", "name": "httpbin"}
    api_b = {"vendor": "country-is", "name": "country-is"}

    view = await access_svc.file(
        actor_id=AGENT_SUB,
        reason="Two APIs, one request",
        items=[*_chain_items(api_a, "bearer"), *_chain_items(api_b, "bearer")],
        identity=_agent_identity(),
    )

    # Fulfil ONLY chain A.
    created_cred = await cred_svc.create(
        CredentialCreate(
            type=CredentialType.BEARER_TOKEN,
            name="httpbin cred",
            api=APIReference(vendor="httpbin.org", name="httpbin", version=""),
            token="secret-a-2",
        ),
        identity=_owner_identity(),
    )
    cred_bind_a = _chain_bind(view, api_a)
    await access_svc.amend(
        view.id,
        identity=_owner_identity(),
        item_amendments=[
            {
                "item_id": cred_bind_a.id,
                "resource_id": created_cred.credential_id,
                "rules": [{"effect": "allow", "methods": ["GET"], "path": ".*"}],
            },
        ],
    )

    refreshed = await access_svc.get(view.id, identity=_owner_identity())
    decided = await access_svc.decide(
        view.id,
        identity=_owner_identity(),
        item_decisions=[
            {"item_id": i.id, "decision": "approved"}
            for i in refreshed.items
            if i.status == "pending"
        ],
    )

    assert decided.status == "partially_approved", [
        (i.resource_type, i.action, i.status, i.decision_reason) for i in decided.items
    ]
    cred_bind_a2 = _chain_bind(decided, api_a)
    cred_bind_b = _chain_bind(decided, api_b)
    assert cred_bind_a2.status == "approved"
    assert cred_bind_b.status == "denied"
    assert "provisioning plan" in (cred_bind_b.decision_reason or "")
    # Chain A's wiring landed despite chain B's denial.
    binding = await _agent_binding_row(ctx, created_cred.credential_id)
    assert binding is not None, "the fulfilled chain must still wire on partial approval"
