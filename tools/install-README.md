# Jentic CLI installer

[`install.sh`](install.sh) is a self-contained shell script that builds the
Jentic CLIs from source and installs them onto your PATH. It builds **two
binaries** — `jenticctl` (install/lifecycle) and `jentic` (API catalog) — that
share the same module and `~/.jentic` state. It detects your OS/arch, makes sure
a suitable Go toolchain is available (downloading one if needed), fetches just
the `cli/` source from GitHub, builds the binaries, and verifies them.

## Quick start

```bash
curl -fsSL https://raw.githubusercontent.com/jentic/jentic-one/main/tools/install.sh | sh
```

This clones the public repo anonymously — no token required. If you're building
from a **private fork** (or a repo you must authenticate to), pass a GitHub token
with read access. The token is needed twice: once for `curl` to fetch the script,
and once (as an env var) for the script to clone the source:

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

1. Checks prerequisites (`git`, `curl`, `tar`, `mktemp`).
2. Detects your platform (`linux`/`darwin`, `amd64`/`arm64`). Windows is not
   supported directly — use WSL.
3. Ensures Go: uses your existing `go` if it's new enough, otherwise downloads a
   pinned Go into `~/.jentic/toolchain` (reused on subsequent runs).
4. Shallow + sparse clones only the `cli/` subtree of the repo into a temp dir.
5. Builds both binaries with version metadata stamped in via `-ldflags`.
6. Installs them to `~/.jentic/bin/{jenticctl,jentic}` and makes them reachable
   by name (see [PATH handling](#path-handling) below).
7. Runs `jenticctl --version` and `jentic --version` to verify.

The temp clone/build directory is removed on exit; the only things left behind
are the binaries (and the cached Go toolchain, if it was downloaded).

## PATH handling

The binaries install into `~/.jentic/bin`. So `jenticctl` / `jentic` work by
name, the installer makes that directory reachable using the first of these
that applies:

1. **Already on `PATH`** — if `~/.jentic/bin` is already on your `PATH`, nothing
   is changed and nothing extra is printed.
2. **Symlink into an on-`PATH` dir** — if a conventional directory that's
   already on your `PATH` is writable (`/usr/local/bin`, then `~/.local/bin`),
   both binaries are symlinked there. This takes effect immediately, no shell
   restart needed.
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
| `JENTIC_REF` | `main` | Branch, tag, or commit to build (also used as the reported version) |
| `JENTIC_INSTALL_DIR` | `~/.jentic/bin` | Where the binaries are installed |
| `JENTIC_GO_VERSION` | `1.26.2` | Go version to download if no suitable `go` is found |
| `GITHUB_TOKEN` | _(unset)_ | Only needed to clone a **private fork** — a token with repo read access (HTTP Basic, never written to disk). Not needed for the public repo. |

Examples:

```bash
# Build a specific branch into /usr/local/bin
JENTIC_REF=feat/my-branch JENTIC_INSTALL_DIR=/usr/local/bin ./tools/install.sh

# Pin the Go version used for the download fallback
JENTIC_GO_VERSION=1.26.2 ./tools/install.sh
```

## Verifying the install

```bash
jenticctl --version
# jenticctl main (commit a1b2c3d, built 2026-06-19T14:00:00Z)

jentic --version
# jentic main (commit a1b2c3d, built 2026-06-19T14:00:00Z)

jenticctl --help
jentic --help
```

## Troubleshooting

- **`clone failed ...`** — the public repo clones anonymously, so this usually
  means a network/proxy issue or a bad `JENTIC_REF`. If you're building from a
  **private fork**, it means no token was provided (or the token lacks access) —
  create a token with repo read scope and re-run with `GITHUB_TOKEN=...`.
- **`Found go1.xx but Go 1.26+ is required`** — your system Go is too old. The
  script downloads a newer Go automatically; if you'd rather use your own, update
  Go to 1.26 or newer.
- **`~/.jentic/bin is not on your PATH`** — the installer appends a guarded
  `export PATH=...` block to your shell profile (`~/.zshrc`/`~/.bashrc`); restart
  your terminal (or `source` the rc file) to pick it up. To use the CLIs in the
  current shell immediately, run the printed `export PATH="$HOME/.jentic/bin:$PATH"`
  line. See [PATH handling](#path-handling).
- **Windows** — run the installer inside WSL; native Windows is not supported.

## Notes

- This builds from source, so it needs network access to GitHub and the Go
  module proxy. There is no prebuilt-binary download path yet.
- The script honors Go's `GOTOOLCHAIN=auto`, so even if the resolved Go is a bit
  older than the one named in `cli/go.mod`, the build can self-upgrade.
