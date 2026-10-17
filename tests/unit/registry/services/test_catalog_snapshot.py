"""The in-memory catalog snapshot's collapse and its per-snapshot view cache."""

from __future__ import annotations

from types import SimpleNamespace

from jentic_one.registry.services.catalog.snapshot import (
    _COLLAPSE_CACHE_SIZE,
    CatalogSnapshot,
    build_snapshot,
)


def _row(source: str, external_id: str, catalog_api_id: str | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        source_slug=source,
        external_id=external_id,
        catalog_api_id=catalog_api_id or external_id,
        catalog_api_id_hint=catalog_api_id or external_id,
        title=None,
        version=None,
        tags=[],
        human_url=None,
        spec_ref={"url": f"https://{source}.example/{external_id}.json"},
        content_hint=None,
    )


def _snapshot() -> CatalogSnapshot:
    """``stripe.com`` from two sources, plus ``n`` single-source entries."""
    rows = [_row("public", "stripe.com"), _row("corp", "stripe.com")]
    rows += [_row("public", f"api{i}.example") for i in range(20)]
    return build_snapshot(("k", "k"), rows, sources={}, mappings=[])


_BOTH = frozenset({"public", "corp"})


def test_collapse_cache_is_capped() -> None:
    snapshot = _snapshot()
    for i in range(_COLLAPSE_CACHE_SIZE * 3):
        snapshot.collapse(frozenset({"public", f"s{i}"}), frozenset())
    assert snapshot.cached_views == _COLLAPSE_CACHE_SIZE == 8


def test_collapse_cache_keeps_recently_used_views() -> None:
    snapshot = _snapshot()
    hot = snapshot.collapse(_BOTH, frozenset())
    for i in range(_COLLAPSE_CACHE_SIZE * 2):
        snapshot.collapse(frozenset({f"s{i}"}), frozenset())
        assert snapshot.collapse(_BOTH, frozenset()) is hot


def test_a_new_registered_set_evicts_the_older_collapsed_views() -> None:
    snapshot = _snapshot()
    before = snapshot.collapse(_BOTH, frozenset())
    snapshot.collapse(frozenset({"public"}), frozenset())
    uncollapsed = snapshot.collapse(_BOTH, frozenset(), include_alternates=True)
    assert snapshot.cached_views == 3

    after = snapshot.collapse(_BOTH, frozenset({"public/stripe.com"}))
    assert after is not before
    # The include_alternates view does not depend on the registered set and stays.
    assert snapshot.cached_views == 2
    assert snapshot.collapse(_BOTH, frozenset(), include_alternates=True) is uncollapsed


def test_a_registered_set_change_outside_shared_ids_keeps_the_views() -> None:
    snapshot = _snapshot()
    view = snapshot.collapse(_BOTH, frozenset())
    assert snapshot.collapse(_BOTH, frozenset({"public/api3.example"})) is view


def test_include_alternates_view_names_no_winners() -> None:
    snapshot = _snapshot()
    view = snapshot.collapse(_BOTH, frozenset(), include_alternates=True)
    assert {"public/stripe.com", "corp/stripe.com"} <= view.ids
    assert view.winner_by_api_id == {}
    assert view.alternates == {}
    collapsed = snapshot.collapse(_BOTH, frozenset({"public/stripe.com"}))
    assert collapsed.winner_by_api_id["stripe.com"] == "public/stripe.com"
    assert collapsed.winner_of == {"corp/stripe.com": "public/stripe.com"}
