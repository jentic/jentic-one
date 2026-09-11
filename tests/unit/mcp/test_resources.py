"""The ``skill://`` resource surface on the mounted ``/mcp`` app.

Pins the wire contract of the skill-resources slice (plan:
``jentic-one-plans/themes/local-mcp/mcp-skill-resources.md``, reshaped PR A —
rev 2's PR A mechanics plus the rev 3 D11 reference arm):

- ``resources/list`` = the shipped skill set + its lane-filtered references
  (``skill://<name>/references/<file>`` for every shipped reference OUTSIDE
  ``CLI_ONLY_REFERENCES``) + ``skill://index``, derived from the same globs
  the HTTP routes serve (``shipped_skill_names()`` /
  ``shipped_skill_references()``) — the pins parametrize over them, so they
  survive a fourth skill or a new reference;
- ``resources/read skill://<name>`` serves the raw package-data bytes
  (identical to ``GET /skills/<name>.md``) with the ``one.jentic/*``
  provenance ``_meta``; reference reads serve the raw reference bytes
  (identical to ``GET /skills/<name>/references/<file>``) stamped with the
  OWNING skill's version; ``skill://index`` serves the SAME manifest
  ``GET /skills/index.json`` serves (one function computes both);
- the D11 lane filter holds on BOTH sides: ``cli.md`` is never listed and
  never readable on the mount (RESOURCE_NOT_FOUND) while staying public over
  HTTP — and the listed set == the readable set as a general invariant (the
  strongest pin for D11: the mount refuses to read what it does not list);
- the D4 three-layer defense on the pre-auth read seam: the resolver is
  provably three-armed (unit characterization), ``on_read_resource`` is a
  bare delegation to it (structural lock), and a hostile-URI probe battery —
  extended with reference shapes — answers -32002 twice — credential-less
  and with a valid bearer (tripwire);
- ``initialize.instructions`` point at ``skill://jentic``/``skill://index``/
  ``skill://jentic/references/mcp.md`` (Go-parity sentence — a resource
  surface nobody is told about is the original bug in miniature).
"""

from __future__ import annotations

import ast
import hashlib
import inspect
import json
from typing import Any, cast
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from mcp.shared.exceptions import MCPError

import jentic_one.mcp.app as mcp_app
from jentic_one.mcp.app import PRE_AUTH_METHODS, build_mcp_server
from jentic_one.mcp.resources import (
    META_SOURCE_KEY,
    META_VERSION_KEY,
    REFERENCES_INFIX,
    RESOURCE_NOT_FOUND,
    SKILL_INDEX_MIME,
    SKILL_INDEX_URI,
    SKILL_PROVENANCE_NOTE,
    SKILL_URI_SCHEME,
    SOURCE_HOSTED,
    read_skill_resource,
    skill_resources,
)
from jentic_one.shared.web.agent_discovery import (
    CLI_ONLY_REFERENCES,
    MARKDOWN_MEDIA_TYPE,
    _parse_frontmatter,
    get_agent_discovery_router,
    load_skill_markdown,
    load_skill_reference,
    shipped_skill_names,
    shipped_skill_references,
    skills_index_rows,
)

from .test_mount_gate import _ACCEPT, _BASE, _GOOD_BEARER, _rpc, make_client

_AUTH = {**_ACCEPT, "Authorization": f"Bearer {_GOOD_BEARER}"}

#: The MCP-visible reference URIs: every shipped reference outside the CLI
#: lane (D11) — derived from the same glob the resolver consumes.
_REFERENCE_URIS = {
    SKILL_URI_SCHEME + name + REFERENCES_INFIX + file
    for name in shipped_skill_names()
    for file in shipped_skill_references(name)
    if file not in CLI_ONLY_REFERENCES
}

#: Every URI the mount serves: the shipped set + lane-filtered references +
#: the index.
_SERVED_URIS = (
    {SKILL_URI_SCHEME + name for name in shipped_skill_names()}
    | _REFERENCE_URIS
    | {SKILL_INDEX_URI}
)

