# Troubleshoot Jentic One — agent runbook

Symptom-keyed fixes for installs created by [install.md](install.md). The
rules there apply here too — especially rule 3: if the symptom is not in this
file, **report to the human and stop**; do not improvise a recovery.

## CLI download fails (no URL resolved, or 404)

A release tag can exist before CI finishes attaching its binaries; the
install snippet already skips asset-less releases by matching on
`browser_download_url`. If it still fails, the assets are genuinely
unavailable right now. **Do not build from source, do not go looking for
installer scripts, and do not construct download URLs by hand.** Report the
error and stop.

## `${VER}`/`${PGPASS}` resolve empty; Postgres exits with "superuser password is not specified"

`docker compose` interpolates those from `~/.jentic/.env` (it sits next to
the compose file); shell variables from earlier steps do not survive between
command blocks. If `.env` is missing or incomplete, recreate it:
`VER=<installed version>` (`jentic --version`) and, for Postgres, the
existing `PGPASS`.

If Postgres already tried to initialise with an empty password, the volume is
half-initialised. On a **fresh** install:

```bash
docker compose -p jentic -f ~/.jentic/docker-compose.yaml down
docker volume rm jentic_db-data
```

then fix `.env` and re-run migrations (install.md Step 6).

## Postgres schemas missing, or someone suggests an init script

Never add `/docker-entrypoint-initdb.d/…` scripts to the `db` service. The
migration run creates the `registry`/`control`/`admin` schemas itself,
idempotently. Postgres init scripts run exactly once, on an empty volume, and
a script that fails mid-init leaves a broken volume that never gets its
schemas — that is why this system deliberately avoids them. If a broken init
already happened on a fresh install, remove the `jentic_db-data` volume (see
above) and re-run migrations.

## Image pull denied

The package may be private on this network: `docker login ghcr.io` or mirror
the image, then retry. Digest pinning and cosign verification:
[installation/README.md](../installation/README.md).

## Migrations fail

- The data volume was created **by this run** (fresh install): discard it so
  the retry starts clean, fix the cause, and re-run —
  `docker compose -p jentic -f ~/.jentic/docker-compose.yaml down`
  then `docker volume rm jentic_jentic-data` (Postgres: `jentic_db-data`).
- The volume **pre-existed** (reinstall): do **not** remove it — it may hold
  real data. Stop and report the migration error to the human.

## `address already in use` on port 8000 or 8100 at start

`docker compose … up` fails because another process (or another container)
already listens on a published port. Diagnose — find what holds the port:

```bash
lsof -iTCP:8000 -sTCP:LISTEN -n -P
lsof -iTCP:8100 -sTCP:LISTEN -n -P
docker ps --filter publish=8000 --filter publish=8100
```

Fix: stop the conflicting process (or `docker stop <container>`) and re-run
`docker compose -p jentic -f ~/.jentic/docker-compose.yaml up -d`. If the
port must stay taken, change the published host port in
`~/.jentic/docker-compose.yaml` (the left side of `"127.0.0.1:8000:8000"`) —
but note the app port is part of `auth.canonical_base_url`, so registered
agents and the URLs you hand the human change with it. Prefer freeing the
port.

## App or broker never becomes healthy

A cold start (first boot, empty database) can take tens of seconds — poll,
don't probe once. If it still never answers, read the logs and report:

```bash
docker compose -p jentic -f ~/.jentic/docker-compose.yaml logs
```

## `setup_required` flaps

A single `false` right after startup can be a DB-warmup blip. Trust it only
when two reads ~2 s apart agree.

## `invalid_grant` on register or token exchange

The token audience is an **exact string match** against
`auth.canonical_base_url`. Register with exactly that URL — `127.0.0.1` vs
`localhost` is the classic mismatch on a local install.

## `jentic register` times out (exit 3, `TIMEOUT_PENDING`)

The registration is saved. Have the human approve at `/app/agents`, then
re-run the same command — it resumes idempotently.

## `jentic execute` fail-closes against a remote install

Registering with a remote install requires an explicit
`--broker-url https://<broker-host>` — it is never derived from the
control-plane URL. Re-register with both URLs.

## SQLite "disk I/O error"

The database must live on a named volume, not a host bind mount — Docker
Desktop's file sharing lacks the locking semantics SQLite needs.

## Import fails: server URL can't be made absolute

The broker routes every call by its upstream host, so ingest makes each
relative server URL (`url: /api/v3`, as in the stock Petstore) absolute. It
resolves against the URL the description was retrieved from, after redirects:
importing `https://petstore3.swagger.io/api/v3/openapi.json` gives
`https://petstore3.swagger.io/api/v3`. The import job's result lists the
servers it resolved (`server_url_changes`) — check the host before you bind a
credential to the API. A description that declares no `servers` at all imports
as inspect-only: `execute` refuses its operations with `RESOLVE_FAILED`.

