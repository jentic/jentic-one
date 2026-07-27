# Current-state map (grounded)

> Factual snapshot of the mechanisms this analysis reasons about, with
> `file:line` citations. No recommendations here — see [`03`](03-mitigations.md).

## Broker: how agent requests are authorised

- Catch-all proxy route `proxy()` at `broker/web/routers/execute.py:846`; dependency
  chain `require_execute_within_rate_limit → require_execute_scope →
  require_broker_identity` (`broker/web/deps.py:98,119,144`).
- Token extraction: `x-jentic-api-key` header first, else `Authorization: Bearer`
  (`shared/web/auth.py:11-28`). Validated by a `CompositeTokenValidator`
  dispatching on prefix — `jntc_live_` toolkit keys, `jak_`/`sak_` API keys,
  3-segment JWTs, else opaque (`broker/services/auth/token_validation.py:136-159`).
- Dev JWT verifier is HS256 with an explicit `frozenset({"HS256"})` allowlist to
  block `alg:none` downgrade (`token_validation.py:32,75-84`).
- **Credential choice is not caller-controlled.** Toolkit is derived as the
  intersection of the agent's admin-DB `agent_toolkit_bindings` and control-DB
  toolkits whose bound credential covers the *discovered* API identity
  (`broker/repos/toolkit_binding_resolver.py:90-136`). Credential is chosen by
  coverage + most-specific-wins (`broker/services/credentials/resolver.py:66-145`).
- **PBAC, default-deny** on every proxied call: `rule_evaluator.evaluate(...)`,
  first-match-wins, empty rule set → DENY (`rule_evaluator.py:128-156,206-208`;
  enforcement `execute.py:547-592`). SSRF egress allowlist on the reconstructed
  URL (`execute.py:483-487,637-641`).

## Credentials: encryption + key location

- **AES-256-GCM** via `cryptography`'s `AESGCM`, 12-byte random nonce, blob format
  `<key_id>:<base64(nonce‖ct‖tag)>` (`shared/crypto/encryption.py:12,24-25,49-54`).
  This is the only module allowed to import `cryptography` (arch-test enforced).
- Decryption happens **only inside the Broker at injection time**
  (`broker/core/injection.py:47-87`, oauth `refresh.py`). The decrypted value goes
  straight into the outbound request and is **never** in the response
  (`_assemble_response`, `execute.py:746-762`).
- Secret is echoed to the caller **once, only on create** (the value they just
  submitted; `control/…/credentials.py:318-321`); GET/list return redacted
  previews only (last 3 chars). **No reveal/decrypt/export endpoint exists.**
- **Keyset source:** config model `EncryptionConfig` (`shared/config.py:258-276`),
  loaded from YAML and/or `JENTIC__*` env overrides (`config.py:881-929`). Docs
  direct operators to supply it via env/secret-manager, out of files and out of
  the DB (`hardening.md:109-111,187`). The key is **never** read from the DB.
- Keyset rotation: `active_id` names the encrypt key; decrypt selects by the
  blob's embedded `key_id`, so retired keys keep decrypting old ciphertext
  (`encryption.py:49-65`).

## Admin / operator + UI auth

- **Email + password → stateless HS256 JWT (Bearer)**, signed with
  `admin.auth.jwt_secret`; claims include the fully-expanded `permissions` set;
  1-hour TTL (`admin/services/auth_service.py:54-151`; `shared/auth/tokens.py:11-19`;
  `config.py:185`). No server-side session store.
- **Token lives in browser `localStorage` key `jentic-one.access_token`**
  (`ui/src/shared/api/token-store.ts:14,21-42`), sent as `Authorization: Bearer`
  (`ui/src/shared/api/client.ts:29`). Logout is client-side disposal only.
- **No CSRF token and none needed** — auth is a header Bearer, not an ambient
  cookie; no `set_cookie`/`SameSite` anywhere in the Python surfaces. (The one
  cookie-adjacent path is the OAuth connect callback, deliberately unauth'd and
  bound by a signed state JWT — `control/web/routers/credentials.py:210-234`.)
