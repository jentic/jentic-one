<!--
GENERATED FILE — DO NOT EDIT.

This endpoint + scope reference is generated from code by `make endpoints`
(tools/endpoint_tree.py). Editing it by hand will be overwritten and will fail
the drift-guard test.

How to update (humans & agents)
-------------------------------
- The scope of a route is read from its `get_current_identity(required_permissions=[...])`
  dependency. To make a route's scope appear here, add that argument upstream.
- For routes whose scope is enforced in the service layer, edit the curated map
  `PATH_SCOPE_OVERRIDES` / `ACTOR_TYPE_OVERRIDES` in
  `src/jentic_one/shared/web/endpoint_scopes.py`.
- Then run `make endpoints` (regenerates this file + endpoints.json) and
  `make openapi` (regenerates the specs), and commit code + artifacts together.

Agents: treat `src/jentic_one/shared/web/endpoint_scopes.py` as the editable
source of truth, never this file.
-->

# Endpoint & scope reference

> **Generated file — do not edit by hand.** Produced by `make endpoints` from code. To correct an entry, edit `src/jentic_one/shared/web/endpoint_scopes.py` and regenerate (see [docs/reference/README.md](README.md)).

Every API endpoint grouped by its **typical caller**, then by surface, annotated with the **scope(s)** it requires.

> The grouping and the _Typical caller_ column are an **advisory hint** at who usually calls a route, inferred from the scope family. They are **not** an enforced restriction: access is gated by the **scope**, not the actor kind, so any actor holding the required scope can call the endpoint.

_Total endpoints: **150**._


## Agent-facing (typically agent / service-account / toolkit) (31)


### `apis`

| Method | Path | Scope(s) | Typical caller | Summary |
|---|---|---|---|---|
| GET | `/apis` | `apis:read` | agent | List Apis |
| GET | `/apis/{vendor}/{name}/{version}` | `apis:read` | agent | Get Api |
| GET | `/apis/{vendor}/{name}/{version}/openapi` | `apis:read` | agent | Get Api Spec |
| GET | `/apis/{vendor}/{name}/{version}/operations` | `apis:read` | agent | List Api Operations |
| GET | `/apis/{vendor}/{name}/{version}/overlays` | `apis:read` | agent | List Overlays |
| GET | `/apis/{vendor}/{name}/{version}/overlays/{overlay_id}` | `apis:read` | agent | Get Overlay |
| GET | `/apis/{vendor}/{name}/{version}/revisions` | `apis:read` | agent | List Api Revisions |
| GET | `/apis/{vendor}/{name}/{version}/revisions/{revision_id}` | `apis:read` | agent | Get Api Revision |
| GET | `/apis/{vendor}/{name}/{version}/revisions/{revision_id}/openapi` | `apis:read` | agent | Get Api Revision Spec |
| GET | `/apis/{vendor}/{name}/{version}/revisions/{revision_id}/operations` | `apis:read` | agent | List Api Revision Operations |
| GET | `/apis/{vendor}/{name}/{version}/security-schemes` | `apis:read` | agent | List security schemes for an API |

### `broker`

| Method | Path | Scope(s) | Typical caller | Summary |
|---|---|---|---|---|
| DELETE | `/{upstream_url}` | `capabilities:execute` | agent | Execute an upstream API operation |
| GET | `/{upstream_url}` | `capabilities:execute` | agent | Execute an upstream API operation |
| PATCH | `/{upstream_url}` | `capabilities:execute` | agent | Execute an upstream API operation |
| POST | `/{upstream_url}` | `capabilities:execute` | agent | Execute an upstream API operation |
| PUT | `/{upstream_url}` | `capabilities:execute` | agent | Execute an upstream API operation |

### `catalog`

| Method | Path | Scope(s) | Typical caller | Summary |
|---|---|---|---|---|
| GET | `/catalog` | `capabilities:read` | agent | List Catalog |
| GET | `/catalog/{api_id}` | `capabilities:read` | agent | Get Catalog Entry |
| GET | `/catalog/{api_id}/operations` | `capabilities:read` | agent | Preview Catalog Operations |
| POST | `/catalog/{api_id}:import` | `catalog:import` | agent | Import Catalog Entry |

### `events`

| Method | Path | Scope(s) | Typical caller | Summary |
|---|---|---|---|---|
| GET | `/events` | `events:read` | agent | List Events |
| GET | `/events/stream` | `events:read` | agent | Stream Events |
| GET | `/events/{event_id}` | `events:read` | agent | Get Event |

