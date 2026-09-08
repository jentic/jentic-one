"""End-to-end test of the provisioning-plan access-request flow.

Drives the real service layer against a real DB: an agent files a full
provisioning plan (toolkit:create + credential:provision + credential:bind +
toolkit:bind), the "wizard" fulfils the create/provision steps by creating a
real toolkit + credential and amending their ids onto the bind item, then the
operator approves the whole request. Asserts the approval actually wired the
credential->toolkit binding (+ rules) and the agent->toolkit binding — i.e. the
plan reaches an executable state, not a hollow yes.

This is a scratch verification test (added during end-to-end validation); it can
be folded into the permanent suite or removed.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any

import pytest
from sqlalchemy import delete, select, text

from jentic_one.auth.repos import ToolkitNameRepository
from jentic_one.broker.repos.toolkit_binding_resolver import ToolkitBindingResolver
from jentic_one.broker.services.credentials.resolver import CredentialResolver
from jentic_one.control.core.schema.access_request_items import AccessRequestItem
from jentic_one.control.core.schema.access_requests import AccessRequest
from jentic_one.control.core.schema.credentials import Credential
from jentic_one.control.core.schema.toolkit_credential_bindings import ToolkitCredentialBinding
from jentic_one.control.core.schema.toolkit_permission_rules import ToolkitPermissionRule
from jentic_one.control.core.schema.toolkits import Toolkit
from jentic_one.control.repos.toolkit_binding_repo import ToolkitBindingRepository
from jentic_one.control.repos.toolkit_permission_repo import ToolkitPermissionRepository
from jentic_one.control.services.access_requests.service import AccessRequestService
from jentic_one.control.services.credentials.schemas.credentials import CredentialCreate
from jentic_one.control.services.credentials.schemas.provision import APIReference
from jentic_one.control.services.credentials.service import CredentialService
from jentic_one.control.services.toolkits.service import ToolkitService
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
        permissions=["agents:write", "toolkits:write", "credentials:write"],
    )


@pytest.fixture()
async def clean(
    control_db: DatabaseSession, admin_db: DatabaseSession
) -> AsyncGenerator[None, None]:
    async def _wipe() -> None:
        async with control_db.session() as session:
            await session.execute(delete(ToolkitPermissionRule))
            await session.execute(delete(ToolkitCredentialBinding))
            await session.execute(delete(AccessRequestItem))
            await session.execute(delete(AccessRequest))
            await session.execute(delete(Credential))
            await session.execute(delete(Toolkit))
            await session.commit()
        async with admin_db.session() as session:
            await session.execute(
                text("DELETE FROM agent_toolkit_bindings WHERE agent_id = :a"), {"a": AGENT_SUB}
            )
            await session.execute(text("DELETE FROM agents WHERE id = :a"), {"a": AGENT_SUB})
            await session.commit()

    await _wipe()
    # The agent must exist for the toolkit:bind admin effect's FK.
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


async def test_provisioning_plan_end_to_end(integration_context: Context, clean: None) -> None:
    ctx = integration_context
    access_svc = AccessRequestService(ctx)
    toolkit_svc = ToolkitService(ctx)
    cred_svc = CredentialService(ctx)

    api = {"vendor": "httpbin.org", "name": "httpbin", "version": "1.0.0"}

    # 1. AGENT files the provisioning plan (as the CLI --provision builder does).
    plan_items: list[dict[str, Any]] = [
        {"resource_type": "toolkit", "action": "create", "resource_reference": api},
        {
            "resource_type": "credential",
            "action": "provision",
            "resource_reference": {**api, "security_scheme": "bearer"},
        },
        {
            "resource_type": "credential",
            "action": "bind",
            "rules": [{"effect": "allow", "methods": ["GET"], "path": ".*"}],
        },
        {"resource_type": "toolkit", "action": "bind", "resource_reference": api},
    ]
    view = await access_svc.file(
        actor_id=AGENT_SUB,
        reason="Make httpbin executable",
        items=plan_items,
        identity=_agent_identity(),
    )
    assert view.status == "pending"
    assert len(view.items) == 4
    bind_item = next(
        i for i in view.items if i.resource_type == "credential" and i.action == "bind"
    )
    agent_bind_item = next(
        i for i in view.items if i.resource_type == "toolkit" and i.action == "bind"
    )

    # 2. WIZARD (operator) fulfils the create/provision steps with real resources.
    _create = await toolkit_svc.create(name="httpbin.org/httpbin", identity=_owner_identity())
    created_toolkit = _create.toolkit
    created_cred = await cred_svc.create(
        CredentialCreate(
            type=CredentialType.BEARER_TOKEN,
            name="httpbin cred",
            api=APIReference(vendor="httpbin.org", name="httpbin", version="1.0.0"),
            token="secret-token-value-123",
        ),
        identity=_owner_identity(),
    )

    # 3. WIZARD amends the resolved ids + confirmed rules onto the bind items.
    #    The credential:bind gets to_id (toolkit) + resource_id (credential); the
    #    toolkit:bind (agent->toolkit) gets the concrete toolkit id so it resolves
    #    by id rather than by the credential join (which isn't populated until the
    #    credential:bind effect applies later in the same decision).
    await access_svc.amend(
        view.id,
        identity=_owner_identity(),
        item_amendments=[
            {
                "item_id": bind_item.id,
                "to_id": created_toolkit.id,
                "resource_id": created_cred.credential_id,
                "rules": [{"effect": "allow", "methods": ["GET"], "path": ".*"}],
            },
            {"item_id": agent_bind_item.id, "resource_id": created_toolkit.id},
        ],
    )

    # 4. OPERATOR approves every pending item.
    refreshed = await access_svc.get(view.id, identity=_owner_identity())
    decisions = [
        {"item_id": i.id, "decision": "approved"} for i in refreshed.items if i.status == "pending"
    ]
    decided = await access_svc.decide(view.id, identity=_owner_identity(), item_decisions=decisions)

    # 5. ASSERT the plan reached an executable state (not a hollow yes).
    assert decided.status == "approved", [
        (i.resource_type, i.action, i.status, i.decision_reason) for i in decided.items
    ]

    async with ctx.control_db.session() as session:
        binding = await ToolkitBindingRepository.get(
            session, created_toolkit.id, created_cred.credential_id
        )
        assert binding is not None, "credential:bind did not create a toolkit_credential_binding"
        rules = await ToolkitPermissionRepository.list_rules(
            session, created_toolkit.id, created_cred.credential_id
        )
        assert any(r.effect == "allow" for r in rules), "no allow rule written on the binding"

    async with ctx.admin_db.session() as session:
        bound = await session.execute(
            text("SELECT 1 FROM agent_toolkit_bindings WHERE agent_id = :a AND toolkit_id = :t"),
            {"a": AGENT_SUB, "t": created_toolkit.id},
        )
        assert bound.scalar_one_or_none() is not None, "toolkit:bind did not bind the agent"

    # whoami / list_toolkits now reports which APIs the binding serves, so an
    # agent can tell it already has access without a throwaway denied execute.
    # Exercise the served-APIs repository (the cross-boundary read whoami uses).
    async with ctx.control_db.session() as session:
        served = await ToolkitNameRepository.get_served_apis_for_ids(session, [created_toolkit.id])
    apis = served.get(created_toolkit.id, [])
    assert any(vendor == "httpbin-org" for vendor, _name, _version in apis), (
        f"binding should report the served API, got {apis}"
    )


async def test_provisioning_plan_fulfilled_into_existing_toolkit(
    integration_context: Context, clean: None
) -> None:
    """A plan can be fulfilled into a PRE-EXISTING toolkit — the #897 reuse path.

    The reporter's scenario: the operator already has a toolkit (serving other
    APIs) and wants the new credential added to IT, not a parallel toolkit. The
    wizard's "use existing" choice amends the bind items at the existing toolkit
    id and — audit honesty — stamps the inert ``toolkit:create`` /
    ``credential:provision`` placeholders with the ids that fulfilled them, so
    the approved record reads "fulfilled by tk_…" instead of implying a create
    that never happened. This drives that exact amend+decide shape through the
    real service layer: it must reach FULL approval (the agent's ``--wait``
    exits 0) with no second toolkit anywhere.
    """
    ctx = integration_context
    access_svc = AccessRequestService(ctx)
    toolkit_svc = ToolkitService(ctx)
    cred_svc = CredentialService(ctx)

    # The operator's pre-existing toolkit ("My Travel Agent") — created BEFORE
    # the plan is filed, outside any wizard session.
    _existing = await toolkit_svc.create(name="My Travel Agent", identity=_owner_identity())
    existing_toolkit = _existing.toolkit

    api = {"vendor": "httpbin.org", "name": "httpbin", "version": "1.0.0"}
    plan_items: list[dict[str, Any]] = [
        {"resource_type": "toolkit", "action": "create", "resource_reference": api},
        {
            "resource_type": "credential",
            "action": "provision",
            "resource_reference": {**api, "security_scheme": "bearer"},
        },
        {
            "resource_type": "credential",
            "action": "bind",
            "rules": [{"effect": "allow", "methods": ["GET"], "path": ".*"}],
        },
        {"resource_type": "toolkit", "action": "bind", "resource_reference": api},
    ]
    view = await access_svc.file(
        actor_id=AGENT_SUB,
        reason="Extend my existing toolkit",
        items=plan_items,
        identity=_agent_identity(),
    )
    by_key = {(i.resource_type, i.action): i for i in view.items}

    # Wizard fulfilment: only the credential is created; the toolkit is reused.
    created_cred = await cred_svc.create(
        CredentialCreate(
            type=CredentialType.BEARER_TOKEN,
            name="httpbin cred (reuse)",
            api=APIReference(vendor="httpbin.org", name="httpbin", version="1.0.0"),
            token="secret-token-value-456",
        ),
        identity=_owner_identity(),
    )
    await access_svc.amend(
        view.id,
        identity=_owner_identity(),
        item_amendments=[
            {
                "item_id": by_key[("credential", "bind")].id,
                "to_id": existing_toolkit.id,
                "resource_id": created_cred.credential_id,
                "rules": [{"effect": "allow", "methods": ["GET"], "path": ".*"}],
            },
            {"item_id": by_key[("toolkit", "bind")].id, "resource_id": existing_toolkit.id},
            # Placeholder stamping: resource_id on fulfilment-only items must be
            # amendable (the scope-grant allow-list guard only fires for
            # scope:grant) so the record names the reused objects.
            {"item_id": by_key[("toolkit", "create")].id, "resource_id": existing_toolkit.id},
            {
                "item_id": by_key[("credential", "provision")].id,
                "resource_id": created_cred.credential_id,
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
    decided_create = next(
        i for i in decided.items if i.resource_type == "toolkit" and i.action == "create"
    )
    assert decided_create.resource_id == existing_toolkit.id

    # The wiring landed on the EXISTING toolkit, and no parallel toolkit exists.
    async with ctx.control_db.session() as session:
        binding = await ToolkitBindingRepository.get(
            session, existing_toolkit.id, created_cred.credential_id
        )
        assert binding is not None, "credential:bind did not attach to the existing toolkit"
        toolkit_ids = (await session.execute(select(Toolkit.id))).scalars().all()
        assert toolkit_ids == [existing_toolkit.id], f"unexpected extra toolkits: {toolkit_ids}"

    async with ctx.admin_db.session() as session:
        bound = await session.execute(
            text("SELECT 1 FROM agent_toolkit_bindings WHERE agent_id = :a AND toolkit_id = :t"),
            {"a": AGENT_SUB, "t": existing_toolkit.id},
        )
        assert bound.scalar_one_or_none() is not None, "agent was not bound to the existing toolkit"


async def test_noauth_plan_is_executable_via_broker_resolvers(
    integration_context: Context, clean: None
) -> None:
    """A fulfilled NO-AUTH plan must be resolvable by BOTH broker resolvers at
    execute time — the toolkit-binding resolver AND the credential resolver —
    when the operation resolves to a concrete version.

    This is the end-to-end guard for issue #775. A no-auth API's credential is
    versionless (api_version NULL = "covers all versions"), and the broker
    resolves the operation to a concrete version (e.g. "4.2.3"). Every resolver
    on the execute path must treat NULL as a wildcard, or a fully-approved plan
    still 403s (no_toolkit_binding) / 424s (credential_not_provisioned) despite
    valid bindings. The provisioning-path test above stops at approval; this one
    drives the actual resolver logic the broker runs on `jentic execute`.
    """
    ctx = integration_context
    access_svc = AccessRequestService(ctx)
    toolkit_svc = ToolkitService(ctx)
    cred_svc = CredentialService(ctx)

    # A no-auth API. The version the OPERATION resolves to at execute time.
    api = {"vendor": "country-is", "name": "country-is"}
    resolved_version = "4.2.3"

    # 1. AGENT files a no-auth plan (as `--provision … --auth none` builds it).
    view = await access_svc.file(
        actor_id=AGENT_SUB,
        reason="Look up the caller's country from their IP",
        items=[
            {"resource_type": "toolkit", "action": "create", "resource_reference": api},
            {
                "resource_type": "credential",
                "action": "provision",
                "resource_reference": {**api, "security_scheme": "no_auth"},
            },
            {
                "resource_type": "credential",
                "action": "bind",
                "rules": [{"effect": "allow", "methods": ["GET"], "path": ".*"}],
            },
            {"resource_type": "toolkit", "action": "bind", "resource_reference": api},
        ],
        identity=_agent_identity(),
    )
    bind_item = next(
        i for i in view.items if i.resource_type == "credential" and i.action == "bind"
    )
    agent_bind_item = next(
        i for i in view.items if i.resource_type == "toolkit" and i.action == "bind"
    )

    # 2. WIZARD fulfils: create the toolkit + a NO_AUTH credential (no version →
    #    persisted NULL), amend their ids onto the binds, then approve.
    _create = await toolkit_svc.create(name="country-is/country-is", identity=_owner_identity())
    created_toolkit = _create.toolkit
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
                "to_id": created_toolkit.id,
                "resource_id": created_cred.credential_id,
                "rules": [{"effect": "allow", "methods": ["GET"], "path": ".*"}],
            },
            {"item_id": agent_bind_item.id, "resource_id": created_toolkit.id},
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
    #    (a) toolkit-binding resolver — which toolkit serves this API for the agent.
    toolkit_resolver = ToolkitBindingResolver(ctx.admin_db, ctx.control_db)
    derivation = await toolkit_resolver.derive_toolkits(
        agent_id=AGENT_SUB, vendor="country-is", name="country-is", version=resolved_version
    )
    assert derivation.toolkits == (created_toolkit.id,), (
        "toolkit-binding resolver must serve the no-auth API at a concrete version "
        f"(NULL-version credential wildcard); got {derivation.toolkits}"
    )

    #    (b) credential resolver — the credential to inject (a no-op for NO_AUTH).
    cred_resolver = CredentialResolver(ctx)
    resolved = await cred_resolver.resolve(
        api=APIReference(vendor="country-is", name="country-is", version=resolved_version),
        caller=AGENT_SUB,
    )
    assert resolved.credential_id == created_cred.credential_id
    assert resolved.wire_type == CredentialType.NO_AUTH


async def test_plain_approve_of_unfulfilled_plan_is_denied_legibly(
    integration_context: Context, clean: None
) -> None:
    """A plan approved WITHOUT the wizard's fulfilment must deny the binds with a
    plan-aware reason — not the cryptic 'to_id missing' / 'no toolkit serves API'.

    Reproduces the real dogfooding failure: the operator approved the plan through
    the plain path, the inert toolkit:create/credential:provision items were
    skipped, and the two bind items failed with confusing errors. The guard now
    denies them pointing at the setup wizard.
    """
    ctx = integration_context
    access_svc = AccessRequestService(ctx)
    api = {"vendor": "httpbin.org", "name": "httpbin", "version": "1.0.0"}

    view = await access_svc.file(
        actor_id=AGENT_SUB,
        reason="Make httpbin executable",
        items=[
            {"resource_type": "toolkit", "action": "create", "resource_reference": api},
            {
                "resource_type": "credential",
                "action": "provision",
                "resource_reference": {**api, "security_scheme": "bearer"},
            },
            {
                "resource_type": "credential",
                "action": "bind",
                "rules": [{"effect": "allow", "methods": ["GET"], "path": ".*"}],
            },
            {"resource_type": "toolkit", "action": "bind", "resource_reference": api},
        ],
        identity=_agent_identity(),
    )

    # Approve every item WITHOUT fulfilling (no wizard, no amend) — the plain path.
    decided = await access_svc.decide(
        view.id,
        identity=_owner_identity(),
        item_decisions=[{"item_id": i.id, "decision": "approved"} for i in view.items],
    )

    # The plan cannot complete: the two intents approve (inert no-ops) but the
    # binds are denied with the plan-aware reason pointing at the wizard.
    by_key = {(i.resource_type, i.action): i for i in decided.items}
    cred_bind = by_key[("credential", "bind")]
    tk_bind = by_key[("toolkit", "bind")]
    assert cred_bind.status == "denied"
    assert tk_bind.status == "denied"
    assert "provisioning plan" in (cred_bind.decision_reason or "")
    assert "provisioning plan" in (tk_bind.decision_reason or "")
    # And no half-provisioned state leaked (no binding created).
    async with ctx.control_db.session() as session:
        rows = (await session.execute(select(ToolkitCredentialBinding))).scalars().all()
        assert rows == [], "a denied plan must not create any credential binding"


def _chain_items(api: dict[str, str], scheme: str) -> list[dict[str, Any]]:
    """One provisioning chain exactly as the CLI --provision builder emits it,
    including the API reference stamped on the credential:bind (the chain
    marker: item order is not guaranteed, so the reference is what keeps a
    composite request's chains attributable)."""
    return [
        {"resource_type": "toolkit", "action": "create", "resource_reference": api},
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
        {"resource_type": "toolkit", "action": "bind", "resource_reference": api},
    ]


def _chain_binds(view: Any, api: dict[str, str]) -> tuple[Any, Any]:
    """The (credential:bind, toolkit:bind) items of the chain for ``api``,
    matched by the stamped reference — never by position."""

    def _matches(item: Any) -> bool:
        ref = item.resource_reference or {}
        return ref.get("vendor") == api["vendor"] and ref.get("name") == api["name"]

    cred_bind = next(
        i
        for i in view.items
        if i.resource_type == "credential" and i.action == "bind" and _matches(i)
    )
    tk_bind = next(
        i for i in view.items if i.resource_type == "toolkit" and i.action == "bind" and _matches(i)
    )
    return cred_bind, tk_bind


async def test_composite_request_two_chains_plus_plain_items_end_to_end(
    integration_context: Context, clean: None
) -> None:
    """One composite request — two provisioning chains + a plain reference
    toolkit:bind to a pre-existing toolkit + a scope:grant — fulfils and
    approves to a fully wired state (issue #844).

    Also guards the mixed-composite fix: the plain reference bind rides in a
    request that IS a provisioning plan (sibling chains carry fulfilment
    intents), and must resolve against the existing toolkit instead of being
    auto-denied with the plan-aware reason.
    """
    ctx = integration_context
    access_svc = AccessRequestService(ctx)
    toolkit_svc = ToolkitService(ctx)
    cred_svc = CredentialService(ctx)

    api_a = {"vendor": "httpbin.org", "name": "httpbin"}
    api_b = {"vendor": "country-is", "name": "country-is"}
    api_existing = {"vendor": "postman-echo.com", "name": "echo"}

    # 0. A toolkit already serving api_existing (toolkit + credential + binding),
    #    so the composite's plain toolkit:bind reference can resolve.
    _existing = await toolkit_svc.create(name="postman-echo.com/echo", identity=_owner_identity())
    existing_toolkit = _existing.toolkit
    existing_cred = await cred_svc.create(
        CredentialCreate(
            type=CredentialType.BEARER_TOKEN,
            name="echo cred",
            api=APIReference(vendor="postman-echo.com", name="echo", version=""),
            token="echo-secret-1",
        ),
        identity=_owner_identity(),
    )
    async with ctx.control_db.session() as session:
        await ToolkitBindingRepository.bind(
            session,
            toolkit_id=existing_toolkit.id,
            credential_id=existing_cred.credential_id,
            created_by=OWNER_SUB,
        )
        await session.commit()

    # 1. AGENT files ONE composite request, as the CLI composes it.
    items = [
        *_chain_items(api_a, "bearer"),
        *_chain_items(api_b, "no_auth"),
        {"resource_type": "toolkit", "action": "bind", "resource_reference": api_existing},
        {"resource_type": "scope", "action": "grant", "resource_id": "catalog:import"},
    ]
    view = await access_svc.file(
        actor_id=AGENT_SUB,
        reason="Set up the release-notes automation",
        items=items,
        identity=_agent_identity(),
    )
    assert view.status == "pending"
    assert len(view.items) == 10

    # 2. WIZARD fulfils BOTH chains, resolving each by its stamped reference.
    fulfilled: dict[str, tuple[str, str]] = {}
    for api, cred_type, token in (
        (api_a, CredentialType.BEARER_TOKEN, "secret-a-1"),
        (api_b, CredentialType.NO_AUTH, None),
    ):
        _create = await toolkit_svc.create(
            name=f"{api['vendor']}/{api['name']}", identity=_owner_identity()
        )
        created_cred = await cred_svc.create(
            CredentialCreate(
                type=cred_type,
                name=f"{api['name']} cred",
                api=APIReference(vendor=api["vendor"], name=api["name"], version=""),
                token=token,
            ),
            identity=_owner_identity(),
        )
        fulfilled[api["vendor"]] = (_create.toolkit.id, created_cred.credential_id)

        cred_bind, tk_bind = _chain_binds(view, api)
        await access_svc.amend(
            view.id,
            identity=_owner_identity(),
            item_amendments=[
                {
                    "item_id": cred_bind.id,
                    "to_id": _create.toolkit.id,
                    "resource_id": created_cred.credential_id,
                    "rules": [{"effect": "allow", "methods": ["GET"], "path": ".*"}],
                },
                {"item_id": tk_bind.id, "resource_id": _create.toolkit.id},
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
    #    agent to the EXISTING toolkit, and the scope granted.
    async with ctx.control_db.session() as session:
        for vendor, (toolkit_id, credential_id) in fulfilled.items():
            binding = await ToolkitBindingRepository.get(session, toolkit_id, credential_id)
            assert binding is not None, f"chain for {vendor} did not wire its credential binding"

    async with ctx.admin_db.session() as session:
        bound = await session.execute(
            text("SELECT toolkit_id FROM agent_toolkit_bindings WHERE agent_id = :a"),
            {"a": AGENT_SUB},
        )
        bound_ids = {row[0] for row in bound.fetchall()}
    expected = {tk for tk, _cred in fulfilled.values()} | {existing_toolkit.id}
    assert bound_ids == expected, f"agent bindings {bound_ids} != expected {expected}"


async def test_composite_partial_fulfilment_is_partially_approved(
    integration_context: Context, clean: None
) -> None:
    """Fulfilling only one of a composite's chains and approving everything
    yields ``partially_approved``: the fulfilled chain wires, the unfulfilled
    chain's binds are auto-denied with the plan-aware reason — per chain, not
    per request."""
    ctx = integration_context
    access_svc = AccessRequestService(ctx)
    toolkit_svc = ToolkitService(ctx)
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
    _create = await toolkit_svc.create(name="httpbin.org/httpbin", identity=_owner_identity())
    created_cred = await cred_svc.create(
        CredentialCreate(
            type=CredentialType.BEARER_TOKEN,
            name="httpbin cred",
            api=APIReference(vendor="httpbin.org", name="httpbin", version=""),
            token="secret-a-2",
        ),
        identity=_owner_identity(),
    )
    cred_bind_a, tk_bind_a = _chain_binds(view, api_a)
    await access_svc.amend(
        view.id,
        identity=_owner_identity(),
        item_amendments=[
            {
                "item_id": cred_bind_a.id,
                "to_id": _create.toolkit.id,
                "resource_id": created_cred.credential_id,
                "rules": [{"effect": "allow", "methods": ["GET"], "path": ".*"}],
            },
            {"item_id": tk_bind_a.id, "resource_id": _create.toolkit.id},
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
    cred_bind_a2, tk_bind_a2 = _chain_binds(decided, api_a)
    cred_bind_b, tk_bind_b = _chain_binds(decided, api_b)
    assert cred_bind_a2.status == "approved" and tk_bind_a2.status == "approved"
    assert cred_bind_b.status == "denied" and tk_bind_b.status == "denied"
    assert "provisioning plan" in (cred_bind_b.decision_reason or "")
    # Chain A's wiring landed despite chain B's denial.
    async with ctx.control_db.session() as session:
        binding = await ToolkitBindingRepository.get(
            session, _create.toolkit.id, created_cred.credential_id
        )
        assert binding is not None, "the fulfilled chain must still wire on partial approval"
