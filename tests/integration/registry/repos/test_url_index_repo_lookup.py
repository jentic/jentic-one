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