### `executions`

| Method | Path | Scope(s) | Typical caller | Summary |
|---|---|---|---|---|
| GET | `/executions` | `executions:read` | agent | List Executions |
| GET | `/executions/{execution_id}` | `executions:read` | agent | Get Execution |

### `inspect`

| Method | Path | Scope(s) | Typical caller | Summary |
|---|---|---|---|---|
| GET | `/inspect` | `apis:read` | agent | Inspect operation |

### `jobs`

| Method | Path | Scope(s) | Typical caller | Summary |
|---|---|---|---|---|
| GET | `/jobs` | `jobs:read` | agent | List Jobs |
| GET | `/jobs/{job_id}` | `jobs:read` | agent | Get Job |
| GET | `/jobs/{job_id}/result` | `jobs:read` | agent | Get Job Result |

### `oauth`

| Method | Path | Scope(s) | Typical caller | Summary |
|---|---|---|---|---|
| POST | `/oauth/mint` | _any authenticated_ | agent | Mint Endpoint |

### `search`

| Method | Path | Scope(s) | Typical caller | Summary |
|---|---|---|---|---|
| POST | `/search` | `apis:read` | agent | Search operations |

## Operator-facing (typically a human operator / admin) (43)


### `access-requests`

| Method | Path | Scope(s) | Typical caller | Summary |
|---|---|---|---|---|
| POST | `/access-requests/{request_id}:decide` | `agents:write` | operator | Decide access request items |

### `actors`

| Method | Path | Scope(s) | Typical caller | Summary |
|---|---|---|---|---|
| GET | `/actors` | `users:read` | operator | List Actors |

### `agents`

| Method | Path | Scope(s) | Typical caller | Summary |
|---|---|---|---|---|
| GET | `/agents` | `agents:read` | operator | List Agents |
| POST | `/agents` | `agents:write` | operator | Create Agent |
| DELETE | `/agents/{agent_id}` | `agents:write` | operator | Archive Agent |
| PATCH | `/agents/{agent_id}` | `agents:write` | operator | Update Agent |
| GET | `/agents/{agent_id}/api-key` | `agents:read` | operator | Get Agent Api Key Info |
| GET | `/agents/{agent_id}/api-key/history` | `agents:read` | operator | Get Agent Api Key History |
| GET | `/agents/{agent_id}/scopes` | `agents:read` | operator | Get Agent Scopes |
| PUT | `/agents/{agent_id}/scopes` | `agents:write` | operator | Replace Agent Scopes |
| POST | `/agents/{agent_id}/toolkits` | `agents:write` | operator | Bind Toolkit |
| DELETE | `/agents/{agent_id}/toolkits/{toolkit_id}` | `agents:write` | operator | Unbind Toolkit |
| POST | `/agents/{agent_id}:approve` | `agents:write` | operator | Approve Agent |
| POST | `/agents/{agent_id}:deny` | `agents:write` | operator | Deny Agent |
| POST | `/agents/{agent_id}:disable` | `agents:write` | operator | Disable Agent |
| POST | `/agents/{agent_id}:enable` | `agents:write` | operator | Enable Agent |
| POST | `/agents/{agent_id}:generate-api-key` | `agents:write` | operator | Generate Agent Api Key |
| POST | `/agents/{agent_id}:revoke-api-key` | `agents:write` | operator | Revoke Agent Api Key |

### `audit`

| Method | Path | Scope(s) | Typical caller | Summary |
|---|---|---|---|---|
| GET | `/audit` | `audit:read` | operator | List Audit Entries |
| GET | `/audit/{audit_id}` | `audit:read` | operator | Get Audit Entry |

### `catalog:refresh`

| Method | Path | Scope(s) | Typical caller | Summary |
|---|---|---|---|---|
| POST | `/catalog:refresh` | `org:admin` | operator | Refresh Catalog |

### `events`

| Method | Path | Scope(s) | Typical caller | Summary |
|---|---|---|---|---|
| PATCH | `/events/{event_id}` | `events:write` | operator | Acknowledge Event |

### `monitoring`

| Method | Path | Scope(s) | Typical caller | Summary |
|---|---|---|---|---|
| GET | `/monitoring/executions` | `org:admin` | operator | Get Execution Stats |
| GET | `/monitoring/usage` | `org:admin` | operator | Get Usage Stats |