A manual import (URL, paste, or upload) is refused when a `servers` list is
left with no absolute URL. The job error names the reason:

| Error starts with | Cause | Fix |
| --- | --- | --- |
| `relative server URL has no base` | Pasted or uploaded inline: there is no document URL. | Import from the description's URL, or make the server URL absolute. |
| `spec host serves files` | The description is served from a file host (listed below), not from the API. Resolving would send the API's credentials to the file host. | Make the server URL absolute (`https://api.example.com/v3`), or import the catalog entry instead. |
| `server URL looks like a host; add https://` | `api.example.com/v1`, `localhost:8080`, or `[::1]:8080/v1` is a host without a scheme, not a path. | Add the scheme. |
| `server URL starts with a file name` | A path like `index.php/api` can't be told from a mistyped host, so it is refused too. | Write it as a path from the root (`/index.php/api`), or make it absolute. |
| `spec URL redirected to http` | An https URL redirected to plaintext http. | Import from the final https URL, or make the server URL absolute. |
| `server URL must be http(s)`, `… only a query or fragment`, `… starts with a relative variable`, `… has an empty host` | The URL can't name an http(s) API host (`file:`, `?v=3`, `{basePath}/v1` with a relative default, `//`). | Make the server URL absolute. |

A description is on a file host when the URL it was requested from, or the URL
its redirects ended on, matches one of these:

| Kind | Hosts |
| --- | --- |
| Git forges | `github.com`, `gist.github.com`, `raw.github.com`, `codeload.github.com`, `*.githubusercontent.com` (`raw.`, `gist.`, `media.`, `objects.`, …), `gitlab.com`, `bitbucket.org`, and any URL whose path has GitLab's `/-/raw/` or `/-/blob/` (self-hosted GitLab) |
| Repository CDNs and registries | `*.jsdelivr.net`, `*.githack.com` (`raw.`, `rawcdn.`, …), `unpkg.com`, `app.swaggerhub.com`, `api.swaggerhub.com` |
| Amazon S3 | `s3.amazonaws.com`, `s3.<region>.amazonaws.com`, `s3-<region>.amazonaws.com`, `<bucket>.s3.amazonaws.com`, `<bucket>.s3.<region>.amazonaws.com`, and the dual-stack, website, accelerate, and `.amazonaws.com.cn` forms |
| Google Cloud and Firebase Storage | `storage.googleapis.com`, `<bucket>.storage.googleapis.com`, `storage.cloud.google.com`, `firebasestorage.googleapis.com`, `*.appspot.com` |
| Azure Storage | `*.blob.core.windows.net`, `*.dfs.core.windows.net`, `*.web.core.windows.net`, and their China and US Government cloud equivalents |
| File sharing | `dropbox.com`, `*.dropbox.com`, `*.dropboxusercontent.com` |
| Static sites | `*.github.io`, `*.gitlab.io` |
| The catalog mirror | the host of `catalog.manifest_url`, and every host the cached catalog manifest serves spec files from |

Host matching ignores case, a trailing dot, and the port. General-purpose CDNs
and app platforms (CloudFront, Netlify, Vercel, Firebase Hosting) are not on
the list, since they often serve the API itself. An App Engine app on
`*.appspot.com` is refused along with Firebase's buckets; give it absolute
servers. No list covers every file host. For one that isn't listed, the
remaining protection is that a manual import always lands as a draft that must
be promoted: check the resolved host in `server_url_changes` before you promote
it or bind a credential to the API.

Unresolvable entries next to an absolute server in the same list are dropped
instead, and listed in `server_url_changes`. Catalog imports, catalog updates,
and overlay materializations never fail on this: an unresolvable server stays
as declared and the operations it covers are inspect-only. An overlay
materialization never resolves a relative server at all, since the person
confirming the overlay never sees the host it would get. Fix such an API with
an [overlay](../guides/overlays.md) that sets an absolute server, or re-import
it as described below.

A resolved server is the host the description was imported from. That host
passed the import egress policy (`ingest.egress`), but `execute` checks
`broker.egress` separately: for an internal host, list it in
`broker.egress.allowed_internal_domains` as well as
`ingest.egress.allowed_internal_domains`.

**APIs stored with host-relative servers.** An API imported before ingest
resolved relative servers keeps host-relative operations that `execute`
refuses with `RESOLVE_FAILED` ("has no upstream host"). Import the description
from its URL again (`jentic apis import <url>`) and promote the new draft
(`jentic apis promote <vendor/name/version> <revision_id>`); the new revision
stores the resolved servers, so it doesn't collide with the live one. If
credentials are bound to the API, promoting needs `credentials:write`, since
the API's host changes.

