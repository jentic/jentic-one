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
from collections.abc import Iterator, Mapping, Sequence
from typing import Any
from unittest.mock import MagicMock

import pytest

from jentic_one.registry.services.catalog import manifest_builder as mb
from jentic_one.registry.services.catalog import search
from jentic_one.registry.services.catalog.search import (
    TOKEN_STRATEGY_NAME,
    CatalogMatch,
    CatalogSearchStrategy,
    RankedEntry,
    TokenSearchStrategy,
    UnknownCatalogSearchStrategyError,
    normalize_ranking,
    register_catalog_search_strategy,
    registered_catalog_search_strategies,
    resolve_catalog_search_strategy,
    validate_catalog_search_config,
)
from jentic_one.registry.web.app import create_app, install_on_app
from jentic_one.shared.config import AppConfig, ConfigError
from jentic_one.shared.pagination import (
    InvalidCursorError,
    decode_catalog_cursor,
    encode_catalog_cursor,
    parse_catalog_cursor,
)


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

    async def rank(
        self,
        q: str,
        entries: Sequence[mb.ManifestEntry],
        *,
        deadline_ms: int | None = None,
        context: Mapping[str, object] | None = None,
    ) -> list[RankedEntry]:
        return list(self._rows)


@pytest.fixture()
def isolated_registry(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Run against a copy of the process-global registry so tests don't leak strategies."""
    monkeypatch.setattr(search, "_STRATEGIES", dict(search._STRATEGIES))
    yield


# ── protocol + default strategy ──────────────────────────────────────────────


def test_token_strategy_is_registered_by_default() -> None:
    strategy = resolve_catalog_search_strategy(TOKEN_STRATEGY_NAME)
    assert isinstance(strategy, TokenSearchStrategy)
    assert isinstance(strategy, CatalogSearchStrategy)
    assert TOKEN_STRATEGY_NAME in registered_catalog_search_strategies()


async def test_token_strategy_matches_score_entries_with_int_scores() -> None:
    entries = [_entry(a) for a in ["pay.com", "weather.gov", "pay-api.com", "apiary.io"]]
    expected = [(e, s) for e, s in mb.score_entries(entries, "pay api") if s is not None]

    ranked = await TokenSearchStrategy().rank("pay api", entries)

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


@pytest.mark.parametrize("bad", [math.nan, math.inf, -math.inf, True, "3", None])
def test_normalize_drops_non_finite_or_non_numeric_scores(bad: object) -> None:
    a, b = _entry("a"), _entry("b")
    rows = [RankedEntry(a, bad), RankedEntry(b, 1)]  # type: ignore[arg-type]

    result = normalize_ranking(rows, [a, b])

    assert [r.entry.api_id for r in result.rows] == ["b"]
    assert result.dropped_invalid == 1


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


def test_normalize_bounds_match_evidence() -> None:
    a, b, c = _entry("a"), _entry("b"), _entry("c")
    many = tuple(f"ch{i}" for i in range(20))
    rows = [
        RankedEntry(a, 3, CatalogMatch(("ok", "", "x" * 33, 7, *many))),  # type: ignore[arg-type]
        RankedEntry(b, 2, CatalogMatch(("",))),
        RankedEntry(c, 1, {"channels": ["token"]}),  # type: ignore[arg-type]
    ]

    rows_by_id = {r.entry.api_id: r for r in normalize_ranking(rows, [a, b, c]).rows}

    a_match = rows_by_id["a"].match
    assert a_match is not None
    assert a_match.channels[0] == "ok"
    assert len(a_match.channels) == 8
    assert rows_by_id["b"].match is None
    assert rows_by_id["c"].match is None


# ── registration ─────────────────────────────────────────────────────────────


@pytest.mark.usefixtures("isolated_registry")
def test_register_and_resolve() -> None:
    strategy = _Fixed("semantic")
    register_catalog_search_strategy(strategy)
    assert resolve_catalog_search_strategy("semantic") is strategy


@pytest.mark.usefixtures("isolated_registry")
def test_register_is_idempotent_for_same_strategy() -> None:
    strategy = _Fixed("semantic")
    register_catalog_search_strategy(strategy)
    register_catalog_search_strategy(strategy)
    assert resolve_catalog_search_strategy("semantic") is strategy


@pytest.mark.usefixtures("isolated_registry")
def test_register_refuses_to_shadow_existing_name() -> None:
    first = _Fixed("semantic")
    register_catalog_search_strategy(first)
    with pytest.raises(ValueError, match="already registered"):
        register_catalog_search_strategy(_Fixed("semantic"))
    assert resolve_catalog_search_strategy("semantic") is first


@pytest.mark.usefixtures("isolated_registry")
def test_register_refuses_to_shadow_token() -> None:
    with pytest.raises(ValueError, match="already registered"):
        register_catalog_search_strategy(_Fixed(TOKEN_STRATEGY_NAME))
    assert isinstance(resolve_catalog_search_strategy(TOKEN_STRATEGY_NAME), TokenSearchStrategy)


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


def test_resolve_unknown_raises() -> None:
    with pytest.raises(UnknownCatalogSearchStrategyError) as excinfo:
        resolve_catalog_search_strategy("does_not_exist")
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


def test_registry_create_app_fails_on_unknown_strategy(
    sample_config_dict: dict[str, Any],
) -> None:
    """App assembly is the startup gate: an unknown name is a boot error, not a fallback."""
    ctx = MagicMock()
    ctx.config = _config(sample_config_dict, search_strategy="does_not_exist")
    with pytest.raises(ConfigError, match="does_not_exist"):
        create_app(ctx)
    with pytest.raises(ConfigError, match="does_not_exist"):
        install_on_app(MagicMock(), ctx)


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
    api_id, decoded = decode_catalog_cursor(encode_catalog_cursor("x", score))
    assert api_id == "x"
    assert decoded == score
    assert type(decoded) is type(score)


@pytest.mark.parametrize(
    "payload",
    [
        {"id": "x", "st": ""},
        {"id": "x", "st": 3},
        {"id": "x", "s": "NaN"},
        {"id": "x", "s": "Infinity"},
        {"id": 3},
        {"s": 1},
        ["x"],
    ],
)
def test_malformed_catalog_cursors_are_invalid(payload: object) -> None:
    cursor = base64.b64encode(json.dumps(payload).encode()).decode()
    with pytest.raises(InvalidCursorError):
        parse_catalog_cursor(cursor)
