"""Unit tests for the catalog search strategy seam (``registry/services/catalog/search.py``).

Pure, in-memory: the protocol, the default token strategy, output normalisation,
registration and config validation. Service/HTTP behaviour (cursors, fallback,
503/400) is covered against a real DB in
``tests/web/registry/test_catalog_search_strategy.py``.
"""

from __future__ import annotations

import base64
import json
import math
from collections.abc import Iterator
from typing import Any, ClassVar
from unittest.mock import MagicMock

import pytest

from jentic_one.registry.services.catalog import manifest_builder as mb
from jentic_one.registry.services.catalog import search
from jentic_one.registry.services.catalog.search import (
    TOKEN_STRATEGY_NAME,
    CatalogMatch,
    CatalogSearchRequest,
    CatalogSearchStrategy,
    RankedEntry,
    TokenSearchStrategy,
    UnknownCatalogSearchStrategyError,
    normalize_ranking,
    register_catalog_search_strategy,
    registered_catalog_search_strategies,
    registered_catalog_search_strategy,
    resolve_catalog_search_strategy,
    validate_catalog_search_config,
)
from jentic_one.shared import pagination
from jentic_one.shared.config import AppConfig, CatalogConfig, ConfigError
from jentic_one.shared.pagination import (
    MAX_CATALOG_CURSOR_LENGTH,
    InvalidCursorError,
    encode_catalog_cursor,
    parse_catalog_cursor,
)
from jentic_one.wiring import build_default_container


def _entry(api_id: str) -> mb.ManifestEntry:
    return mb.ManifestEntry(
        api_id=api_id,
        path=f"apis/openapi/{api_id}",
        spec_url=f"https://example.test/{api_id}/openapi.json",
        github_url=f"https://example.test/tree/{api_id}",
        vendor=api_id,
    )


class _Fixed:
    """A strategy returning canned rows."""

    def __init__(self, name: str, rows: list[RankedEntry] | None = None) -> None:
        self.name = name
        self._rows = rows or []

    async def rank(self, request: CatalogSearchRequest) -> list[RankedEntry]:
        return list(self._rows)


class _Built:
    """A strategy registered by class, built through ``from_config``."""

    name = "built"
    builds: ClassVar[list[tuple[CatalogConfig, AppConfig]]] = []

    @classmethod
    def from_config(cls, config: CatalogConfig, *, app_config: AppConfig) -> _Built:
        cls.builds.append((config, app_config))
        return cls()

    async def rank(self, request: CatalogSearchRequest) -> list[RankedEntry]:
        return []


