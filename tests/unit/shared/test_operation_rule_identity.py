"""Unit tests for canonical permission-rule operation identity (#1081)."""

from __future__ import annotations

from jentic_one.shared.models.api_identity import CredentialScope
from jentic_one.shared.permissions.evaluation import (
    PermissionRule,
    evaluate_rules,
    first_matching_rule,
    is_registry_operation_id,
    normalize_methods,
    rule_verdict,
)
from jentic_one.shared.permissions.matching import compile_matcher
from jentic_one.shared.permissions.operation_resolution import (
    LiveOperationRow,
    match_spec_path,
    resolve_dry_run_operation,
    resolve_live_operation,
)

_REG_PK = "op_" + "a1b2c3d4" * 5
_OTHER_PK = "op_" + "f0e1d2c3" * 5


def test_is_registry_operation_id_distinguishes_pk_from_spec_ids() -> None:
    assert is_registry_operation_id(_REG_PK) is True
    assert is_registry_operation_id("listCharges") is False
    assert is_registry_operation_id("op_list") is False
    assert is_registry_operation_id("op_" + "A1B2C3D4" * 5) is False
    assert is_registry_operation_id("op_" + "a" * 39) is False


def test_namespace_isolation_prevents_spec_operation_id_spoofing_registry_pk() -> None:
    """A spec declaring ``operationId`` shaped like ``op_<40-hex>`` never matches a PK rule."""
    rules = [
        PermissionRule(
            effect="allow",
            methods=None,
            path=None,
            operations=(_REG_PK,),
        )
    ]
    # Matching registry_operation_id allows.
    assert (
        evaluate_rules(
            rules,
            method="GET",
            path="/items",
            operation_id="get_items",
            registry_operation_id=_REG_PK,
        )
        is True
    )
    # Different registry_operation_id with crafted spec operation_id == _REG_PK denies.
    assert (
        evaluate_rules(
            rules,
            method="GET",
            path="/evil",
            operation_id=_REG_PK,
            registry_operation_id=_OTHER_PK,
        )
        is False
    )


def test_spec_operation_id_starting_with_op_prefix_still_matches_as_spec_id() -> None:
    """A legitimate spec ``operationId`` like ``op_create`` is not mistaken for a registry PK."""
    rules = [
        PermissionRule(
            effect="allow",
            methods=None,
            path=None,
            operations=("op_create",),
        )
    ]
    assert (
        evaluate_rules(
            rules,
            method="POST",
            path="/ops",
            operation_id="op_create",
            registry_operation_id=_REG_PK,
        )
        is True
    )
    assert (
        evaluate_rules(
            rules,
            method="POST",
            path="/ops",
            operation_id="op_create_v2",
            registry_operation_id=_REG_PK,
        )
        is False
    )


def test_ambiguous_operation_ids_fail_closed_across_allow_deny_and_require_approval() -> None:
    ambiguous = frozenset({"list_records"})

    # 1. Ambiguous allow fails closed.
    allow_only = [
        PermissionRule(effect="allow", methods=None, path=None, operations=("list_records",))
    ]
    idx = first_matching_rule(
        allow_only,
        method="GET",
        path="/records",
        operation_id=None,
        registry_operation_id=_REG_PK,
        ambiguous_operation_ids=ambiguous,
    )
    assert idx is None
    assert rule_verdict(allow_only, idx) == "deny"

    # 2. Ambiguous deny still matches and denies ahead of a broader allow.
    deny_then_allow = [
        PermissionRule(effect="deny", methods=None, path=None, operations=("list_records",)),
        PermissionRule(
            effect="allow",
            methods=normalize_methods(["GET"]),
            path=compile_matcher("/.*", "regex"),
            operations=None,
        ),
    ]
    idx_deny = first_matching_rule(
        deny_then_allow,
        method="GET",
        path="/records",
        operation_id=None,
        registry_operation_id=_REG_PK,
        ambiguous_operation_ids=ambiguous,
    )
    assert idx_deny == 0
    assert rule_verdict(deny_then_allow, idx_deny) == "deny"

    # 3. Ambiguous require-approval halts default-deny instead of falling through to allow.
    hold_then_allow = [
        PermissionRule(
            effect="require-approval", methods=None, path=None, operations=("list_records",)
        ),
        PermissionRule(
            effect="allow",
            methods=normalize_methods(["GET"]),
            path=compile_matcher("/.*", "regex"),
            operations=None,
        ),
    ]
    idx_hold = first_matching_rule(
        hold_then_allow,
        method="GET",
        path="/records",
        operation_id=None,
        registry_operation_id=_REG_PK,
        ambiguous_operation_ids=ambiguous,
    )
    assert idx_hold is None
    assert rule_verdict(hold_then_allow, idx_hold) == "deny"


