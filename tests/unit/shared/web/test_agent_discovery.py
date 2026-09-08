"""Tests for the public agent-discovery documents (skill set + llms.txt).

Covers ``GET /skills/{name}.md`` / ``GET /SKILL.md`` (#651), the served skill
set + ``GET /skills/index.json`` (multi-skill distribution), and ``GET
/llms.txt`` / ``GET /.well-known/llms.txt`` (#809): content, content type,
base-URL resolution, allowlist + traversal rejection, schema hiding, and
availability on both the combined app and standalone surface apps.
"""

from __future__ import annotations

import hashlib
from typing import Any

import pytest
from fastapi.testclient import TestClient

from jentic_one.__main__ import _expand_allowed_dbs
from jentic_one.auth.web.app import create_app as create_auth_app
from jentic_one.broker.web.app import create_app as create_broker_app
from jentic_one.shared.config import AppConfig
from jentic_one.shared.context import Context
from jentic_one.shared.web.agent_discovery import (
    LLMS_TXT_PATH,
    LLMS_TXT_WELL_KNOWN_PATH,
    SKILL_ALIAS_PATH,
    SKILL_PATH,
    load_skill_markdown,
    render_llms_txt,
    shipped_skill_names,
)
from jentic_one.shared.web.app_factory import create_combined_app
from jentic_one.wiring import build_default_container


@pytest.fixture()
def app_config(sample_config_dict: dict[str, Any]) -> AppConfig:
    return AppConfig.model_validate(sample_config_dict)


@pytest.fixture()
def ctx(app_config: AppConfig) -> Context:
    return Context(app_config)


@pytest.fixture()
def client(ctx: Context) -> TestClient:
    app = create_combined_app(ctx, ["registry", "admin", "control", "auth"])
    return TestClient(app, raise_server_exceptions=False)


def test_skill_served_as_markdown(client: TestClient) -> None:
    """GET /skills/jentic.md returns the onboarding skill as markdown."""
    resp = client.get(SKILL_PATH)
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "text/markdown; charset=utf-8"
    assert resp.text == load_skill_markdown("jentic")
    assert "# Using Jentic from the CLI" in resp.text


def test_each_shipped_skill_fetchable(client: TestClient) -> None:
    """Every skill in the shipped set is fetchable at /skills/<name>.md."""
    names = shipped_skill_names()
    assert "jentic" in names
    assert "contribute-spec-fix" in names
    assert "import-new-api" in names
    for name in names:
        resp = client.get(f"/skills/{name}.md")
        assert resp.status_code == 200, name
        assert resp.headers["content-type"] == "text/markdown; charset=utf-8"
        assert resp.text.strip(), f"{name} served empty"
        assert resp.text == load_skill_markdown(name)


def test_skill_alias_serves_same_content(client: TestClient) -> None:
    """GET /SKILL.md is an alias for the jentic onboarding skill document."""
    resp = client.get(SKILL_ALIAS_PATH)
    assert resp.status_code == 200
    assert resp.text == client.get(SKILL_PATH).text


def test_unknown_skill_name_is_404(client: TestClient) -> None:
    """A well-formed but unshipped name is not served (allowlist miss → 404)."""
    resp = client.get("/skills/does-not-exist.md")
    assert resp.status_code == 404
    assert "Using Jentic" not in resp.text  # no file body leaked


@pytest.mark.parametrize(
    "path",
    [
        "/skills/a/b.md",  # slash cannot match the default str converter
        "/skills/..%2fjentic.md",  # encoded traversal attempt
        "/skills/%2e%2e%2fjentic.md",
        "/skills/../jentic.md",
    ],
)
def test_skill_traversal_attempts_do_not_serve(client: TestClient, path: str) -> None:
    """Traversal-shaped requests never serve a file body, and never 5xx.

    The layered guard (regex + allowlist) must reject before any resource read,
    so a traversal-shaped or grammar-valid-but-unshipped name is a clean 404 —
    never a 500 from a failed file read. ``raise_server_exceptions=False`` on the
    client means a 500 would otherwise slip past a bare ``!= 200`` check.
    """
    resp = client.get(path)
    assert resp.status_code != 200
    assert resp.status_code < 500
    assert "# Using Jentic from the CLI" not in resp.text


