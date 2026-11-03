"""``UrlIndexRepository`` candidate lookups: results and SQLAlchemy-warning-free execution.

The candidate lookups run on every broker call (route discovery), so they must
not touch a SQLAlchemy API that emits a warning: each warning walks the stack
before ``warnings`` dedupes it, a measurable per-call CPU cost (#1820). The test
escalates SQLAlchemy's warning categories to ``always`` so a deprecated call
cannot hide behind the once-per-location dedupe; warnings from other libraries
are out of scope and ignored.

The deprecations this pins (``Result.tuples()`` and friends) only warn on
SQLAlchemy >= 2.1. On the 2.0 line that ``uv.lock`` pins, the warning assertion
passes either way and ``tests/arch/test_no_sqlalchemy_tuple_shims.py`` is the CI
guard; the identity assertions hold on every version.
"""

from __future__ import annotations

import uuid
import warnings
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.exc import SADeprecationWarning, SAPendingDeprecationWarning, SAWarning
from sqlalchemy.ext.asyncio import AsyncSession

from jentic_one.registry.core.schema.api_revisions import ApiRevision
from jentic_one.registry.core.schema.apis import Api
from jentic_one.registry.core.schema.operation_url_index import OperationURLIndex
from jentic_one.registry.core.schema.operations import Operation
from jentic_one.registry.repos.url_index_repo import UrlIndexCandidate, UrlIndexRepository
from jentic_one.shared.db.session import DatabaseSession

pytestmark = pytest.mark.integration

_SA_WARNINGS: tuple[type[Warning], ...] = (
    SAWarning,
    SADeprecationWarning,
    SAPendingDeprecationWarning,
)
_HOST = "api.example.com"

Lookup = Callable[[AsyncSession, uuid.UUID], Awaitable[list[UrlIndexCandidate]]]


async def _seed(session: AsyncSession, api: Api, revision: ApiRevision) -> None:
    """Make ``revision`` live with a concrete-host row and a regex-host row."""
    session.add_all(
        [
            Operation(id="op_list", revision_id=revision.id, path="/v1/pets", method="GET"),
            Operation(id="op_regex", revision_id=revision.id, path="/v1/items", method="GET"),
        ]
    )
    await session.flush()
    session.add_all(
        [
            OperationURLIndex(
                operation_id="op_list",
                revision_id=revision.id,
                method="GET",
                host=_HOST,
                host_regex=r"^api\.example\.com$",
                path_template="/v1/pets",
                path_regex=r"^/v1/pets$",
                param_names=[],
                segment_count=2,
            ),
            OperationURLIndex(
                operation_id="op_regex",
                revision_id=revision.id,
                method="GET",
                host=None,
                host_regex=r"^[^.]+\.example\.com$",
                path_template="/v1/items",
                path_regex=r"^/v1/items$",
                param_names=[],
                segment_count=2,
            ),
        ]
    )
    live_revision = await session.get(ApiRevision, revision.id)
    live_api = await session.get(Api, api.id)
    assert live_revision is not None and live_api is not None
    live_revision.state = "published"
    live_api.current_revision_id = revision.id
    await session.commit()


_LOOKUPS: dict[str, tuple[Lookup, str]] = {
    "by_host": (
        lambda s, rev_id: UrlIndexRepository.lookup_by_host(
            s, revision_id=rev_id, method="GET", host=_HOST, segment_count=2
        ),
        "op_list",
    ),
    "by_host_live": (
        lambda s, _rev_id: UrlIndexRepository.lookup_by_host_live(
            s, method="GET", host=_HOST, segment_count=2
        ),
        "op_list",
    ),
    "by_host_regex": (
        lambda s, rev_id: UrlIndexRepository.lookup_by_host_regex(
            s, revision_id=rev_id, method="GET", segment_count=2
        ),
        "op_regex",
    ),
    "by_host_regex_live": (
        lambda s, _rev_id: UrlIndexRepository.lookup_by_host_regex_live(
            s, method="GET", segment_count=2
        ),
        "op_regex",
    ),
}


@pytest.mark.asyncio
@pytest.mark.parametrize("lookup_name", list(_LOOKUPS))
async def test_candidate_lookup_returns_identity_without_sqlalchemy_warnings(
    registry_db: DatabaseSession,
    sample_revision: tuple[Api, ApiRevision],
    lookup_name: str,
) -> None:
    """Each candidate lookup unpacks its joined row into the API identity and the
    operation's path + method, and emits no SQLAlchemy warning while doing so."""
    api, revision = sample_revision
    lookup, expected_operation_id = _LOOKUPS[lookup_name]
    async with registry_db.session() as session:
        await _seed(session, api, revision)

    async with registry_db.session() as session:
        with warnings.catch_warnings(record=True) as caught:
            for category in _SA_WARNINGS:
                warnings.simplefilter("always", category)
            candidates = await lookup(session, revision.id)

    assert [str(w.message) for w in caught if issubclass(w.category, _SA_WARNINGS)] == []
    assert len(candidates) == 1
    candidate = candidates[0]
    assert isinstance(candidate.row, OperationURLIndex)
    assert candidate.row.operation_id == expected_operation_id
    context = candidate.context()
    assert (context.api.vendor, context.api.name, context.api.version) == (
        api.vendor,
        api.name,
        api.version,
    )
    expected_path = "/v1/pets" if expected_operation_id == "op_list" else "/v1/items"
    assert (context.path, context.method) == (expected_path, "GET")