#: The D4 probe battery: unknown schemes, grammar violations, traversal junk,
#: unknown names, ``init-design`` (lives in ``skills/`` but is deliberately
#: un-served — the allowlist is the shipped wheel glob, not the repo tree),
#: and the D11 reference shapes: the lane-filtered ``cli.md``, unknown files,
#: traversal (raw and percent-encoded — the resolver sees the raw URI string,
#: so an encoded dot-segment is just grammar junk), a reference on a skill
#: that ships none, references of unknown/unserved skills, the bare
#: ``references/`` stem, and index collisions
#: (``skill://index/references/…`` — ``index`` is minted, never a skill).
#: Every one answers -32002, pre- and post-auth alike.
_PROBES = (
    "config://x",
    "file:///etc/passwd",
    "https://example.com/skills/jentic.md",
    "skill://../../x",
    "skill://Jentic",
    "skill://unknown",
    "skill://init-design",
    "skill://",
    "skill",
    "",
    "skill://jentic/references/cli.md",  # lane-filtered (D11): listed nowhere, readable nowhere
    "skill://jentic/references/unknown.md",
    "skill://jentic/references/../SKILL.md",
    "skill://jentic/references/%2e%2e/SKILL.md",
    "skill://jentic/references/%2e%2e%2fSKILL.md",
    "skill://jentic/references/mcp",  # missing .md
    "skill://jentic/references/MCP.md",  # grammar: uppercase stem
    "skill://jentic/references/",  # bare — empty filename
    "skill://jentic/references",  # no trailing slash — not a document name either
    "skill://contribute-spec-fix/references/mcp.md",  # skill ships no references
    "skill://unknown/references/mcp.md",
    "skill://init-design/references/mcp.md",
    "skill://index/references/mcp.md",  # index is minted, never a skill with references
    "skill://jentic/references/mcp.md/references/mcp.md",
)


def make_resource_client(**kwargs: Any) -> TestClient:
    """The mount-gate client plus the agent-discovery HTTP routes, so the
    mount's reads can be pinned against the HTTP documents at the same base."""
    client = make_client(**kwargs)
    cast(FastAPI, client.app).include_router(get_agent_discovery_router())
    return client


def _read(client: TestClient, uri: str, headers: dict[str, str] = _ACCEPT) -> dict[str, Any]:
    resp = client.post("/mcp", json=_rpc("resources/read", {"uri": uri}), headers=headers)
    assert resp.status_code == 200, uri
    return cast(dict[str, Any], resp.json())


def _read_contents(client: TestClient, uri: str) -> dict[str, Any]:
    body = _read(client, uri)
    contents = body["result"]["contents"]
    assert len(contents) == 1
    return cast(dict[str, Any], contents[0])


# --- resources/list: the shipped set + index, from the HTTP routes' glob -----


