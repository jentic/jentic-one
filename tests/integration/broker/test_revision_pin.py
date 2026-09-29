"""Integration tests for §10 Jentic-Revision in-process pinning.

Seeds a real Registry DB with one API carrying multiple revisions (published,
owned draft, foreign draft, archived) plus URL-index rows per revision, and
exercises the broker's in-process pin resolution through the injected
``InProcessRegistryResolver`` (the production wiring) + the broker
``discovery.resolve_pin_for_api`` seam.

Asserts:
- a published pin resolves in-process to the pinned ``revision_id`` and lets
  discovery resolve the operation against that revision;
- an owned ``draft`` resolves; a foreign ``draft`` → ``403``
  (``UnauthorizedRevisionPinError``); an ``archived`` revision → ``422``
  (``InvalidRevisionPinError``); an unknown label → ``422``;
- **no** outbound HTTP is made — resolution is a pure Registry-DB read (any
  ``httpx`` send during resolution fails the test).
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator

import httpx
import pytest
from sqlalchemy import delete, select, update

from jentic_one.broker.core.exceptions import (
    InvalidRevisionPinError,
    UnauthorizedRevisionPinError,
)
from jentic_one.broker.services.discovery import (
    discover,
    discover_via_pins,
    resolve_pin_for_api,
)
from jentic_one.registry.core.schema.api_revisions import ApiRevision
from jentic_one.registry.core.schema.apis import Api
from jentic_one.registry.core.schema.operation_url_index import OperationURLIndex
from jentic_one.registry.core.schema.operations import Operation
from jentic_one.registry.core.url_index import build_index_entry
from jentic_one.registry.repos.api_repo import ApiRepository
from jentic_one.registry.repos.operation_repo import OperationInput, OperationRepository
from jentic_one.registry.repos.url_index_repo import UrlIndexRepository
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.broker.protocols import RevisionPinOutcome
from jentic_one.shared.db.session import DatabaseSession
from jentic_one.shared.models import ApiRevisionState
from jentic_one.shared.schemas import APIReference
from jentic_one.wiring import InProcessRegistryResolver

pytestmark = pytest.mark.integration

VENDOR = "acme.com"
NAME = "pets-api"
VERSION = "v1"
HOST = "api.acme.com"
# Each revision indexes a *distinct* path so each test can tell which revision
# answered. Identical URLs across revisions are also supported — the URL-index
# key is scoped to the revision (``uq_operation_url_index_lookup``) —
# and are covered by ``test_pin_and_live_share_one_url``.
PUBLISHED_PATH = "/v1/pets"
OWNED_DRAFT_PATH = "/v1/pets-owned-draft"
FOREIGN_DRAFT_PATH = "/v1/pets-foreign-draft"
ARCHIVED_PATH = "/v1/pets-archived"
PUBLISHED_URL = f"https://{HOST}{PUBLISHED_PATH}"
OWNER = "usr_owner"
OTHER = "usr_other"


@pytest.fixture()
async def clean_url_index(registry_db: DatabaseSession) -> AsyncGenerator[None, None]:
    """Truncate the tables this module touches, before and after each test."""

    async def _truncate() -> None:
        async with registry_db.session() as session:
            await session.execute(delete(OperationURLIndex))
            await session.execute(delete(Operation))
            await session.execute(update(Api).values(current_revision_id=None))
            await session.execute(delete(ApiRevision))
            await session.execute(delete(Api))
            await session.commit()

    await _truncate()
    yield
    await _truncate()


async def _seed_revision(
    registry_db: DatabaseSession,
    *,
    api_id: uuid.UUID,
    state: ApiRevisionState,
    submitted_by: str | None,
    make_current: bool,
    path: str,
) -> uuid.UUID:
    """Seed one revision (+ its operation + url-index row) for an existing API."""
    async with registry_db.session() as session:
        revision = ApiRevision(
            api_id=api_id,
            state=state.value,
            source_type="url",
            submitted_by=submitted_by,
        )
        session.add(revision)
        await session.flush()
        rev_id = revision.id
        if make_current:
            await ApiRepository.set_current_revision(session, api_id, rev_id)
        await session.commit()

    async with registry_db.session() as session:
        op_ids = await OperationRepository.bulk_create(
            session, rev_id, [OperationInput(path=path, method="GET")], created_by="usr_test"
        )
        await session.commit()

    entry = build_index_entry(HOST, path, "https")
    async with registry_db.session() as session:
        await UrlIndexRepository.upsert_entry(
            session,
            revision_id=rev_id,
            operation_id=op_ids[0],
            method="GET",
            entry=entry,
            created_by="usr_test",
        )
        await session.commit()

    return rev_id


@pytest.fixture()
async def seeded(registry_db: DatabaseSession, clean_url_index: None) -> dict[str, uuid.UUID]:
    """One API with published + owned-draft + foreign-draft + archived revisions."""
    async with registry_db.session() as session:
        api = Api(vendor=VENDOR, name=NAME, version=VERSION)
        session.add(api)
        await session.commit()
        api_id = api.id

    published = await _seed_revision(
        registry_db,
        api_id=api_id,
        state=ApiRevisionState.PUBLISHED,
        submitted_by=OWNER,
        make_current=True,
        path=PUBLISHED_PATH,
    )
    owned_draft = await _seed_revision(
        registry_db,
        api_id=api_id,
        state=ApiRevisionState.DRAFT,
        submitted_by=OWNER,
        make_current=False,
        path=OWNED_DRAFT_PATH,
    )
    foreign_draft = await _seed_revision(
        registry_db,
        api_id=api_id,
        state=ApiRevisionState.DRAFT,
        submitted_by=OTHER,
        make_current=False,
        path=FOREIGN_DRAFT_PATH,
    )
    archived = await _seed_revision(
        registry_db,
        api_id=api_id,
        state=ApiRevisionState.ARCHIVED,
        submitted_by=OWNER,
        make_current=False,
        path=ARCHIVED_PATH,
    )
    return {
        "api_id": api_id,
        "published": published,
        "owned_draft": owned_draft,
        "foreign_draft": foreign_draft,
        "archived": archived,
    }


def _identity(sub: str) -> Identity:
    return Identity(sub=sub)


def _label(rev_id: uuid.UUID) -> str:
    return f"rev_{rev_id.hex}"


def _api_ref() -> APIReference:
    return APIReference(vendor=VENDOR, name=NAME, version=VERSION)


def _no_http_monkeypatch(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make any outbound HTTP during resolution an immediate test failure."""

    async def _boom(*_a: object, **_k: object) -> object:
        raise AssertionError("revision-pin resolution made an outbound HTTP call")

    monkeypatch.setattr(httpx.AsyncClient, "send", _boom)


