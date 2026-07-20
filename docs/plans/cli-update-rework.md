# Design: rework `jenticctl update` (package-manager-aware, release-tag-based)

> **STATUS: design — feature split out of the release procedure.** Not beta-blocking;
> follows the GoReleaser CLI-binaries work.

## Problem

Today `jenticctl update` (`cli/internal/cmd/update.go`) does **both** halves in one shot:
it rebuilds and swaps **both** CLI binaries (`updateCLI`) **and** the server/stack
(`updateStack`, forward-only migrations + restart), from a tracked **git ref**, comparing
**commit SHAs** (`RemoteCommit`/`SameCommit`). Two problems once we ship prebuilt binaries +
brew:

1. **It overwrites a package-manager-managed binary.** A `brew install`ed CLI updated by
   `jenticctl update` desyncs Homebrew's version tracking — the footgun no mature tool
   allows (gh, gcloud, rustup, deno, flyctl all refuse to self-update a managed binary).
2. **Security (see the security review):** `stageCLIBuild` fetches `install.sh` over the
   network and `bash`-executes it, rebuilding from a **mutable branch with no
   signature/checksum verification** — arbitrary code execution as a credential-broker
   operator. This path is *retained* for installer users, so it must be hardened.

## Target design

- **Detect the install source.** Prefer a **build-time `packageManaged` flag** stamped by
  the Homebrew formula's ldflags (deterministic, no `brew` shell-out); or flyctl's runtime
  `isUnderHomebrew()` (compare `os.Executable()` vs `$(brew --prefix)/bin`).
- **When package-managed:** `update` **refuses to swap the binary** and prints the exact
  command (`gh`-style — print, don't auto-run `brew`, which dodges the "bottle not landed"
  race): *"This CLI was installed via Homebrew — run `brew upgrade jentic` to update it.
  `jenticctl update` now upgrades the server stack only."*
- **When installer-managed:** keep the in-place swap (`stageCLIBuild` + `ReplaceBinary`
  with `.bak`) **but verify a cosign signature / `checksums.txt` against the release tag
  before executing/replacing**, and default to **tagged releases**, not branches. Verify
  the go.dev toolchain download checksum too.
- **Narrow `update` to the deployed product** — server/stack + migrations (what brew can't
  do). `--cli-only` prints the brew command when package-managed.
- **Release-tag-aware comparison:** replace SHA compare with `golang.org/x/mod/semver`
  (none today). **Support two manifest generations** during the transition — existing
  installs recorded a `ref + SHA`, not a tag, so "installed from a branch, no tag" must
  degrade gracefully ("can't compare, here's latest").
- **Homebrew `caveats` block:** "`brew upgrade jentic` updates the CLI; `jenticctl update`
  upgrades the server stack."
- **Migration safety** (from gitlab/sentry): backup-by-default (refuse `--stack` without
  confirmation, `--no-backup` to opt out), one-minor-at-a-time hard stop, `--dry-run`.

## Effort / risk

~2–4 days Go. Highest breakage risk of the release workstreams (touches
`internal/update/update.go` + `internal/cmd/update.go` + tests, and the dual-manifest
transition). Sequence **after** GoReleaser ships tag-stamped binaries — the version string
flips from ref→semver at that point, so the two are coupled.

## References

- gh (no self-update; prints pkg-mgr command): <https://github.com/cli/cli/issues/166>
- flyctl `isUnderHomebrew()`: <https://github.com/superfly/flyctl/blob/master/internal/update/update.go>
- gcloud / rustup / deno refusing self-update under a package manager; Homebrew `caveats`: see the release-procedure References section.
