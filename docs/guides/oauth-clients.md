# Register an OAuth client

An OAuth client represents a third-party application that authenticates users
through Jentic One using the Authorization Code flow with PKCE (S256). Clients
are **confidential** — a `client_id` and `client_secret` are issued at
registration, and the secret must be presented alongside the authorization code
when exchanging at `/oauth/token`.

## Creating a client via the UI

1. Open **Settings** in the operator UI (`/app`).
2. Click **Add OAuth client**.
3. Fill in:
   - **Name** — identifies the client in the list (e.g. `my-app-production`).
   - **Redirect URIs** — one per line. Include every environment the app
     runs in (dev, staging, prod). Each URI must be HTTPS in production;
     `http://` is only accepted for the loopback hosts `localhost`,
     `127.0.0.1`, and `::1` (RFC 8252).
   - **Allowed scopes** — pick from the live permission catalogue; the
     wildcard `*` scope resets the restriction to unrestricted. Leaving
     the picker empty denies all non-OIDC scopes.
4. Click **Create**. The generated `client_id` and **one-time
   `client_secret`** are shown. The secret is only ever shown once — copy it
   and configure it in the third-party application immediately.

## Creating a client via the API

```
POST /admin/oauth-clients
```

Requires the `oauth-clients:write` permission (`org:admin` implies it).

```json
{
  "name": "my-app-production",
  "redirect_uris": ["https://app.example.com/oauth/callback"],
  "description": "Production deployment",
  "allowed_scopes": ["openid", "agents:write", "toolkits:read"]
}
```

| Field | Required | Notes |
|-------|----------|-------|
| `name` | yes | Max 255 characters |
| `redirect_uris` | yes | 1–20 URIs, max 2048 characters each |
| `description` | no | Free text |
| `allowed_scopes` | no | If set, the client cannot request scopes outside this list. An empty list denies all non-OIDC scopes. `null` means unrestricted. |

The 201 response includes the generated `client_id`, the internal `id` (used
for admin CRUD), and the one-time `client_secret` — store the secret securely;
it is never returned again.

## Rotating the secret

```
POST /admin/oauth-clients/{id}/rotate-secret
```

Immediately invalidates the previous secret. The new secret is returned once.
Deploy it to the client before rotating, or clients will fail authentication.

## Updating and deactivating

```
PATCH /admin/oauth-clients/{id}
```

Pass only the fields you want to change. `allowed_scopes` has three modes:

- omit the field → no change
- `[]` → deny-all (client cannot request any non-OIDC scopes)
- `["*"]` → reset to unrestricted (clear the restriction)

To soft-delete a client:

```
DELETE /admin/oauth-clients/{id}
```

Sets `active=false`. The client can no longer start authorization flows, and
outstanding access/refresh tokens issued by it stop resolving on both the auth
surface and the broker data plane on the next request. Reactivate by patching
`active: true`.

## Consent

Third-party clients always show the consent screen before authorization codes
are minted, regardless of the `require_consent` field on the client row. The
consent flow uses an opaque server-side handle (5-minute TTL, single-use); no
user identity or scope information leaks into the URL. If the user denies, no
local account is provisioned — provisioning is deferred until after approve.

## How the client is used

Once registered, the third-party application initiates the OAuth flow by
redirecting to:

```
GET /authorize
  ?response_type=code
  &client_id=<client_id>
  &redirect_uri=<uri>
  &code_challenge=<challenge>
  &code_challenge_method=S256
  &scope=openid+agents:write
  &state=<state>
```

The user authenticates, optionally approves the requested scopes on the consent
screen, and is redirected back to the registered `redirect_uri` with an
authorization code. The application exchanges the code (with the PKCE verifier
and the client secret) at `/oauth/token`:

```
POST /oauth/token
Content-Type: application/x-www-form-urlencoded

grant_type=authorization_code
&code=<code>
&code_verifier=<verifier>
&redirect_uri=<uri>
&client_id=<client_id>
&client_secret=<client_secret>
```

The client credentials can also be sent as HTTP Basic auth (RFC 6749 §2.3.1).

## Refresh

```
POST /oauth/token
grant_type=refresh_token
&refresh_token=<rt_...>
&client_id=<client_id>
&client_secret=<client_secret>
```

RFC 6749 §6 client authentication is enforced on the refresh grant for
confidential clients. A mismatched `client_id` on refresh revokes the entire
token family.

## Scope ceiling

The client's `allowed_scopes` acts as a ceiling at every token verdict:
`/oauth/introspect`, the auth surface's resolver, and the broker's in-process
resolver all intersect the token's scopes with the client's current ceiling.
Tightening `allowed_scopes` on a live client immediately narrows the effective
permissions of its outstanding tokens.
