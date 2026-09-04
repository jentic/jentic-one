"""Public agent-discovery documents: the served skill set and ``llms.txt``.

Serves the shipped skill set over HTTP so an agent that can reach a deployment
can self-onboard from the base URL alone — no manual copying of a CLI-generated
skill file, no drift from the running version:

- ``GET /skills/{name}.md`` — the raw markdown of any shipped skill (allowlisted
  to the set globbed from package data). ``GET /SKILL.md`` is a legacy alias for
  the ``jentic`` onboarding skill.
- ``GET /skills/index.json`` — a manifest of the set: ``name``, ``description``,
  ``version``, raw-bytes ``sha256``, and a base-stamped absolute ``url``.
- ``GET /llms.txt`` (alias ``GET /.well-known/llms.txt``) — an ``llms.txt`` index
  linking every discovery document, including a ``## Skills`` section.

The served set is the same canonical content the CLI embeds and renders into
agent runtimes (``cli/internal/skillgen/content/<name>.md``); a drift test pins
the copies to each other (``tests/arch/test_skill_drift.py``). The single source
of truth for the *backend* served set is the shipped ``content/*.md`` package
data, globbed at runtime — deliberately NOT an import of the repo-root ``tools``
package, which is not shipped in the wheel.

The backend serves each skill's RAW bytes verbatim: BaseURL is a render-time
concern owned only by the CLI, so nothing here interpolates it.

Hidden from the OpenAPI schema, like ``GET /reference/endpoints.json``: these
are tooling/onboarding documents, not a product API.

Split deployments: the router is mounted on every surface app, but the links
in ``llms.txt`` span surfaces (auth, registry, control), so standalone
surfaces should set ``auth.canonical_base_url`` to the gateway URL — otherwise
the rendered links point at the single surface's own host and may 404 there.
"""

from __future__ import annotations

import hashlib
import importlib.resources
import re
from functools import cache

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, PlainTextResponse

from jentic_one.shared.context import Context
from jentic_one.shared.web.deps import get_ctx
from jentic_one.shared.web.links import deployment_base_url

SKILL_ALIAS_PATH = "/SKILL.md"
LLMS_TXT_PATH = "/llms.txt"
LLMS_TXT_WELL_KNOWN_PATH = "/.well-known/llms.txt"

#: The ``jentic`` onboarding skill's canonical path. Kept as a module constant
#: for the ``llms.txt`` emphasis line and for tests; served by the parameterized
#: ``/skills/{name}.md`` route like every other skill in the set.
SKILL_PATH = "/skills/jentic.md"

#: The onboarding skill whose full body ``llms.txt`` points at up front.
ONBOARDING_SKILL = "jentic"

MARKDOWN_MEDIA_TYPE = "text/markdown; charset=utf-8"

#: Agent Skills ``name`` grammar (Anthropic spec): 1-64 chars, lowercase
#: alphanumerics and single interior hyphens, no leading/trailing hyphen. Kept
#: as an independent constant here (not imported from ``tools``) so the runtime
#: backend has no dependency on the un-shipped repo-root ``tools`` package.
SKILL_NAME_RE = re.compile(r"^[a-z0-9]([a-z0-9-]{0,62}[a-z0-9])?$")

#: The app-state attribute :func:`jentic_one.mcp.installer.install_mcp_mount`
#: stamps on shapes that actually carry the ``/mcp`` transport (control-plane
#: shapes only — never standalone-auth or the broker, which serve at most the
#: discovery challenge placeholder). Read by name (not imported) so this shared
#: module keeps zero dependency on the ``jentic_one.mcp`` layer; the installer
#: pins the attribute as its own module constant (``_STATE_ATTR``).
_MCP_MOUNT_STATE_ATTR = "mcp_mount"

_CONTENT_PACKAGE = "jentic_one.shared.web"
_CONTENT_DIR = "content"