def test_llms_txt_served_with_request_base_url(client: TestClient) -> None:
    """GET /llms.txt links every discovery document under the request base URL."""
    resp = client.get(LLMS_TXT_PATH)
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "text/markdown; charset=utf-8"
    body = resp.text
    assert body.startswith("# Jentic One")
    # TestClient's base URL is http://testserver.
    for path in (
        "/skills/jentic.md",
        "/openapi.json",
        "/reference/endpoints.json",
        "/.well-known/oauth-authorization-server",
        "/register",
        "/oauth/token",
        "/access-requests",
    ):
        assert f"http://testserver{path}" in body, f"llms.txt missing link to {path}"


def test_llms_txt_well_known_alias(client: TestClient) -> None:
    """GET /.well-known/llms.txt serves the same document as /llms.txt."""
    resp = client.get(LLMS_TXT_WELL_KNOWN_PATH)
    assert resp.status_code == 200
    assert resp.text == client.get(LLMS_TXT_PATH).text


def test_llms_txt_honors_canonical_base_url(sample_config_dict: dict[str, Any]) -> None:
    """A configured auth.canonical_base_url overrides the request base URL."""
    config_dict = dict(sample_config_dict)
    config_dict["auth"] = {
        **config_dict.get("auth", {}),
        "canonical_base_url": "https://jentic.example.com/",
    }
    ctx = Context(AppConfig.model_validate(config_dict))
    app = create_combined_app(ctx, ["control"])
    client = TestClient(app, raise_server_exceptions=False)
    body = client.get(LLMS_TXT_PATH).text
    assert "https://jentic.example.com/skills/jentic.md" in body
    assert "http://testserver" not in body


def test_skills_index_manifest(client: TestClient) -> None:
    """GET /skills/index.json manifests the shipped set with verifiable hashes."""
    resp = client.get("/skills/index.json")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/json")
    rows = resp.json()
    assert isinstance(rows, list)

    names = [row["name"] for row in rows]
    assert names == sorted(shipped_skill_names())  # covers exactly the shipped set, sorted

    for row in rows:
        assert set(row) == {"name", "description", "version", "sha256", "url"}
        assert row["description"].strip()
        assert row["version"]
        # url is absolute + base-stamped (TestClient base URL is http://testserver).
        assert row["url"] == f"http://testserver/skills/{row['name']}.md"
        # sha256 is the digest of the RAW bytes served at that url.
        served = client.get(f"/skills/{row['name']}.md").content
        assert row["sha256"] == hashlib.sha256(served).hexdigest()


def test_skills_index_defaults_version_for_freeform(client: TestClient) -> None:
    """Freeform skills without a top-level version fall back to "1"."""
    rows = {row["name"]: row for row in client.get("/skills/index.json").json()}
    assert rows["contribute-spec-fix"]["version"] == "1"
    assert rows["import-new-api"]["version"] == "1"


def test_discovery_documents_hidden_from_schema(ctx: Context) -> None:
    """Onboarding documents are tooling metadata, kept out of the OpenAPI spec."""
    app = create_combined_app(ctx, ["control"])
    paths = app.openapi()["paths"]
    for path in (
        SKILL_PATH,
        SKILL_ALIAS_PATH,
        LLMS_TXT_PATH,
        LLMS_TXT_WELL_KNOWN_PATH,
        "/skills/{name}.md",
        "/skills/index.json",
    ):
        assert path not in paths


def test_standalone_surface_serves_discovery_documents(ctx: Context) -> None:
    """Standalone surface apps serve the documents too (split deployments)."""
    app = create_auth_app(ctx)
    client = TestClient(app, raise_server_exceptions=False)
    assert client.get(SKILL_PATH).status_code == 200
    assert client.get(LLMS_TXT_PATH).status_code == 200


