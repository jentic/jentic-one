# Install Jentic One — agent runbook

You are an AI agent installing Jentic One for a human. This runbook shows how
to install Jentic (App, Broker, Database). Two steps are **human gates** — you must stop,
hand the human a URL, and wait; they are marked `HUMAN GATE`. (A hardened
install adds a third gate at Step 3.)

Related files: [operate.md](operate.md) (start/stop/upgrade/uninstall),
[troubleshoot.md](troubleshoot.md) (when a step fails),
[harden.md](harden.md) (read before this install touches a real credential),
[use.md](use.md) (what to do once installed).

## Rules for the installing agent

1. **Never regenerate secrets over an existing install.** If
   `~/.jentic/jentic-one.yaml` already exists, this is a reinstall: keep that
   file (especially the `credentials.encryption` block) and `~/.jentic/.env`,
   and skip to [Step 6](#6-run-database-migrations). A rotated encryption key
   silently makes every stored credential unreadable.
2. **Do not print secret values** into chat, logs, or shell history beyond the
   generated files themselves. Generate secrets with command substitution, not
   by echoing them. If the human chose the
   [hardened install](#hardened-install--the-human-holds-the-secrets), never
   read the secret-bearing files at all.
3. **Do not invent flags, endpoints, or fallback paths.** If a command from
   this runbook fails, check [troubleshoot.md](troubleshoot.md); if the
   symptom is not there, **stop and report to the human** — never improvise a
   recovery. For any `jentic` command not written here, read
   `jentic <command> --help` first.
4. **Shell variables do not survive between steps.** Each command block may
   run in a fresh shell session. Anything a later step needs
   (`VER`, `PGPASS`) is persisted to `~/.jentic/.env` when first set —
   `docker compose` reads that file automatically because it sits next to the
   compose file. Never rely on a variable exported in an earlier block.
5. Ask the human the four questions in Step 0 before writing anything, unless
   they already told you.

## 0. Decisions (ask the human)

| Question | Default | Notes |
| -------- | ------- | ----- |
| Database: SQLite or Postgres? | SQLite | SQLite is fine for a single-host install. For an external/production Postgres follow [docker.md](../installation/docker.md) instead. |
| Reachable from other machines, or this machine only? | This machine only (`127.0.0.1`) | Anything else → read [harden.md](harden.md) first; a LAN bind publishes the app, broker, and UI to the network. |
| Enable anonymous usage telemetry? | Off | If yes, the config gets `enabled: true` plus a random `instance_id` (UUID) and `host_os`; if no, an explicit `enabled: false` records the decision. |
| Is it acceptable that I (the installing agent) could read the instance secrets? | Ask — do not assume | The generated secrets land in files my shell writes and my OS user can read. Fine for trying things out with throwaway keys. If the answer is **no** (real credentials will be stored), follow the [hardened install](#hardened-install--the-human-holds-the-secrets) variant of Step 3. |

The rest of this runbook assumes the defaults; the Postgres and hardened
variants are given inline where they differ.

### Hardened install — the human holds the secrets

If the human said **no** to the last question, the goal is that no secret
value ever enters your shell, your context, or your transcript. Be honest
with the human about the limit first:

> As long as I run as the same OS user that owns `~/.jentic`, I *could* read
> the secret files afterwards — file permissions can't stop that. This
> variant keeps secrets out of everything I generate, see, and log, and we
> can verify that from the transcript. For a hard guarantee, a human (or a
> separate OS user) runs the secret steps — see
> [harden.md](harden.md#the-one-rule-that-dominates-everything).

Then apply these changes, marked `HARDENED` at the affected steps:

- **Step 3 becomes a human gate:** you write the config with placeholder
  markers; the human runs a one-liner that fills in the secrets (and, on
  Postgres, `PGPASS`) — the values only ever exist in their shell.
- **Never read the secret-bearing files back** (`jentic-one.yaml`,
  `~/.jentic/.env`). To inspect the config, use
  `grep -v -E 'jwt_secret|pepper|material|state_secret|password|PGPASS'`.

Every other step is unchanged — none of them touch secret values.

## 1. Preflight

Check all of these before writing any file. Stop and report to the human if
one fails.

```bash
docker info >/dev/null           # daemon is running (not just installed)
docker compose version           # compose v2 is available
curl --version >/dev/null && openssl version >/dev/null && command -v lsof >/dev/null
# Ports 8000 (app) and 8100 (broker) must be free (the lsof check above is
# what makes this fail closed — a missing lsof would otherwise false-pass):
! lsof -iTCP:8000 -sTCP:LISTEN -n -P && ! lsof -iTCP:8100 -sTCP:LISTEN -n -P
```

Create the state directory. `~/.jentic` must be owner-only (`0700`) — the
files inside are container-readable, so this parent directory is the
host-side protection:

```bash
mkdir -p ~/.jentic/logs
chmod 700 ~/.jentic
chmod 777 ~/.jentic/logs   # containers run as a non-root user and must write here; safe only under the 0700 parent
```

(The image runs as a non-root user; its numeric uid is an image-build detail,
not a contract. If you ever need the actual value — e.g. for a `chown` — read
it from the image itself: `docker run --rm --entrypoint id
ghcr.io/jentic/jentic-one-app:<version>`.)

## 2. Install the `jentic` CLI

The agent-facing CLI is a single static binary from GitHub Releases (full
matrix and cosign verification: [installation/cli.md](../installation/cli.md)).
The snippet resolves the newest release that actually has an asset for this
platform:

```bash
OS=$(uname -s | tr '[:upper:]' '[:lower:]'); ARCH=$(uname -m | sed 's/x86_64/amd64/;s/aarch64/arm64/')
URL=$(curl -fsSL 'https://api.github.com/repos/jentic/jentic-one/releases?per_page=10' \
  | grep -Eo '"browser_download_url": *"[^"]*/jentic_[0-9][^"]*_'"${OS}_${ARCH}"'\.tar\.gz"' \
  | sed 's/.*"\(https[^"]*\)".*/\1/' | head -n 1)
[ -n "$URL" ] || { echo "ERROR: no recent release has a jentic binary for ${OS}/${ARCH}" >&2; exit 1; }
VER=$(basename "$URL" | sed 's/^jentic_\(.*\)_'"${OS}_${ARCH}"'\.tar\.gz$/\1/')
echo "Installing jentic v${VER} from ${URL}"
curl -fsSL -o /tmp/jentic.tar.gz "$URL"
tar -xzf /tmp/jentic.tar.gz -C /tmp jentic
sudo install /tmp/jentic /usr/local/bin/ && rm -f /tmp/jentic /tmp/jentic.tar.gz
jentic --version
# Persist VER for the rest of the install — later steps run in other shells:
echo "VER=${VER}" > ~/.jentic/.env && chmod 600 ~/.jentic/.env
```

The `sudo install` line is a **possible human gate**: if `sudo` needs a
password, the block stalls on an interactive prompt — ask the human to run it
(or pre-authorise sudo) rather than waiting silently. Alternatively, avoid
sudo entirely: re-run the block above with the `sudo install` line replaced
by the user-local install below, and make sure `~/.local/bin` is on `PATH`:

```bash
mkdir -p ~/.local/bin
install -m 0755 /tmp/jentic ~/.local/bin/jentic && rm -f /tmp/jentic /tmp/jentic.tar.gz
```

If it fails: [troubleshoot.md](troubleshoot.md#cli-download-fails-no-url-resolved-or-404)
— do not improvise a download.

## 3. Generate the config — `~/.jentic/jentic-one.yaml`

**Reinstall guard:** if this file already exists, do not touch it. Skip to
Step 4 and reuse it as-is.

Four independent 32-byte secrets are generated fresh; the human never chooses
them and you never display them:

```bash
cat > ~/.jentic/jentic-one.yaml <<EOF
# Generated by an agent following docs/agent/install.md.
# Secrets are machine-generated. On a reinstall REUSE this file — rotating
# credentials.encryption makes existing stored credentials unreadable.
databases:
  registry: {backend: sqlite, path: /data/registry.db, schema_name: registry}
  control:  {backend: sqlite, path: /data/control.db,  schema_name: control}
  admin:    {backend: sqlite, path: /data/admin.db,    schema_name: admin}
runtime:
  debug: false
  log_level: INFO
logging:
  file_enabled: true
  file_dir: /logs
  file_name: app.jsonl
server:
  host: 0.0.0.0        # in-container bind; host exposure is decided by the compose port prefix
  port: 8000
  reload: false
apps: [registry, admin, control, auth]
auth:
  canonical_base_url: http://127.0.0.1:8000   # must EXACTLY match the URL agents register with
admin:
  auth:
    jwt_secret: "$(openssl rand -base64 32)"
  invite:
    pepper: "$(openssl rand -base64 32)"
credentials:
  encryption:
    active_id: v1
    entries:
      - id: v1
        material: "$(openssl rand -base64 32)"
  providers:
    direct_oauth2:
      kind: direct_oauth2
      redirect_uri: http://127.0.0.1:8000/credentials/oauth/callback
  connect:
    state_secret: "$(openssl rand -base64 32)"
observability:
  metrics: {exporter: none}
  tracing: {exporter: none}
search:
  enabled: true
  search_enabled: true
  search_mode: lexical
telemetry:
  enabled: false
EOF
chmod 644 ~/.jentic/jentic-one.yaml   # the app container's non-root user must read it; ~/.jentic (0700) protects it host-side
```

Adjustments from Step 0:

- **Telemetry on:** replace the `telemetry` block with
  `enabled: true`, `instance_id: "$(uuidgen | tr 'A-Z' 'a-z')"`, and
  `host_os: <linux|darwin|windows>` (the host's OS — the container would
  misreport Linux).
- **Postgres:** generate and persist the password first:

  ```bash
  PGPASS=$(openssl rand -hex 24)   # hex only — it crosses YAML and env boundaries unquoted
  echo "PGPASS=${PGPASS}" >> ~/.jentic/.env
  ```

  then replace each database entry with
  `{host: db, port: 5432, name: jentic, user: postgres, password: "$PGPASS", schema_name: <registry|control|admin>}`,
  writing the config **in the same shell session** so `$PGPASS` expands (in a
  new session, load it first with `. ~/.jentic/.env`).
- **Non-loopback install:** `auth.canonical_base_url` and the
  `redirect_uri` must carry the real public URL — see [harden.md](harden.md).
- **HARDENED — human gate:** write the same file, but with the literal
  placeholder `__GENERATE__` in place of each of the four
  `$(openssl rand -base64 32)` substitutions (use a quoted heredoc,
  `<<'EOF'`, so nothing expands). Because *nothing* expands, non-secret
  substitutions must be pre-expanded before writing the file: with telemetry
  on, run `uuidgen | tr 'A-Z' 'a-z'` first and insert the resulting value as
  the literal `instance_id` (it is not a secret — only the four
  `__GENERATE__` markers are left for the human). Then hand the human this
  and wait:

  > Run this once — it replaces each placeholder with a fresh secret that
  > never leaves your shell:
  >
  > ```bash
  > perl -i -pe 's/__GENERATE__/chomp($s=`openssl rand -base64 32`);$s/ge' ~/.jentic/jentic-one.yaml
  > ```

  Confirm completion **without reading the file's values**:

  ```bash
  grep -q __GENERATE__ ~/.jentic/jentic-one.yaml && echo "NOT DONE — placeholders remain"
  ```

  From here on, never read `~/.jentic/jentic-one.yaml` or `~/.jentic/.env`
  back into your context.
- **HARDENED + Postgres:** also write `password: "__PGPASS__"` in the three
  database entries (instead of expanding `$PGPASS`), skip the
  agent-side `PGPASS` generation above, and fold the password into the same
  human gate:

  > Also run this — it generates the database password, pins it for compose,
  > and fills it into the config:
  >
  > ```bash
  > PGPASS=$(openssl rand -hex 24)
  > echo "PGPASS=${PGPASS}" >> ~/.jentic/.env
  > perl -i -pe "s/__PGPASS__/${PGPASS}/g" ~/.jentic/jentic-one.yaml
  > ```

  Confirm with `grep -q __PGPASS__ ~/.jentic/jentic-one.yaml && echo "NOT
  DONE"`. Steps 4–10 need no secret values, so nothing else changes.

Every other key and its default: [configuration reference](../reference/config.md).

## 4. Write the compose file — `~/.jentic/docker-compose.yaml`

The broker always runs as its own service (`JENTIC__APPS=broker`) on its own
port. The project name is pinned to `jentic` so volume names are
deterministic; the `127.0.0.1` port prefix is what keeps a loopback install
off the network. The heredoc delimiter is quoted (`<<'EOF'`): write the file
**exactly as shown, escaping nothing** — compose resolves `${VER}` (and
`${PGPASS}`, `$HOME`) from `~/.jentic/.env` and the environment on every
invocation.

```bash
cat > ~/.jentic/docker-compose.yaml <<'EOF'
name: jentic
services:
  app:
    image: ghcr.io/jentic/jentic-one-app:${VER}
    environment:
      JENTIC_CONFIG_FILE: /etc/jentic/jentic-one.yaml
      JENTIC__APPS: registry,admin,control,auth
    ports:
      - "127.0.0.1:8000:8000"
    volumes:
      - $HOME/.jentic/jentic-one.yaml:/etc/jentic/jentic-one.yaml:ro
      - $HOME/.jentic/logs:/logs
      - jentic-data:/data
  broker:
    image: ghcr.io/jentic/jentic-one-app:${VER}
    environment:
      JENTIC_CONFIG_FILE: /etc/jentic/jentic-one.yaml
      JENTIC__APPS: broker
      JENTIC__SERVER__PORT: "8100"
    ports:
      - "127.0.0.1:8100:8100"
    volumes:
      - $HOME/.jentic/jentic-one.yaml:/etc/jentic/jentic-one.yaml:ro
      - $HOME/.jentic/logs:/logs
      - jentic-data:/data
volumes:
  jentic-data:
EOF
chmod 600 ~/.jentic/docker-compose.yaml
```

Notes:

- SQLite **must** live on the named volume, never a host bind mount
  ([why](troubleshoot.md#sqlite-disk-io-error)).
- **Postgres variant:** write this complete file instead (do not hand-merge
  fragments). It adds a `db` service, makes app/broker wait on its
  healthcheck, and drops the `jentic-data` volume (data lives in Postgres):

```bash
cat > ~/.jentic/docker-compose.yaml <<'EOF'
name: jentic
services:
  app:
    image: ghcr.io/jentic/jentic-one-app:${VER}
    environment:
      JENTIC_CONFIG_FILE: /etc/jentic/jentic-one.yaml
      JENTIC__APPS: registry,admin,control,auth
    ports:
      - "127.0.0.1:8000:8000"
    volumes:
      - $HOME/.jentic/jentic-one.yaml:/etc/jentic/jentic-one.yaml:ro
      - $HOME/.jentic/logs:/logs
    depends_on:
      db:
        condition: service_healthy
  broker:
    image: ghcr.io/jentic/jentic-one-app:${VER}
    environment:
      JENTIC_CONFIG_FILE: /etc/jentic/jentic-one.yaml
      JENTIC__APPS: broker
      JENTIC__SERVER__PORT: "8100"
    ports:
      - "127.0.0.1:8100:8100"
    volumes:
      - $HOME/.jentic/jentic-one.yaml:/etc/jentic/jentic-one.yaml:ro
      - $HOME/.jentic/logs:/logs
    depends_on:
      db:
        condition: service_healthy
  db:
    image: postgres:16
    environment:
      POSTGRES_DB: jentic
      POSTGRES_USER: postgres
      POSTGRES_PASSWORD: "${PGPASS}"
    volumes:
      - db-data:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U postgres -d jentic"]
      interval: 5s
      timeout: 3s
      retries: 5
volumes:
  db-data:
EOF
chmod 600 ~/.jentic/docker-compose.yaml
```

  Postgres rules: **no init scripts** on the `db` service (migrations create
  the schemas — [why](troubleshoot.md#postgres-schemas-missing-or-someone-suggests-an-init-script));
  **never publish 5432** to the host; on a reinstall over an existing
  `jentic_db-data` volume, keep the volume's original password
  (`POSTGRES_PASSWORD` only applies at first initialisation).

## 5. Pull the image

Pull through compose so the tag comes from `~/.jentic/.env`, not from your
shell:

```bash
docker compose -p jentic -f ~/.jentic/docker-compose.yaml pull
```

Pull denied → [troubleshoot.md](troubleshoot.md#image-pull-denied).

## 6. Run database migrations

A one-shot app container applies migrations (this also creates the volume,
and for Postgres waits on the db healthcheck):

```bash
docker compose -p jentic -f ~/.jentic/docker-compose.yaml \
  run --rm -T app python -m jentic_one.migrations.run
```

Failures → [troubleshoot.md](troubleshoot.md#migrations-fail) — the recovery
differs critically between a fresh volume and a pre-existing one.
Re-running migrations is also the upgrade step — see [operate.md](operate.md).

## 7. Start the stack and wait for health

```bash
docker compose -p jentic -f ~/.jentic/docker-compose.yaml up -d
```

Poll liveness — a cold start can take tens of seconds:

```bash
for i in $(seq 1 45); do
  curl -fsS http://127.0.0.1:8000/health >/dev/null 2>&1 && break
  sleep 2
done
curl -fsS http://127.0.0.1:8000/health          # app is up
curl -fsS http://127.0.0.1:8100/health          # broker is up
```

Never healthy → [troubleshoot.md](troubleshoot.md#app-or-broker-never-becomes-healthy).

## 8. First admin account — HUMAN GATE

The database ships with **zero users**; there is no default account or
password, and the first admin's password must never pass through you. Check
whether setup is still required:

```bash
curl -fsS http://127.0.0.1:8000/admin/health   # → {"setup_required": true|false, ...}
```

- `setup_required: false` **twice in a row** (~2 s apart — a single read can
  be a [warmup blip](troubleshoot.md#setup_required-flaps)): an admin already
  exists (reinstall over live data) — tell the human to sign in at
  `http://127.0.0.1:8000/app/login` and continue to Step 9.
- `setup_required: true`: tell the human —

  > Open **http://127.0.0.1:8000/app/setup** and create the first admin
  > account (email + password, minimum 12 characters). I'll wait.

  Then poll `setup_required` every few seconds until it is `false`.

No human available (CI, fleet installs) →
[troubleshoot.md](troubleshoot.md#no-human-available-at-the-first-admin-gate-ci-fleet-installs).

## 9. Register this agent — HUMAN GATE

Now connect the agent (you, or the machine you run on) to the install. Use
`127.0.0.1`, **never** `localhost` — the token audience must exactly match
`auth.canonical_base_url` ([details](troubleshoot.md#invalid_grant-on-register-or-token-exchange)):

```bash
jentic register --url http://127.0.0.1:8000
```

This registers a new agent identity and then **waits for a human to approve
it**. It prints the approval link; tell the human:

> Approve the new agent at **http://127.0.0.1:8000/app/agents** — I'll
> continue automatically once you have.

The command exits successfully once approved and a token is minted. Timeout
(exit 3) → [troubleshoot.md](troubleshoot.md#jentic-register-times-out-exit-3-timeout_pending);
remote installs also need `--broker-url`
([why](troubleshoot.md#jentic-execute-fail-closes-against-a-remote-install)).

Then verify, and optionally write the Jentic skill into your own runtime's
native layout (Claude/Cursor/Codex/…):

```bash
jentic doctor        # identity, token, broker reachability
jentic skill init    # optional: install the usage skill for your runtime
```

## 10. Verify end to end

```bash
jentic catalog list            # the API catalog answers (bare `jentic catalog` opens an interactive TUI on a terminal)
jentic access whoami           # who you are and what you may call
```

You are done installing. **Now read [use.md](use.md) before doing anything
else** — it covers how to import an API, request access, and make calls; do
not guess commands from package-manager habit. The first two you will need:

```bash
jentic catalog search "<api name>"      # find an importable API in the public catalog
jentic catalog import <vendor/name>     # import it into this instance's registry
```

Report to the human:

- URLs: app/UI `http://127.0.0.1:8000`, broker `http://127.0.0.1:8100`
- Files: config `~/.jentic/jentic-one.yaml`, compose
  `~/.jentic/docker-compose.yaml`, version/db-password pin `~/.jentic/.env`,
  logs `~/.jentic/logs/app.jsonl`
- Data: docker volume `jentic_jentic-data` (Postgres: `jentic_db-data`)
- Next: make a [first brokered call](../guides/first-call.md); day-2 usage
  patterns in [use.md](use.md); before storing a real credential, read
  [harden.md](harden.md).