### `service-accounts`

| Method | Path | Scope(s) | Typical caller | Summary |
|---|---|---|---|---|
| GET | `/service-accounts` | `service-accounts:read` | operator | List Service Accounts |
| POST | `/service-accounts` | `service-accounts:write` | operator | Create Service Account |
| DELETE | `/service-accounts/{service_account_id}` | `service-accounts:write` | operator | Archive Service Account |
| GET | `/service-accounts/{service_account_id}/scopes` | `service-accounts:read` | operator | Get Service Account Scopes |
| PUT | `/service-accounts/{service_account_id}/scopes` | `service-accounts:write` | operator | Replace Service Account Scopes |
| POST | `/service-accounts/{service_account_id}:approve` | `service-accounts:write` | operator | Approve Service Account |
| POST | `/service-accounts/{service_account_id}:deny` | `service-accounts:write` | operator | Deny Service Account |
| POST | `/service-accounts/{service_account_id}:disable` | `service-accounts:write` | operator | Disable Service Account |
| POST | `/service-accounts/{service_account_id}:enable` | `service-accounts:write` | operator | Enable Service Account |
| POST | `/service-accounts/{service_account_id}:generate-api-key` | `service-accounts:write` | operator | Generate Service Account Api Key |

### `users`

| Method | Path | Scope(s) | Typical caller | Summary |
|---|---|---|---|---|
| GET | `/users` | `users:read` | operator | List Users |
| POST | `/users` | `users:write` | operator | Create User |
| DELETE | `/users/{user_id}` | `users:write` | operator | Delete User |
| GET | `/users/{user_id}` | `users:read` | operator | Get User |
| PATCH | `/users/{user_id}` | `users:write` | operator | Update User |
| PUT | `/users/{user_id}/permissions` | `users:write` | operator | Set User Permissions |
| POST | `/users/{user_id}:disable` | `users:write` | operator | Disable User |
| POST | `/users/{user_id}:enable` | `users:write` | operator | Enable User |
| POST | `/users/{user_id}:reissue-invite` | `users:write` | operator | Reissue Invite |

## Any authenticated actor (59)


### `access-requests`

| Method | Path | Scope(s) | Typical caller | Summary |
|---|---|---|---|---|
| GET | `/access-requests` | _any authenticated_ | any | List access requests |
| POST | `/access-requests` | _any authenticated_ | any | File access request |
| GET | `/access-requests/{request_id}` | _any authenticated_ | any | Get access request |
| POST | `/access-requests/{request_id}:amend` | _any authenticated_ | any | Amend access request |
| POST | `/access-requests/{request_id}:withdraw` | _any authenticated_ | any | Withdraw access request |

### `admin`

| Method | Path | Scope(s) | Typical caller | Summary |
|---|---|---|---|---|
| GET | `/admin/config/providers` | `config:read` | any | List credential provider configs |
| GET | `/admin/config/providers/{name}` | `config:read` | any | Get a credential provider config |
| PUT | `/admin/config/providers/{name}` | `config:write` | any | Set a credential provider config |

### `agents`

| Method | Path | Scope(s) | Typical caller | Summary |
|---|---|---|---|---|
| GET | `/agents/{agent_id}` | _any authenticated_ | any | Get Agent |
| GET | `/agents/{agent_id}/toolkits` | _any authenticated_ | any | List Toolkits |

### `apis`

| Method | Path | Scope(s) | Typical caller | Summary |
|---|---|---|---|---|
| POST | `/apis` | `apis:write` | any | Import Apis |
| DELETE | `/apis/{vendor}/{name}/{version}` | `apis:write` | any | Delete Api |
| PATCH | `/apis/{vendor}/{name}/{version}` | `apis:write` | any | Update Api |
| POST | `/apis/{vendor}/{name}/{version}/overlays` | `apis:write` | any | Submit Overlay |
| DELETE | `/apis/{vendor}/{name}/{version}/overlays/{overlay_id}` | `apis:write` | any | Deprecate Overlay |
| PATCH | `/apis/{vendor}/{name}/{version}/overlays/{overlay_id}` | `apis:write` | any | Update Overlay |
| POST | `/apis/{vendor}/{name}/{version}/overlays/{overlay_id}:confirm` | `apis:write` | any | Confirm Overlay |
| DELETE | `/apis/{vendor}/{name}/{version}/revisions/{revision_id}` | `apis:write` | any | Delete Revision |
| POST | `/apis/{vendor}/{name}/{version}/revisions/{revision_id}:archive` | `apis:write` | any | Archive Revision |
| POST | `/apis/{vendor}/{name}/{version}/revisions/{revision_id}:promote` | `apis:write` | any | Promote Revision |

