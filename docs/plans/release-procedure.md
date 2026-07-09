# Release & Distribution Procedure — Proposal

> **PROPOSAL — nothing is wired up yet.** For review. **DECISION** = needs sign-off.
> Facts re-verified against the repo. Supersedes the manual bump steps in
> [`deploy/README.md`](../../deploy/README.md).

## The idea in one picture

```mermaid
flowchart LR
    A[PR merged to main<br/>conventional commit] --> B[release-please<br/>opens/updates Release PR]
    B --> C{maintainer<br/>reviews + edits<br/>CHANGELOG}
    C -->|merge| D[tag vX.Y.Z<br/>+ GitHub Release]
    D -->|App token| E[GoReleaser: signed jenticctl + jentic<br/>binaries + brew + checksums]
    D --> G[release notes]
    H[server /health] -. version .-> I[CLI + UI<br/>update / skew nudge]
```

**In words:** you already write Conventional Commits and squash-merge. **release-please**
turns those into a reviewable Release PR; merging it cuts the tag, changelog, and GitHub
Release; the tag then publishes the **prebuilt, signed CLI binaries** (the install). Separately, the CLI
and UI passively check `/health` to nudge when a newer version (or a version-skewed
remote server) is detected.

> **Note — this is a deliberate change from jentic-mini.** mini used **semantic-release**,
> the *fully automatic* model: every qualifying merge to `main` shipped a release
> immediately (version + notes + tag + GitHub Release), with **no human gate and no
> changelog editing**. release-please is the **gated** model: a standing Release PR you
> **approve** (and *may* edit the changelog on) before it ships. It's normal and common
> (it's release-please's design), and the trade-off is deliberate: for a self-hosted
> product where a bad release hits operators — and runs DB migrations — a human "bless
> each release" step is safer than auto-ship, at the cost of one extra click. Editing the
> changelog is *optional* (you can merge the Release PR as-is). If the team prefers mini's
> zero-touch flow, release-please can be configured more automatically — but the gate is
> recommended here. **DECISION:** confirm we want the gated model over auto-ship.

## What happens after the GitHub Release

Everything below is triggered *by the tag/Release appearing*. release-please pushes the
tag using a **GitHub App token** — the default `GITHUB_TOKEN` is deliberately blocked from
triggering other workflows (loop-prevention), so the App token is what lets the Release
kick off the publish steps.

```mermaid
flowchart TD
    R[release-please: tag vX.Y.Z + GitHub Release<br/>pushed via App token] --> G[1 · CI-on-tag gate<br/>build + migrate on fresh DB + /health]
    G -->|pass| C[2 · GoReleaser → signed jenticctl + jentic binaries<br/>checksums + cosign + brew, attached to Release]
    G -->|fail| X[stop — nothing publishes]
    R -. later, continuous .-> N[server polls GitHub Releases<br/>→ /health exposes latest_version]
    N --> U[CLI + UI nudge:<br/>update available / version skew]
```

1. **CI-on-tag gate (safety check before publishing).** Runs on the exact tagged commit:
   build the app, spin up a **fresh empty DB and run all migrations**, hit `/health`. Today
   CI only runs on `main`, so the tag itself is untested — and a broken migration is our top
   risk. If this fails, we stop and **nothing publishes**.
2. **Publish the CLI binaries → GoReleaser.** From the tag, compile **`jenticctl` + `jentic`**
   for every OS/arch, produce `checksums.txt` + a **cosign signature**, a **Homebrew** formula,
   and **attach them to the GitHub Release**. This *is* the install — `jenticctl` holds the
   setup wizard, and the CLI is also a standalone client an agent runs on a *different host*
   than the server.

*(The Docker image and Helm chart are deliberately **not** in these steps for beta — deferred
until their tripwires; see the distribution table.)*

**Separately, a continuous notification loop** (not triggered by the release): the server
periodically checks the GitHub Releases API and exposes the latest version on `/health`; the
CLI and UI read it and passively nudge "vX.Y.Z available," and the CLI also warns if its
version differs from the remote server it targets. Pure read, sends no data, default-on with
one flag off, silenced by `--offline`.

## Why now (the gap)

