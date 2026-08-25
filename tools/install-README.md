# Jentic CLI installer

[`install.sh`](install.sh) is a self-contained shell script that builds the
Jentic CLIs from signed release archives or source and installs them onto your
`PATH`. The default download installs `jentic`, the agent CLI. Set
`JENTIC_INSTALL_BINARIES=both` to also install `jenticctl`, the local-stack
installer and lifecycle CLI.

## Quick start

```bash
# Install only jentic from a published release for use with a remote deployment.
curl -fsSL https://raw.githubusercontent.com/jentic/jentic-one/main/tools/install.sh \
  | env JENTIC_INSTALL_METHOD=binary sh

# Install both binaries and start the local-stack wizard.
curl -fsSL https://raw.githubusercontent.com/jentic/jentic-one/main/tools/install.sh \
  | env JENTIC_INSTALL_BINARIES=both sh
```

The public release and source repository need no token. If you're installing
from a **private fork**, pass a GitHub token with read access. The token is
needed twice: once for `curl` to fetch the script and once by the script:

```bash
curl -fsSL -H "Authorization: Bearer $GITHUB_TOKEN" \
  https://raw.githubusercontent.com/jentic/jentic-one/main/tools/install.sh \
  | GITHUB_TOKEN=$GITHUB_TOKEN sh
```

You can also run it from a checkout:

```bash
./tools/install.sh
```

## What it does

1. Checks prerequisites (`git`, `curl`, `tar`, `mktemp`) and detects the
   platform (`linux`/`darwin`, `amd64`/`arm64`). Windows is supported through
   WSL.
2. Resolves the requested ref, using the latest release when `JENTIC_REF` is
   unset.
3. In the default `auto` mode, downloads the matching release archive and
   verifies its SHA-256 checksum. If `cosign` is available, it also verifies
   the signed checksum file. If no matching release asset exists, it falls back
   to a source build.
4. Downloads `jentic` by default. `JENTIC_INSTALL_BINARIES=both` also downloads
   `jenticctl`. The source-build path always builds both binaries.
5. For a source build, uses a suitable Go toolchain or downloads the pinned
   fallback, then shallow-clones the `cli/` subtree and builds both binaries.
6. Installs the selected binaries under `~/.jentic/bin`, verifies that they
   respond to `--version`, and records the install manifest.
7. If `jenticctl` was installed and a terminal is available, starts
   `jenticctl install`. Set `JENTIC_NO_INSTALL=1` to skip this hand-off.

Temporary download or build directories are removed on exit. A downloaded Go
toolchain is cached for later source builds.

## PATH handling

The binaries install into `~/.jentic/bin`. The installer makes that directory
reachable using the first of these that applies:

1. **Already on `PATH`** — if `~/.jentic/bin` is already on your `PATH`, nothing
   is changed and nothing extra is printed.
2. **Symlink into an on-`PATH` dir** — if a conventional directory that's
   already on your `PATH` is writable (`/usr/local/bin`, then `~/.local/bin`),
   the selected binaries are symlinked there. This takes effect immediately,
   with no shell restart needed.
3. **Append to your shell profile** — otherwise the installer appends a single
   guarded block to the right rc file for your login shell and prints the exact
   `export` line to use right now:
   - **zsh** → `~/.zshrc`
   - **bash** → `~/.bashrc` and `~/.bash_profile`
   - other shells → the first existing common rc, else `~/.profile`

   The block is marked with a comment so re-running the installer **never**
   duplicates it — it's added at most once and left in place afterward. After a
   fresh append, **restart your terminal** (or `source` the rc file) so the new
   `PATH` takes effect.

To install somewhere already on your `PATH` and skip all of the above, set
`JENTIC_INSTALL_DIR` (e.g. `JENTIC_INSTALL_DIR=/usr/local/bin`).

## Configuration

All optional, set as environment variables:

| Variable | Default | Description |
| --- | --- | --- |
| `JENTIC_REPO` | `jentic/jentic-one` | `owner/name` of the source repo |
| `JENTIC_REF` | _latest release tag_ | Release tag to download, or branch/tag/commit to build. When unset, the installer resolves the latest release. |
| `JENTIC_INSTALL_DIR` | `~/.jentic/bin` | Where the binaries are installed |
| `JENTIC_GO_VERSION` | `1.26.2` | Go version to download if no suitable `go` is found |
| `JENTIC_INSTALL_METHOD` | `auto` | `auto` prefers a release archive and falls back to source; `binary` requires a matching release archive; `source` forces a source build. |
| `JENTIC_INSTALL_BINARIES` | `jentic` | Download `jentic` only, or `both` to also download `jenticctl`. Source builds always build both. |
| `JENTIC_NO_INSTALL` | `0` | Set to `1` to skip the `jenticctl install` hand-off. |
| `GITHUB_TOKEN` | _(unset)_ | Only needed to clone a **private fork** — a token with repo read access (HTTP Basic, never written to disk). Not needed for the public repo. |

Examples:

```bash
# Default: install the jentic release archive, falling back to source
./tools/install.sh

# Install both release binaries without starting the local-stack wizard
JENTIC_INSTALL_BINARIES=both JENTIC_NO_INSTALL=1 ./tools/install.sh

# Build the main branch instead of downloading a release
JENTIC_INSTALL_METHOD=source JENTIC_REF=main ./tools/install.sh

# Pin an exact release tag
JENTIC_REF=v0.24.0 ./tools/install.sh

# Pin the Go version used for the download fallback
JENTIC_GO_VERSION=1.26.2 ./tools/install.sh
```

## Verifying the install

```bash
jentic --version
# jentic v0.24.0 (commit a1b2c3d, built 2026-06-19T14:00:00Z)

jentic --help

# If you installed both binaries:
jenticctl --version
jenticctl --help
```

## Troubleshooting

- **`clone failed ...`** — source builds clone the public repo anonymously, so this usually
  means a network/proxy issue or a bad `JENTIC_REF` (the ref must be a branch,
  tag, or commit that exists in the repo). If you're building from a
  **private fork**, it means no token was provided (or the token lacks access) —
  create a token with repo read scope and re-run with `GITHUB_TOKEN=...`.
- **`Found go1.xx but Go 1.25+ is required`** — your system Go is too old. The
  script downloads a newer Go automatically; if you'd rather use your own, update
  Go to 1.25 or newer.
- **`~/.jentic/bin is not on your PATH`** — the installer appends a guarded
  `export PATH=...` block to your shell profile (`~/.zshrc`/`~/.bashrc`); restart
  your terminal (or `source` the rc file) to pick it up. To use the CLIs in the
  current shell immediately, run the printed `export PATH="$HOME/.jentic/bin:$PATH"`
  line. See [PATH handling](#path-handling).
- **Windows** — run the installer inside WSL; native Windows is not supported.

## Notes

- Binary mode downloads release archives from GitHub. Source mode additionally
  needs access to the Go module proxy.
- Release archives are checked against `checksums.txt`. If `cosign` is
  installed, the script also verifies `checksums.txt.sig` with
  `checksums.txt.pem`.
- The script honors Go's `GOTOOLCHAIN=auto`, so even if the resolved Go is a bit
  older than the one named in `cli/go.mod`, the build can self-upgrade.