def test_resources_list_is_the_shipped_set_plus_references_plus_index() -> None:
    with make_client() as client:
        resp = client.post("/mcp", json=_rpc("resources/list"), headers=_ACCEPT)
        assert resp.status_code == 200
        result = resp.json()["result"]
        assert "nextCursor" not in result  # no pagination, ever
        uris = [r["uri"] for r in result["resources"]]
        # Keying by URI below would silently dedupe — assert no duplicates
        # first (a shipped ``index.md`` would list ``skill://index`` twice).
        assert len(uris) == len(set(uris))
        listed = {r["uri"]: r for r in result["resources"]}
        assert set(listed) == _SERVED_URIS

        for name in shipped_skill_names():
            row = listed[SKILL_URI_SCHEME + name]
            assert row["name"] == name
            assert row["title"] == f"Jentic skill: {name}"
            assert row["mimeType"] == MARKDOWN_MEDIA_TYPE
            description = _parse_frontmatter(load_skill_markdown(name)).get("description", "")
            assert row["description"] == description + SKILL_PROVENANCE_NOTE

            for file in shipped_skill_references(name):
                if file in CLI_ONLY_REFERENCES:
                    continue
                # The Go stdio server's reference vocabulary, byte-parallel
                # (``registerResources``): name ``<name>/references/<file>``,
                # title ``Jentic skill reference: <name>/<file>`` — the lane
                # is obvious from the filename on both doors.
                ref = listed[SKILL_URI_SCHEME + name + REFERENCES_INFIX + file]
                assert ref["name"] == f"{name}/references/{file}"
                assert ref["title"] == f"Jentic skill reference: {name}/{file}"
                assert ref["mimeType"] == MARKDOWN_MEDIA_TYPE
                assert f"reference document of the {name} skill" in ref["description"]
                # The daemon provenance note: package data + the _meta keys.
                assert f"GET /skills/{name}/references/{file}" in ref["description"]
                assert META_SOURCE_KEY in ref["description"]

        index = listed[SKILL_INDEX_URI]
        assert index["name"] == "index"
        assert index["title"] == "Jentic skill index"
        assert index["mimeType"] == SKILL_INDEX_MIME
        assert index["description"].endswith(SKILL_PROVENANCE_NOTE)
        # D3: the mount stamps one source, so the Go description's
        # source-mismatch caveat must not be parroted here.
        assert "same one.jentic/source as the index read" not in index["description"]


def test_listing_lane_filter_and_reference_rows() -> None:
    """The D11 lane filter on the listing, pinned concretely: the ``jentic``
    skill's MCP-visible references (``mcp.md``, ``recovery.md``) are listed,
    the CLI-lane ``cli.md`` is NOT — even though it ships in the package data
    and stays public over HTTP — and single-file skills contribute no
    reference rows at all."""
    listed = {str(r.uri) for r in skill_resources()}

    # Grounded against the actual shipped set, not hardcoded wholesale: the
    # jentic skill must ship all three lanes for the filter to mean anything.
    assert {"cli.md", "mcp.md", "recovery.md"} <= set(shipped_skill_references("jentic"))
    assert SKILL_URI_SCHEME + "jentic" + REFERENCES_INFIX + "mcp.md" in listed
    assert SKILL_URI_SCHEME + "jentic" + REFERENCES_INFIX + "recovery.md" in listed
    assert SKILL_URI_SCHEME + "jentic" + REFERENCES_INFIX + "cli.md" not in listed

    for name in shipped_skill_names():
        if shipped_skill_references(name):
            continue
        assert not any(
            uri.startswith(SKILL_URI_SCHEME + name + REFERENCES_INFIX) for uri in listed
        ), f"single-file skill {name} must contribute no reference rows"


def test_resource_templates_stay_empty() -> None:
    """D2: ``skill://<name>`` is a closed, enumerable set fully described by
    resources/list — no URI template advertises an open namespace."""
    with make_client() as client:
        resp = client.post("/mcp", json=_rpc("resources/templates/list"), headers=_ACCEPT)
        assert resp.status_code == 200
        assert resp.json()["result"]["resourceTemplates"] == []


def test_index_never_enters_the_shipped_set() -> None:
    """``skill://index`` is minted by the resources module, never shipped as a
    document: ``read_skill_resource`` matches ``SKILL_INDEX_URI`` before the
    name arm, so a future ``content/index.md`` would be listed as a document
    yet silently shadowed by the manifest on every read. Fail loudly here
    instead — rename any such document before shipping it."""
    assert "index" not in shipped_skill_names()


# --- resources/read: documents (bytes + provenance, HTTP-route parity) -------


def test_read_skill_documents_match_the_http_route_bytes() -> None:
    """Each ``skill://<name>`` read serves the RAW package-data bytes —
    identical to ``load_skill_markdown`` AND to ``GET /skills/<name>.md`` —
    with the Go-parity MIME type and the D3 provenance ``_meta``."""
    with make_resource_client() as client:
        for name in shipped_skill_names():
            contents = _read_contents(client, SKILL_URI_SCHEME + name)
            raw = load_skill_markdown(name)
            assert contents["uri"] == SKILL_URI_SCHEME + name
            assert contents["mimeType"] == MARKDOWN_MEDIA_TYPE
            assert contents["text"] == raw
            assert contents["text"] == client.get(f"/skills/{name}.md").text
            version = _parse_frontmatter(raw).get("version") or "1"
            assert contents["_meta"] == {
                META_SOURCE_KEY: SOURCE_HOSTED,
                META_VERSION_KEY: version,
            }