| | |
| --- | --- |
| ✅ We have | Conventional Commits + squash-merge, enforced (`cz` + lefthook) |
| ❌ We lost | The release automation (jentic-mini's semantic-release pipeline, deleted in the 2026-07-01 OSS scrub) |
| ⚠️ Result | No versioning/tagging/release/changelog automation; the git-conventions rule still points at an "auto-generated changelog" that no longer exists; **nothing is published anywhere — every install builds from source** |

## Distribution strategy — the CLI is the front door

Research across comparable self-hosted OSS (Sentry, PostHog, Supabase, Meilisearch,
Plausible) and mainstream CLIs (`gh`, `kubectl`, `terraform`, `supabase`, Claude Code)
is consistent: **the install is a single prebuilt CLI, and nobody's happy path is
compile-from-source** — source-build is always the last-resort fallback ([References](#references--prior-art)).

For jentic-one that CLI is **`jenticctl`** (with the `install` wizard) — it's already the
front door today (`curl | sh` → `jenticctl` → wizard sets up config + DB + starts the
server). The one thing to change is *how the binary is produced*: build **once in CI**
(prebuilt) instead of **on the user's machine** (from source).

| Artifact | Beta stance | Why |
| --- | --- | --- |
| **`jentic` + `jenticctl` binaries** — prebuilt, signed (GoReleaser + Homebrew + `curl \| sh` that *downloads*) | **Ship — headline install** | This *is* the "install jentic-one" story. See rationale below. |
| **Tag + release notes + checksums** | **Ship** | Always. |
| **Source build** (`curl \| sh` that compiles) | **Keep as fallback** | For auditing / unsupported platforms; a liability only if it's the *only* path. |
| **Python wheel (PyPI)** | **Not an install path** | Dropped. See "Why not a wheel" below. |
| **Docker server image (GHCR)** | **Defer — honestly** | Keep "build locally" for beta, **documented as the current state** (not dressed up). A hosted image is a standing promise (needs a CVE-rebuild cron). **Tripwire:** the first "how do I run this without building?" from a non-author → add a signed multi-arch GHCR image + rebuild cron + rewire the installer to pull it. |
| **Helm chart (OCI publish)** | **Defer** | Strongest-supported deferral (PostHog killed theirs, Supabase declined it in-repo, Meilisearch isolates it — see [References](#references--prior-art)). Keep a repo-referenced `charts/` dir. **Tripwire:** ≥2–3 real k8s users. |

### Why prebuilt CLI binaries (not build-from-source)

A Go program must be compiled. Today `curl | sh` **compiles on the user's machine** —
it downloads a Go toolchain and builds, which is slow (minutes), needs a toolchain, can
fail on version/network/arch quirks, and is only as trustworthy as the build script.
**Prebuilt** means *we* compile once per release in CI, for every OS/arch, sign
(cosign) + checksum, and publish the finished executables; the user's `curl | sh` just
downloads the right one.

- **Fast + zero prerequisites** — seconds, no Go toolchain to install.
- **Reliable** — no "build failed on your machine."
- **Verifiable supply chain** — signed, checksummed artifacts prove *we* built them,
  untampered (stronger than "trust this build script").
- **Enables `brew install jentic`** and GitHub Release assets — these distribute
  binaries, not source.
- **It's the universal norm** — `gh`, `kubectl`, `terraform`, `supabase`, Claude Code
  all ship prebuilt signed binaries and offer source-build only as a fallback.
- **Low effort** — one GoReleaser config, fired by the release tag we already create.

### Why *not* a Python wheel as an install path

An earlier draft proposed publishing a `jentic-one` wheel to PyPI as a "no-Docker"
install. **Dropped**, because:

- **The product is two programs:** the **server** (Python) and the **CLI** (`jenticctl`,
  Go). `pip install` can only deliver the Python server — **not** the CLI, and the CLI
  *is* the front door (it holds the setup wizard). So `pip install jentic-one` would
  give you a bare server with **no** config, no migrations, no wizard — not a usable
  install.
- Making it usable would mean building a **second, Python-side onboarding flow**
  (`jentic-one setup`) that duplicates the Go wizard — real complexity for a near-empty
  audience (embedding the package / pure-PaaS deploys), which isn't a beta priority.
- It's not how comparable tools work — you install *the CLI*, and the CLI runs the
  server. (Claude Code etc. hand you one CLI, not a language package.)

If a genuine "embed the server as a library" use case appears later, a wheel can be
revisited then — but it is **not** the beta install path, and the docs should point
users at the CLI.

## Installing jentic-one (the two steps)

The user-facing install is two commands, which is **normal and idiomatic** for
self-hosted infra (Supabase, Temporal, Fly.io, Dagger all work this way — [References](#references--prior-art)):

```bash
brew install jentic       # 1 · install the CLI (or: curl | sh that downloads the binary)
jenticctl install         # 2 · the wizard: generate config, migrate the DB, start the server
```

**Why two steps:** they do different jobs. Step 1 (package manager) puts the *tool* on
your machine; step 2 (`jenticctl install`, the wizard) uses that tool to set up and run
the *product*. Step 2 can't exist before step 1 delivers the `jenticctl` command.

Things to get right so this isn't confusing:

- **The Homebrew formula must ship BOTH binaries** — `jentic` (client) *and* `jenticctl`
  (installer/operator). GoReleaser's Homebrew support handles multi-binary formulas.
  Otherwise `brew install jentic` wouldn't give you `jenticctl` and step 2 would fail.
- **`install` still needs a runtime underneath** — the wizard sets things up, but the
  server runs via the local venv (needs `uv`) or Docker (needs Docker). "Two commands"
  is the *interface*; the machine still needs the chosen runtime (the dependency-light
  path is local venv + SQLite).
- **Naming friction (`jentic` package vs `jenticctl` command):** minor, and a
  well-trodden split (`kubectl`/`kubeadm`, `docker`/`dockerd`). If we ever want to remove
  it, the alternative is a single binary with subcommands (`jentic install`) instead of
  two binaries — noted, not proposed for beta.

## CLI as a remote client (the VPC / different-host case)

The `jentic` CLI is **already a standalone HTTP client**: a separate Go module, no Python
dependency, and it already targets a remote instance via `--base-url` (control plane) +
`--broker-scheme`/`--broker-host` (broker), persisted per profile. An agent on host B can
already drive a jentic-one in a VPC on host A — this is documented in
[`docs/security/hardening.md`](../security/hardening.md).

**Implication:** the CLI is really a *client* to a server it may not have installed and
doesn't control (think `kubectl` ↔ Kubernetes). That affects versioning.

| Decision | Beta choice | Rationale |
| --- | --- | --- |
| **Versioning model** | **Lockstep for now** (one version), but **publish the CLI as standalone binaries** | Full decoupling (independent trains + a compatibility matrix) is real complexity for an unknown-audience beta. Lockstep is simple; the CLI still ships separately because agents run it elsewhere. |
| **Version-skew safety** | **Add now** | When the CLI talks to a remote instance, warn if its version ≠ the server's (`/health` already exposes the server version — today it's only displayed, never compared). |
| **Remote-client UX** | **Add now** | A single **instance URL** that derives both control-plane + broker, and a `JENTIC_BASE_URL` env var (only `JENTIC_HOME`/`JENTIC_PROFILE` exist today). Smooths the "point at a different broker/control plane" flow. |
| **Full decouple** | **Defer** | **Tripwire:** evidence agents run mismatched CLI/server versions against remote instances → split release trains + publish a compatibility window. |

## Update & version notifications (firm requirement)

Both the CLI and UI must nudge when a newer version exists. Privacy-respecting, Grafana/Gitea model ([References](#references--prior-art)):

- **Mechanism:** a one-way GET to the GitHub Releases API (or a static `versions.json`), compared to the running version. **Two skews to surface:** CLI/server **vs latest release**, and CLI **vs the remote server it's talking to** (from `/health`).
- **Surfaces:** server `/health` gains an optional `latest_version`/`update_available` field (server does the check; CLI + UI already read `/health`); CLI shows a dim "vX.Y.Z available" line (reuse `VersionPanel` + the existing `update` "Update available: X → Y" phrasing); UI shows a dismissible banner (the UI shows **no** version today — needs the admin `/health` schema to include `version` + a banner in the app shell).
- **Privacy:** pure pull, **sends no data**; **default-on but one flag off** (`check_for_updates=false` / `JENTIC_CHECK_FOR_UPDATES=false`); **separate from telemetry** (which stays opt-in); a single `--offline` silences all outbound calls.
- **Build-on:** add a semver lib to the Go CLI (`golang.org/x/mod/semver` — none today; the current `update` compares git SHAs, not tags).

## `jenticctl update` vs the package manager (a conflict to resolve)

**Today `jenticctl update` updates *everything*:** it rebuilds and swaps **both** CLI
binaries (`jenticctl` + `jentic`) **and** the server/stack (rebuild + forward-only
migrations + restart), all from a tracked **git ref**, comparing **commit SHAs** (not
release tags). `--cli-only` / `--stack-only` / `--check` scope it.

**Why that becomes a problem once we ship brew + prebuilt releases:** if a user
`brew install`s the CLI and then runs `jenticctl update`, the self-update **overwrites
the brew-managed binary**, so Homebrew's version tracking is now wrong (it thinks you're
on the released version, but the file is a from-source build of a branch). Two update
mechanisms fighting over the same binary is a real footgun — and it exists today only as
a *pre-release artifact* (before brew/releases, rebuild-from-git was the only option, so
it was simplest to have `update` do both halves).

**The fix (target design) — validated by how comparable tools resolve this:** the
binary is owned by whoever installed it, and the CLI respects that. No mature tool
overwrites a package-manager-managed binary in place. Concretely:

- **The CLI updates itself via its package manager** — `brew upgrade jentic`. `jenticctl
  update` should **detect the install source** and, when the binary is package-managed,
  **refuse to swap it** and instead print the exact upgrade command (the `gh` approach —
  detect + print, don't auto-run `brew`, which dodges the "bottle hasn't landed / brew
  update skipped" race `flyctl`/`gh` both hit). Detection is ~15 lines: compare
  `os.Executable()` against `$(brew --prefix)/bin` (copyable from flyctl's
  `isUnderHomebrew()`), or a build-time `packageManaged` flag stamped by the formula
  (gh/rustup/deno).
- **Keep in-place self-update only for the standalone `install.sh` path** — the existing
  `stageCLIBuild` + `ReplaceBinary` (with `.bak` rollback) is right for curl-installer
  users; just gate it behind "not package-managed."
- **`jenticctl update` narrows to the deployed product** — the server/stack + DB
  migrations, which the package manager genuinely can't manage. Every stack-managing tool
  (supabase, fly, gitlab-ctl, sentry) keeps "upgrade the CLI" and "upgrade the managed
  thing" as separate, differently-owned commands; a single verb that swaps the binary
  *and* migrates the backend (today's behavior) is the anomaly.
- **Add a Homebrew `caveats` block** stating that `brew upgrade jentic` updates the CLI
  and `jenticctl update` upgrades the stack — Homebrew's sanctioned way to set this
  expectation at install time (there is no `auto_updates` escape hatch for formulae).
- **Release-aware, not commit-aware:** once tags/releases + prebuilt binaries exist,
  version comparison should use **semver tags** and pull **prebuilt binaries** rather
  than recompiling a branch.
- **Borrow migration-safety conventions** (gitlab/sentry) for the stack half:
  one-version-at-a-time / hard-stop enforcement and a `--dry-run` preview, complementing
  the backup warning `updateStack` already prints.

See [References](#references--prior-art) for the tool-by-tool evidence (gh, gcloud,
flyctl, rustup, deno, supabase, Homebrew).

## Decisions needed (sign-off before building)

1. **Version baseline** — clean `v0.1.0` (honest for a new public repo) **or** continue `0.x` → `v0.14.0` (continuity, needs a "continues our internal predecessor" note). **Not `1.0.0`** while the README allows breaking changes without a major bump. *(No tag-collision risk — tags are local-only.)*
2. **Distribution (settled above):** the install is prebuilt signed CLI binaries (GoReleaser + brew); no Python wheel install path; Docker image deferred-but-honest; Helm deferred. Confirm.
3. **Versioning model (settled above):** lockstep for beta, CLI shipped separately, skew-warning + remote-UX added now, full decouple deferred. Confirm.
4. **App token** — reuse mini's `ARAZZO_BUILDER_APP_ID`-style app, or provision new.
5. **Doc home** — this proposal lives in `docs/plans/`; the ratified procedure likely belongs in `docs/` or `deploy/`.

---

<details>
<summary><b>Detail: current-state facts</b></summary>

| Thing | Reality today |
| --- | --- |
| **Version** | `pyproject.toml` = **`0.1.1`**, Helm charts = **`0.1.0`**, tags reach **`v0.13.2`** → three-way drift |
| **Tags** | `v0.1.0`…`v0.13.2` + 19 `backup/*` exist **only locally** — **0 tags on every remote** |
| **Automation** | None. 3 workflows (ci, dependabot, smoke-helm); no tag/release triggers; CI doesn't run on tags |
| **Changelog** | No `CHANGELOG.md`, no GitHub Releases, no `.github/release.yml` |
| **Install path** | `install.sh` / `jenticctl` **build from source** at a git ref — they never pull a registry artifact |
| **CLI** | Separate Go module, no Python dep; already targets a remote via `--base-url` + broker flags; **no** CLI↔server version check; no GoReleaser yet |

</details>

<details>
<summary><b>Detail: release-please + Helm gotchas</b></summary>

- **Root** uses `release-type: python` (bumps `pyproject.toml`); add a **`uv.lock`** updater/step or `uv sync --frozen` in CI breaks.
- **Helm:** manage the **umbrella** `Chart.yaml` (`version`+`appVersion`) and `observability` via `release-type: helm`. Only these 2 charts have `appVersion`; the 6 subcharts have `version` only.
- **Blocker to plan for:** the umbrella pins each subchart version in `dependencies:` and each subchart pins the `file://` `common` lib — **release-please won't rewrite these**, so bumping subcharts breaks `helm dependency build`. Fix: loosen `file://` pins or add `extra-files` updaters.
- **Image tag** comes from the **umbrella** `appVersion`/`global.image.tag`, not subchart appVersions.
- **Seed `.release-please-manifest.json`** to the baseline; if continuing at `v0.14.0`, set `bootstrap-sha` to a current-`main` commit so the first changelog doesn't replay pre-scrub history.

</details>

<details>
<summary><b>Detail: tag → publish pipeline</b></summary>

On the release-please tag (via App token — `GITHUB_TOKEN`, and `on: release`, won't fire downstream):

1. **CI-on-tag gate** — build + run migrations on a fresh DB + `/health` **before** the Release is published (tags get no CI today; scope `cancel-in-progress` to PRs).
2. **GoReleaser** — `jentic` + `jenticctl` binaries + `checksums.txt`, **cosign-signed**, Homebrew cask, attached to the Release. *(This is the install.)*
3. **(deferred)** Docker image + Helm OCI — only once their tripwires fire. When added: multi-arch, cosign-signed, SBOM, provenance, image Trivy scan; publish **umbrella + observability only**.

</details>

<details>
<summary><b>Detail: changelog & upgrade notes</b></summary>

- **`CHANGELOG.md`** in Keep-a-Changelog format, generated by release-please and **editable in the Release PR** (the human-curation gate). One root file, sectioned by commit scope (CLI vs server findable).
- **`UPGRADING.md`** with Vector-style "Action needed" blocks for any migration/breaking release — the operator "what must I *do*" surface, separate from "what *changed*".
- **`.github/release.yml`** label categories; operators read GitHub Releases (Watch→Releases).

</details>

<details>
<summary><b>Detail: migrations, governance, housekeeping (gaps to close)</b></summary>

- **Migrations (~65, forward-only, "data unrecoverable"):** upgrade **one minor at a time**; rollback = restore backup + pin previous tag; **backup is a required pre-upgrade step**; define a hotfix flow (`release-0.X` branch → patch tag).
- **Governance:** protect the Release PR (merging it *is* the ship action); confirm release-please/bot commits satisfy DCO; add a "Releases" section to `CONTRIBUTING.md`; reconcile the git-conventions "auto-generated changelog" wording.
- **Housekeeping:** fix the 3-way version drift; local-only cleanup of `v0.*` + `backup/*` tags (don't confuse release-please — hygiene only); add `VERSIONING.md`; the stale `broker.jentic.ai` default in `skillgen/content/jentic.md` vs the real `127.0.0.1:8100`.

</details>

<details>
<summary><b>Detail: what jentic-mini did (proven prior art)</b></summary>

Node **semantic-release**, push-to-`main`: analyze commits → notes → stamp `pyproject.toml` +
`Dockerfile` → commit `chore(release): cut X.Y.Z` + tag → publish GitHub Release → trigger
`docker-publish.yml` → `ghcr.io/jentic/jentic-mini`, bridged by the `ARAZZO_BUILDER_APP_ID`
App token. Removed in the OSS scrub. We're re-establishing a proven model, adapted to the
polyglot-friendly release-please.

</details>

## Implementation order (once decisions land)

1. Fix version drift (pyproject + 9 charts) + add `VERSIONING.md`; local tag cleanup.
2. Add `release-please` (manifest, lockstep, seeded + `bootstrap-sha`, Helm-pin handling, `uv.lock`).
3. Cut the first Release PR; verify version + changelog (no pre-scrub replay).
4. Tag-triggered publish behind the App token: **GoReleaser CLI binaries** (`jenticctl` + `jentic`, signed, + brew) + notes/checksums — this is the install. (Docker/Helm deferred to their tripwires; no wheel.)
5. **Version notifications:** `/health` `latest_version` field + CLI passive nudge + CLI↔server skew warning + UI version/banner; add the Go semver lib; wire the `check_for_updates` flag.
6. **Remote-client UX:** single instance-URL config + `JENTIC_BASE_URL` env var.
7. **Rework `jenticctl update`:** detect install source, let brew/installer own the CLI binary, narrow `update` to server/stack + migrations, and make version comparison release-tag-aware (not commit-SHA).
8. Add `CHANGELOG.md` + `UPGRADING.md` + `.github/release.yml`; reconcile git-conventions + CONTRIBUTING; document migration/upgrade/hotfix policy.

## References / prior art

Sources behind the "other projects do this" claims in this doc (gathered during research;
verify before treating any as load-bearing — some are point-in-time blog posts).

**Distribution & CLI packaging**
- Sentry self-hosted — `git clone` + install script that *pulls* images: <https://develop.sentry.dev/self-hosted/>
- Meilisearch install (binary / docker / brew / apt; source-build last): <https://www.meilisearch.com/docs/learn/getting_started/installation>
- GoReleaser (cross-compile, sign, checksums, Homebrew, attach to Release): <https://goreleaser.com/>
- GitHub CLI (`gh`) install (prebuilt binaries + brew, not source): <https://github.com/cli/cli#installation>
- Homebrew formula shipping multiple binaries via GoReleaser: <https://goreleaser.com/customization/homebrew/>

**Helm deferral**
- PostHog sunsetting Helm support: <https://posthog.com/blog/sunsetting-helm-support-posthog>
- Supabase declining an in-repo Helm chart (support-load rationale): <https://github.com/supabase/supabase/discussions/6603>
- Meilisearch Kubernetes chart kept in a separate repo: <https://github.com/meilisearch/meilisearch-kubernetes>

**Two-step install (CLI then setup)**
- Supabase CLI: <https://supabase.com/docs/guides/local-development/cli/getting-started>
- Temporal CLI: <https://docs.temporal.io/cli>
- Fly.io `flyctl`: <https://fly.io/docs/flyctl/install/>

**Update / version notifications**
- Grafana `check_for_updates` (pull-only, no data sent): <https://grafana.com/docs/grafana/latest/setup-grafana/configure-grafana/#check_for_updates>
- GitHub Releases API (version source): <https://docs.github.com/en/rest/releases/releases#get-the-latest-release>

**CLI self-update vs. package manager**
- `gh` — no self-update; prints the package-manager command (design rationale): <https://github.com/cli/cli/issues/166> · build-flag notice: <https://github.com/cli/cli/issues/10242>
- flyctl `isUnderHomebrew()` (runtime brew-prefix detection, copyable Go): <https://github.com/superfly/flyctl/blob/master/internal/update/update.go>
- gcloud — component update disabled under a package manager: <https://cloud.google.com/sdk/docs/components>
- rustup `no-self-update` build: <https://rust-lang.github.io/rustup/basics.html>
- deno — disable self-upgrade for package-manager installs: <https://github.com/denoland/deno/pull/19910>
- Homebrew FAQ (evergreen; `auto_updates` is casks-only) + `caveats`: <https://docs.brew.sh/FAQ>
- Supabase CLI (package-manager-updated; CLI verbs act on the managed stack): <https://supabase.com/docs/guides/local-development/cli/getting-started>
- Stateful-upgrade safety (one-version-at-a-time / backups): GitLab <https://docs.gitlab.com/update/plan_your_upgrade/> · Sentry <https://develop.sentry.dev/self-hosted/releases/>

**Changelog / release notes**
- Keep a Changelog: <https://keepachangelog.com/en/1.1.0/>
- release-please (Release PR + CHANGELOG + GitHub Release): <https://github.com/googleapis/release-please>
- GitHub auto-generated release notes + `.github/release.yml`: <https://docs.github.com/en/repositories/releasing-projects-on-github/automatically-generated-release-notes>
- Vector upgrade guides ("Action needed" blocks) + VERSIONING: <https://vector.dev/highlights/> · <https://github.com/vectordotdev/vector/blob/master/VERSIONING.md>

**Supply chain (for when images ship)**
- cosign / keyless signing: <https://docs.sigstore.dev/cosign/signing/signing_with_containers/>
- SLSA build provenance (`actions/attest-build-provenance`): <https://github.com/actions/attest-build-provenance>

