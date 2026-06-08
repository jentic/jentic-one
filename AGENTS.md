# AGENTS.md — Jentic Mini Agent Onboarding

This document tells AI agents how to use a Jentic Mini instance. For the full
skill integration guide, see <https://clawhub.ai/skills/jentic>.

## What is Jentic Mini?

Jentic Mini is a self-hosted API middleware for AI agents. It lets you
**search** for APIs, **inspect** their schemas, and **execute** requests
through a broker that injects credentials on your behalf.

**Credentials live in Jentic, not in the agent.** API secrets are stored
and managed in the broker — agents never see or handle them directly.
This eliminates prompt injection risks from embedded API keys.

### Two-actor trust boundary

| Actor | Auth method | Capabilities |
|-------|-------------|--------------|
| Agent | OAuth `Authorization: Bearer at_…` (recommended) **or** legacy `X-Jentic-API-Key: tk_xxx` | Search, inspect, execute, request permissions |
| Human | Username + password (UI session) | Approve agent registrations, manage credentials, OAuth flows |

## Getting Started

### 1. Human: Create an admin account

Open `/setup` in a browser on this Jentic Mini instance and create an admin
account (username + password). This account is used for human-only
operations like approving agent registrations and managing credentials.

### 2. Agent: Register via OAuth (recommended)

Discover the endpoints, register, wait for approval, then mint an access token:

```
GET /.well-known/oauth-authorization-server
→ { "issuer": "...", "registration_endpoint": "...", "token_endpoint": "..." }

POST /register
Content-Type: application/json
{
  "client_name": "my-agent",
  "jwks": { "keys": [ { "kty": "OKP", "crv": "Ed25519", "x": "...", "kid": "k1" } ] }
}
→ { "client_id": "agnt_…", "registration_access_token": "rat_…", "status": "pending" }

# Poll until a human approves the registration:
GET /register/{client_id}
Authorization: Bearer rat_…
→ { "status": "approved", ... }

# Mint an access token by signing a JWT-bearer assertion (RFC 7523) with
# the private key matching the JWKS you registered:
POST /oauth/token
Content-Type: application/x-www-form-urlencoded
grant_type=urn:ietf:params:oauth:grant-type:jwt-bearer&assertion=eyJhbGciOiJFZERTQSIs...
→ { "access_token": "at_…", "refresh_token": "rt_…", "expires_in": 900, ... }
```

Send `Authorization: Bearer at_…` on subsequent requests. When the access
token nears expiry, exchange the refresh token at the same endpoint with
`grant_type=refresh_token`. Rotated refresh tokens form a chain — reusing
a consumed `rt_` revokes the entire family per RFC 6749 BCP §4.14.

For the full OAuth flow (registration, JWT-bearer assertion shape, rotation,
revocation, key management), see
[docs/agent-identity.md](docs/agent-identity.md).

### 2b. Agent: Legacy toolkit-key flow

If a human has already issued you a toolkit key out of band (e.g. created
via `Toolkits → default → Keys` in the admin UI), you can use it directly
instead of the OAuth flow:

```
X-Jentic-API-Key: tk_xxx
```

This path is retained for backwards compatibility. New integrations should
prefer the OAuth flow above. The `tk_` and `at_` paths reach the same
broker, executor, and trace pipeline — only the principal carried in the
audit trail differs.

### 3. Start using the API

Set whichever credential you obtained on every subsequent request:

```
Authorization: Bearer at_…    # OAuth (recommended)
# or
X-Jentic-API-Key: tk_xxx       # Legacy toolkit key
```

Then follow the Search, Inspect, Execute workflow below.

## Authentication

Send your credentials on every request:

```
Authorization: Bearer at_…
```

(or, for the legacy flow, `X-Jentic-API-Key: tk_xxx`).

Public endpoints that do not require credentials: `/health`,
`/.well-known/oauth-authorization-server`, `/docs`, `/openapi.json`,
`/llms.txt`. The OAuth registration and token endpoints (`POST /register`,
`POST /oauth/token`) are also public — they are how unauthenticated agents
become authenticated agents.

## Core Workflow: Search, Inspect, Execute

### 1. Search

Find APIs and workflows by intent:

```
GET /search?q=send+email&n=5
```

Returns ranked results with capability IDs and links.

### 2. Inspect

Get the full schema for a capability before calling it:

```
GET /inspect/POST%2Fapi.sendgrid.com%2Fv3%2Fmail%2Fsend
```

Returns parameters, response schema, auth requirements, error taxonomy, and
an `_links.execute` URL. Supports `Accept: text/markdown` for an LLM-friendly
format.

