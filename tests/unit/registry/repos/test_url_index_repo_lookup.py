"""``UrlIndexRepository`` candidate lookups: results and warning-free execution.

The candidate lookups run on every broker call (route discovery), so they must
not touch a SQLAlchemy API that emits a deprecation warning: each warning walks
the stack before ``warnings`` dedupes it, a measurable per-call CPU cost
(#1820). The test escalates every warning to ``always`` so a deprecated call
cannot hide behind the once-per-location dedupe.

Run against a real in-memory SQLite registry DB (no DB mocking —
``tests/arch/test_no_db_mocking.py``); the session fixture lives in the shared
``tests/unit/registry/conftest.py``.
"""

from __future__ import annotations

import uuid
import warnings
from collections.abc import Awaitable, Callable

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from jentic_one.registry.core.schema.api_revisions import ApiRevision
from jentic_one.registry.core.schema.apis import Api
from jentic_one.registry.core.schema.operation_url_index import OperationURLIndex
from jentic_one.registry.core.schema.operations import Operation
from jentic_one.registry.repos.url_index_repo import UrlIndexCandidate, UrlIndexRepository

_API_ID = uuid.UUID("00000000-0000-0000-0000-0000000000a1")
_REVISION_ID = uuid.UUID("00000000-0000-0000-0000-0000000000b1")
_HOST = "api.example.com"

Lookup = Callable[[AsyncSession], Awaitable[list[UrlIndexCandidate]]]


async def _seed(session: AsyncSession) -> None:
    """One live revision with a concrete-host row and a regex-host row."""
    session.add(
        Api(
            id=_API_ID,
            vendor="example.com",
            name="pets",
            version="v1",
            display_name="Pets (display label)",
            current_revision_id=_REVISION_ID,
        )
    )
    session.add(ApiRevision(id=_REVISION_ID, api_id=_API_ID, state="published"))
    session.add_all(
        [
            Operation(id="op_list", revision_id=_REVISION_ID, path="/v1/pets", method="GET"),
            Operation(id="op_regex", revision_id=_REVISION_ID, path="/v1/items", method="GET"),
        ]
    )
    session.add_all(
        [
            OperationURLIndex(
                operation_id="op_list",
                revision_id=_REVISION_ID,
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
                revision_id=_REVISION_ID,
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
    await session.flush()


_LOOKUPS: dict[str, tuple[Lookup, str]] = {
    "by_host": (
        lambda s: UrlIndexRepository.lookup_by_host(
            s, revision_id=_REVISION_ID, method="GET", host=_HOST, segment_count=2
        ),
        "op_list",
    ),
    "by_host_live": (
        lambda s: UrlIndexRepository.lookup_by_host_live(
            s, method="GET", host=_HOST, segment_count=2
        ),
        "op_list",
    ),
    "by_host_regex": (
        lambda s: UrlIndexRepository.lookup_by_host_regex(
            s, revision_id=_REVISION_ID, method="GET", segment_count=2
        ),
        "op_regex",
    ),
    "by_host_regex_live": (
        lambda s: UrlIndexRepository.lookup_by_host_regex_live(s, method="GET", segment_count=2),
        "op_regex",
    ),
}


@pytest.mark.asyncio
@pytest.mark.parametrize("lookup_name", list(_LOOKUPS))
async def test_candidate_lookup_returns_identity_without_warnings(
    url_index_sqlite_session: AsyncSession, lookup_name: str
) -> None:
    """Each candidate lookup unpacks its joined row into the API identity and the
    operation's path + method, and emits no warning at all while doing so."""
    lookup, expected_operation_id = _LOOKUPS[lookup_name]
    await _seed(url_index_sqlite_session)

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        candidates = await lookup(url_index_sqlite_session)

    assert [str(w.message) for w in caught] == []
    assert len(candidates) == 1
    candidate = candidates[0]
    assert isinstance(candidate.row, OperationURLIndex)
    assert candidate.row.operation_id == expected_operation_id
    context = candidate.context()
    assert (context.api.vendor, context.api.name, context.api.version) == (
        "example.com",
        "pets",
        "v1",
    )
    expected_path = "/v1/pets" if expected_operation_id == "op_list" else "/v1/items"
    assert (context.path, context.method) == (expected_path, "GET")