def test_broker_catch_all_does_not_shadow_discovery_documents(
    app_config: AppConfig,
) -> None:
    """The broker's /{upstream_url:path} proxy must not swallow the documents.

    The discovery router is registered before the surface routers precisely so
    the broker's auth-gated catch-all cannot shadow these literal paths.
    """
    broker_ctx = Context(app_config, allowed_dbs=_expand_allowed_dbs(["broker"], app_config))
    app = create_broker_app(broker_ctx)
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.get(SKILL_PATH)
    assert resp.status_code == 200
    assert resp.text == load_skill_markdown("jentic")
    assert client.get(LLMS_TXT_PATH).status_code == 200


def test_broker_catch_all_does_not_shadow_non_jentic_skill(
    app_config: AppConfig,
) -> None:
    """A non-jentic skill is served by the discovery router, not proxied.

    Guards that the parameterized /skills/{name}.md route (not just the literal
    jentic path) is registered ahead of the broker's forward-proxy catch-all.
    """
    broker_ctx = Context(app_config, allowed_dbs=_expand_allowed_dbs(["broker"], app_config))
    app = create_broker_app(broker_ctx)
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.get("/skills/contribute-spec-fix.md")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "text/markdown; charset=utf-8"
    assert resp.text == load_skill_markdown("contribute-spec-fix")


def test_llms_txt_quickstart_matches_backend_contract(client: TestClient) -> None:
    """Served instructions an LLM follows verbatim must match the backend.

    Regression guard for review findings: the poll loop must key on the real
    ``active`` status (there is no ``approved``), and the token call must be
    described as JSON (the endpoint 422s a form-encoded body).

    The ``approved`` check is scoped to the Quickstart section (the steps an
    agent follows), not the whole document — later sections like ``## Skills``
    render skill descriptions that may legitimately mention approval workflows.
    """
    body = client.get(LLMS_TXT_PATH).text
    quickstart = body.split("## Quickstart", 1)[-1].split("\n## ", 1)[0]
    assert "`active`" in quickstart
    # The registration status value is `active`, never `approved`.
    assert "approved" not in quickstart
    assert "JSON body" in body
    assert "unique `jti`" in body
    # The assertion-TTL bound is rendered from config, not hardcoded prose.
    assert "300 seconds in the future" in body


def test_llms_txt_advertises_mcp_server(client: TestClient) -> None:
    """llms.txt advertises the local `jentic mcp` server and the routing rule (#1176).

    The document must advertise MCP as the preferred surface for MCP-capable
    runtimes (with the CLI for bootstrap/recovery), state that both surfaces
    reach the same instance (verifiable via ``backend``/``host`` in the
    identity stamp), and still preempt the ``/mcp``-probe misdiagnosis (#753):
    MCP runs through the local stdio server, not an HTTP endpoint on the
    deployment. The ``/mcp`` wording must stay accurate in *both*
    ``server.mcp.oauth.enabled`` arms (enabled deployments answer a
    401 OAuth discovery challenge there, not a 404).
    """
    body = client.get(LLMS_TXT_PATH).text
    assert "no MCP endpoint" not in body
    assert "`jentic mcp`" in body
    assert "jentic mcp --help" in body
    # The routing paragraph: MCP preferred, CLI for recovery, same instance.
    assert "prefer them" in body
    assert "`setup`/`access` recovery" in body
    assert "same instance" in body
    assert "`backend`/`host`" in body
    # The /mcp probe answers 404 or the OAuth-enabled 401 discovery challenge — the
    # advertisement must not imply a live HTTP MCP endpoint in either arm.
    assert "`/mcp` on the control plane serves no MCP server today" in body
    assert "401 OAuth discovery challenge" in " ".join(body.split())
    # The stale single-arm claim must not come back.
    assert "still returns 404" not in body


