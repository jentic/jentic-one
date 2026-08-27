# Operate a Jentic One install — agent runbook

Day-2 operations for an install created by [install.md](install.md); failures
→ [troubleshoot.md](troubleshoot.md). All commands assume the pinned compose
project and file:

```bash
alias jc='docker compose -p jentic -f ~/.jentic/docker-compose.yaml'
```

## Status

```bash
jc ps                                          # container state
curl -fsS http://127.0.0.1:8000/health          # app liveness
curl -fsS http://127.0.0.1:8100/health          # broker liveness
curl -fsS http://127.0.0.1:8000/admin/health    # includes setup_required
```

## Start / stop

```bash
jc up -d      # start (idempotent)
jc down       # stop and remove containers — volumes (the database) are preserved
```

## Logs

```bash
jc logs -f                     # container stdout/stderr
tail -f ~/.jentic/logs/app.jsonl   # structured JSON log sink (one object per line)
```

## Upgrade

Upgrading is: install the new CLI, repin `VER` in `~/.jentic/.env` (the single
place the server version lives — the compose file references `${VER}`), run
migrations, then restart. Never skip the migration step, and keep the CLI and
server on the same release.

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
jc pull
jc run --rm -T app python -m jentic_one.migrations.run
jc up -d
```

Check the schema state without modifying anything by appending `--check` to
the migration command (it prints an `OVERALL current|uninitialized|pending`
verdict; a non-zero exit with `pending` means migrations are needed, by
design). Migrations are forward-only: on a database that matters, snapshot
the volume first (`docker run --rm -v jentic_jentic-data:/data -v "$PWD":/backup
alpine tar czf /backup/jentic-data.tgz -C /data .`).

## Reinstall / reconfigure

To change configuration, edit `~/.jentic/jentic-one.yaml` and `jc up -d
--force-recreate`. Rules:

- **Never replace the `credentials.encryption` block** while the data volume
  still holds data — stored credentials become unreadable. Key rotation is an
  explicit multi-key procedure (add a `v2` entry, flip `active_id`, keep `v1`
  until re-encryption completes).
- Postgres: `POSTGRES_PASSWORD` in the compose file only applies at first
  volume initialisation. On an existing `jentic_db-data` volume the config
  must keep the original password.
- Changing `auth.canonical_base_url` invalidates the audience of every
  registered agent — they must re-register against the new URL.

## Reset an admin password

```bash
jc run --rm -T app python -m jentic_one reset-password --email <email>
```

The temporary password is read from stdin (pipe it; never put it in argv) and
must be changed at next sign-in. Prefer having the human do this in the UI
when possible.

## Uninstall

Destructive — confirm with the human, and offer a volume backup first
(see Upgrade above).

```bash
jc down -v                                          # containers + declared volumes
docker volume rm jentic_jentic-data 2>/dev/null     # belt-and-suspenders (SQLite)
docker volume rm jentic_db-data 2>/dev/null         # (Postgres)
rm -rf ~/.jentic                                    # config, compose, logs, CLI state
```

To keep the data for a later reinstall, run `jc down` (no `-v`), leave the
volumes, and **keep `~/.jentic/jentic-one.yaml` and `~/.jentic/.env`** — the
config's encryption key is what makes the preserved data readable, and on
Postgres `.env` holds the password the data volume was initialised with.