- **UI is same-origin under `/app` on the admin surface**; no CORS middleware on
  admin/auth/control (only on the broker) (`shared/web/static.py:12-19,140-205`;
  `admin/web/app.py:36,118-131`; `shared/web/app_factory.py:401,489`).
- **First-run bootstrap:** `POST /users:create-admin` is unauthenticated but
  self-closing (410 once any user exists), grants `org:admin`, and auto-logs-in
  (`admin/web/routers/auth.py:96-125`).
- **What a hijacked `org:admin` session can do:** `org:admin` transitively implies
  every write scope (`admin/core/permissions.py:52-81`). It can create credentials,
  create/approve agents and **replace their scopes with no subset check**
  (`auth/services/agent_service.py:374-405`), generate agent API keys / client
  secrets (ownership check bypassed for `org:admin`,
  `agent_auth_service.py:101,122,153`), issue toolkit API keys whose plaintext is
  returned once, and approve access requests. It **cannot** read/export existing
  stored secrets (no such endpoint). So the escalation is *provision-and-use*, not
  *dump*: mint a fully-scoped agent/service-account and obtain a usable credential
  for it.

## Agent identity + token minting

- Agent generates an **Ed25519 keypair locally**, stored `0600` under
  `~/.jentic/<profile>`; publishes only the public JWKS
  (`cli/internal/profile/store.go:30,46,116,131-183`).
- **Dynamic Client Registration** (`POST /register`) is **unauthenticated** and
  self-service, but the agent starts inactive and needs operator approval before
  it can mint (`auth/services/registration_service.py:59-158`;
  `auth/web/routers/registration.py:38-51`; rejected until approved,
  `assertion_service.py:92`).
- Agent mints its **own** tokens via an EdDSA JWT-bearer assertion (RFC 7523) —
  needs the private key, no admin auth (`auth/services/assertion_service.py:64-137`).
  Tokens are opaque `at_`/`rt_`, SHA-256 at rest, rotation + reuse detection
  (`token_service.py:24-33,96-219`). Scopes resolve **live** from
  `ActorScopeGrant` rows for non-ephemeral tokens (`token_service.py:294-347`).
- **Any local process as the same user can read the `0600` key/token files** and
  mint/use agent tokens directly.

## One-line install / default local posture

- `install.sh` builds both Go CLIs into `~/.jentic/bin` as the **invoking user**
  (no separate/service user, no daemon account), then chains into `jenticctl
  install` on a TTY (`install.sh:114-153,683-726`).
- Wizard binds **loopback** (`127.0.0.1:8000`, broker `:8100`) for the source
  runtime; **`0.0.0.0` inside the Docker container**, published to the host
  (`install/draft.go:154-156`; `install/render.go:217-221`).
- Runs app/broker as **background processes owned by the installing user**
  (`cmd/install.go:393-419`) — no dedicated user.
- Secrets (**AES-256 encryption key**, `jwt_secret`, invite pepper, connect state
  secret) are **freshly generated and written into `~/.jentic/jentic-one.yaml` at
  `0600`** (`install/secrets.go:22-53`; `cmd/install.go:48,123-140`;
  `install/render.go:72-97,238-253`).
- Admin login (HS256 JWT) is **on by default** with a real random secret; there is
  no no-auth mode. Production placeholder-secret guard only fires when
  `JENTIC_ENV=production` (`config.py:34-50,184-193`).

### The one-sentence takeaway

In the default local posture, everything the boundary depends on — the AES key,
the `jwt_secret`, the agent's Ed25519 key, and the agent's bearer tokens — lives
in `0600` files under `~/.jentic` owned by the **same user the agent runs as**.
File permissions are the *only* control between the agent and total boundary
bypass, and `0600` does not stop the owning user.