### `credentials`

| Method | Path | Scope(s) | Typical caller | Summary |
|---|---|---|---|---|
| GET | `/credentials` | `credentials:read`, `owner:credentials:read` | any | List credentials |
| POST | `/credentials` | `credentials:write` | any | Create credential |
| GET | `/credentials/providers` | `credentials:read`, `owner:credentials:read` | any | List credential providers |
| DELETE | `/credentials/{credential_id}` | `credentials:write` | any | Delete credential |
| GET | `/credentials/{credential_id}` | `credentials:read`, `owner:credentials:read` | any | Get credential |
| PATCH | `/credentials/{credential_id}` | `credentials:write` | any | Update or rotate credential |
| POST | `/credentials/{credential_id}/connect` | `credentials:write` | any | Begin OAuth connect flow |

### `jobs`

| Method | Path | Scope(s) | Typical caller | Summary |
|---|---|---|---|---|
| POST | `/jobs/{job_id}:cancel` | `jobs:write` | any | Cancel Job |

### `me`

| Method | Path | Scope(s) | Typical caller | Summary |
|---|---|---|---|---|
| GET | `/me` | _any authenticated_ | any | Get Me |

### `notes`

| Method | Path | Scope(s) | Typical caller | Summary |
|---|---|---|---|---|
| GET | `/notes` | _any authenticated_ | any | List Notes |
| POST | `/notes` | _any authenticated_ | any | Create Note |
| DELETE | `/notes/{note_id}` | _any authenticated_ | any | Delete Note |
| GET | `/notes/{note_id}` | _any authenticated_ | any | Get Note |
| PATCH | `/notes/{note_id}` | _any authenticated_ | any | Update Note |

### `oauth`

| Method | Path | Scope(s) | Typical caller | Summary |
|---|---|---|---|---|
| POST | `/oauth/introspect` | _any authenticated_ | any | Introspect Endpoint |
| POST | `/oauth/revoke` | _any authenticated_ | any | Revoke Endpoint |

### `permissions`

| Method | Path | Scope(s) | Typical caller | Summary |
|---|---|---|---|---|
| GET | `/permissions` | _any authenticated_ | any | List Permissions |

### `register`

| Method | Path | Scope(s) | Typical caller | Summary |
|---|---|---|---|---|
| DELETE | `/register/{agent_id}` | _any authenticated_ | any | Delete Registration Endpoint |
| GET | `/register/{agent_id}` | _any authenticated_ | any | Poll Status Endpoint _(Authenticated with the Registration-Access-Token issued at registration (RFC 7592), not a platform bearer token.)_ |
| PUT | `/register/{agent_id}` | _any authenticated_ | any | Update Registration Endpoint |

### `service-accounts`

| Method | Path | Scope(s) | Typical caller | Summary |
|---|---|---|---|---|
| GET | `/service-accounts/{service_account_id}` | _any authenticated_ | any | Get Service Account |

### `toolkits`