def test_read_references_match_the_http_route_bytes() -> None:
    """Each listed ``skill://<name>/references/<file>`` read serves the RAW
    reference bytes — identical to ``load_skill_reference`` AND to
    ``GET /skills/<name>/references/<file>`` (same package data, so parity is
    structural) — markdown MIME, ``_meta`` = source + the OWNING skill's
    frontmatter version (a reference has no frontmatter of its own). The
    lane-filtered ``cli.md`` meanwhile stays public over HTTP while the mount
    refuses it: the filter is a serving decision, not a secret."""
    with make_resource_client() as client:
        for name in shipped_skill_names():
            version = _parse_frontmatter(load_skill_markdown(name)).get("version") or "1"
            for file in shipped_skill_references(name):
                http = client.get(f"/skills/{name}/references/{file}")
                assert http.status_code == 200, (name, file)  # HTTP: the neutral channel
                if file in CLI_ONLY_REFERENCES:
                    read = _read(client, SKILL_URI_SCHEME + name + REFERENCES_INFIX + file)
                    assert read["error"]["code"] == RESOURCE_NOT_FOUND  # the mount: filtered
                    continue
                uri = SKILL_URI_SCHEME + name + REFERENCES_INFIX + file
                contents = _read_contents(client, uri)
                assert contents["uri"] == uri
                assert contents["mimeType"] == MARKDOWN_MEDIA_TYPE
                assert contents["text"] == load_skill_reference(name, file)
                assert contents["text"] == http.text
                assert contents["_meta"] == {
                    META_SOURCE_KEY: SOURCE_HOSTED,
                    META_VERSION_KEY: version,
                }


def test_read_index_is_the_http_manifest_at_the_same_base() -> None:
    """``skill://index`` serves the SAME manifest ``GET /skills/index.json``
    serves at the same deployment base — parity is structural (one function,
    ``skills_index_rows``, computes both) and pinned byte-level anyway. The
    ``_meta`` carries the source only (Go parity: versions ride per row), and
    each row's sha256 verifies the corresponding document read — the
    client-side loop the manifest exists for."""
    with make_resource_client() as client:
        contents = _read_contents(client, SKILL_INDEX_URI)
        assert contents["mimeType"] == SKILL_INDEX_MIME
        assert contents["_meta"] == {META_SOURCE_KEY: SOURCE_HOSTED}

        http = client.get("/skills/index.json")
        assert contents["text"] == http.text  # byte parity, same base
        rows = json.loads(contents["text"])
        assert rows == skills_index_rows(_BASE)
        assert [row["name"] for row in rows] == sorted(shipped_skill_names())

        for row in rows:
            # Hosted-manifest shape: absolute `url`, never `uri` (the Go
            # server's documented tell for backend-produced rows). Rows for
            # skills that ship references carry a `references` array; rows
            # without omit the key (never null/empty).
            assert set(row) <= {"name", "description", "version", "sha256", "url", "references"}
            assert {"name", "description", "version", "sha256", "url"} <= set(row)
            assert row["url"] == f"{_BASE}/skills/{row['name']}.md"
            doc = _read_contents(client, SKILL_URI_SCHEME + row["name"])
            digest = hashlib.sha256(doc["text"].encode("utf-8")).hexdigest()
            assert row["sha256"] == digest
            # The row's version and the document read's ``_meta`` version are
            # computed by two independent ``get("version") or "1"`` copies
            # (``skills_index_rows`` / ``read_skill_resource``) — cross-pin
            # them so neither default can drift from the other.
            assert doc["_meta"][META_VERSION_KEY] == row["version"]

            has_references = "references" in row
            assert has_references == bool(shipped_skill_references(row["name"]))
            for ref in row.get("references", []):
                assert set(ref) == {"name", "sha256", "url"}
                assert ref["url"] == f"{_BASE}/skills/{row['name']}/references/{ref['name']}"
                if ref["name"] in CLI_ONLY_REFERENCES:
                    continue  # verifiable over HTTP only — the mount won't read it
                # The manifest's per-reference sha256 verifies against the
                # corresponding resource read — the client-side loop the
                # manifest exists for, extended to the reference rows.
                ref_doc = _read_contents(
                    client, SKILL_URI_SCHEME + row["name"] + REFERENCES_INFIX + ref["name"]
                )
                assert ref["sha256"] == hashlib.sha256(ref_doc["text"].encode("utf-8")).hexdigest()


