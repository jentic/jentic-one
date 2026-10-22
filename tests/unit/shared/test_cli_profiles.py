"""CLI profiles: every shipped manifest validates, references real code and expands.

The shipped manifests are the source of the facade tables, the agent wiring
and the connect presets, so a manifest that loads but points at a missing
rule set, hard-deny list, spec operation or vendor entry must fail here.
"""

from __future__ import annotations

import importlib
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

import pytest
import yaml
from tools.cli_profile_schema_export import (
    CLI_PROFILE_SCHEMA_PATH,
    build_cli_profile_schema,
    dump_schema_json,
)

from jentic_one.broker.core.facades import (
    CLOUDFLARE_FACADE,
    GITHUB_GRAPHQL_PATH,
    GITHUB_GRAPHQL_UPSTREAM,
    GITHUB_PREFIX_FACADES,
    PROXY_API_VENDORS,
    PROXY_ERROR_SHAPE_DOMAINS,
)
from jentic_one.shared.cli_facades import FACADE_PROTOCOLS, SERVED_FACADE_CLIS
from jentic_one.shared.cli_profiles import (
    ApiRole,
    CliProfileError,
    PresetError,
    SourceKind,
    assign_rule_roles,
    declaring_profile,
    expand_preset,
    expand_preset_for_profile,
    load_profiles,
    parse_profile,
    profile_api_for_catalog_api_id,
    profile_api_for_connect,
    profile_api_for_identity,
    profile_api_role,
    profile_for_connect,
    shipped_spec_text,
)
from jentic_one.shared.cli_profiles.rule_sets import RULE_SETS
from jentic_one.shared.permissions.github_graphql import (
    GITHUB_GRAPHQL_PRESETS,
    PR_WRITE_MUTATIONS,
)

_REPO_ROOT = Path(__file__).resolve().parents[3]
_PROFILE_DIR = _REPO_ROOT / "src" / "jentic_one" / "shared" / "cli_profiles"


def _local_vendor_entries() -> Mapping[str, Any]:
    config = yaml.safe_load((_REPO_ROOT / "config" / "local.yaml").read_text())
    entries: Mapping[str, Any] = config["vendors"]["entries"]
    return entries


def _resolve(ref: str) -> object:
    module, _, attribute = ref.partition(":")
    return getattr(importlib.import_module(module), attribute)


# ── shipped manifests ────────────────────────────────────────────────────────


def test_every_shipped_profile_loads() -> None:
    profiles = load_profiles()
    assert set(profiles) == {"github", "cloudflare"}
    files = sorted(p.stem for p in _PROFILE_DIR.glob("*.yaml"))
    assert files == sorted(profiles)


def test_schema_file_matches_the_model() -> None:
    # Regenerate with `make cli-profile-schema`.
    assert CLI_PROFILE_SCHEMA_PATH.read_text() == dump_schema_json(build_cli_profile_schema())


@pytest.mark.parametrize("name", sorted(load_profiles()))
def test_hard_deny_sources_name_real_lists(name: str) -> None:
    for source in load_profiles()[name].hard_deny:
        target = _resolve(source.source)
        if source.kind == "git_ref":
            assert isinstance(target, Callable)  # type: ignore[arg-type]
        else:
            assert isinstance(target, Mapping | frozenset) and len(target) > 0


@pytest.mark.parametrize("name", sorted(load_profiles()))
def test_connect_entries_exist_and_target_their_api(name: str) -> None:
    entries = _local_vendor_entries()
    profile = load_profiles()[name]
    for api in profile.apis:
        if api.connect is None:
            continue
        assert api.connect in entries, f"config/local.yaml has no vendor entry {api.connect!r}"
        assert entries[api.connect]["vendor"] == api.catalog_api_id
    if profile.connect is not None:
        assert profile.connect.vendor_entry in entries


@pytest.mark.parametrize("name", sorted(load_profiles()))
def test_presets_expand_for_every_role_they_cover(name: str) -> None:
    profile = load_profiles()[name]
    for preset_name, preset in profile.presets.items():
        params = {
            key: {"repo_list": "o/r", "ref_glob": "agent/*", "flag": "true"}[spec.kind]
            for key, spec in preset.params.items()
        }
        for role in {rule.api for rule in preset.rules}:
            rules = expand_preset(profile, preset_name, params, role=role)
            assert rules, (preset_name, role)


