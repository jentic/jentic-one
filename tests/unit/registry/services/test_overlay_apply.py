"""Unit tests for the overlay applier (remove-then-set subset)."""

from __future__ import annotations

import pytest

from jentic_one.registry.services.overlay_apply import OverlayApplyError, apply_overlay

_BASE = {
    "openapi": "3.0.0",
    "info": {"title": "Widgets", "version": "1.0.0"},
    "servers": [{"url": "https://wrong.example.com"}],
    "paths": {"/x": {"get": {"summary": "old"}}},
}


def test_root_update_sets_keys_in_place() -> None:
    doc = {"actions": [{"target": "$", "update": {"servers": [{"url": "https://right.example"}]}}]}
    out = apply_overlay(_BASE, doc)
    assert out["servers"] == [{"url": "https://right.example"}]
    # Untouched keys survive.
    assert out["openapi"] == "3.0.0"


def test_remove_then_set_servers_pair() -> None:
    doc = {
        "actions": [
            {"target": "$.servers", "remove": True},
            {"target": "$", "update": {"servers": [{"url": "https://fixed.example"}]}},
        ]
    }
    out = apply_overlay(_BASE, doc)
    assert out["servers"] == [{"url": "https://fixed.example"}]


def test_bracket_key_segment_and_nested_update() -> None:
    doc = {"actions": [{"target": "$.paths['/x'].get", "update": {"summary": "new"}}]}
    out = apply_overlay(_BASE, doc)
    assert out["paths"]["/x"]["get"]["summary"] == "new"


def test_remove_nested_key() -> None:
    doc = {"actions": [{"target": "$.info", "remove": True}]}
    out = apply_overlay(_BASE, doc)
    assert "info" not in out


def test_base_is_not_mutated() -> None:
    doc = {"actions": [{"target": "$.servers", "remove": True}]}
    apply_overlay(_BASE, doc)
    assert _BASE["servers"] == [{"url": "https://wrong.example.com"}]


def test_unresolvable_target_raises() -> None:
    doc = {"actions": [{"target": "$.does.not.exist", "update": {"a": 1}}]}
    with pytest.raises(OverlayApplyError, match="does not resolve"):
        apply_overlay(_BASE, doc)


def test_unsupported_array_index_target_raises() -> None:
    doc = {"actions": [{"target": "$.servers[0]", "update": {"url": "x"}}]}
    with pytest.raises(OverlayApplyError, match="unsupported overlay target"):
        apply_overlay(_BASE, doc)


def test_unsupported_filter_target_raises() -> None:
    doc = {"actions": [{"target": "$.servers[?(@.url=='x')]", "remove": True}]}
    with pytest.raises(OverlayApplyError, match="unsupported overlay target"):
        apply_overlay(_BASE, doc)


def test_target_not_starting_with_dollar_raises() -> None:
    doc = {"actions": [{"target": "servers", "remove": True}]}
    with pytest.raises(OverlayApplyError, match="must start with"):
        apply_overlay(_BASE, doc)


def test_missing_actions_raises() -> None:
    with pytest.raises(OverlayApplyError, match="no 'actions' list"):
        apply_overlay(_BASE, {"overlay": "1.0.0"})


def test_action_without_target_raises() -> None:
    with pytest.raises(OverlayApplyError, match="missing 'target'"):
        apply_overlay(_BASE, {"actions": [{"update": {"a": 1}}]})


def test_action_neither_remove_nor_update_raises() -> None:
    with pytest.raises(OverlayApplyError, match="neither"):
        apply_overlay(_BASE, {"actions": [{"target": "$"}]})


def test_remove_root_raises() -> None:
    with pytest.raises(OverlayApplyError, match="cannot remove the document root"):
        apply_overlay(_BASE, {"actions": [{"target": "$", "remove": True}]})


def test_replace_scalar_leaf() -> None:
    doc = {"actions": [{"target": "$.openapi", "update": "3.1.0"}]}
    out = apply_overlay(_BASE, doc)
    assert out["openapi"] == "3.1.0"
