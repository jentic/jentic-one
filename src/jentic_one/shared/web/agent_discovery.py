"""Public agent-discovery documents: the onboarding skill and ``llms.txt``.

Serves the canonical "how to use Jentic" onboarding skill over HTTP
(``GET /skills/jentic.md``, alias ``GET /SKILL.md``) and an ``llms.txt`` index
(``GET /llms.txt``, alias ``GET /.well-known/llms.txt``) so an agent that can
reach a deployment can self-onboard from the base URL alone — no manual copying
of the CLI-generated skill file, no drift from the running version.

The skill markdown is the same content the CLI embeds and writes into agent
runtimes (``cli/internal/skillgen/content/jentic.md``); a drift test pins the
two copies to each other (``tests/arch/test_skill_drift.py``).

Hidden from the OpenAPI schema, like ``GET /reference/endpoints.json``: these
are tooling/onboarding documents, not a product API.
"""

from __future__ import annotations

import importlib.resources
from functools import cache

from fastapi import APIRouter, Depends, Request
from fastapi.responses import PlainTextResponse

from jentic_one.shared.config import AuthConfig
from jentic_one.shared.context import Context
from jentic_one.shared.web.deps import get_ctx

SKILL_PATH = "/skills/jentic.md"
SKILL_ALIAS_PATH = "/SKILL.md"
LLMS_TXT_PATH = "/llms.txt"
LLMS_TXT_WELL_KNOWN_PATH = "/.well-known/llms.txt"

MARKDOWN_MEDIA_TYPE = "text/markdown; charset=utf-8"


@cache
def load_skill_markdown() -> str:
    """The canonical onboarding-skill markdown, read once from package data."""
    resource = importlib.resources.files("jentic_one.shared.web") / "content" / "jentic.md"
    return resource.read_text(encoding="utf-8")


def _base_url(config: AuthConfig, request: Request) -> str:
    """Deployment base URL: the configured canonical URL, else the request's."""
    if config.canonical_base_url:
        return config.canonical_base_url.rstrip("/")
    return str(request.base_url).rstrip("/")


def render_llms_txt(base: str) -> str:
    """Render the llms.txt document for a deployment base URL.

    Follows the llms.txt convention (H1 + blockquote summary + link sections)
    and carries the agent onboarding quickstart inline, so an LLM landing on
    ``{base}/llms.txt`` needs no further out-of-band context.
    """
    return f"""\
# Jentic One

> Jentic One is an API broker for AI agents: discover operations across many
> third-party APIs, then execute them through a single audited gateway without
> handling each API's credentials. This deployment is self-describing — every
> document linked below is served by the running service and cannot drift from
> it.

Agents: read the onboarding skill at {base}{SKILL_PATH} first. It is the
canonical guide to the identity → discover → request access → execute loop,
and is the same content the `jentic` CLI installs into agent runtimes.

## Quickstart (agent onboarding)

Prefer the `jentic` CLI where available: your operator runs `jentic bootstrap`
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
   approved.
3. Get a token: `POST {base}/oauth/token` with
   `grant_type=urn:ietf:params:oauth:grant-type:jwt-bearer` and an
   EdDSA-signed JWT assertion (see the OAuth discovery document below).
4. Discover: `POST {base}/search` to search operations across APIs;
   `GET {base}/apis` to list registered APIs;
   `GET {base}/reference/endpoints.json` for the full endpoint + scope map.
5. Request access: `POST {base}/access-requests` for the toolkit/API you need,
   then wait for a human to approve.
6. Execute through the broker gateway (the CLI's `jentic execute` is the
   audited path; credentials are injected by the broker, never handled by
   you).

## Docs

- [Agent onboarding skill]({base}{SKILL_PATH}): canonical "how to use Jentic"
  guide for agents; identical to the CLI-installed skill
- [OpenAPI specification]({base}/openapi.json): the control-plane API
- [Endpoint and scope reference]({base}/reference/endpoints.json): every
  endpoint with required scopes and typical caller (agent / operator)
- [OAuth discovery]({base}/.well-known/oauth-authorization-server): RFC 8414
  metadata — token endpoint, registration endpoint, supported grants
- [Interactive API docs]({base}/docs): Swagger UI over the live spec
"""


def get_agent_discovery_router() -> APIRouter:
    """Router exposing the public, schema-hidden agent-discovery documents."""
    router = APIRouter()

    @router.get(SKILL_PATH, include_in_schema=False)
    @router.get(SKILL_ALIAS_PATH, include_in_schema=False)
    async def onboarding_skill() -> PlainTextResponse:
        return PlainTextResponse(load_skill_markdown(), media_type=MARKDOWN_MEDIA_TYPE)

    @router.get(LLMS_TXT_PATH, include_in_schema=False)
    @router.get(LLMS_TXT_WELL_KNOWN_PATH, include_in_schema=False)
    async def llms_txt(request: Request, ctx: Context = Depends(get_ctx)) -> PlainTextResponse:
        base = _base_url(ctx.config.auth, request)
        return PlainTextResponse(render_llms_txt(base), media_type=MARKDOWN_MEDIA_TYPE)

    return router