def test_llms_txt_advertises_http_endpoint_only_when_enabled(
    sample_config_dict: dict[str, Any],
) -> None:
    """Phase-3 item 8: `server.mcp.enabled` flips the llms.txt MCP paragraph.

    Enabled — on a shape actually carrying the mount (the composition root's
    container installs it on control-plane shapes): the document advertises
    the Streamable HTTP endpoint at ``{base}/mcp`` (bearer per request), keeps
    the stdio server, and points stdio-only runtimes at the
    ``mcp-remote``/``mcp-proxy`` bridge recipes.
    Disabled (the default, pinned by ``test_llms_txt_advertises_mcp_server``):
    the pre-phase-3 wording — a 404-answering endpoint is never advertised.
    """
    config_dict = dict(sample_config_dict)
    config_dict["server"] = {"mcp": {"enabled": True}}
    ctx = Context(AppConfig.model_validate(config_dict))
    app = create_combined_app(ctx, ["control"], container=build_default_container(ctx))
    client = TestClient(app, raise_server_exceptions=False)
    body = client.get(LLMS_TXT_PATH).text

    assert "http://testserver/mcp" in body
    assert "Streamable HTTP endpoint" in body
    assert "Authorization: Bearer" in body
    # The disabled-arm denial wording must be gone in this arm.
    assert "serves no MCP server today" not in body
    # The stdio server stays advertised; stdio-only runtimes get the bridges.
    assert "`jentic mcp`" in body
    assert "mcp-remote" in body
    assert "mcp-proxy" in body
    assert "docs/mcp-http-endpoint.md" in body
    # The routing paragraph (§3.5) is arm-independent.
    assert "prefer them" in body
    assert "`backend`/`host`" in body


def test_llms_txt_config_only_enablement_does_not_advertise(
    sample_config_dict: dict[str, Any],
) -> None:
    """The enabled arm is shape-gated, not config-gated: an app built without
    the composition root's mount installer (config flag on, mount absent)
    keeps the disabled wording — the config flag alone must never advertise a
    URL this app answers with 404/401."""
    config_dict = dict(sample_config_dict)
    config_dict["server"] = {"mcp": {"enabled": True}}
    ctx = Context(AppConfig.model_validate(config_dict))
    app = create_combined_app(ctx, ["control"])  # default container: no mount
    client = TestClient(app, raise_server_exceptions=False)
    body = client.get(LLMS_TXT_PATH).text

    assert "http://testserver/mcp" not in body
    assert "Streamable HTTP endpoint" not in body
    assert "serves no MCP server today" in body


def test_llms_txt_auth_standalone_never_advertises_the_endpoint(
    sample_config_dict: dict[str, Any],
) -> None:
    """Review L1: a standalone-auth backend sharing a `server.mcp.enabled`
    config must not advertise ``http://<own-host>/mcp`` — that shape carries
    only the challenge placeholder (404/401 on `/mcp`), never the mount.
    The real endpoint lives on the control-plane shape (previous test)."""
    config_dict = dict(sample_config_dict)
    config_dict["server"] = {"mcp": {"enabled": True}}
    config_dict["apps"] = ["auth"]
    ctx = Context(AppConfig.model_validate(config_dict))
    app = create_auth_app(ctx, container=build_default_container(ctx))
    client = TestClient(app, raise_server_exceptions=False)
    body = client.get(LLMS_TXT_PATH).text

    assert "http://testserver/mcp" not in body
    assert "Streamable HTTP endpoint" not in body
    assert "mcp-remote" not in body
    # The disabled arm's probe-misdiagnosis wording stays.
    assert "serves no MCP server today" in body


def test_llms_txt_disabled_arm_never_mentions_the_endpoint_url(client: TestClient) -> None:
    """The disabled arm is silent about the endpoint: no URL-shaped `/mcp`
    advertisement an agent could wire an entry to (the path only appears in
    the probe-misdiagnosis explanation)."""
    body = client.get(LLMS_TXT_PATH).text
    assert "http://testserver/mcp" not in body
    assert "Streamable HTTP endpoint" not in body
    assert "mcp-remote" not in body


