"""The compose() port — the Go plan-builder table tests, replayed in Python.

Mirrors ``cli/internal/cli/api/access_plan_test.go`` (provisioning chains,
auth validation, rules parsing, wire-shape omissions) plus the compose-level
duplicate/conflict validation and keyed-value resolution the Go CLI covers
through its command tests, and the ``rulesJSONValues`` normalization arms from
``mcp_access.go``. The composed dicts are additionally round-tripped through
the REST pydantic schemas — the validation-parity contract the handler relies
on (``AccessRequestFileRequest`` accepts every compose() output).
"""

from __future__ import annotations

from typing import Any

import pytest

from jentic_one.control.web.schemas.access_requests import AccessRequestItemRequest
from jentic_one.mcp.access_compose import (
    AccessRequestOptions,
    AccessTargetRequiredError,
    ComposeError,
    rules_json_values,
)

# ── the provisioning chain (Go: TestPlanBuildsFullProvisioningChain) ─────────


def test_provision_builds_the_full_four_item_chain() -> None:
    items = AccessRequestOptions(
        provisions=["posthog.com/posthog-api"],
        auths=["bearer"],
        rules_jsons=['[{"effect":"allow","methods":["GET"],"path":".*"}]'],
    ).compose()
    assert [(i["resource_type"], i["action"]) for i in items] == [
        ("toolkit", "create"),
        ("credential", "provision"),
        ("credential", "bind"),
        ("toolkit", "bind"),
    ]
    # The credential:bind item carries the proposed rules AND the API
    # reference: item order is not guaranteed server-side, so the reference
    # ties the bind to its chain in a composite request.
    assert items[2]["rules"] == [{"effect": "allow", "methods": ["GET"], "path": ".*"}]
    assert items[2]["resource_reference"] == {"vendor": "posthog.com", "name": "posthog-api"}
    # The provision item carries the detected auth type and the API reference.
    assert items[1]["resource_reference"]["security_scheme"] == "bearer"
    assert items[1]["resource_reference"]["vendor"] == "posthog.com"


def test_no_auth_provision_carries_the_no_auth_scheme() -> None:
    """A no-auth plan is the SAME four-item shape — a credential row is still
    required for the credential:bind effect to attach rules to; the wizard
    auto-creates a NO_AUTH credential (Go: TestPlanNoAuthProvisionsNoAuthCredential)."""
    items = AccessRequestOptions(provisions=["open-meteo.com/forecast"], auths=["none"]).compose()
    assert len(items) == 4
    assert items[1]["resource_type"] == "credential"
    assert items[1]["action"] == "provision"
    assert items[1]["resource_reference"]["security_scheme"] == "no_auth"


def test_invalid_auth_is_rejected() -> None:
    with pytest.raises(ComposeError, match="bearer, api_key, basic, oauth2, none"):
        AccessRequestOptions(provisions=["x.com/api"], auths=["kerberos"]).compose()


def test_malformed_rules_json_is_rejected() -> None:
    with pytest.raises(ComposeError, match="JSON array of rules"):
        AccessRequestOptions(provisions=["x.com/api"], rules_jsons=["not json"]).compose()


def test_rules_json_must_be_an_array_of_objects() -> None:
    with pytest.raises(ComposeError, match="JSON array of rules"):
        AccessRequestOptions(provisions=["x.com/api"], rules_jsons=['["allow"]']).compose()


def test_empty_rules_leave_the_rules_key_off_the_wire() -> None:
    """Absent rules stay OMITTED (the server substitutes a read-only default
    on the credential:bind item) — never an empty list (Go:
    TestParseProposedRulesEmpty / TestPlanItemsSerializeWithoutEmptyIDs)."""
    items = AccessRequestOptions(provisions=["x.com/api"]).compose()
    assert "rules" not in items[2]
    assert "resource_id" not in items[2]
    assert "to_id" not in items[2]


def test_provision_defaults_to_bearer_auth() -> None:
    items = AccessRequestOptions(provisions=["x.com/api"]).compose()
    assert items[1]["resource_reference"]["security_scheme"] == "bearer"


# ── fulfilment order & the flat targets ──────────────────────────────────────