def _url_row(operation_id: str, revision_id: uuid.UUID) -> OperationURLIndex:
    return OperationURLIndex(
        operation_id=operation_id,
        revision_id=revision_id,
        method="GET",
        host=_HOST,
        host_regex=r"^api\.example\.com$",
        path_template="/v1/pets",
        path_regex=r"^/v1/pets$",
        param_names=[],
        segment_count=2,
    )


@pytest.mark.asyncio
async def test_live_lookup_returns_only_current_revisions_newest_first(
    registry_db: DatabaseSession, clean_registry: None
) -> None:
    """Pins #1976: with several APIs on one host, each holding archived, draft
    and live revisions that all index the same URL, the unpinned lookup returns
    one row per API — its live revision's, published or imported — ordered
    most-recently-live first (``promoted_at``, falling back to ``created_at``),
    ties broken by ``revision_id``. An API with no live revision contributes
    nothing, and a pinned lookup still resolves an archived revision."""
    base = datetime(2026, 1, 1, tzinfo=UTC)
    # name -> (live state, live promoted_at offset in days or None for the
    # created_at fallback, live created_at offset, live revision id or None).
    # The created_at offsets alone order beta, delta, gamma, alpha, so sorting
    # on created_at instead of the coalesce fails; alpha and gamma tie on
    # promoted_at and get fixed ids so the revision_id tiebreak is deterministic.
    shapes: dict[str, tuple[str, int | None, int, uuid.UUID | None]] = {
        "alpha": ("published", 5, 1, uuid.UUID("00000000-0000-4000-8000-000000000002")),
        "beta": ("published", None, 9, None),
        "gamma": ("published", 5, 2, uuid.UUID("00000000-0000-4000-8000-000000000001")),
        "delta": ("imported", 3, 8, None),
        # No live revision: current_revision_id stays NULL.
        "epsilon": ("", None, 0, None),
    }
    live_ids: dict[str, uuid.UUID] = {}
    archived_ids: dict[str, uuid.UUID] = {}
    async with registry_db.session() as session:
        for name, (state, promoted_days, created_days, live_id) in shapes.items():
            api = Api(vendor="test.com", name=name, version="v1")
            session.add(api)
            await session.flush()
            archived = ApiRevision(
                api_id=api.id,
                state="archived",
                spec_digest=f"sha256:{name}-archived",
                source_type="url",
                created_at=base,
                promoted_at=base + timedelta(days=20),
            )
            draft = ApiRevision(
                api_id=api.id,
                state="draft",
                spec_digest=f"sha256:{name}-draft",
                source_type="url",
                created_at=base + timedelta(days=30),
            )
            revisions = [("archived", archived), ("draft", draft)]
            if state:
                live = ApiRevision(
                    api_id=api.id,
                    state=state,
                    spec_digest=f"sha256:{name}-live",
                    source_type="url",
                    created_at=base + timedelta(days=created_days),
                    promoted_at=(
                        base + timedelta(days=promoted_days) if promoted_days is not None else None
                    ),
                )
                if live_id is not None:
                    live.id = live_id
                revisions.append(("live", live))
            session.add_all([revision for _, revision in revisions])
            await session.flush()
            for label, revision in revisions:
                op_id = f"op_{name}_{label}"
                session.add(
                    Operation(id=op_id, revision_id=revision.id, path="/v1/pets", method="GET")
                )
                await session.flush()
                session.add(_url_row(op_id, revision.id))
            if state:
                api.current_revision_id = live.id
                live_ids[name] = live.id
            archived_ids[name] = archived.id
        await session.commit()

    async with registry_db.session() as session:
        candidates = await UrlIndexRepository.lookup_by_host_live(
            session, method="GET", host=_HOST, segment_count=2
        )
        pinned = await UrlIndexRepository.lookup_by_host(
            session,
            revision_id=archived_ids["alpha"],
            method="GET",
            host=_HOST,
            segment_count=2,
        )

    # beta: created_at +9 (no promoted_at); gamma, alpha: promoted_at +5, tie
    # broken by revision_id; delta: promoted_at +3; epsilon: no live revision.
    expected = ["beta", "gamma", "alpha", "delta"]
    assert [(c.name, c.row.revision_id) for c in candidates] == [
        (name, live_ids[name]) for name in expected
    ]
    assert [c.row.operation_id for c in candidates] == [f"op_{name}_live" for name in expected]
    assert [(c.name, c.row.operation_id) for c in pinned] == [("alpha", "op_alpha_archived")]