**Capability ID format:** `METHOD/host/path` (e.g., `GET/api.stripe.com/v1/customers`).

### 3. Execute

Call the API through the broker. The broker injects credentials automatically:

```
POST /api.sendgrid.com/v3/mail/send
Authorization: Bearer at_…
Content-Type: application/json

{"personalizations": [{"to": [{"email": "user@example.com"}]}], ...}
```

(Legacy toolkit-key callers swap `Authorization: Bearer at_…` for
`X-Jentic-API-Key: tk_xxx` — every other example below works the same way.)

The broker resolves your toolkit, finds the matching credential, injects the
auth header, forwards to the upstream API, logs a trace, and returns the
response verbatim.

**Multiple credentials for the same host** (e.g. Google Calendar + Gmail both on
`www.googleapis.com`): add `X-Jentic-Service: google_calendar` to select by
service name. The response includes `X-Jentic-Credential-Used` so you can verify
which credential was injected. If ambiguous, `X-Jentic-Credential-Ambiguous: true`
is set as a warning.

## Workflow Execution

Multi-step workflows (Arazzo specs) are executed the same way:

```
POST /workflows/summarise-discourse-topics
Authorization: Bearer at_…
Content-Type: application/json

{"forum_url": "discourse.example.com", "topic_count": 5}
```

Each step in the workflow routes through the broker, so every upstream call
gets credential injection and tracing.

## Async Execution

Use `Prefer: wait=0` to get an immediate 202 with a job ID:

```
POST /api.stripe.com/v1/charges
Prefer: wait=0
Authorization: Bearer at_…
```

Poll `GET /jobs/{job_id}` until status is `complete` or `failed`.

## Permission Escalation

If a request is denied by policy (403), request expanded permissions:

```
POST /toolkits/{toolkit_id}/access-requests
Authorization: Bearer at_…
Content-Type: application/json

{
  "type": "modify_permissions",
  "credential_id": "slack-bot",
  "rules": [{"effect": "allow", "methods": ["POST"], "path": "chat.postMessage"}],
  "reason": "Need to send messages to Slack channels"
}
```

A human approves the request, then retry the original call.

## Observability

- `GET /traces` — list recent execution traces. Filter with `toolkit_id`,
  `agent_id`, `api_id` (host substring), `status`, `capability_id`,
  `since`, `until`. All filters compose with AND.
- `GET /traces/{trace_id}` — full trace detail (timing, status, step outputs).
- `GET /traces/usage` — aggregate stats, time buckets, and top groups for the
  Monitor page. Defaults to last 24h. `group_by={toolkit|api|agent}` (default
  `toolkit`). Bucket width is chosen by the server (60s → 1d) based on the
  window length.
- `GET /jobs` — list async jobs. Filter with `status`, `toolkit_id`,
  `agent_id`, `since`, `until`. Returns `agent_id` on each row when the job
  was created via an agent access token (`at_…`).
- `GET /jobs/{job_id}` — poll a single async job for completion.

## API Reference

Interactive docs at `/docs` (Swagger UI) and `/redoc`. Machine-readable
OpenAPI spec at `/openapi.json`.

## Key Endpoints Summary

| Action | Endpoint |
|--------|----------|
| Bootstrap | `GET /health` |
| OAuth discovery | `GET /.well-known/oauth-authorization-server` |
| Register agent (DCR) | `POST /register` |
| Mint / refresh token | `POST /oauth/token` |
| Revoke token | `POST /oauth/revoke` |
| Search | `GET /search?q=TEXT` |
| Inspect | `GET /inspect/{capability_id}` |
| Execute | `{METHOD} /{upstream_host}/{path}` |
| Workflows | `GET /workflows`, `POST /workflows/{slug}` |
| Traces | `GET /traces`, `GET /traces/{trace_id}`, `GET /traces/usage` |
| Async jobs | `GET /jobs`, `GET /jobs/{job_id}` |
| Catalog | `GET /apis`, `GET /catalog` |
| Escalation | `POST /toolkits/{id}/access-requests` |

## Credential Injection

Credentials are **never** exposed to agents. The broker:

1. Identifies the upstream API from the request URL
2. Looks up credentials bound to your toolkit for that API
3. Reads the security scheme from the spec (+ overlays)
4. Injects the auth header (Bearer, Basic, or apiKey)
5. Forwards to the upstream API
6. Logs a trace

If no credentials are configured for an API, the broker returns
`CREDENTIAL_LOOKUP_FAILED` with instructions to request access.