def test_every_include_names_a_rule_set() -> None:
    includes = {
        rule.include
        for profile in load_profiles().values()
        for preset in profile.presets.values()
        for rule in preset.rules
        if rule.include is not None
    }
    assert includes <= set(RULE_SETS)


def test_github_graphql_spec_is_one_bearer_post() -> None:
    profile = load_profiles()["github"]
    api = profile.api(ApiRole.GRAPHQL)
    assert api is not None and api.source_kind == SourceKind.SHIPPED
    spec = yaml.safe_load(shipped_spec_text(api))
    assert spec["info"]["x-vendor"] == "github.com"
    assert list(spec["paths"]) == ["/graphql"]
    assert list(spec["paths"]["/graphql"]) == ["post"]
    assert spec["components"]["securitySchemes"]["bearer"] == {
        "type": "http",
        "scheme": "bearer",
        "description": "A GitHub OAuth or personal access token.",
    }
    assert spec["servers"] == [{"url": "https://api.github.com"}]


def test_lookups_find_each_github_api() -> None:
    found = profile_api_for_catalog_api_id("github.com/github-graphql")
    assert found is not None and found[1].role == ApiRole.GRAPHQL
    found = profile_api_for_connect("github-git")
    assert found is not None and found[1].role == ApiRole.GIT
    found = profile_api_for_identity("github.com", "api.github.com")
    assert found is not None and found[1].role == ApiRole.REST
    assert profile_api_for_catalog_api_id("stripe.com") is None


# ── tables derived from the manifests ────────────────────────────────────────


def test_facade_tables_come_from_the_manifests() -> None:
    assert dict(SERVED_FACADE_CLIS) == {"github": "gh", "cloudflare": "cf"}
    assert FACADE_PROTOCOLS["github"] == ("rest", "graphql", "git")
    assert FACADE_PROTOCOLS["cloudflare"] == ("rest",)
    assert CLOUDFLARE_FACADE.prefix == "/_facade/cloudflare"
    assert CLOUDFLARE_FACADE.upstream_base == "https://api.cloudflare.com/client/v4"
    assert [(f.prefix, f.upstream_base) for f in GITHUB_PREFIX_FACADES] == [
        ("/api/v3", "https://api.github.com"),
        ("/api/uploads", "https://uploads.github.com"),
    ]
    assert (GITHUB_GRAPHQL_PATH, GITHUB_GRAPHQL_UPSTREAM) == (
        "/api/graphql",
        "https://api.github.com/graphql",
    )
    assert PROXY_API_VENDORS["github"] == frozenset({"github.com"})
    assert PROXY_API_VENDORS["aws"] == frozenset({"amazonaws.com"})
    assert dict(PROXY_ERROR_SHAPE_DOMAINS) == {
        "github.com": "github",
        "cloudflare.com": "cloudflare",
    }


# ── preset expansion ─────────────────────────────────────────────────────────


def _github() -> Any:
    return load_profiles()["github"]


def test_read_only_expands_per_role() -> None:
    profile = _github()
    assert expand_preset(profile, "read_only", role=ApiRole.REST) == [
        {"effect": "allow", "match_mode": "prefix", "methods": ["GET"], "path": "/"}
    ]
    assert expand_preset(profile, "read_only", role=ApiRole.GRAPHQL) == [
        {"effect": "allow", "path": "query", "match_mode": "graphql"},
        {"effect": "deny", "path": "mutation", "match_mode": "graphql"},
    ]
    assert expand_preset(profile, "read_only", {"repo": "o/a, o/b*"}, role=ApiRole.GIT) == [
        {"effect": "allow", "match_mode": "git", "path": "fetch:o/a"},
        {"effect": "allow", "match_mode": "git", "path": "fetch:o/b*"},
    ]