| Method | Path | Scope(s) | Typical caller | Summary |
|---|---|---|---|---|
| GET | `/toolkits` | `toolkits:read`, `owner:toolkits:read` | any | List toolkits |
| POST | `/toolkits` | `toolkits:write` | any | Create toolkit |
| DELETE | `/toolkits/{toolkit_id}` | `toolkits:write` | any | Delete toolkit |
| GET | `/toolkits/{toolkit_id}` | `toolkits:read`, `owner:toolkits:read` | any | Get toolkit |
| PATCH | `/toolkits/{toolkit_id}` | `toolkits:write` | any | Update toolkit |
| GET | `/toolkits/{toolkit_id}/agents` | `toolkits:read`, `owner:toolkits:read` | any | List agents bound to toolkit |
| GET | `/toolkits/{toolkit_id}/credentials` | `toolkits:read`, `owner:toolkits:read` | any | List toolkit credential bindings |
| POST | `/toolkits/{toolkit_id}/credentials` | `toolkits:write` | any | Bind credential to toolkit |
| DELETE | `/toolkits/{toolkit_id}/credentials/{credential_id}` | `toolkits:write` | any | Unbind credential from toolkit |
| GET | `/toolkits/{toolkit_id}/credentials/{credential_id}/permissions` | `toolkits:read`, `owner:toolkits:read` | any | List binding permission rules |
| PATCH | `/toolkits/{toolkit_id}/credentials/{credential_id}/permissions` | `toolkits:write` | any | Patch binding permission rules |
| PUT | `/toolkits/{toolkit_id}/credentials/{credential_id}/permissions` | `toolkits:write` | any | Replace binding permission rules |
| GET | `/toolkits/{toolkit_id}/keys` | `toolkits:read`, `owner:toolkits:read` | any | List toolkit keys |
| POST | `/toolkits/{toolkit_id}/keys` | `toolkits:write` | any | Issue toolkit key |
| DELETE | `/toolkits/{toolkit_id}/keys/{key_id}` | `toolkits:write` | any | Revoke toolkit key |
| PATCH | `/toolkits/{toolkit_id}/keys/{key_id}` | `toolkits:write` | any | Update toolkit key |

### `users`

| Method | Path | Scope(s) | Typical caller | Summary |
|---|---|---|---|---|
| GET | `/users/me` | _any authenticated_ | any | Get current user |
| POST | `/users/me:change-password` | _any authenticated_ | any | Change own password |

## Public (unauthenticated) (17)


### `.well-known`

| Method | Path | Scope(s) | Typical caller | Summary |
|---|---|---|---|---|
| GET | `/.well-known/jwks.json` | _public — no auth_ | — | JSON Web Key Set |
| GET | `/.well-known/oauth-authorization-server` | _public — no auth_ | — | OAuth authorization server metadata |

### `admin`

| Method | Path | Scope(s) | Typical caller | Summary |
|---|---|---|---|---|
| GET | `/admin/health` | _public — no auth_ | — | Health |

### `auth`

| Method | Path | Scope(s) | Typical caller | Summary |
|---|---|---|---|---|
| GET | `/auth/health` | _public — no auth_ | — | Auth health |
| POST | `/auth/login` | _public — no auth_ | — | Log in |

### `authorize`

| Method | Path | Scope(s) | Typical caller | Summary |
|---|---|---|---|---|
| GET | `/authorize` | _public — no auth_ | — | Authorize Endpoint |

### `control`

| Method | Path | Scope(s) | Typical caller | Summary |
|---|---|---|---|---|
| GET | `/control/health` | _public — no auth_ | — | Control health |

### `credentials`

| Method | Path | Scope(s) | Typical caller | Summary |
|---|---|---|---|---|
| GET | `/credentials/oauth/callback` | _public — no auth_ | — | OAuth connect callback |

### `error`

| Method | Path | Scope(s) | Typical caller | Summary |
|---|---|---|---|---|
| GET | `/error` | _public — no auth_ | — | Error Page |

### `health`

| Method | Path | Scope(s) | Typical caller | Summary |
|---|---|---|---|---|
| GET | `/health` | _public — no auth_ | — | Health |

### `oauth`

| Method | Path | Scope(s) | Typical caller | Summary |
|---|---|---|---|---|
| GET | `/oauth/callback` | _public — no auth_ | — | Oauth Callback |
| POST | `/oauth/token` | _public — no auth_ | — | Token Endpoint |

### `ready`

| Method | Path | Scope(s) | Typical caller | Summary |
|---|---|---|---|---|
| GET | `/ready` | _public — no auth_ | — | Broker readiness (saturation-aware) |

### `register`

| Method | Path | Scope(s) | Typical caller | Summary |
|---|---|---|---|---|
| POST | `/register` | _public — no auth_ | — | Register Endpoint |

### `registry`

| Method | Path | Scope(s) | Typical caller | Summary |
|---|---|---|---|---|
| GET | `/registry/health` | _public — no auth_ | — | Registry health |

### `users:create-admin`

| Method | Path | Scope(s) | Typical caller | Summary |
|---|---|---|---|---|
| POST | `/users:create-admin` | _public — no auth_ | — | Create first admin (one-time setup) |

### `users:redeem-invite`

| Method | Path | Scope(s) | Typical caller | Summary |
|---|---|---|---|---|
| POST | `/users:redeem-invite` | _public — no auth_ | — | Redeem invite |