#: The pre-phase-3 MCP paragraph, verbatim from the base branch's
#: ``render_llms_txt`` — the golden for the disabled-arm byte-identity pin.
_PRE_PHASE3_MCP_PARAGRAPH = """\
This deployment is reachable over **MCP** via the local `jentic mcp` stdio
server — available in the `jentic` CLI from the next release; check
`jentic mcp --help`. It exposes the same discover → execute loop as the CLI
tools against this deployment. MCP access runs through that local server, not
an HTTP endpoint here: `/mcp` on the control plane serves no MCP server today —
it answers either 404 or, on deployments preparing interactive OAuth, a 401
OAuth discovery challenge — and a 401 from the broker is its auth-gated
forward proxy, not a hidden MCP server."""


def _enabled_mcp_paragraph(base: str) -> str:
    """The enabled-arm MCP paragraph for ``base`` (golden for the swap pin)."""
    return f"""\
This deployment is reachable over **MCP** two ways. It serves a **stateless
Streamable HTTP endpoint at {base}/mcp** (spec revision 2026-07-28):
configure a URL-based MCP entry pointing at it and authenticate every request
with `Authorization: Bearer <agent API key or access token>`. Alternatively,
the local `jentic mcp` stdio server — available in the `jentic` CLI from the
next release; check `jentic mcp --help` — spawns on the agent machine and
talks to this deployment with the agent's registered identity. Both expose
the same discover → execute loop as the CLI tools. Stdio-only MCP runtimes
can reach {base}/mcp through a stdio↔HTTP bridge such as `mcp-remote` or
`mcp-proxy` — exact entries in the
[MCP endpoint guide](https://raw.githubusercontent.com/jentic/jentic-one/refs/heads/main/docs/mcp-http-endpoint.md).
A 401 from the broker host is its auth-gated forward proxy, not a
second MCP server."""


def test_llms_txt_disabled_arm_is_byte_identical_to_pre_phase3_render() -> None:
    """Review N3: the disabled arm's full render is byte-identical to the
    pre-phase-3 document — pinned by equality, not substrings.

    Three properties combine into the byte-identity guarantee:
    the kwarg default is the disabled arm (every pre-existing caller renders
    it unchanged); the disabled arm carries the base branch's MCP paragraph
    verbatim (the golden above); and the flag swaps exactly that paragraph
    and nothing else, so the rest of the document is the one shared template.
    """
    base = "https://example.test"
    disabled = render_llms_txt(base, assertion_max_ttl_seconds=300)
    assert disabled == render_llms_txt(base, assertion_max_ttl_seconds=300, mcp_http_enabled=False)
    assert _PRE_PHASE3_MCP_PARAGRAPH in disabled
    enabled = render_llms_txt(base, assertion_max_ttl_seconds=300, mcp_http_enabled=True)
    assert enabled == disabled.replace(_PRE_PHASE3_MCP_PARAGRAPH, _enabled_mcp_paragraph(base))
    assert enabled != disabled


def test_render_llms_txt_stamps_base_everywhere() -> None:
    """No hardcoded host survives rendering; every link uses the given base."""
    body = render_llms_txt("https://example.test", assertion_max_ttl_seconds=300)
    assert "https://example.test/skills/jentic.md" in body
    assert "{base}" not in body
    assert "testserver" not in body


def test_llms_txt_skills_section_lists_exactly_shipped_set(client: TestClient) -> None:
    """llms.txt has a ## Skills section listing exactly the shipped set."""
    body = client.get(LLMS_TXT_PATH).text
    assert "## Skills" in body
    section = body.split("## Skills", 1)[1]
    names = shipped_skill_names()
    for name in names:
        # Each shipped skill appears as a single-line markdown link item.
        assert f"- [{name}](http://testserver/skills/{name}.md):" in section
    # No skill link outside the shipped set.
    linked = {
        line[line.index("[") + 1 : line.index("]")]
        for line in section.splitlines()
        if line.startswith("- [") and "/skills/" in line
    }
    assert linked == set(names)


def test_llms_txt_skill_descriptions_are_single_line(client: TestClient) -> None:
    """Multi-line frontmatter descriptions are collapsed so list items stay intact."""
    body = client.get(LLMS_TXT_PATH).text
    section = body.split("## Skills", 1)[1]
    for line in section.splitlines():
        if line.startswith("- ["):
            # A well-formed item has the "- [name](url): description" shape on one line.
            assert "): " in line