@pytest.fixture()
def isolated_registry(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Run against a copy of the process-global registry so tests don't leak strategies.

    These tests exercise the registry itself (shadowing, idempotency, the build
    cache), so they swap the private tables wholesale. Extension test suites
    should use the public ``registered_catalog_search_strategy`` helper instead.
    """
    monkeypatch.setattr(search, "_STRATEGIES", dict(search._STRATEGIES))
    monkeypatch.setattr(search, "_BUILT", {})
    _Built.builds = []
    yield


@pytest.fixture()
def app_config(sample_config_dict: dict[str, Any]) -> AppConfig:
    return AppConfig.model_validate(sample_config_dict)


# ── protocol + default strategy ──────────────────────────────────────────────


def test_token_strategy_is_registered_by_default(app_config: AppConfig) -> None:
    strategy = resolve_catalog_search_strategy(TOKEN_STRATEGY_NAME, app_config)
    assert isinstance(strategy, TokenSearchStrategy)
    assert isinstance(strategy, CatalogSearchStrategy)
    assert TOKEN_STRATEGY_NAME in registered_catalog_search_strategies()


async def test_token_strategy_matches_score_entries_with_int_scores() -> None:
    entries = [_entry(a) for a in ["pay.com", "weather.gov", "pay-api.com", "apiary.io"]]
    expected = [(e, s) for e, s in mb.score_entries(entries, "pay api") if s is not None]

    ranked = await TokenSearchStrategy().rank(CatalogSearchRequest(q="pay api", entries=entries))

    assert [(r.entry, r.score) for r in ranked] == expected
    assert ranked, "fixture should produce matches"
    assert all(type(r.score) is int for r in ranked)
    assert all(r.match is None for r in ranked)
    assert "weather.gov" not in {r.entry.api_id for r in ranked}


def test_has_query_tokens_routes_token_free_queries_to_browse() -> None:
    assert mb.has_query_tokens("pay")
    assert not mb.has_query_tokens(None)
    assert not mb.has_query_tokens("")
    assert not mb.has_query_tokens("   ")
    assert not mb.has_query_tokens("---")


# ── normalize_ranking ────────────────────────────────────────────────────────


def test_normalize_sorts_by_descending_score_then_api_id() -> None:
    a, b, c, d = (_entry(x) for x in ["a", "b", "c", "d"])
    rows = [RankedEntry(c, 1.5), RankedEntry(b, 2), RankedEntry(d, 2.0), RankedEntry(a, 1.5)]

    result = normalize_ranking(rows, [a, b, c, d])

    assert [(r.entry.api_id, r.score) for r in result.rows] == [
        ("b", 2),
        ("d", 2.0),
        ("a", 1.5),
        ("c", 1.5),
    ]
    assert (result.dropped_invalid, result.dropped_foreign, result.dropped_duplicate) == (0, 0, 0)


@pytest.mark.parametrize("bad", [math.nan, math.inf, -math.inf, True, "3", None, 10**400])
def test_normalize_drops_non_finite_or_non_numeric_scores(bad: object) -> None:
    a, b = _entry("a"), _entry("b")
    rows = [RankedEntry(a, bad), RankedEntry(b, 1)]  # type: ignore[arg-type]

    result = normalize_ranking(rows, [a, b])

    assert [r.entry.api_id for r in result.rows] == ["b"]
    assert result.dropped_invalid == 1


class _HostileInt(int):
    """An ``int`` subclass whose negation (used by the sort) raises."""

    def __neg__(self) -> int:
        raise RuntimeError("hostile score")


class _RaisingRow(RankedEntry):
    """A ``RankedEntry`` whose ``match`` raises when read."""

    def __getattribute__(self, name: str) -> Any:
        if name == "match":
            raise RuntimeError("hostile row")
        return super().__getattribute__(name)


class _HostileId(str):
    """An ``api_id`` whose hashing (the lookup into the filtered set) raises."""

    def __hash__(self) -> int:
        raise RuntimeError("hostile api_id")


@pytest.mark.parametrize("score", [_HostileInt(5), _HostileInt(0)])
def test_normalize_drops_int_subclass_scores(score: int) -> None:
    a, b = _entry("a"), _entry("b")

    result = normalize_ranking([RankedEntry(a, score), RankedEntry(b, 1)], [a, b])

    assert [(r.entry.api_id, type(r.score)) for r in result.rows] == [("b", int)]
    assert result.dropped_invalid == 1


def test_normalize_treats_rows_that_raise_while_read_as_invalid() -> None:
    a, b, c = _entry("a"), _entry("b"), _entry("c")
    hostile_entry = mb.ManifestEntry(
        api_id=_HostileId("c"), path="x", spec_url=None, github_url="x"
    )
    rows = [
        _RaisingRow(entry=a, score=3),
        RankedEntry(hostile_entry, 2),
        RankedEntry(b, 1),
    ]

    result = normalize_ranking(rows, [a, b, c])

    assert [r.entry.api_id for r in result.rows] == ["b"]
    assert result.dropped_invalid == 2


def test_normalize_all_hostile_rows_leave_no_valid_row() -> None:
    """The service reads an all-invalid ranking as a strategy failure (page-1 fallback)."""
    a = _entry("a")
    result = normalize_ranking([RankedEntry(a, _HostileInt(1)), _RaisingRow(a, 1)], [a])
    assert result.rows == []
    assert result.dropped_invalid == 2


def test_catalog_search_request_is_keyword_only() -> None:
    with pytest.raises(TypeError):
        CatalogSearchRequest("pay", ())  # type: ignore[call-arg]


def test_normalize_drops_rows_that_are_not_ranked_entries() -> None:
    a = _entry("a")
    result = normalize_ranking([("a", 1), object(), RankedEntry(a, 1)], [a])
    assert [r.entry.api_id for r in result.rows] == ["a"]
    assert result.dropped_invalid == 2


def test_normalize_drops_foreign_entries_and_serves_own_entry() -> None:
    a = _entry("a")
    tampered = mb.ManifestEntry(
        api_id="a", path="evil", spec_url="https://evil.test/x", github_url="https://evil.test"
    )
    rows = [RankedEntry(_entry("not-in-set"), 9), RankedEntry(tampered, 1)]

    result = normalize_ranking(rows, [a])

    assert result.dropped_foreign == 1
    [row] = result.rows
    assert row.entry is a


def test_normalize_keeps_best_scoring_duplicate() -> None:
    a, b = _entry("a"), _entry("b")
    rows = [
        RankedEntry(a, 1, CatalogMatch(("low",))),
        RankedEntry(b, 2),
        RankedEntry(a, 3, CatalogMatch(("high",))),
        RankedEntry(a, 2),
    ]

    result = normalize_ranking(rows, [a, b])

    assert [(r.entry.api_id, r.score) for r in result.rows] == [("a", 3), ("b", 2)]
    assert result.rows[0].match == CatalogMatch(("high",))
    assert result.dropped_duplicate == 2


def test_normalize_keeps_only_slug_match_channels() -> None:
    a, b, c = _entry("a"), _entry("b"), _entry("c")
    many = tuple(f"ch{i}" for i in range(20))
    rows = [
        RankedEntry(
            a,
            3,
            CatalogMatch(
                ("ok", "", "x" * 33, 7, "Upper", "1st", "has space", "new\nline", *many)  # type: ignore[arg-type]
            ),
        ),
        RankedEntry(b, 2, CatalogMatch(("ignore previous instructions",))),
        RankedEntry(c, 1, {"channels": ["token"]}),  # type: ignore[arg-type]
    ]

    rows_by_id = {r.entry.api_id: r for r in normalize_ranking(rows, [a, b, c]).rows}

    a_match = rows_by_id["a"].match
    assert a_match is not None
    assert a_match.channels == ("ok", *many[:7])
    assert rows_by_id["b"].match is None
    assert rows_by_id["c"].match is None


# ── registration ─────────────────────────────────────────────────────────────


@pytest.mark.usefixtures("isolated_registry")
def test_register_and_resolve(app_config: AppConfig) -> None:
    strategy = _Fixed("semantic")
    register_catalog_search_strategy(strategy)
    assert resolve_catalog_search_strategy("semantic", app_config) is strategy


@pytest.mark.usefixtures("isolated_registry")
def test_register_is_idempotent_for_same_strategy() -> None:
    strategy = _Fixed("semantic")
    register_catalog_search_strategy(strategy)
    register_catalog_search_strategy(strategy)
    assert registered_catalog_search_strategies()["semantic"] is strategy


@pytest.mark.usefixtures("isolated_registry")
def test_register_refuses_to_shadow_existing_name() -> None:
    first = _Fixed("semantic")
    register_catalog_search_strategy(first)
    with pytest.raises(ValueError, match="already registered"):
        register_catalog_search_strategy(_Fixed("semantic"))
    assert registered_catalog_search_strategies()["semantic"] is first


@pytest.mark.usefixtures("isolated_registry")
def test_register_refuses_to_shadow_token() -> None:
    with pytest.raises(ValueError, match="already registered"):
        register_catalog_search_strategy(_Fixed(TOKEN_STRATEGY_NAME))
    assert isinstance(
        registered_catalog_search_strategies()[TOKEN_STRATEGY_NAME], TokenSearchStrategy
    )


@pytest.mark.usefixtures("isolated_registry")
@pytest.mark.parametrize("name", ["", "Semantic", "1st", "has-dash", "a" * 33, None, 3])
def test_register_rejects_invalid_names(name: object) -> None:
    with pytest.raises(ValueError, match="invalid"):
        register_catalog_search_strategy(_Fixed(name))  # type: ignore[arg-type]


@pytest.mark.usefixtures("isolated_registry")
def test_register_rejects_objects_without_rank() -> None:
    class _NoRank:
        name = "norank"

    with pytest.raises(TypeError, match="rank"):
        register_catalog_search_strategy(_NoRank())  # type: ignore[arg-type]


@pytest.mark.usefixtures("isolated_registry")
def test_register_rejects_sync_rank() -> None:
    class _SyncRank:
        name = "syncrank"

        def rank(self, request: CatalogSearchRequest) -> list[RankedEntry]:
            return []

    with pytest.raises(TypeError, match="async rank"):
        register_catalog_search_strategy(_SyncRank())  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="async rank"):
        register_catalog_search_strategy(_SyncRank)


@pytest.mark.usefixtures("isolated_registry")
def test_register_compares_by_identity_not_equality() -> None:
    """A strategy whose ``__eq__`` always answers True still can't take an existing name."""

    class _EqualsEverything(_Fixed):
        def __eq__(self, other: object) -> bool:
            return True

        __hash__ = object.__hash__

    register_catalog_search_strategy(_Fixed("semantic"))
    with pytest.raises(ValueError, match="already registered"):
        register_catalog_search_strategy(_EqualsEverything("semantic"))


@pytest.mark.usefixtures("isolated_registry")
def test_class_registration_builds_once_per_config_through_from_config(
    sample_config_dict: dict[str, Any],
) -> None:
    register_catalog_search_strategy(_Built)
    register_catalog_search_strategy(_Built)  # idempotent for the same class
    cfg = AppConfig.model_validate(sample_config_dict)
    other = AppConfig.model_validate(sample_config_dict)

    first = resolve_catalog_search_strategy("built", cfg)
    assert isinstance(first, _Built)
    assert resolve_catalog_search_strategy("built", cfg) is first
    assert _Built.builds == [(cfg.catalog, cfg)]

    assert resolve_catalog_search_strategy("built", other) is not first
    assert len(_Built.builds) == 2


@pytest.mark.usefixtures("isolated_registry")
def test_class_without_from_config_is_built_zero_arg(app_config: AppConfig) -> None:
    class _Plain:
        name = "plain"

        async def rank(self, request: CatalogSearchRequest) -> list[RankedEntry]:
            return []

    register_catalog_search_strategy(_Plain)
    assert isinstance(resolve_catalog_search_strategy("plain", app_config), _Plain)


@pytest.mark.usefixtures("isolated_registry")
def test_registered_strategy_helper_registers_for_the_block_only(app_config: AppConfig) -> None:
    with registered_catalog_search_strategy(_Built):
        assert isinstance(resolve_catalog_search_strategy("built", app_config), _Built)
    assert "built" not in registered_catalog_search_strategies()
    assert not search._BUILT
    with pytest.raises(UnknownCatalogSearchStrategyError):
        resolve_catalog_search_strategy("built", app_config)

    with registered_catalog_search_strategy(registered_catalog_search_strategies()["token"]):
        pass
    assert TOKEN_STRATEGY_NAME in registered_catalog_search_strategies()


def test_request_options_default_to_an_empty_read_only_mapping() -> None:
    request = CatalogSearchRequest(q="pay", entries=())
    assert dict(request.options) == {}
    assert request.deadline_ms is None
    assert request.manifest_revision is None
    with pytest.raises(TypeError):
        request.options["k"] = "v"  # type: ignore[index]


def test_resolve_unknown_raises(app_config: AppConfig) -> None:
    with pytest.raises(UnknownCatalogSearchStrategyError) as excinfo:
        resolve_catalog_search_strategy("does_not_exist", app_config)
    assert excinfo.value.name == "does_not_exist"


# ── config ───────────────────────────────────────────────────────────────────


def _config(base: dict[str, Any], **catalog: object) -> AppConfig:
    return AppConfig.model_validate({**base, "catalog": {**base.get("catalog", {}), **catalog}})


def test_config_defaults(sample_config_dict: dict[str, Any]) -> None:
    cfg = _config(sample_config_dict)
    assert cfg.catalog.search_strategy == TOKEN_STRATEGY_NAME
    assert cfg.catalog.search_deadline_ms > 0
    validate_catalog_search_config(cfg)


def test_config_naming_unknown_strategy_fails_validation(
    sample_config_dict: dict[str, Any],
) -> None:
    cfg = _config(sample_config_dict, search_strategy="does_not_exist")
    with pytest.raises(ConfigError, match="does_not_exist") as excinfo:
        validate_catalog_search_config(cfg)
    assert "token" in str(excinfo.value)


@pytest.mark.usefixtures("isolated_registry")
def test_config_naming_registered_strategy_validates(sample_config_dict: dict[str, Any]) -> None:
    register_catalog_search_strategy(_Fixed("semantic"))
    cfg = _config(sample_config_dict, search_strategy="semantic")
    validate_catalog_search_config(cfg)


@pytest.mark.usefixtures("isolated_registry")
def test_config_whose_strategy_cannot_be_built_fails_validation(
    sample_config_dict: dict[str, Any],
) -> None:
    class _Refuses:
        name = "refuses"

        @classmethod
        def from_config(cls, config: CatalogConfig, *, app_config: AppConfig) -> _Refuses:
            raise RuntimeError("missing endpoint")

        async def rank(self, request: CatalogSearchRequest) -> list[RankedEntry]:
            return []

    register_catalog_search_strategy(_Refuses)
    cfg = _config(sample_config_dict, search_strategy="refuses")
    with pytest.raises(ConfigError, match="could not be built"):
        validate_catalog_search_config(cfg)


@pytest.mark.parametrize("registry_db", [True, False])
def test_composition_root_fails_on_unknown_strategy(
    sample_config_dict: dict[str, Any], registry_db: bool
) -> None:
    """``build_default_container`` is the startup gate for every process that can serve searches."""
    ctx = MagicMock()
    ctx.config = _config(sample_config_dict, search_strategy="does_not_exist")
    ctx.config.apps = ["registry"]
    ctx.is_db_allowed.side_effect = lambda name: registry_db and name == "registry"
    if registry_db:
        with pytest.raises(ConfigError, match="does_not_exist"):
            build_default_container(ctx)
    else:
        build_default_container(ctx)


# ── cursor encoding ──────────────────────────────────────────────────────────


def _raw(cursor: str) -> str:
    return base64.b64decode(cursor).decode()


def test_token_and_browse_cursor_bytes_unchanged() -> None:
    assert _raw(encode_catalog_cursor("pay.com", 7)) == '{"id": "pay.com", "s": 7}'
    assert _raw(encode_catalog_cursor("pay.com")) == '{"id": "pay.com"}'


def test_strategy_cursor_records_st() -> None:
    raw = json.loads(_raw(encode_catalog_cursor("pay.com", 0.5, strategy="semantic")))
    assert raw == {"id": "pay.com", "s": 0.5, "st": "semantic"}
    parsed = parse_catalog_cursor(encode_catalog_cursor("pay.com", 0.5, strategy="semantic"))
    assert (parsed.api_id, parsed.score, parsed.strategy) == ("pay.com", 0.5, "semantic")


@pytest.mark.parametrize(
    "score", [7, 2**60 + 1, 0.1, 1 / 3, 0.30000000000000004, 1e-300, -2.5, 123456789.123456789]
)
def test_cursor_scores_round_trip_exactly(score: int | float) -> None:
    parsed = parse_catalog_cursor(encode_catalog_cursor("x", score))
    assert parsed.api_id == "x"
    assert parsed.score == score
    assert type(parsed.score) is type(score)


def _b64(payload: object) -> str:
    return base64.b64encode(json.dumps(payload).encode()).decode()


@pytest.mark.parametrize(
    "payload",
    [
        {"id": "x", "st": ""},
        {"id": "x", "st": 3},
        {"id": "x", "st": "Semantic"},
        {"id": "x", "st": "evil\nlog_line=forged"},
        {"id": "x", "st": "a" * 33},
        {"id": "x", "s": "NaN"},
        {"id": "x", "s": "Infinity"},
        {"id": "x", "s": "1.5"},
        {"id": "x", "s": True},
        {"id": "x", "s": [1]},
        {"id": 3},
        {"s": 1},
        ["x"],
    ],
)
def test_malformed_catalog_cursors_are_invalid(payload: object) -> None:
    with pytest.raises(InvalidCursorError):
        parse_catalog_cursor(_b64(payload))


def test_over_long_and_deeply_nested_cursors_are_invalid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(InvalidCursorError):
        parse_catalog_cursor("A" * (MAX_CATALOG_CURSOR_LENGTH + 1))
    # Nested deep enough to exhaust the JSON decoder's recursion: with the length
    # gate lifted it must still be a 400-class error, never a crash.
    nested = base64.b64encode(b"[" * 100_000 + b"]" * 100_000).decode()
    monkeypatch.setattr(pagination, "MAX_CATALOG_CURSOR_LENGTH", len(nested))
    with pytest.raises(InvalidCursorError):
        parse_catalog_cursor(nested)