# --- D4 layer 1: resolver characterization (the proof) -----------------------


@pytest.mark.parametrize("uri", [u for u in _PROBES if not u.startswith(SKILL_URI_SCHEME)])
def test_resolver_first_arm_rejects_every_non_skill_uri(uri: str) -> None:
    """Every string not prefixed ``skill://`` raises -32002 — the resolver's
    first arm is scheme-closed by construction."""
    with pytest.raises(MCPError) as exc:
        read_skill_resource(uri, _BASE)
    assert exc.value.code == RESOURCE_NOT_FOUND


def test_resolver_skill_arm_serves_exactly_the_listed_set() -> None:
    """The ``skill://`` arms serve exactly the listed set —
    ``shipped_skill_names()`` + their lane-filtered references + ``index`` —
    provably three-armed: the readable set is derived from the same globs as
    the listing (never a second hand-maintained list), and every other
    ``skill://``-prefixed string (grammar, allowlist, or lane-filter miss)
    raises -32002."""
    for uri in _SERVED_URIS:
        result = read_skill_resource(uri, _BASE)
        assert str(result.contents[0].uri) == uri
    for uri in (u for u in _PROBES if u.startswith(SKILL_URI_SCHEME)):
        with pytest.raises(MCPError) as exc:
            read_skill_resource(uri, _BASE)
        assert exc.value.code == RESOURCE_NOT_FOUND, uri


def test_listed_set_equals_readable_set() -> None:
    """The D11 invariant, asserted generally: every listed URI reads, and the
    readable set is exactly the listed set — the mount refuses to read what
    it does not list (the lane filter can never be listing-only), and it
    never lists what it cannot read. The readable side's exhaustiveness over
    an infinite URI space is carried by the resolver characterization above
    (three arms by construction, each allowlisted from the same globs the
    listing derives from); this test pins the two derivations to each other."""
    listed = {str(r.uri) for r in skill_resources()}
    assert listed == _SERVED_URIS
    for uri in listed:
        result = read_skill_resource(uri, _BASE)  # every listed URI reads
        assert str(result.contents[0].uri) == uri
    # No listed URI is CLI-lane, and every lane-filtered URI is unreadable.
    for name in shipped_skill_names():
        for file in shipped_skill_references(name):
            uri = SKILL_URI_SCHEME + name + REFERENCES_INFIX + file
            if file in CLI_ONLY_REFERENCES:
                assert uri not in listed
                with pytest.raises(MCPError) as exc:
                    read_skill_resource(uri, _BASE)
                assert exc.value.code == RESOURCE_NOT_FOUND
            else:
                assert uri in listed


# --- D4 layer 2: delegation pin (the structural lock) ------------------------