@cache
def shipped_skill_names() -> tuple[str, ...]:
    """The served skill set, derived by globbing the shipped ``content/*.md``.

    This package data is the single source of truth for the backend allowlist:
    a skill is served iff ``content/<name>.md`` ships in the wheel. Names are
    validated against the Agent-Skills grammar and returned sorted so callers
    (index manifest, llms.txt) render in a stable order. Cached: the shipped set
    is fixed for the life of the process.
    """
    content = importlib.resources.files(_CONTENT_PACKAGE) / _CONTENT_DIR
    names: list[str] = []
    for entry in content.iterdir():
        name = entry.name
        if not name.endswith(".md"):
            continue
        stem = name[: -len(".md")]
        if SKILL_NAME_RE.fullmatch(stem):
            names.append(stem)
    return tuple(sorted(names))


@cache
def load_skill_markdown(name: str) -> str:
    """Raw markdown for a shipped skill, read once from package data.

    Cached, but only ever called with a name already validated against
    ``shipped_skill_names()`` (a finite key space), so the cache cannot be
    poisoned by arbitrary input. Returns the RAW served bytes as text — no
    BaseURL interpolation (the backend serves verbatim; only the CLI renders).
    """
    resource = importlib.resources.files(_CONTENT_PACKAGE) / _CONTENT_DIR / f"{name}.md"
    return resource.read_text(encoding="utf-8")


def _parse_frontmatter(text: str) -> dict[str, str]:
    """Extract flat top-level ``key: value`` frontmatter scalars.

    Mirrors the simple parse in ``tools/skills_sync`` (kept local so the runtime
    backend does not import the un-shipped ``tools`` package): only flat
    top-level scalars are read — indented lines (nested ``metadata:``) and lines
    without a colon are skipped. Returns an empty dict if there is no
    frontmatter block.
    """
    if not text.startswith("---\n"):
        return {}
    end = text.find("\n---", 4)
    if end < 0:
        return {}
    fm: dict[str, str] = {}
    for line in text[4:end].splitlines():
        if line.startswith((" ", "\t")) or ":" not in line:
            continue  # nested (metadata:) or continuation — not a top-level scalar
        key, _, value = line.partition(":")
        fm[key.strip()] = value.strip()
    return fm


def _skill_description(name: str) -> str:
    """One-line ``description`` from a shipped skill's frontmatter."""
    fm = _parse_frontmatter(load_skill_markdown(name))
    return fm.get("description", "")


def _collapse_ws(text: str) -> str:
    """Collapse all runs of whitespace (incl. newlines) to single spaces.

    Descriptions may span multiple lines; a single line keeps the
    ``- [name](url): description`` llms.txt list-item shape intact.
    """
    return " ".join(text.split())


def _skills_index(base: str) -> list[dict[str, str]]:
    """Manifest rows for the shipped set: name, description, version, sha256, url.

    ``sha256`` is the digest of the RAW served bytes (exactly what
    ``GET /skills/<name>.md`` returns), so a client can verify a fetched skill
    against the manifest. ``url`` is base-stamped absolute. Sorted by name.
    """
    rows: list[dict[str, str]] = []
    for name in shipped_skill_names():
        text = load_skill_markdown(name)
        fm = _parse_frontmatter(text)
        rows.append(
            {
                "name": name,
                "description": fm.get("description", ""),
                "version": fm.get("version") or "1",
                "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                "url": f"{base}/skills/{name}.md",
            }
        )
    return rows


def _render_skills_section(base: str) -> str:
    """The ``## Skills`` llms.txt section listing the served set."""
    lines = ["## Skills", ""]
    for name in shipped_skill_names():
        description = _collapse_ws(_skill_description(name))
        lines.append(f"- [{name}]({base}/skills/{name}.md): {description}")
    return "\n".join(lines)