def test_read_and_prs_reuses_the_graphql_pr_preset() -> None:
    rules = expand_preset(_github(), "read_and_prs", role=ApiRole.GRAPHQL)
    expected = [
        {k: v for k, v in rule.items() if k != "_comment"}
        for rule in GITHUB_GRAPHQL_PRESETS["pr_write"]
    ]
    assert rules == expected
    assert any(r["path"] == f"mutation:{','.join(PR_WRITE_MUTATIONS)}" for r in rules)


@pytest.mark.parametrize(
    ("force", "expected"),
    [
        ("false", ["push:o/r:refs/heads/agent/*"]),
        ("true", ["push:o/r:refs/heads/agent/*", "force:o/r:refs/heads/agent/*"]),
        (None, ["push:o/r:refs/heads/agent/*"]),
    ],
)
def test_push_branches_with_optional_force(force: str | None, expected: list[str]) -> None:
    params = {"repo": "o/r", "branches": "agent/*"}
    if force is not None:
        params["force"] = force
    rules = expand_preset(_github(), "push_branches", params, role=ApiRole.GIT)
    assert [r["path"] for r in rules] == expected
    assert {r["effect"] for r in rules} == {"allow"}


@pytest.mark.parametrize(
    ("preset", "params", "role", "message"),
    [
        ("nope", {}, ApiRole.REST, "has no preset 'nope'"),
        ("read_only", {}, ApiRole.GIT, "needs parameter 'repo'"),
        ("read_only", {"branch": "x"}, ApiRole.REST, "takes no parameter 'branch'"),
        ("read_only", {"repo": "just-a-name"}, ApiRole.GIT, "is not '<owner>/<repo>'"),
        ("push_branches", {"repo": "o/r", "branches": "refs/heads/x"}, ApiRole.GIT, "branch glob"),
        ("push_branches", {"repo": "o/r", "branches": "a b"}, ApiRole.GIT, "branch glob"),
        (
            "push_branches",
            {"repo": "o/r", "branches": "x", "force": "maybe"},
            ApiRole.GIT,
            "true or false",
        ),
        ("push_branches", {"repo": "o/r", "branches": "x"}, ApiRole.REST, "grants nothing"),
    ],
)
def test_preset_errors(preset: str, params: dict[str, str], role: ApiRole, message: str) -> None:
    with pytest.raises(PresetError, match=message):
        expand_preset(_github(), preset, params, role=role)


# ── strict validation ────────────────────────────────────────────────────────


def _github_doc() -> dict[str, Any]:
    doc: dict[str, Any] = yaml.safe_load((_PROFILE_DIR / "github.yaml").read_text())
    return doc


def _parse(doc: dict[str, Any]) -> None:
    parse_profile(yaml.safe_dump(doc))


def test_the_unmodified_document_parses() -> None:
    _parse(_github_doc())


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda d: d.update(unknown_key=1), "Extra inputs are not permitted"),
        (lambda d: d.pop("connect"), "needs the profile's connect block"),
        (lambda d: d["apis"][0].update(connect="github"), "is the profile's own connect"),
        (lambda d: d["apis"][1]["identity"].update(vendor="gh"), "share one identity vendor"),
        (lambda d: d["apis"].append(dict(d["apis"][0], connect="x")), "once in a profile"),
        (
            lambda d: d["apis"][0]["identity"].update(name="rest"),
            "registers as",
        ),
        (
            lambda d: d["presets"]["read_only"]["rules"][1].update(include="github_graphql:nope"),
            "unknown rule set",
        ),
        (
            lambda d: d["presets"]["read_only"]["rules"].append(
                {"api": "git", "effect": "allow", "match_mode": "prefix", "path": "/"}
            ),
            "match_mode",
        ),
        (
            lambda d: d["presets"]["read_only"]["rules"].append(
                {"api": "git", "effect": "allow", "match_mode": "git", "path": "fetch:${owner}/x"}
            ),
            "undeclared parameter 'owner'",
        ),
        (lambda d: d["apis"][2].update(source="builtin:github.com/nope"), "not a built-in API"),
        (lambda d: d["apis"][1].update(source="shipped:specs/missing.yaml"), "cannot read"),
        (lambda d: d["routing"]["facade"].update(mode="path"), "facade mode"),
        (lambda d: d["agent"]["wrapper"]["env_set"].update(GH_HOST="{nope}"), "placeholder"),
        (lambda d: d["cli"].update(supported_range="2.x"), "comparators"),
        (lambda d: d["hard_deny"][0].update(source="os:path"), "jentic_one"),
        (lambda d: d["vendor"].update(id="gitlab"), "no cli_facades.vendors entry"),
    ],
)
def test_invalid_documents_are_refused(
    mutate: Callable[[dict[str, Any]], None], message: str
) -> None:
    doc = _github_doc()
    mutate(doc)
    with pytest.raises(CliProfileError, match=message):
        _parse(doc)


