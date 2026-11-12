# Installing the CLIs

Two Go binaries, each its own release archive — no runtime, no dependencies:

| Binary | Role | Install on |
| ------ | ---- | ---------- |
| `jentic` | Agent CLI — `register`, `search`, `inspect`, `execute` | Every host inside the network that calls the instance |
| `jenticctl` | Operator CLI — install, lifecycle, admin | The admin host only |

## Package managers (start here)

```bash
brew install --cask jentic/tap/jentic      # macOS / Linux — installs BOTH binaries
```

```powershell
# Windows — jentic (agent CLI) only, via our Scoop bucket:
scoop bucket add jentic https://github.com/jentic/scoop-bucket
scoop install jentic

# or winget, once Microsoft has merged the manifest for the release:
winget install Jentic.Jentic
```

The Scoop and winget channels publish from the first release after v0.44.0 on; for v0.44.0 and
earlier on Windows, use the [manual download](#manual-download).

All three resolve your CPU architecture, verify the download hash, put the
binary on `PATH`, and upgrade with the manager's native command (`brew
upgrade`, `winget upgrade`, `scoop update`). `jenticctl` is not shipped for
native Windows — use WSL ([windows.md](windows.md)).

Beyond package managers: native [Linux packages](#linux-packages-deb--rpm--apk), the
[one-line installer script](#one-line-installer-script),
which downloads, sha256-checks, and cosign-verifies for you, and the manual
path: locked-down hosts, air-gapped transfer, or when you want to verify the
supply chain yourself.

## Linux packages (.deb / .rpm / .apk)

From the first release after v0.44.0, every release attaches native packages
for `linux/amd64` and `linux/arm64`, one per binary, so an agent host can
install `jentic` alone. They are listed in the signed `checksums.txt` like the
archives (see [Verify](#verify)), and install the binaries to `/usr/bin`.

```bash
VER=$(curl -fsSL https://api.github.com/repos/jentic/jentic-one/releases/latest \
  | sed -n 's/.*"tag_name": *"v\([^"]*\)".*/\1/p')          # or pin a release: VER=X.Y.Z
: "${VER:?could not resolve the latest version — set VER manually}"
ARCH=$(uname -m | sed 's/x86_64/amd64/;s/aarch64/arm64/')    # amd64 | arm64
BASE="https://github.com/jentic/jentic-one/releases/download/v${VER}"
curl -fsSLO "${BASE}/jentic_${VER}_linux_${ARCH}.deb"        # or .rpm / .apk; jenticctl_… for the admin host

sudo apt install "./jentic_${VER}_linux_${ARCH}.deb"                  # Debian / Ubuntu
sudo dnf install "./jentic_${VER}_linux_${ARCH}.rpm"                  # Fedora / RHEL
sudo apk add --allow-untrusted "./jentic_${VER}_linux_${ARCH}.apk"    # Alpine
```

There is no package repository, so `apt upgrade` / `dnf upgrade` don't pick up
new releases: download and install the newer package the same way.
`jenticctl update` recognises a package install and leaves the binaries to the
package manager (`--stack-only` still refreshes the stack). The `.apk` needs
`--allow-untrusted` because it isn't signed with an Alpine key; the signed
`checksums.txt` is what vouches for it.

## One-line installer script

```bash
# both binaries (default), then straight into the `jenticctl install` stack wizard:
curl -fsSL https://raw.githubusercontent.com/jentic/jentic-one/main/tools/install.sh | sh

# jentic only, verified download — for talking to a remote instance:
curl -fsSL https://raw.githubusercontent.com/jentic/jentic-one/main/tools/install.sh \
  | JENTIC_INSTALL_METHOD=binary JENTIC_INSTALL_BINARIES=jentic sh
```

The script downloads the released archive for your OS/arch, **verifies its
`sha256`** against `checksums.txt` (fail-closed — a mismatch aborts and
installs nothing), and, when `cosign` is on your `PATH`, **verifies the release
signature** too. Without `cosign` it prints a warning and continues with the
sha256 check only. It is bash-only (macOS + Linux); on native Windows it exits
with a "run this inside WSL" message.

Environment knobs:

- `JENTIC_INSTALL_METHOD` — `auto` (default: the verified download when a
  matching release asset exists, otherwise a from-source build — forks, dev
  refs) · `binary` (force download; errors if no asset) · `source` (force the
  from-source build).
- `JENTIC_INSTALL_BINARIES` — `both` (default) · `jentic` (agent CLI only).
- `JENTIC_NO_INSTALL=1` — install the binaries only; **skip** the
  `jenticctl install` stack wizard.
- `GITHUB_TOKEN` — download/clone from a **private** fork (token needs `repo`
  read scope).
- `JENTIC_REF=v0.38.2` — pin the release tag to install (download mode) or the
  ref (tag / branch / commit) to build (source mode).

With no interactive terminal (CI, piped `curl … | sh`), the script installs
the binaries and prints the non-interactive follow-up
(`jenticctl install --defaults`) instead of blocking on the wizard. The
from-source path's own settings (`JENTIC_INSTALL_DIR`, `JENTIC_GO_VERSION`, …)
are in [`tools/install-README.md`](../../tools/install-README.md).

## Manual download

The archive name is `<binary>_<version>_<os>_<arch>.tar.gz`. Auto-detect the
platform and resolve the latest version:

```bash
VER=$(curl -fsSL https://api.github.com/repos/jentic/jentic-one/releases/latest \
  | sed -n 's/.*"tag_name": *"v\([^"]*\)".*/\1/p')          # or pin: VER=0.38.2
[ -n "$VER" ] || echo "could not resolve latest version (GitHub API rate limit?) — set VER manually" >&2
: "${VER:?}"    # stops here when empty (aborts the command, not your shell)
OS=$(uname -s | tr '[:upper:]' '[:lower:]')                  # darwin | linux
ARCH=$(uname -m | sed 's/x86_64/amd64/;s/aarch64/arm64/')    # amd64 | arm64
BASE="https://github.com/jentic/jentic-one/releases/download/v${VER}"

curl -fsSLO "${BASE}/jentic_${VER}_${OS}_${ARCH}.tar.gz"
curl -fsSLO "${BASE}/jenticctl_${VER}_${OS}_${ARCH}.tar.gz"   # admin host only
```

### Platform matrix

| OS | Arch | `jentic` | `jenticctl` |
| -- | ---- | -------- | ----------- |
| Linux | amd64 / arm64 | ✅ | ✅ |
| macOS (darwin) | amd64 / arm64 | ✅ | ✅ |
| Windows | amd64 / arm64 (arm64 untested) | ✅ `jentic_<ver>_windows_<arch>.zip` (`jentic.exe`) | ❌ use WSL |

The full component-by-platform picture (server, installer, CLIs) is in
[platform-support.md](platform-support.md).

## Verify

Do this on a connected machine **before** the archives cross into a locked-down
network — it needs only the downloaded files and `cosign`:

```bash
curl -fsSLO "${BASE}/checksums.txt"
curl -fsSLO "${BASE}/checksums.txt.sig"
curl -fsSLO "${BASE}/checksums.txt.pem"

# The signature covers the checksum file (keyless / Fulcio identity):
cosign verify-blob \
  --certificate checksums.txt.pem \
  --signature checksums.txt.sig \
  --certificate-identity-regexp '^https://github\.com/jentic/jentic-one/\.github/workflows/release\.yml@refs/tags/v.*$' \
  --certificate-oidc-issuer 'https://token.actions.githubusercontent.com' \
  checksums.txt

# The checksum file covers the archives and the Linux packages:
sha256sum --check --ignore-missing checksums.txt   # macOS: shasum -a 256 -c checksums.txt --ignore-missing
```

Air-gapped? Transfer the archives together with all three `checksums.txt*`
files so the same verification can be repeated inside the network.

## Install

```bash
tar xzf "jentic_${VER}_${OS}_${ARCH}.tar.gz" jentic
sudo install jentic /usr/local/bin/

tar xzf "jenticctl_${VER}_${OS}_${ARCH}.tar.gz" jenticctl     # admin host only
sudo install jenticctl /usr/local/bin/

jentic doctor   # sanity check
```

On Windows: unzip, put `jentic.exe` on `PATH`, run `jentic doctor`.
`jenticctl` is unsupported on native Windows — use WSL2 for the server side.
The [platform matrix](platform-support.md) is the authority on what runs
where.

## After installing

Register the host against your instance — an operator approves it in the UI:

```bash
jentic register --url https://jentic.example.com --broker-url https://broker.jentic.example.com
```

Then make the [first brokered call](../guides/first-call.md). Every command and
flag is in the generated [CLI reference](../reference/cli.md); the
[CLI README](../../cli/README.md) has the overview.

## What `jenticctl install` does

For the **Run locally** path, once the config is written:

0. **Preflight** — checks the required tools are on `PATH` before doing any work
   (`uv` always; `git` when the source must be cloned). It prints a checklist
   with versions and aborts with install hints if anything required is missing.
   Requirements are defined per path in `requirementsFor`
   ([`internal/install/preflight.go`](../../cli/internal/install/preflight.go)).
1. **Build** a virtualenv at `~/.jentic/venv` and install `jentic-one` (editable)
   into it. Inside a jentic-one checkout (detected by walking up for
   `pyproject.toml` + `src/jentic_one`) it installs from your local source;
   outside one it clones `github.com/jentic/jentic-one` into `~/.jentic/src`
   first.
2. **Migrate** — runs `jentic_one.migrations.run` with the venv interpreter
   against the generated config. For Postgres, if the database isn't reachable
   yet the wizard warns and leaves the migrate command in the next steps
   instead of failing the whole install.
3. **Start the app (background)** — launches `~/.jentic/venv/bin/python -m
   jentic_one` detached, capturing its console output to `~/.jentic/logs/app.log`
   with the PID in `~/.jentic/app.pid`, and watches the first couple of seconds
   for an early crash. A failure to start is non-fatal. `--no-start` skips this.

For the **Run in Docker** path:

0. **Preflight** — checks `docker` is on `PATH` (plus `git` when the source must
   be cloned to build the image).
1. **Get the app image** — pulls the published, signed
   `ghcr.io/jentic/jentic-one-app` (version-matched to the CLI) by default;
   `--build-local` builds `jentic-one/app:jentic-cli` from a checkout instead
   (the shared `python-base` stages are built first).
2. **Write** `~/.jentic/docker-compose.yaml` (app + a managed Postgres when you
   choose Postgres) with the config mounted read-only at
   `/etc/jentic/jentic-one.yaml`, rendered with container-aware values (bind
   `0.0.0.0`, Postgres host `db`, SQLite under `/data`, logs under `/logs`);
   `JENTIC__APPS` is set from the selected surfaces. SQLite databases live in a
   Docker named volume (`jentic-data`), not a host bind mount — Docker Desktop's
   file sharing doesn't support the locking SQLite needs.
3. **Migrate** in a one-shot container
   (`docker compose run --rm app python -m jentic_one.migrations.run`).
4. **Start the stack** — `docker compose up -d` (unless `--no-start`).
   `jenticctl start` / `jenticctl stop` drive `docker compose up -d` / `down`
   afterwards.
