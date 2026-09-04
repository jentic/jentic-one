# Operate a Jentic One install — agent runbook

Day-2 operations for an install created by [install.md](install.md); failures
→ [troubleshoot.md](troubleshoot.md). All commands spell the pinned compose
project and file in full — every block may run in a fresh shell, so never
rely on an alias or an exported variable (install.md rule 4).

## Status

```bash
docker compose -p jentic -f ~/.jentic/docker-compose.yaml ps   # container state
curl -fsS http://127.0.0.1:8000/health          # app liveness
curl -fsS http://127.0.0.1:8100/health          # broker liveness
curl -fsS http://127.0.0.1:8000/admin/health    # includes setup_required
```

These endpoints are process-liveness only — they do not verify database
connectivity. The canonical monitoring doc (same endpoints, plus logs and
audit records) is [monitoring.md](../operations/monitoring.md).

## Start / stop

```bash
docker compose -p jentic -f ~/.jentic/docker-compose.yaml up -d   # start (idempotent)
docker compose -p jentic -f ~/.jentic/docker-compose.yaml down    # stop and remove containers — volumes (the database) are preserved
```

## Logs

```bash
docker compose -p jentic -f ~/.jentic/docker-compose.yaml logs -f   # container stdout/stderr
tail -f ~/.jentic/logs/app.jsonl   # structured JSON log sink (one object per line)
```

## Upgrade

Upgrading is: install the new CLI, repin `VER` in `~/.jentic/.env` (the single
place the server version lives — the compose file references `${VER}`), stop
the stack, snapshot the data volume, run migrations, then restart. Never skip
the migration step, and keep the CLI and server on the same release.

First install the new CLI. The `sudo` step can block on an interactive
password prompt — treat it as a **possible human gate** (or use the non-sudo
fallback below):

```bash
OS=$(uname -s | tr '[:upper:]' '[:lower:]'); ARCH=$(uname -m | sed 's/x86_64/amd64/;s/aarch64/arm64/')
URL=$(curl -fsSL 'https://api.github.com/repos/jentic/jentic-one/releases?per_page=10' \
  | grep -Eo '"browser_download_url": *"[^"]*/jentic_[0-9][^"]*_'"${OS}_${ARCH}"'\.tar\.gz"' \
  | sed 's/.*"\(https[^"]*\)".*/\1/' | head -n 1)
[ -n "$URL" ] || { echo "ERROR: no recent release has a jentic binary for ${OS}/${ARCH}" >&2; exit 1; }
VER=$(basename "$URL" | sed 's/^jentic_\(.*\)_'"${OS}_${ARCH}"'\.tar\.gz$/\1/')

curl -fsSL -o /tmp/jentic.tar.gz "$URL"
tar -xzf /tmp/jentic.tar.gz -C /tmp jentic
sudo install /tmp/jentic /usr/local/bin/ && rm -f /tmp/jentic /tmp/jentic.tar.gz

sed -i.bak "s/^VER=.*/VER=${VER}/" ~/.jentic/.env && rm -f ~/.jentic/.env.bak
```

No sudo available (or the human is not around to enter a password)? Install
to a user-owned directory instead — make sure `~/.local/bin` is on `PATH`:

```bash
mkdir -p ~/.local/bin
install -m 0755 /tmp/jentic ~/.local/bin/jentic && rm -f /tmp/jentic /tmp/jentic.tar.gz
```

Then pull the new image, **stop the stack, snapshot the data volume while it
is stopped** (SQLite files mid-write are not consistent — the full backup
contract, including the Postgres variant, is
[backup-restore.md](../operations/backup-restore.md)), migrate, and restart:

```bash
docker compose -p jentic -f ~/.jentic/docker-compose.yaml pull
docker compose -p jentic -f ~/.jentic/docker-compose.yaml down
docker run --rm -v jentic_jentic-data:/data -v "$PWD":/backup alpine \
  tar czf /backup/jentic-data-$(date +%F).tgz -C /data .
docker compose -p jentic -f ~/.jentic/docker-compose.yaml \
  run --rm -T app python -m jentic_one.migrations.run
docker compose -p jentic -f ~/.jentic/docker-compose.yaml up -d
```

Check the schema state without modifying anything by appending `--check` to
the migration command (it prints an `OVERALL current|uninitialized|pending`
verdict and exits non-zero unless `OVERALL current`, by design).
Migrations are forward-only: the snapshot *is* the rollback.

## Reinstall / reconfigure

To change configuration, edit `~/.jentic/jentic-one.yaml` and
`docker compose -p jentic -f ~/.jentic/docker-compose.yaml up -d
--force-recreate`. Rules:

- **Never replace the `credentials.encryption` block** while the data volume
  still holds data — stored credentials become unreadable. Key rotation is an
  explicit multi-key procedure (add a `v2` entry, flip `active_id`). Stored
  secrets re-encrypt only when rewritten; there is no bulk re-encrypt or
  completion check — keep retired keys in the keyset indefinitely. The
  canonical contract is in [upgrades.md](../operations/upgrades.md).
- Postgres: `POSTGRES_PASSWORD` in the compose file only applies at first
  volume initialisation. On an existing `jentic_db-data` volume the config
  must keep the original password.
- Changing `auth.canonical_base_url` invalidates the audience of every
  registered agent — they must re-register against the new URL.

## Reset an admin password

The temporary password is read from stdin — pipe it explicitly (never put it
in argv). Replace `__TEMP_PASSWORD__` with a value the human supplied out of
band (or generate one with `openssl rand -base64 18` and hand it to them —
never echo it into chat or logs):

```bash
printf '%s' "__TEMP_PASSWORD__" | docker compose -p jentic -f ~/.jentic/docker-compose.yaml \
  run --rm -T app python -m jentic_one reset-password --email <email>
```

The password must be changed at next sign-in. Prefer having the human do this
in the UI when possible.

## Uninstall

Destructive — confirm with the human, and offer a volume backup first. The
snapshot must be taken with the containers **stopped** (mid-write SQLite
files are inconsistent); the canonical backup contract — including the
Postgres variant — is [backup-restore.md](../operations/backup-restore.md):

```bash
docker compose -p jentic -f ~/.jentic/docker-compose.yaml down
docker run --rm -v jentic_jentic-data:/data -v "$PWD":/backup alpine \
  tar czf /backup/jentic-data-$(date +%F).tgz -C /data .
```

Then remove everything:

```bash
docker compose -p jentic -f ~/.jentic/docker-compose.yaml down -v   # containers + declared volumes
docker volume rm jentic_jentic-data 2>/dev/null     # belt-and-suspenders (SQLite)
docker volume rm jentic_db-data 2>/dev/null         # (Postgres)
rm -rf ~/.jentic                                    # config, compose, logs, CLI state
```

To keep the data for a later reinstall, run
`docker compose -p jentic -f ~/.jentic/docker-compose.yaml down` (no `-v`),
leave the volumes, and **keep `~/.jentic/jentic-one.yaml` and
`~/.jentic/.env`** — the config's encryption key is what makes the preserved
data readable, and on Postgres `.env` holds the password the data volume was
initialised with.