## `credential_undecryptable` (424) on execute

The stored data is intact; the key is wrong. A credential row is decrypted
with the keyset entry named inside its ciphertext, so this fires when
`credentials.encryption` in `~/.jentic/jentic-one.yaml` changed since the
credential was written — a regenerated config, a keyset entry removed after
rotation, or a restore that brought back data without the matching keys.
Restore the original `credentials.encryption` block (from the config backup
that [backup-restore.md](../operations/backup-restore.md) pairs with every
data snapshot) and restart. Never "fix" this by re-generating keys — that
makes every stored credential permanently unreadable. If the keys are truly
lost, the humans must re-enter the affected credentials.

To answer "which credentials are affected" without touching a secret: the
key id is a plaintext `<key_id>:` prefix on every ciphertext column, so this
prints only key ids and counts (Postgres; run against the **control**
database — on SQLite replace `split_part(col, ':', 1)` with
`substr(col, 1, instr(col, ':') - 1)`):

```sql
SELECT 'customer_api_keys' AS tbl, split_part(encrypted_key, ':', 1) AS key_id, count(*)
  FROM control.customer_api_keys GROUP BY 2
UNION ALL SELECT 'basic_credentials', split_part(encrypted_password, ':', 1), count(*)
  FROM control.basic_credentials GROUP BY 2
UNION ALL SELECT 'token_value_credentials', split_part(encrypted_token_value, ':', 1), count(*)
  FROM control.token_value_credentials GROUP BY 2
UNION ALL SELECT 'oauth_client_credentials', split_part(encrypted_client_secret, ':', 1), count(*)
  FROM control.oauth_client_credentials GROUP BY 2
UNION ALL SELECT 'oauth_tokens.access', split_part(encrypted_access_token, ':', 1), count(*)
  FROM control.oauth_tokens GROUP BY 2
UNION ALL SELECT 'oauth_tokens.refresh', split_part(encrypted_refresh_token, ':', 1), count(*)
  FROM control.oauth_tokens WHERE encrypted_refresh_token IS NOT NULL GROUP BY 2
UNION ALL SELECT 'sigv4.secret', split_part(encrypted_secret_access_key, ':', 1), count(*)
  FROM control.sigv4_credentials GROUP BY 2
UNION ALL SELECT 'sigv4.session', split_part(encrypted_session_token, ':', 1), count(*)
  FROM control.sigv4_credentials WHERE encrypted_session_token IS NOT NULL GROUP BY 2;
```

Every `key_id` the result names must exist in `credentials.encryption`'s
`entries` — any that don't are the credentials failing with 424.

## Database connection lost/refused at runtime (Postgres shape)

The app or broker starts returning errors and the logs show connection
refused/reset to `db:5432`. Diagnose — check the `db` container and probe it
directly:

```bash
docker compose -p jentic -f ~/.jentic/docker-compose.yaml ps
docker compose -p jentic -f ~/.jentic/docker-compose.yaml logs db
docker compose -p jentic -f ~/.jentic/docker-compose.yaml exec db pg_isready -U postgres -d jentic
```

Fix, by what you find:

- `db` exited or unhealthy (OOM-killed, host reboot):
  `docker compose -p jentic -f ~/.jentic/docker-compose.yaml restart db`,
  then confirm `pg_isready` answers and the app recovers.
- Auth failures in the logs (`password authentication failed`): `PGPASS` in
  `~/.jentic/.env` no longer matches the password the `jentic_db-data`
  volume was initialised with — restore the original value; never "fix" it
  by changing `POSTGRES_PASSWORD` (it only applies at first initialisation).
- `pg_isready` fine but the app still fails: recreate the app/broker so they
  rejoin the compose network —
  `docker compose -p jentic -f ~/.jentic/docker-compose.yaml up -d --force-recreate app broker`.

## No human available at the first-admin gate (CI, fleet installs)

An operator can create the first admin non-interactively, password piped on
stdin (never argv). Replace `__REPLACE__` with a value sourced from
your secret store or CI secret — never a literal in a script or transcript.
(The placeholder is deliberately 11 characters — one short of the 12-char
minimum — so pasting the block verbatim fails validation instead of
creating an admin account on a published, greppable password.)

```bash
printf '%s' "__REPLACE__" | docker compose -p jentic -f ~/.jentic/docker-compose.yaml \
  run --rm -T app python -m jentic_one create-admin --email <email>
```

Prefer the browser gate (`/app/setup`) whenever a human is present.
