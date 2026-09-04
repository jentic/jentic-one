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
winget install Jentic.Jentic               # Windows — jentic (agent CLI) only

# or, via our Scoop bucket (no Microsoft review lag on brand-new releases):
scoop bucket add jentic https://github.com/jentic/scoop-bucket
scoop install jentic
```

All three resolve your CPU architecture, verify the download hash, put the
binary on `PATH`, and upgrade with the manager's native command (`brew
upgrade`, `winget upgrade`, `scoop update`). `jenticctl` is not shipped for
native Windows — use WSL ([windows.md](windows.md)).

Everything below is the manual path: locked-down hosts, air-gapped transfer,
or when you want to verify the supply chain yourself. There is also a
[one-line installer script](../../cli/README.md#3-one-line-download-verified-binary-no-compiler)
which downloads, sha256-checks, and cosign-verifies for you.

## Manual download

The archive name is `<binary>_<version>_<os>_<arch>.tar.gz`. Auto-detect the
platform and resolve the latest version:

```bash
VER=$(curl -fsSL https://api.github.com/repos/jentic/jentic-one/releases/latest \
  | sed -n 's/.*"tag_name": *"v\([^"]*\)".*/\1/p')          # or pin: VER=0.33.0
[ -n "$VER" ] || { echo "could not resolve latest version (GitHub API rate limit?) — set VER manually" >&2; exit 1; }
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

The full component-by-platform picture (server, installer, confinement) is in
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

# The checksum file covers the archives:
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

On Windows: unzip, put `jentic.exe` on `PATH`, run `jentic doctor`. `jenticctl`
and `jentic run` (the local-agent sandbox) are unsupported on native Windows —
use WSL for those.

## After installing

Register the host against your instance — an operator approves it in the UI:

```bash
jentic register --url https://jentic.example.com --broker-url https://broker.jentic.example.com
```

Then make the [first brokered call](../guides/first-call.md). The full command
surface is in the [CLI README](../../cli/README.md#usage).
