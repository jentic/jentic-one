"""The ``skill://`` resource surface on the mounted ``/mcp`` app.

Pins the wire contract of the skill-resources slice (plan:
``jentic-one-plans/themes/local-mcp/mcp-skill-resources.md``, PR A):

- ``resources/list`` = the shipped skill set + ``skill://index``, derived from
  the same glob the HTTP routes serve (``shipped_skill_names()``) — the pins
  parametrize over it, so they survive a fourth skill;
- ``resources/read skill://<name>`` serves the raw package-data bytes
  (identical to ``GET /skills/<name>.md``) with the ``one.jentic/*``
  provenance ``_meta``; ``skill://index`` serves the SAME manifest
  ``GET /skills/index.json`` serves (one function computes both);
- the D4 three-layer defense on the pre-auth read seam: the resolver is
  provably two-armed (unit characterization), ``on_read_resource`` is a bare
  delegation to it (structural lock), and a hostile-URI probe battery answers
  -32002 twice — credential-less and with a valid bearer (tripwire);
- ``initialize.instructions`` point at ``skill://jentic``/``skill://index``
  (Go-parity sentence — a resource surface nobody is told about is the
  original bug in miniature).
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
    RESOURCE_NOT_FOUND,
    SKILL_INDEX_MIME,
    SKILL_INDEX_URI,
    SKILL_PROVENANCE_NOTE,
    SKILL_URI_SCHEME,
    SOURCE_HOSTED,
    read_skill_resource,
)
from jentic_one.shared.web.agent_discovery import (
    MARKDOWN_MEDIA_TYPE,
    _parse_frontmatter,
    get_agent_discovery_router,
    load_skill_markdown,
    shipped_skill_names,
    skills_index_rows,
)

from .test_mount_gate import _ACCEPT, _BASE, _GOOD_BEARER, _rpc, make_client

_AUTH = {**_ACCEPT, "Authorization": f"Bearer {_GOOD_BEARER}"}

#: Every URI the mount serves: the shipped set + the index (currently four).
_SERVED_URIS = {SKILL_URI_SCHEME + name for name in shipped_skill_names()} | {SKILL_INDEX_URI}

#: The D4 probe battery: unknown schemes, grammar violations, traversal junk,
#: unknown names, and ``init-design`` (lives in ``skills/`` but is
#: deliberately un-served — the allowlist is the shipped wheel glob, not the
#: repo tree). Every one answers -32002, pre- and post-auth alike.
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


def test_resources_list_is_the_shipped_set_plus_index() -> None:
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

        index = listed[SKILL_INDEX_URI]
        assert index["name"] == "index"
        assert index["title"] == "Jentic skill index"
        assert index["mimeType"] == SKILL_INDEX_MIME
        assert index["description"].endswith(SKILL_PROVENANCE_NOTE)
        # D3: the mount stamps one source, so the Go description's
        # source-mismatch caveat must not be parroted here.
        assert "same one.jentic/source as the index read" not in index["description"]


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
            # server's documented tell for backend-produced rows).
            assert set(row) == {"name", "description", "version", "sha256", "url"}
            assert row["url"] == f"{_BASE}/skills/{row['name']}.md"
            doc = _read_contents(client, SKILL_URI_SCHEME + row["name"])
            digest = hashlib.sha256(doc["text"].encode("utf-8")).hexdigest()
            assert row["sha256"] == digest
            # The row's version and the document read's ``_meta`` version are
            # computed by two independent ``get("version") or "1"`` copies
            # (``skills_index_rows`` / ``read_skill_resource``) — cross-pin
            # them so neither default can drift from the other.
            assert doc["_meta"][META_VERSION_KEY] == row["version"]


# --- D4 layer 1: resolver characterization (the proof) -----------------------


@pytest.mark.parametrize("uri", [u for u in _PROBES if not u.startswith(SKILL_URI_SCHEME)])
def test_resolver_first_arm_rejects_every_non_skill_uri(uri: str) -> None:
    """Every string not prefixed ``skill://`` raises -32002 — the resolver's
    first arm is scheme-closed by construction."""
    with pytest.raises(MCPError) as exc:
        read_skill_resource(uri, _BASE)
    assert exc.value.code == RESOURCE_NOT_FOUND


def test_resolver_skill_arm_serves_exactly_the_shipped_set_plus_index() -> None:
    """The ``skill://`` arm serves exactly ``shipped_skill_names() | {index}``
    — provably two-armed: the readable set is derived from the same glob as
    the listing (never a second hand-maintained list), and every other
    ``skill://``-prefixed string (grammar or allowlist miss) raises -32002."""
    for uri in _SERVED_URIS:
        result = read_skill_resource(uri, _BASE)
        assert result.contents[0].uri == uri
    for uri in (u for u in _PROBES if u.startswith(SKILL_URI_SCHEME)):
        with pytest.raises(MCPError) as exc:
            read_skill_resource(uri, _BASE)
        assert exc.value.code == RESOURCE_NOT_FOUND, uri


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
    skill-pointer sentence verbatim — HTTP clients have no other in-handshake
    pointer at the resource surface."""
    server = build_mcp_server(MagicMock())
    instructions = server.instructions
    assert instructions is not None
    assert (
        "The skill://jentic resource is the canonical guide to the whole flow "
        "(skill://index lists every skill document); read it when unsure how "
        "the pieces fit together." in instructions
    )