async def test_published_pin_resolves_in_process_no_http(
    registry_db: DatabaseSession,
    seeded: dict[str, uuid.UUID],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _no_http_monkeypatch(monkeypatch)
    resolver = InProcessRegistryResolver(registry_db)
    pins = {(VENDOR, NAME, VERSION): _label(seeded["published"])}

    revision_id = await resolve_pin_for_api(
        resolver, api=_api_ref(), pins=pins, identity=_identity(OWNER)
    )
    assert revision_id == seeded["published"]

    # Discovery against the pinned revision resolves the operation.
    resolved = await discover(resolver, method="GET", url=PUBLISHED_URL, revision_id=revision_id)
    assert resolved is not None
    assert resolved.api.vendor == VENDOR


async def test_owned_draft_pin_resolves(
    registry_db: DatabaseSession, seeded: dict[str, uuid.UUID]
) -> None:
    resolver = InProcessRegistryResolver(registry_db)
    pins = {(VENDOR, NAME, VERSION): _label(seeded["owned_draft"])}

    revision_id = await resolve_pin_for_api(
        resolver, api=_api_ref(), pins=pins, identity=_identity(OWNER)
    )
    assert revision_id == seeded["owned_draft"]


async def test_foreign_draft_pin_forbidden(
    registry_db: DatabaseSession, seeded: dict[str, uuid.UUID]
) -> None:
    resolver = InProcessRegistryResolver(registry_db)
    pins = {(VENDOR, NAME, VERSION): _label(seeded["foreign_draft"])}

    with pytest.raises(UnauthorizedRevisionPinError):
        await resolve_pin_for_api(resolver, api=_api_ref(), pins=pins, identity=_identity(OWNER))


async def test_archived_pin_unprocessable(
    registry_db: DatabaseSession, seeded: dict[str, uuid.UUID]
) -> None:
    resolver = InProcessRegistryResolver(registry_db)
    pins = {(VENDOR, NAME, VERSION): _label(seeded["archived"])}

    with pytest.raises(InvalidRevisionPinError):
        await resolve_pin_for_api(resolver, api=_api_ref(), pins=pins, identity=_identity(OWNER))


async def test_unknown_revision_unprocessable(
    registry_db: DatabaseSession, seeded: dict[str, uuid.UUID]
) -> None:
    resolver = InProcessRegistryResolver(registry_db)
    pins = {(VENDOR, NAME, VERSION): _label(uuid.uuid4())}

    with pytest.raises(InvalidRevisionPinError):
        await resolve_pin_for_api(resolver, api=_api_ref(), pins=pins, identity=_identity(OWNER))


async def test_no_pin_for_api_returns_none(
    registry_db: DatabaseSession, seeded: dict[str, uuid.UUID]
) -> None:
    resolver = InProcessRegistryResolver(registry_db)
    pins = {("other", "thing", "v9"): _label(seeded["published"])}

    revision_id = await resolve_pin_for_api(
        resolver, api=_api_ref(), pins=pins, identity=_identity(OWNER)
    )
    assert revision_id is None


async def test_resolver_outcomes_directly(
    registry_db: DatabaseSession, seeded: dict[str, uuid.UUID]
) -> None:
    """The resolver classifies each case into the neutral outcome enum."""
    resolver = InProcessRegistryResolver(registry_db)

    published = await resolver.resolve_revision_pin(
        vendor=VENDOR,
        name=NAME,
        version=VERSION,
        rev_label=_label(seeded["published"]),
        identity=_identity(OWNER),
    )
    assert published.outcome is RevisionPinOutcome.RESOLVED
    assert published.revision_id == seeded["published"]

    foreign = await resolver.resolve_revision_pin(
        vendor=VENDOR,
        name=NAME,
        version=VERSION,
        rev_label=_label(seeded["foreign_draft"]),
        identity=_identity(OWNER),
    )
    assert foreign.outcome is RevisionPinOutcome.FORBIDDEN

    archived = await resolver.resolve_revision_pin(
        vendor=VENDOR,
        name=NAME,
        version=VERSION,
        rev_label=_label(seeded["archived"]),
        identity=_identity(OWNER),
    )
    assert archived.outcome is RevisionPinOutcome.ARCHIVED

    unknown = await resolver.resolve_revision_pin(
        vendor=VENDOR,
        name=NAME,
        version=VERSION,
        rev_label="rev_notavaliduuid",
        identity=_identity(OWNER),
    )
    assert unknown.outcome is RevisionPinOutcome.UNKNOWN


async def test_pin_and_live_share_one_url(
    registry_db: DatabaseSession, clean_url_index: None
) -> None:
    """A draft indexing the live revision's exact URL keeps its own row.

    Unpinned discovery still answers from the live revision; a pin to the draft
    answers from the draft — neither displaces the other (#1086).
    """
    async with registry_db.session() as session:
        api = Api(vendor=VENDOR, name=NAME, version=VERSION)
        session.add(api)
        await session.commit()
        api_id = api.id

    live = await _seed_revision(
        registry_db,
        api_id=api_id,
        state=ApiRevisionState.PUBLISHED,
        submitted_by=OWNER,
        make_current=True,
        path=PUBLISHED_PATH,
    )
    draft = await _seed_revision(
        registry_db,
        api_id=api_id,
        state=ApiRevisionState.DRAFT,
        submitted_by=OWNER,
        make_current=False,
        path=PUBLISHED_PATH,
    )
    async with registry_db.session() as session:
        rows = (await session.execute(select(Operation.id, Operation.revision_id))).all()
    op_of = {revision_id: op_id for op_id, revision_id in rows}

    resolver = InProcessRegistryResolver(registry_db)
    unpinned = await discover(resolver, method="GET", url=PUBLISHED_URL)
    assert unpinned is not None
    assert unpinned.operation_id == op_of[live]

    pinned = await discover(resolver, method="GET", url=PUBLISHED_URL, revision_id=draft)
    assert pinned is not None
    assert pinned.operation_id == op_of[draft]


async def test_draft_only_url_resolves_only_through_its_pin(
    registry_db: DatabaseSession, seeded: dict[str, uuid.UUID]
) -> None:
    """A URL only a draft defines is not served unpinned, but its owner can pin it."""
    resolver = InProcessRegistryResolver(registry_db)
    draft_url = f"https://{HOST}{OWNED_DRAFT_PATH}"

    assert await discover(resolver, method="GET", url=draft_url) is None

    pins = {(VENDOR, NAME, VERSION): _label(seeded["owned_draft"])}
    found = await discover_via_pins(
        resolver, method="GET", url=draft_url, pins=pins, identity=_identity(OWNER)
    )
    assert found is not None
    resolved, revision_id = found
    assert revision_id == seeded["owned_draft"]
    assert resolved.api == _api_ref()


async def test_pin_fallback_skips_pins_the_caller_cannot_use(
    registry_db: DatabaseSession, seeded: dict[str, uuid.UUID]
) -> None:
    """Another user's draft (or an archived revision) never serves via the fallback."""
    resolver = InProcessRegistryResolver(registry_db)

    foreign = await discover_via_pins(
        resolver,
        method="GET",
        url=f"https://{HOST}{FOREIGN_DRAFT_PATH}",
        pins={(VENDOR, NAME, VERSION): _label(seeded["foreign_draft"])},
        identity=_identity(OWNER),
    )
    archived = await discover_via_pins(
        resolver,
        method="GET",
        url=f"https://{HOST}{ARCHIVED_PATH}",
        pins={(VENDOR, NAME, VERSION): _label(seeded["archived"])},
        identity=_identity(OWNER),
    )
    assert foreign is None
    assert archived is None