def test_match_spec_path_specificity_and_rfc6570_catch_all() -> None:
    matched_lit, key_lit = match_spec_path("/items/special", "/items/special")
    matched_param, key_param = match_spec_path("/items/{id}", "/items/special")
    assert matched_lit is True
    assert matched_param is True
    assert key_lit < key_param

    matched_catch, key_catch = match_spec_path("/files/{+filepath}", "/files/a/b/c.txt")
    assert matched_catch is True
    matched_trailing, key_trailing = match_spec_path("/files/{filepath}", "/files/a/b/c.txt")
    assert matched_trailing is True
    assert key_catch < key_trailing
    assert match_spec_path("/files/{filepath}", "/files/a")[0] is True
    assert match_spec_path("/files/id-{filepath}", "/files/id-a/b")[0] is False

    # Repeated parameter name enforces equality via backreference.
    assert match_spec_path("/orgs/{id}/mirrors/{id}", "/orgs/acme/mirrors/acme")[0] is True
    assert match_spec_path("/orgs/{id}/mirrors/{id}", "/orgs/acme/mirrors/other")[0] is False


def test_resolve_dry_run_operation_prefers_literal_and_handles_head_fallback() -> None:
    rows = [
        LiveOperationRow("acme", "shop", "1.0", _REG_PK, "get_item", "GET", "/items/{id}"),
        LiveOperationRow("acme", "shop", "1.0", _OTHER_PK, "get_special", "GET", "/items/special"),
    ]
    scope = CredentialScope(vendor="acme", name="shop", version="1.0")
    resolved = resolve_dry_run_operation(
        rows,
        scope=scope,
        method="HEAD",
        normalized_path="/items/special",
        requested_operation_id=None,
    )
    assert resolved.operation_id == "get_special"
    assert resolved.registry_operation_id == _OTHER_PK
    assert resolved.ambiguous_operation_ids == frozenset()


def test_resolve_dry_run_operation_uses_live_nested_path_precedence() -> None:
    trailing = LiveOperationRow("acme", "files", "1.0", _REG_PK, "get_file", "GET", "/files/{path}")
    catch_all = LiveOperationRow(
        "acme", "files", "1.0", _OTHER_PK, "get_catch_all", "GET", "/files/{+path}"
    )
    scope = CredentialScope(vendor="acme", name="files", version="1.0")

    resolved = resolve_dry_run_operation(
        [trailing, catch_all],
        scope=scope,
        method="GET",
        normalized_path="/files/a/b.txt",
        requested_operation_id=None,
    )
    assert resolved.operation_id == "get_catch_all"
    assert resolved.registry_operation_id == _OTHER_PK

    exact = LiveOperationRow(
        "acme", "files", "1.0", "op_" + "1234abcd" * 5, "get_exact", "GET", "/files/a/b.txt"
    )
    resolved = resolve_dry_run_operation(
        [trailing, catch_all, exact],
        scope=scope,
        method="GET",
        normalized_path="/files/a/b.txt",
        requested_operation_id=None,
    )
    assert resolved.operation_id == "get_exact"
    assert resolved.ambiguous_operation_ids == frozenset()


def test_resolve_dry_run_operation_rejects_incomparable_catch_all_matches() -> None:
    rows = [
        LiveOperationRow("acme", "files", "1.0", _REG_PK, "prefix", "GET", "/{+prefix}/b"),
        LiveOperationRow("acme", "files", "1.0", _OTHER_PK, "suffix", "GET", "/a/{+suffix}"),
    ]
    resolved = resolve_dry_run_operation(
        rows,
        scope=CredentialScope(vendor="acme", name="files", version="1.0"),
        method="GET",
        normalized_path="/a/x/b",
        requested_operation_id=None,
    )
    assert resolved.operation_id is None
    assert resolved.registry_operation_id is None
    assert resolved.ambiguous_operation_ids == frozenset({"prefix", "suffix"})


def test_explicit_catch_all_does_not_widen_an_ordinary_trailing_parameter() -> None:
    """Only implicit trailing parameters, never explicit catch-all routes, widen."""
    assert match_spec_path("/{+prefix}/b/{tail}", "/a/b/c/d")[0] is False
    rows = [
        LiveOperationRow(
            "acme", "files", "1.0", _REG_PK, "prefix_tail", "GET", "/{+prefix}/b/{tail}"
        ),
        LiveOperationRow("acme", "files", "1.0", _OTHER_PK, "rest", "GET", "/{+rest}"),
    ]
    resolved = resolve_dry_run_operation(
        rows,
        scope=CredentialScope(vendor="acme", name="files", version="1.0"),
        method="GET",
        normalized_path="/a/b/c/d",
        requested_operation_id=None,
    )
    assert resolved.operation_id == "rest"
    assert resolved.registry_operation_id == _OTHER_PK


def test_catch_all_syntax_in_request_uses_live_candidate_gate() -> None:
    """Literal catch-all markers restrict live and dry-run lookup to catch-all rows."""
    rows = [
        LiveOperationRow("acme", "files", "1.0", _REG_PK, "ordinary", "GET", "/files/{name}"),
        LiveOperationRow("acme", "files", "1.0", _OTHER_PK, "rest", "GET", "/files/{+path}"),
    ]
    resolved = resolve_dry_run_operation(
        rows,
        scope=CredentialScope(vendor="acme", name="files", version="1.0"),
        method="GET",
        normalized_path="/files/a**",
        requested_operation_id=None,
    )
    assert resolved.operation_id == "rest"
    assert resolved.registry_operation_id == _OTHER_PK


def test_resolve_live_operation_handles_synthetic_non_pk_operation_id() -> None:
    resolved = resolve_live_operation(spec_operation_id=None, registry_operation_id="getPet")
    assert resolved.operation_id == "getPet"
    assert resolved.registry_operation_id is None