# ── profile-scoped credentials ───────────────────────────────────────────────


def test_github_is_profile_scoped_through_its_own_connect() -> None:
    profile = _github()
    assert profile.profile_scoped
    assert profile_for_connect("github") is profile
    # The per-API connects stay for a credential of one API only.
    assert profile_for_connect("github-git") is None
    assert profile_for_connect("stripe") is None


def test_per_api_mode_still_parses() -> None:
    doc = _github_doc()
    doc["credential"]["mode"] = "per_api"
    _parse(doc)


@pytest.mark.parametrize(
    ("vendor", "name", "version", "expected"),
    [
        ("github-com", "api-github-com", "1.1.4", "github"),
        ("github.com", "api.github.com", "2022-11-28", "github"),
        ("github-com", "github-graphql", "1", "github"),
        ("github-com", "github-git", "1", "github"),
        # A shipped or built-in API declares one version only.
        ("github-com", "github-git", "2", None),
        # An undeclared API of the same vendor belongs to no profile.
        ("github-com", "gists", "1", None),
        ("cloudflare-com", "cloudflare-com", "4", "cloudflare"),
    ],
)
def test_declaring_profile(vendor: str, name: str, version: str, expected: str | None) -> None:
    assert declaring_profile(vendor, name, version) == expected


def test_profile_api_role() -> None:
    assert profile_api_role("github", "github-com", "github-graphql", "1") == ApiRole.GRAPHQL
    assert profile_api_role("github", "github-com", "api-github-com", "1.1.4") == ApiRole.REST
    assert profile_api_role("github", "github-com", "gists", "1") is None
    assert profile_api_role("nope", "github-com", "github-git", "1") is None


def test_preset_for_profile_tags_every_rule_with_its_role() -> None:
    rules = expand_preset_for_profile(
        _github(), "read_and_push", {"repo": "octo-org/app", "branches": "agent/*"}
    )
    assert {r["api_role"] for r in rules} == {"rest", "graphql", "git"}
    for rule in rules:
        expected = {"git": "git", "graphql": "graphql"}.get(rule["match_mode"], "rest")
        assert rule["api_role"] == expected
    assert {
        "effect": "allow",
        "match_mode": "git",
        "path": "push:octo-org/app:refs/heads/agent/*",
        "api_role": "git",
    } in rules


def test_assign_rule_roles_defaults_by_kind_and_refuses_a_mismatch() -> None:
    profile = _github()
    rules = assign_rule_roles(
        profile,
        [
            {"effect": "allow", "methods": ["GET"], "path": "/", "match_mode": "prefix"},
            {"effect": "allow", "path": "query", "match_mode": "graphql"},
            {"effect": "allow", "path": "fetch:o/r", "match_mode": "git"},
            {"effect": "deny", "path": "mutation", "match_mode": "graphql", "api_role": "graphql"},
        ],
    )
    assert [r["api_role"] for r in rules] == ["rest", "graphql", "git", "graphql"]
    with pytest.raises(PresetError, match="takes match_mode"):
        assign_rule_roles(profile, [{"effect": "allow", "path": "/", "api_role": "graphql"}])
    with pytest.raises(PresetError, match="has no 'uploads' API"):
        assign_rule_roles(
            profile,
            [{"effect": "allow", "path": "/", "match_mode": "prefix", "api_role": "uploads"}],
        )