def render_llms_txt(
    base: str, assertion_max_ttl_seconds: int, *, mcp_http_enabled: bool = False
) -> str:
    """Render the llms.txt document for a deployment base URL.

    Follows the llms.txt convention (H1 + blockquote summary + link sections)
    and carries the agent onboarding quickstart inline, so an LLM landing on
    ``{base}/llms.txt`` needs no further out-of-band context. The ``## Skills``
    section is computed from the shipped set's frontmatter.

    ``mcp_http_enabled`` advertises the daemon-native ``/mcp`` endpoint:
    the serving route passes ``server.mcp.enabled`` AND-ed
    with the surface actually carrying the mount, so a disabled or absent
    endpoint is never advertised (it answers 404, or the discovery
    challenge). The enabled arm adds the endpoint paragraph (plus the
    stdio-bridge escape hatch for stdio-only clients); the disabled arm keeps
    the plain wording with no MCP paragraph.
    """
    skills_section = _render_skills_section(base)
    if mcp_http_enabled:
        mcp_paragraph = f"""\
This deployment is reachable over **MCP** two ways. It serves a **stateless
Streamable HTTP endpoint at {base}/mcp** (spec revision 2026-07-28):
configure a URL-based MCP entry pointing at it and authenticate every request
with `Authorization: Bearer <agent API key or access token>`. Alternatively,
the local `jentic mcp` stdio server spawns on the agent machine and
talks to this deployment with the agent's registered identity. Both expose
the same discover → execute loop as the CLI tools. Stdio-only MCP runtimes
can reach {base}/mcp through a stdio↔HTTP bridge such as `mcp-remote` or
`mcp-proxy` — exact entries in the
[MCP endpoint guide](https://raw.githubusercontent.com/jentic/jentic-one/refs/heads/main/docs/guides/mcp-http-endpoint.md).
A 401 from the broker host is its auth-gated forward proxy, not a
second MCP server."""
    else:
        mcp_paragraph = """\
This deployment is reachable over **MCP** via the local `jentic mcp` stdio
server. It exposes the same discover → execute loop as the CLI
tools against this deployment. MCP access runs through that local server, not
an HTTP endpoint here: `/mcp` on the control plane serves no MCP server today —
it answers either 404 or, on deployments preparing interactive OAuth, a 401
OAuth discovery challenge — and a 401 from the broker is its auth-gated
forward proxy, not a hidden MCP server."""
    return f"""\
# Jentic One

> Jentic One is an API broker for AI agents: discover operations across many
> third-party APIs, then execute them through a single audited gateway without
> handling each API's credentials. This deployment is self-describing — every
> document linked below is served by the running service and cannot drift from
> it.

Agents: read the onboarding skill at {base}{SKILL_PATH} first. It is the
canonical guide to the identity → discover → request access → execute loop —
the same canonical guide the `jentic` CLI renders into agent runtimes.

{mcp_paragraph}

If your session has `jentic` MCP tools, prefer them; use the `jentic` CLI for
`setup`/`access` recovery and anything not exposed over MCP. Both surfaces
talk to the same instance — check `backend`/`host` in the identity stamp on
MCP tool results (or `GET {base}/instance`) if in doubt.

## Quickstart (agent onboarding)

Prefer the `jentic` CLI where available: your operator runs `jentic setup`
(registers this agent, waits for human approval, installs the skill), then you
drive `jentic search` → `jentic inspect` → `jentic execute`. The raw HTTP
sequence is:

1. Register (RFC 7591): generate an Ed25519 keypair, then
   `POST {base}/register` with
   `{{"client_name": "<agent name>", "jwks": {{"keys": [<public key as JWK>]}}}}`.
   The response carries `client_id` and a `registration_access_token`; a human
   operator must approve the registration.
2. Poll approval (RFC 7592): `GET {base}/register/{{client_id}}` with
   `Authorization: Bearer <registration_access_token>` until `status` is
   `active` (it starts as `pending`; `rejected` means a human denied the
   registration).
3. Get a token: `POST {base}/oauth/token` with a JSON body (not form-encoded):
   `{{"grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
   "assertion": "<EdDSA-signed JWT>"}}`. Assertion claims: `iss` = your
   `client_id`, `aud` = `{base}/oauth/token`, `exp` at most
   {assertion_max_ttl_seconds} seconds in the future, and a unique `jti`
   (required — replayed or missing `jti` values are rejected).
4. Discover: `POST {base}/search` to search operations across APIs;
   `GET {base}/apis` to list registered APIs;
   `GET {base}/reference/endpoints.json` for the full endpoint + scope map.
5. Request access: `POST {base}/access-requests` for the toolkit/API you need,
   then wait for a human to approve.
6. Execute by sending the request through the broker's forward proxy with the
   full upstream URL (the broker runs on its own host/port — see the skill's
   execute section). The CLI's `jentic execute` is the equivalent audited
   path. Credentials are injected by the broker, never handled by you.

## Docs

- [Agent onboarding skill]({base}{SKILL_PATH}): canonical "how to use Jentic"
  guide for agents; same canonical content as the CLI-installed skill
- [OpenAPI specification]({base}/openapi.json): the control-plane API
- [Endpoint and scope reference]({base}/reference/endpoints.json): every
  endpoint with required scopes and typical caller (agent / operator)
- [OAuth discovery]({base}/.well-known/oauth-authorization-server): RFC 8414
  metadata — token endpoint, registration endpoint, supported grants
- [Interactive API docs]({base}/docs): Swagger UI over the live spec

{skills_section}
"""