def test_on_read_resource_is_a_bare_delegation_to_the_resolver() -> None:
    """``build_mcp_server``'s ``on_read_resource`` body is a single ``return``
    of a ``read_skill_resource(...)`` call — the app layer provably routes
    every read through the characterized resolver, so a bypass arm cannot
    appear in ``app.py`` without failing this test. (In SDK 2.1.1 there is no
    per-resource routing: the one handler is the entire seam.)"""
    tree = ast.parse(inspect.getsource(mcp_app))
    build = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "build_mcp_server"
    )
    handler = next(
        node
        for node in ast.walk(build)
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "on_read_resource"
    )
    assert len(handler.body) == 1, "on_read_resource must be a single statement"
    ret = handler.body[0]
    assert isinstance(ret, ast.Return)
    call = ret.value
    assert isinstance(call, ast.Call)
    assert isinstance(call.func, ast.Name)
    assert call.func.id == "read_skill_resource"
    # The delegated argument is pinned too: the first argument must be the
    # request's own ``params.uri`` — a hardcoded URI (or anything else) would
    # otherwise pass the "bare delegation" pin while routing reads elsewhere.
    uri_arg = call.args[0]
    assert isinstance(uri_arg, ast.Attribute) and uri_arg.attr == "uri"
    assert isinstance(uri_arg.value, ast.Name) and uri_arg.value.id == "params"


# --- D4 layer 3: the probe battery, credential-less AND with a bearer --------


@pytest.mark.parametrize("uri", _PROBES)
def test_probe_battery_answers_resource_not_found_pre_and_post_auth(uri: str) -> None:
    """Hostile URIs answer JSON-RPC -32002 in-band (the SDK leaves unmapped
    codes at HTTP 200) — asserted twice, credential-less and with a valid
    bearer, byte-equal: pre/post-auth equality is pinned on the probed URIs
    rather than assumed (layers 1+2 carry the exhaustiveness a finite battery
    can't)."""
    with make_client() as client:
        anon = client.post("/mcp", json=_rpc("resources/read", {"uri": uri}), headers=_ACCEPT)
        authed = client.post("/mcp", json=_rpc("resources/read", {"uri": uri}), headers=_AUTH)
        assert anon.status_code == 200
        assert anon.json()["error"]["code"] == RESOURCE_NOT_FOUND
        assert authed.status_code == 200
        assert anon.content == authed.content


def test_every_listed_resource_reads_identically_pre_and_post_auth() -> None:
    """The whole listed surface reads without a credential, and an
    authenticated read returns byte-identical results — the handler never
    reads identity, so the pre-auth door serves exactly the post-auth set."""
    with make_client() as client:
        listing = client.post("/mcp", json=_rpc("resources/list"), headers=_ACCEPT)
        uris = [r["uri"] for r in listing.json()["result"]["resources"]]
        assert set(uris) == _SERVED_URIS
        for uri in uris:
            anon = client.post("/mcp", json=_rpc("resources/read", {"uri": uri}), headers=_ACCEPT)
            authed = client.post("/mcp", json=_rpc("resources/read", {"uri": uri}), headers=_AUTH)
            assert anon.status_code == 200, uri
            assert "error" not in anon.json(), uri
            assert anon.content == authed.content, uri


def test_resources_read_is_pre_auth_whitelisted() -> None:
    """The D4 flip this slice makes: ``resources/read`` rides the pre-auth
    whitelist, alongside the listings it was reserved to join."""
    assert "resources/read" in PRE_AUTH_METHODS
    assert "resources/list" in PRE_AUTH_METHODS
    assert "resources/templates/list" in PRE_AUTH_METHODS
    assert "tools/call" not in PRE_AUTH_METHODS


# --- D5: the instructions point at the skills --------------------------------


def test_instructions_point_at_the_skill_resources() -> None:
    """The mount's ``initialize.instructions`` carry the Go server's
    skill-pointer sentence verbatim — naming ``skill://jentic``,
    ``skill://index``, AND the MCP-session guide
    ``skill://jentic/references/mcp.md`` (the D11 instructions update,
    mirroring the Go stdio server's) — HTTP clients have no other
    in-handshake pointer at the resource surface."""
    server = build_mcp_server(MagicMock())
    instructions = server.instructions
    assert instructions is not None
    assert (
        "The skill://jentic resource is the canonical guide to the whole flow "
        "(skill://index lists every skill document, and "
        "skill://jentic/references/mcp.md carries the MCP-lane detail); "
        "read it when unsure how the pieces fit together." in instructions
    )
