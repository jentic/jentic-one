"""Unit tests for URL lookup specificity ranking and error paths."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from jentic_one.registry.core.url_index import URL_INDEX_FORMAT_MARKER
from jentic_one.registry.repos.url_index_repo import UrlIndexCandidate
from jentic_one.registry.services.inspect.url_lookup import (
    MAX_CANDIDATES,
    URLLookupService,
)
from jentic_one.shared.discovery.errors import (
    AmbiguousMatchError,
    MethodNotAllowedError,
    TooManyCandidatesError,
)


def _make_index_entry(
    *,
    operation_id: str = "op_abc",
    method: str = "GET",
    host: str | None = "api.example.com",
    host_regex: str | None = None,
    path_template: str = "/v1/pets",
    path_regex: str = r"^/v1/pets$",
    param_names: list[str] | None = None,
    segment_count: int = 2,
    revision_id: uuid.UUID | None = None,
    vendor: str = "example.com",
    name: str = "pets",
    version: str = "v1",
) -> UrlIndexCandidate:
    """Create a lookup candidate wrapping a mock OperationURLIndex row."""
    entry = MagicMock()
    entry.operation_id = operation_id
    entry.revision_id = revision_id or REVISION_ID
    entry.method = method
    entry.host = host
    entry.host_regex = host_regex
    entry.path_template = path_template
    # Rows built by the current builder carry the format marker; a row without
    # it takes the legacy re-derivation path (covered by integration tests).
    entry.path_regex = URL_INDEX_FORMAT_MARKER + path_regex
    entry.param_names = param_names or []
    entry.segment_count = segment_count
    return UrlIndexCandidate(
        row=entry,
        vendor=vendor,
        name=name,
        version=version,
        operation_path=path_template,
        operation_method=method,
    )


REVISION_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")
OTHER_REVISION_ID = uuid.UUID("00000000-0000-0000-0000-000000000002")


@pytest.mark.asyncio
async def test_literal_path_beats_parameterized() -> None:
    """A literal path should rank higher than a parameterized one."""
    literal = _make_index_entry(
        operation_id="op_literal",
        path_template="/v1/pets",
        path_regex=r"^/v1/pets$",
        param_names=[],
    )
    parameterized = _make_index_entry(
        operation_id="op_param",
        path_template="/v1/{resource}",
        path_regex=r"^/v1/(?P<resource>[^/]+)$",
        param_names=["resource"],
    )

    with patch(
        "jentic_one.registry.services.inspect.url_lookup.UrlIndexRepository.lookup_by_host",
        new_callable=AsyncMock,
        return_value=[literal, parameterized],
    ):
        svc = URLLookupService(AsyncMock())
        result = await svc.resolve(
            method="GET",
            url="https://api.example.com/v1/pets",
            revision_id=REVISION_ID,
        )

    assert result is not None
    assert result.operation_id == "op_literal"


@pytest.mark.asyncio
async def test_more_literals_beats_fewer() -> None:
    """More literal segments should rank higher."""
    more_literals = _make_index_entry(
        operation_id="op_more",
        path_template="/v1/pets/list",
        path_regex=r"^/v1/pets/list$",
        param_names=[],
        segment_count=3,
    )
    fewer_literals = _make_index_entry(
        operation_id="op_fewer",
        path_template="/v1/pets/{id}",
        path_regex=r"^/v1/pets/(?P<id>[^/]+)$",
        param_names=["id"],
        segment_count=3,
    )

    with patch(
        "jentic_one.registry.services.inspect.url_lookup.UrlIndexRepository.lookup_by_host",
        new_callable=AsyncMock,
        return_value=[more_literals, fewer_literals],
    ):
        svc = URLLookupService(AsyncMock())
        result = await svc.resolve(
            method="GET",
            url="https://api.example.com/v1/pets/list",
            revision_id=REVISION_ID,
        )

    assert result is not None
    assert result.operation_id == "op_more"


@pytest.mark.asyncio
async def test_ambiguous_match_raises_error() -> None:
    """Two candidates with identical specificity should raise AmbiguousMatchError."""
    entry1 = _make_index_entry(
        operation_id="op_1",
        path_template="/v1/{a}",
        path_regex=r"^/v1/(?P<a>[^/]+)$",
        param_names=["a"],
    )
    entry2 = _make_index_entry(
        operation_id="op_2",
        path_template="/v1/{b}",
        path_regex=r"^/v1/(?P<b>[^/]+)$",
        param_names=["b"],
    )

    with (
        patch(
            "jentic_one.registry.services.inspect.url_lookup.UrlIndexRepository.lookup_by_host",
            new_callable=AsyncMock,
            return_value=[entry1, entry2],
        ),
        pytest.raises(AmbiguousMatchError),
    ):
        svc = URLLookupService(AsyncMock())
        await svc.resolve(
            method="GET",
            url="https://api.example.com/v1/test",
            revision_id=REVISION_ID,
        )


@pytest.mark.asyncio
async def test_method_not_allowed_raises_with_allowed_methods() -> None:
    """When URL matches other methods, raise MethodNotAllowedError with allowed list."""
    post_entry = _make_index_entry(
        operation_id="op_post",
        method="POST",
        path_template="/v1/pets",
        path_regex=r"^/v1/pets$",
        param_names=[],
    )

    with (
        patch(
            "jentic_one.registry.services.inspect.url_lookup.UrlIndexRepository.lookup_by_host",
            new_callable=AsyncMock,
            return_value=[],
        ),
        patch(
            "jentic_one.registry.services.inspect.url_lookup.UrlIndexRepository.lookup_by_host_regex",
            new_callable=AsyncMock,
            return_value=[],
        ),
        patch(
            "jentic_one.registry.services.inspect.url_lookup.UrlIndexRepository.lookup_by_host_any_method",
            new_callable=AsyncMock,
            return_value=[post_entry.row],
        ),
        pytest.raises(MethodNotAllowedError) as exc_info,
    ):
        svc = URLLookupService(AsyncMock())
        await svc.resolve(
            method="GET",
            url="https://api.example.com/v1/pets",
            revision_id=REVISION_ID,
        )

    assert "POST" in exc_info.value.allowed_methods


@pytest.mark.asyncio
async def test_too_many_candidates_raises_error() -> None:
    """More than MAX_CANDIDATES should raise TooManyCandidatesError."""
    candidates = [
        _make_index_entry(
            operation_id=f"op_{i}",
            path_template=f"/v1/resource{i}",
            path_regex=f"^/v1/resource{i}$",
        )
        for i in range(MAX_CANDIDATES + 1)
    ]

    with (
        patch(
            "jentic_one.registry.services.inspect.url_lookup.UrlIndexRepository.lookup_by_host",
            new_callable=AsyncMock,
            return_value=candidates,
        ),
        pytest.raises(TooManyCandidatesError),
    ):
        svc = URLLookupService(AsyncMock())
        await svc.resolve(
            method="GET",
            url="https://api.example.com/v1/test",
            revision_id=REVISION_ID,
        )


@pytest.mark.asyncio
async def test_head_falls_back_to_get() -> None:
    """HEAD requests should fall back to GET if no HEAD route exists."""
    get_entry = _make_index_entry(
        operation_id="op_get",
        method="GET",
        path_template="/v1/pets",
        path_regex=r"^/v1/pets$",
        param_names=[],
    )

    call_count = 0

    async def mock_lookup_by_host(
        session: object, *, revision_id: object, method: str, host: str, segment_count: int
    ) -> list[UrlIndexCandidate]:
        nonlocal call_count
        call_count += 1
        if method == "GET":
            return [get_entry]
        return []

    with (
        patch(
            "jentic_one.registry.services.inspect.url_lookup.UrlIndexRepository.lookup_by_host",
            side_effect=mock_lookup_by_host,
        ),
        patch(
            "jentic_one.registry.services.inspect.url_lookup.UrlIndexRepository.lookup_by_host_regex",
            new_callable=AsyncMock,
            return_value=[],
        ),
    ):
        svc = URLLookupService(AsyncMock())
        result = await svc.resolve(
            method="HEAD",
            url="https://api.example.com/v1/pets",
            revision_id=REVISION_ID,
        )

    assert result is not None
    assert result.operation_id == "op_get"
    assert call_count == 2


@pytest.mark.asyncio
async def test_no_match_returns_none() -> None:
    """When nothing matches, resolve returns None."""
    with (
        patch(
            "jentic_one.registry.services.inspect.url_lookup.UrlIndexRepository.lookup_by_host",
            new_callable=AsyncMock,
            return_value=[],
        ),
        patch(
            "jentic_one.registry.services.inspect.url_lookup.UrlIndexRepository.lookup_by_host_regex",
            new_callable=AsyncMock,
            return_value=[],
        ),
        patch(
            "jentic_one.registry.services.inspect.url_lookup.UrlIndexRepository.lookup_by_host_any_method",
            new_callable=AsyncMock,
            return_value=[],
        ),
        patch(
            "jentic_one.registry.services.inspect.url_lookup.UrlIndexRepository.lookup_by_host_regex_any_method",
            new_callable=AsyncMock,
            return_value=[],
        ),
    ):
        svc = URLLookupService(AsyncMock())
        result = await svc.resolve(
            method="GET",
            url="https://unknown.example.com/nothing",
            revision_id=REVISION_ID,
        )

    assert result is None


@pytest.mark.asyncio
async def test_equal_matches_across_live_revisions_of_one_api_prefer_the_newest() -> None:
    """Two live revisions of one API resolve to the most recently promoted one.

    Unpinned candidates arrive newest-live revision first, and a request names
    a URL rather than a version, so the API's own latest is the answer. Both
    candidates are the same ``vendor/name``, which is what keeps this a version
    choice rather than the cross-API ambiguity below.
    """
    newest = _make_index_entry(operation_id="op_new", revision_id=OTHER_REVISION_ID)
    older = _make_index_entry(operation_id="op_old", revision_id=REVISION_ID)

    with patch(
        "jentic_one.registry.services.inspect.url_lookup.UrlIndexRepository.lookup_by_host_live",
        new_callable=AsyncMock,
        return_value=[newest, older],
    ):
        svc = URLLookupService(AsyncMock())
        result = await svc.resolve(method="GET", url="https://api.example.com/v1/pets")

    assert result is not None
    assert result.operation_id == "op_new"


@pytest.mark.asyncio
async def test_ambiguity_within_the_winning_revision_still_raises() -> None:
    """Two equally specific templates in the same live revision stay ambiguous."""
    first = _make_index_entry(
        operation_id="op_1",
        path_template="/v1/{a}",
        path_regex=r"^/v1/(?P<a>[^/]+)$",
        param_names=["a"],
        revision_id=OTHER_REVISION_ID,
    )
    second = _make_index_entry(
        operation_id="op_2",
        path_template="/v1/{b}",
        path_regex=r"^/v1/(?P<b>[^/]+)$",
        param_names=["b"],
        revision_id=OTHER_REVISION_ID,
    )

    with (
        patch(
            "jentic_one.registry.services.inspect.url_lookup.UrlIndexRepository.lookup_by_host_live",
            new_callable=AsyncMock,
            return_value=[first, second],
        ),
        pytest.raises(AmbiguousMatchError),
    ):
        svc = URLLookupService(AsyncMock())
        await svc.resolve(method="GET", url="https://api.example.com/v1/test")


@pytest.mark.asyncio
async def test_equal_matches_across_two_live_apis_raise_ambiguous() -> None:
    """Two different live APIs serving one URL is ambiguous, not a newest-wins pick.

    The winner's identity is what credential coverage is evaluated against, so
    resolving this by promotion order would silently decide which credential
    the broker injects. The error names both APIs so the operator can re-scope
    or retire one.
    """
    newest = _make_index_entry(
        operation_id="op_twin",
        revision_id=OTHER_REVISION_ID,
        name="pets-admin",
    )
    older = _make_index_entry(operation_id="op_pets", revision_id=REVISION_ID)

    with (
        patch(
            "jentic_one.registry.services.inspect.url_lookup.UrlIndexRepository.lookup_by_host_live",
            new_callable=AsyncMock,
            return_value=[newest, older],
        ),
        pytest.raises(AmbiguousMatchError) as caught,
    ):
        svc = URLLookupService(AsyncMock())
        await svc.resolve(method="GET", url="https://api.example.com/v1/pets")

    assert caught.value.apis == ["example.com/pets", "example.com/pets-admin"]
    assert "example.com/pets-admin" in caught.value.detail


@pytest.mark.asyncio
async def test_two_live_apis_on_one_host_with_distinct_paths_still_resolve() -> None:
    """A gateway host serving several APIs on different paths is unaffected.

    Only an equally specific tie is ambiguous. These two APIs share the host
    but not the path, so each URL still resolves to exactly one of them.
    """
    pets = _make_index_entry(operation_id="op_pets")
    admin = _make_index_entry(
        operation_id="op_admin",
        revision_id=OTHER_REVISION_ID,
        name="pets-admin",
        path_template="/v1/admin",
        path_regex=r"^/v1/admin$",
    )

    with patch(
        "jentic_one.registry.services.inspect.url_lookup.UrlIndexRepository.lookup_by_host_live",
        new_callable=AsyncMock,
        return_value=[admin, pets],
    ):
        svc = URLLookupService(AsyncMock())
        resolved_pets = await svc.resolve(method="GET", url="https://api.example.com/v1/pets")
        resolved_admin = await svc.resolve(method="GET", url="https://api.example.com/v1/admin")

    assert resolved_pets is not None
    assert resolved_pets.operation_id == "op_pets"
    assert resolved_admin is not None
    assert resolved_admin.operation_id == "op_admin"


@pytest.mark.asyncio
async def test_a_more_specific_match_wins_before_cross_api_ambiguity_applies() -> None:
    """Specificity is still decided first: an exact path beats another API's template.

    Ambiguity is only raised among the *equally* specific matches, so a literal
    path on one API outranks a parameterized one on another rather than
    colliding with it.
    """
    literal = _make_index_entry(operation_id="op_literal")
    templated = _make_index_entry(
        operation_id="op_templated",
        revision_id=OTHER_REVISION_ID,
        name="pets-admin",
        path_template="/v1/{slug}",
        path_regex=r"^/v1/(?P<slug>[^/]+)$",
        param_names=["slug"],
    )

    with patch(
        "jentic_one.registry.services.inspect.url_lookup.UrlIndexRepository.lookup_by_host_live",
        new_callable=AsyncMock,
        return_value=[templated, literal],
    ):
        svc = URLLookupService(AsyncMock())
        result = await svc.resolve(method="GET", url="https://api.example.com/v1/pets")

    assert result is not None
    assert result.operation_id == "op_literal"