def get_agent_discovery_router() -> APIRouter:
    """Router exposing the public, schema-hidden agent-discovery documents."""
    router = APIRouter()

    # Register the more specific literal paths before the parameterized
    # ``/skills/{name}.md``: Starlette matches routes in registration order, so
    # ``/skills/index.json`` (a literal segment) and the ``/SKILL.md`` alias must
    # come first. The ``{name}.md`` route then serves the whole set, ``jentic``
    # included, so there is no separate literal ``/skills/jentic.md`` handler to
    # keep in sync — one allowlisted param route is the single serving path.

    @router.get("/skills/index.json", include_in_schema=False)
    async def skills_index(request: Request, ctx: Context = Depends(get_ctx)) -> JSONResponse:
        base = deployment_base_url(ctx.config.auth, request)
        return JSONResponse(_skills_index(base), media_type="application/json")

    @router.get(SKILL_ALIAS_PATH, include_in_schema=False)
    async def onboarding_skill_alias() -> PlainTextResponse:
        return PlainTextResponse(
            load_skill_markdown(ONBOARDING_SKILL), media_type=MARKDOWN_MEDIA_TYPE
        )

    @router.get("/skills/{name}.md", include_in_schema=False)
    async def skill_markdown(name: str) -> PlainTextResponse:
        # Layered, fail-closed validation BEFORE loading (defense in depth):
        # the grammar rejects malformed names and the allowlist rejects unknown
        # ones. The default ``str`` path converter never matches a slash, so
        # traversal like ``/skills/a/b.md`` simply does not route here.
        if not SKILL_NAME_RE.fullmatch(name) or name not in shipped_skill_names():
            raise HTTPException(status_code=404)
        return PlainTextResponse(load_skill_markdown(name), media_type=MARKDOWN_MEDIA_TYPE)

    @router.get(LLMS_TXT_PATH, include_in_schema=False)
    @router.get(LLMS_TXT_WELL_KNOWN_PATH, include_in_schema=False)
    async def llms_txt(request: Request, ctx: Context = Depends(get_ctx)) -> PlainTextResponse:
        base = deployment_base_url(ctx.config.auth, request)
        # The enabled arm is gated on the surface actually carrying the mount,
        # not on config alone: this router rides every standalone surface
        # (split deployments), but the real ``/mcp`` transport is installed on
        # control-plane shapes only. A standalone-auth/broker backend sharing
        # a config with ``server.mcp.enabled: true`` (and no canonical base
        # URL) would otherwise advertise ``http://<own-host>/mcp`` — a URL
        # that very host answers with 404/401.
        serves_mcp = getattr(request.app.state, _MCP_MOUNT_STATE_ATTR, None) is not None
        body = render_llms_txt(
            base,
            ctx.config.auth.assertion_max_ttl_seconds,
            mcp_http_enabled=ctx.config.server.mcp.enabled and serves_mcp,
        )
        return PlainTextResponse(body, media_type=MARKDOWN_MEDIA_TYPE)

    return router