def test_composite_composes_in_fulfilment_order() -> None:
    """Provision chains first (argument order), then toolkit binds by
    reference, by id, then scope grants — the wire order the Go MCP test pins
    (TestMCPRequestAccess_FilesComposedPlanPendingWithApproveURL)."""
    items = AccessRequestOptions(
        provisions=["stripe.com/api"],
        toolkits=["github.com/api"],
        toolkit_ids=["tk_1"],
        scopes=["catalog:import"],
    ).compose()
    assert [(i["resource_type"], i["action"]) for i in items] == [
        ("toolkit", "create"),
        ("credential", "provision"),
        ("credential", "bind"),
        ("toolkit", "bind"),
        ("toolkit", "bind"),
        ("toolkit", "bind"),
        ("scope", "grant"),
    ]
    assert items[4]["resource_reference"] == {"vendor": "github.com", "name": "api"}
    assert items[5]["resource_id"] == "tk_1"
    assert items[6]["resource_id"] == "catalog:import"


def test_version_rides_the_reference() -> None:
    items = AccessRequestOptions(toolkits=["acme/pets/v2"]).compose()
    assert items[0]["resource_reference"] == {"vendor": "acme", "name": "pets", "version": "v2"}


def test_whitespace_only_values_are_dropped_before_counting() -> None:
    with pytest.raises(AccessTargetRequiredError):
        AccessRequestOptions(toolkits=["  ", ""], scopes=[" "]).compose()


def test_no_target_raises_target_required() -> None:
    with pytest.raises(AccessTargetRequiredError):
        AccessRequestOptions().compose()


def test_bad_reference_shape_is_rejected() -> None:
    with pytest.raises(ComposeError, match="vendor/name"):
        AccessRequestOptions(toolkits=["just-a-vendor"]).compose()


# ── duplicate / conflict validation ──────────────────────────────────────────


def test_auth_without_provision_is_rejected() -> None:
    with pytest.raises(ComposeError, match='only apply with "provision"'):
        AccessRequestOptions(toolkits=["acme/pets"], auths=["bearer"]).compose()


def test_rules_json_without_provision_is_rejected() -> None:
    with pytest.raises(ComposeError, match='only apply with "provision"'):
        AccessRequestOptions(scopes=["catalog:import"], rules_jsons=["[]"]).compose()


def test_duplicate_provision_is_rejected_across_slug_spellings() -> None:
    """Vendor/name are slugified exactly like the server, so raw-domain and
    slug spellings of the same API collide here instead of filing as two
    chains the server would then merge."""
    with pytest.raises(ComposeError, match="given more than once"):
        AccessRequestOptions(provisions=["httpbin.org/http-bin", "httpbin-org/http-bin"]).compose()


def test_toolkit_and_provision_naming_the_same_api_conflict() -> None:
    with pytest.raises(ComposeError, match="provisioning plan already ends"):
        AccessRequestOptions(provisions=["acme/pets"], toolkits=["acme/pets"]).compose()


@pytest.mark.parametrize(
    ("kwargs", "dup"),
    [
        ({"toolkit_ids": ["tk_1", "tk_1"]}, "tk_1"),
        ({"scopes": ["apis:read", "apis:read"]}, "apis:read"),
        ({"toolkits": ["acme/pets", "acme/pets"]}, "acme/pets"),
    ],
)
def test_duplicate_flat_targets_are_rejected(kwargs: dict[str, Any], dup: str) -> None:
    with pytest.raises(ComposeError, match="given more than once"):
        AccessRequestOptions(**kwargs).compose()


# ── keyed auth / rules_json resolution ───────────────────────────────────────


def test_keyed_values_route_to_their_chains() -> None:
    items = AccessRequestOptions(
        provisions=["a.com/api", "b.com/api"],
        auths=["a.com/api=api_key", "b.com/api=none"],
        rules_jsons=['a.com/api=[{"effect":"allow","path":".*"}]'],
    ).compose()
    assert items[1]["resource_reference"]["security_scheme"] == "api_key"
    assert items[5]["resource_reference"]["security_scheme"] == "no_auth"
    assert items[2]["rules"] == [{"effect": "allow", "path": ".*"}]
    assert "rules" not in items[6], "the unkeyed chain keeps the server default"


def test_bare_value_with_repeated_provisions_must_be_keyed() -> None:
    with pytest.raises(ComposeError, match="must be keyed by API"):
        AccessRequestOptions(provisions=["a.com/api", "b.com/api"], auths=["bearer"]).compose()


def test_key_naming_an_unrequested_chain_is_rejected() -> None:
    with pytest.raises(ComposeError, match='not among the "provision" targets'):
        AccessRequestOptions(provisions=["a.com/api"], auths=["b.com/api=bearer"]).compose()


def test_keyed_value_repeated_for_one_chain_is_rejected() -> None:
    with pytest.raises(ComposeError, match="given more than once for"):
        AccessRequestOptions(
            provisions=["a.com/api"], auths=["a.com/api=bearer", "a.com/api=basic"]
        ).compose()


def test_json_payload_containing_equals_is_never_probed_for_a_key() -> None:
    """A rules array can legitimately contain '=' (e.g. inside a path regex);
    a value starting like a JSON document is always a bare payload."""
    items = AccessRequestOptions(
        provisions=["a.com/api"],
        rules_jsons=['[{"effect":"allow","path":"^/v1/things\\\\?q=.*"}]'],
    ).compose()
    assert items[2]["rules"] == [{"effect": "allow", "path": "^/v1/things\\?q=.*"}]


def test_unparsable_key_prefix_reads_as_a_bare_value() -> None:
    """ "bearer" has no '/', so "auth=bearer"-shaped confusion falls back to
    bare-value semantics (here: a valid bare auth for the single chain)."""
    items = AccessRequestOptions(provisions=["a.com/api"], auths=["api_key"]).compose()
    assert items[1]["resource_reference"]["security_scheme"] == "api_key"


# ── rules_json_values normalization (Go: rulesJSONValues) ────────────────────


def test_rules_json_array_of_objects_stays_one_value() -> None:
    """The natural JSON array of rule objects is kept whole as ONE stringified
    value — commas inside rules must never split it."""
    values = rules_json_values([{"effect": "allow", "methods": ["GET", "POST"], "path": ".*"}])
    assert len(values) == 1
    items = AccessRequestOptions(provisions=["a.com/api"], rules_jsons=values).compose()
    assert items[2]["rules"] == [{"effect": "allow", "methods": ["GET", "POST"], "path": ".*"}]


def test_rules_json_single_string_passes_through() -> None:
    assert rules_json_values('[{"effect":"deny"}]') == ['[{"effect":"deny"}]']


def test_rules_json_list_of_keyed_strings_passes_through() -> None:
    keyed = ['a.com/api=[{"effect":"allow"}]', 'b.com/api=[{"effect":"deny"}]']
    assert rules_json_values(keyed) == keyed


def test_rules_json_single_object_wraps_into_an_array() -> None:
    values = rules_json_values({"effect": "allow", "path": ".*"})
    assert len(values) == 1
    items = AccessRequestOptions(provisions=["a.com/api"], rules_jsons=values).compose()
    assert items[2]["rules"] == [{"effect": "allow", "path": ".*"}]


def test_rules_json_absent_and_empty_yield_no_values() -> None:
    assert rules_json_values(None) == []
    assert rules_json_values("") == []


@pytest.mark.parametrize("bad", [42, 3.5, True])
def test_rules_json_scalar_is_rejected(bad: Any) -> None:
    with pytest.raises(ComposeError, match='parameter "rules_json"'):
        rules_json_values(bad)


# ── the target count & filing-params probe (arm exclusivity inputs) ──────────


def test_target_count_counts_each_provision_as_one() -> None:
    opts = AccessRequestOptions(
        provisions=["a.com/api"], toolkits=["b.com/api"], toolkit_ids=["tk_1"], scopes=["s"]
    )
    assert opts.target_count() == 4


def test_has_filing_params_sees_strays_without_targets() -> None:
    assert AccessRequestOptions(reason="please").has_filing_params()
    assert AccessRequestOptions(auths=["bearer"]).has_filing_params()
    assert AccessRequestOptions(rules_jsons=["[]"]).has_filing_params()
    assert not AccessRequestOptions().has_filing_params()


# ── validation parity: compose() output round-trips the REST schemas ─────────


def test_every_composed_item_passes_the_rest_pydantic_schema() -> None:
    """The handler files ``[i.model_dump(exclude_none=True) …]`` after
    validating through the REST schemas — compose() output must always pass
    them (the valid (resource_type, action) pairs, exactly-one-of
    resource_id/resource_reference, rule shape)."""
    items = AccessRequestOptions(
        provisions=["stripe.com/api"],
        toolkits=["github.com/api"],
        toolkit_ids=["tk_1"],
        scopes=["catalog:import"],
        auths=["bearer"],
        rules_jsons=['[{"effect":"allow","methods":["GET"],"path":".*"}]'],
    ).compose()
    for item in items:
        validated = AccessRequestItemRequest.model_validate(item)
        dumped = validated.model_dump(exclude_none=True)
        # The schema may ENRICH (rule defaults like match_mode) but must
        # never drop or rewrite what compose() filed.
        for key, value in item.items():
            if key == "rules":
                for filed, round_tripped in zip(value, dumped["rules"], strict=True):
                    assert filed == {k: round_tripped[k] for k in filed}
            else:
                assert dumped[key] == value
