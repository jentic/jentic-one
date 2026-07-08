# Release Procedure — Proposal

> **STATUS: PROPOSAL for review.** Documents where jentic-one's release process
> stands today, what we did before (jentic-mini), what mature OSS projects do, and a
> recommended procedure. Nothing here is wired up. Open decisions are **DECISION**
> blocks. Revised after a multi-lens review (facts re-verified against the repo on
> the `docs/release-procedure-plan` branch).
>
> **Supersedes** the manual "To bump the release" steps in
> [`deploy/README.md`](../../deploy/README.md) ("Version source of truth") — adopting
> this means updating that section too.

## TL;DR

- jentic-one **inherited a full semantic-release → tag → GitHub Release → GHCR
  publish pipeline from jentic-mini** (reached `v0.13.2`), but the **2026-07-01 OSS
  scrub removed all of it** and the OSS repo's version files were (re)introduced at
  `0.1.x`. Today there is **no versioning / tagging / release / changelog
  automation** — only CI.
- The *inputs* survive: **Conventional Commits + squash-merge are enforced** (Python
  `cz` + lefthook + `tests/arch/test_commit_convention.py`). Nothing consumes them,
  so the git-conventions rule still refers to an "auto-generated changelog" that no
  longer exists.
- Recommendation: adopt **release-please** (language-agnostic, reviewable Release PR,
  polyglot-native) → on its tag, run **GoReleaser + Docker + Helm-to-GHCR**. Version
  baseline is an **open decision** (§4.1) — a clean `v0.1.0` and continuing `0.x`
  (`v0.14.0`) are both viable; `1.0.0` is not, while the README promises breaking
  changes without a major bump.
- **Before any of this ships, resolve the distribution disconnect (§4.7):** the
  install path builds from source at a git ref — it does **not** pull a published
  tagged image/chart. Releasing artifacts nobody installs is pointless until this is
  reconciled.

## 1. Current state (verified facts)

| Concern | Today |
| --- | --- |
| Version source of truth | `pyproject.toml [project].version` → `scripts/version.sh` → Make `$(VERSION)` → Docker tag; Helm `appVersion` mirrored **manually** |
| Declared version | **`pyproject.toml` = `0.1.1`**; all Helm `Chart.yaml` = `0.1.0`; Go CLI defaults `version = "dev"` (stamped via ldflags). **Three-way drift** vs the `v0.13.2` tag line |
| Helm charts | **9 `Chart.yaml`**: umbrella `jentic-one` + 6 app subcharts (broker, app, registry, admin, control, gateway) + a `common` **library** chart + a separate `observability` umbrella. **Only 2 carry `appVersion`** (umbrella + observability); subcharts have `version` only. The umbrella also **pins each subchart version** in its `dependencies:` block (all `0.1.0`), plus `postgresql 16.4.3` |
| Image tag source | The container image tag resolves from `global.image.tag` (set by the root Makefile from pyproject) → **umbrella** `.Chart.AppVersion` → `latest` (`charts/common/templates/_image.tpl`). Subchart `appVersion` is **not** involved |
| Tags | Semver line `v0.1.0`…**`v0.13.2`**, all **lightweight**; plus **19** `backup/*` snapshot tags. **All of these exist only in local clones — `git ls-remote --tags origin` returns 0 tags on every remote (origin/opensource/internal)** |
| How `0.1.x` came to be | Not an in-place `0.13.2 → 0.1.0` edit. During the OSS cutover, `pyproject.toml` + all `Chart.yaml` were **(re)introduced as new files at `0.1.0`** on a rewritten history (commit `2654c92c` adds them; there was no `0.13.2` in its parent). pyproject later moved to `0.1.1` |
| Release automation | **None.** `.github/workflows/` has only `ci.yml`, `dependabot-auto-merge.yml`, `smoke-helm.yml`. No `tags:`/`release:` triggers. CI does **not** run on tags |
| Docker/GHCR publish | **Commented-out TODO** scaffolding in `ci.yml` |
| Changelog | No `CHANGELOG.md`, no `.github/release.yml` |
| Install / update path | `tools/install.sh` clones `JENTIC_REPO@JENTIC_REF` (default **`main`**) and builds; `jenticctl install` builds the app image locally as `jentic-one/app:jentic-cli`; `jenticctl update` tracks a **git commit**, not a tag. **Nothing pulls `ghcr.io/...:X.Y.Z`** |

## 2. What we did before (jentic-mini)

The old pipeline (still live in the sibling `jentic-mini/` checkout, `pyproject` at
`0.13.2`) was **Node semantic-release**, push-to-`main`, fully automated:

`.releaserc.json` → `commit-analyzer` computes next `vX.Y.Z` → `release-notes-generator`
→ `exec` stamps `pyproject.toml` + `Dockerfile ARG APP_VERSION` → `git` commits
`chore(release): cut the X.Y.Z release` + lightweight tag → `@semantic-release/github`
publishes the Release → triggers `docker-publish.yml` pushing to
`ghcr.io/jentic/jentic-mini`. A **GitHub App token** (`ARAZZO_BUILDER_APP_ID`) let the
release commit's tag trigger the downstream publish.

That stack was removed in the OSS scrub in favor of the Python hooks.

**Lesson:** we already proved the Conventional-Commit-driven release model here — we're
re-establishing a proven process, not inventing one, adapted from Node/semantic-release
to a polyglot-friendly tool.

## 3. What mature OSS projects do (benchmark)

- **Conventional-commit auto-release:** `release-please` (gated Release-PR,
  language-agnostic, polyglot/monorepo-native), `semantic-release` (push-to-main,
  weak polyglot), `changesets` (JS-centric), `git-cliff` (notes only).
- **Polyglot artifacts:** prefer a **single lockstep version** for a self-hosted
  product; move to independent versioning only if CLI/chart cadence diverges.
- **Go CLI:** **GoReleaser** on tag push — cross-platform binaries + `checksums.txt`
  (cosign-signed) attached to the GitHub Release. **The version is stamped from the
  git tag via ldflags — never a committed source literal.**
- **Docker + Helm:** tag images to the release; publish the chart to GHCR OCI; sign +
  attest images (cosign / SBOM / SLSA provenance).
- **Release notes:** categorized (feat/fix/breaking), not raw dumps.
- **Pre-1.0:** SemVer `0.x` = unstable; breaking → **minor** bump (release-please
  `bump-minor-pre-major: true`). Vector's `VERSIONING.md` is the model.

## 4. Recommended procedure

### 4.1 Version baseline — DECISION (needs ratification)

Three viable options; **`1.0.0` is excluded** (it promises backward compatibility,
the opposite of the README's "breaking changes without a major bump" beta policy, and
is the least reversible choice).

- **(a) Clean `v0.1.0`** for the new public repo. Most honest about the *public*
  project's age (jentic-one is a new public repo with rewritten history and **no
  public release lineage** — the `v0.1..v0.13` tags were never pushed; §1). Pair with
  a one-line "open-source successor to our internal platform" note. Cost: under-sells
  the engineering maturity of the code.
- **(b) Continue `0.x` → `v0.14.0`.** Preserves internal continuity and signals
  "past infancy." Cost: to an external adopter the number implies 13 public releases
  that don't exist; needs a release-notes sentence explaining the gap.
- **(c) Fresh `0.1.0` new lineage** — effectively (a).

**There is no tag-collision constraint.** An earlier draft argued `0.1.0` would
collide with existing tags; verified false — **0 tags on every remote**. The only tag
hygiene needed is a **one-time local cleanup** (`git tag -d 'v0.*' 'backup/*'`) and
confirming `git ls-remote --tags origin` is empty, and it applies to **all** options.

**Recommendation:** lean **(a) clean `v0.1.0`** for honesty to external adopters, or
**(b) `v0.14.0`** as a defensible compromise if internal continuity is valued (add
the explanatory note). Either is `0.x`, SemVer-correct for beta, and mechanically
clean. Configure release-please `bump-minor-pre-major: true`. Reserve `1.0.0` for when
the README's breaking-changes caveat is removed.

### 4.2 Tooling: release-please (manifest, single lockstep version)

- `release-please` **manifest mode**, one lockstep version. Root uses `release-type:
  python` (bumps `pyproject.toml`; add `uv.lock` as an `extra-files`/post-bump `uv
  lock` step — release-please won't touch the lockfile and `uv sync --frozen` in CI
  will otherwise fail).
- **Helm:** manage the **umbrella** `Chart.yaml` (`version` + `appVersion`) and
  `observability` via `release-type: helm`. **The subchart version pins in the
  umbrella `dependencies:` block and each subchart's `file://` `common` pin are NOT
  managed by release-please** — bumping subchart `version` while leaving the pins at
  `0.1.0` **breaks `helm dependency build`**. Fix by either (i) loosening `file://`
  dep pins to `">=0.0.0"`, or (ii) adding explicit `extra-files` updaters for the
  pinned versions. This is the most likely first-release breakage.
- `bump-minor-pre-major: true` for the beta breaking-change policy.
- Merging the standing **Release PR** is the release action (reviewable changelog +
  version before publish — safer than push-to-main for a self-hosted product).
- **Seed `.release-please-manifest.json`** to the chosen baseline. If continuing at
  `v0.14.0`, also set `bootstrap-sha`/`last-release-sha` to a current-`main` commit so
  the first changelog starts at the OSS cutover — otherwise the rewritten history may
  replay pre-scrub commits into the first release notes (§4.1 history was rewritten).

### 4.3 The Go CLI — stamped by GoReleaser, not release-please

The CLI version (`cli/internal/cmd/root.go`) is `version = "dev"`, a **build-time
ldflag placeholder** — it is deliberately not a committed release literal. Do **not**
put the CLI in the release-please manifest. Instead **GoReleaser stamps the version
from the git tag** (`-ldflags -X …cmd.version={{.Version}}`), so the CLI is lockstep
*by being built from the release tag*, with no committed version to drift.
(`cli/Makefile`'s `VERSION ?= 0.1.0` default only affects non-GoReleaser local builds;
cosmetic.)

### 4.4 Tag → publish

On the release-please tag:

1. **GoReleaser** — `jentic` + `jenticctl` cross-platform binaries + `checksums.txt`,
   **cosign-signed**, attached to the GitHub Release.
2. **Docker buildx** — **multi-arch** (confirm the base image supports arm64) →
   `ghcr.io/jentic/jentic-one/*:X.Y.Z`, **cosign-signed + SBOM (syft) + SLSA
   provenance** (`actions/attest-build-provenance`). Add a Trivy **image** scan gate
   (CI currently only scans the filesystem).
3. **Helm** — patch the **umbrella** `version`/`appVersion` to `X.Y.Z`, `helm push …
   oci://ghcr.io/jentic/...`, with a duplicate-version guard. **Publish only the
   umbrella (and separately `observability`)** — subcharts are bundled into the
   umbrella `.tgz` via `file://` deps and have no standalone consumer, and `common` is
   a **library** chart that cannot be installed. Do not publish subcharts
   independently.
4. **GHCR package visibility = public** (anonymous `helm pull` / install), with a
   minimal `permissions:` block (`contents: write`, `packages: write`) on the release
   job.

### 4.5 Trigger bridge — DECISION: reuse a GitHub App token

release-please's `GITHUB_TOKEN` **cannot trigger downstream workflows** (GitHub
loop-prevention). Note `on: release` does **not** avoid this — a Release created via
`GITHUB_TOKEN` won't fire `on: release` either. **Use a GitHub App token** on the
release-please action (mini used `ARAZZO_BUILDER_APP_ID`) so the tag/Release is
attributed to the App and downstream `on: push: tags:` / `on: release:` fires. (A
`repository_dispatch` from the release job, with `actions: write`, is a valid
alternative.)

### 4.6 Release notes & changelog

- release-please Conventional-Commit `CHANGELOG.md` (committed) + GitHub Release body.
- `.github/release.yml` label categories, with an explicit **Breaking Changes**
  section operators must see on upgrade.
- **CLI vs server notes:** a single lockstep version means one `CHANGELOG.md` mixing
  Go CLI and Python server commits. Keep the version lockstep but use commit `scope`
  to section CLI vs server changes so each audience can find its own.
- Operators read **GitHub Releases** (with authored **Upgrade Notes**); `CHANGELOG.md`
  is the raw dev feed.

### 4.7 Distribution reconciliation — DECISION (gates the whole plan)

The published image/chart is **orphaned from how the product installs**: `install.sh`
(`JENTIC_REF` default `main`) and `jenticctl install`/`update` **build from source at a
git ref** and never pull `ghcr.io/...:X.Y.Z`. So "releasing v0.X.0" would ship
artifacts nobody installs. Decide one of:

- **(a)** Make the install path pull the released image and default `JENTIC_REF` to
  the tag — the artifacts become the install; or
- **(b)** Explicitly scope the first release as "source-built; the tag is a git
  marker," and sequence image-consuming install as a follow-up.

Also: document `JENTIC_REF=vX.Y.Z curl … | sh` as the supported "install a specific
version" path; point `install.sh` at GoReleaser binaries (checksum-verified) with
source build as fallback; consider a Homebrew tap.

### 4.8 Upgrade, migrations & support policy

This product ships **~65 forward-only Alembic migrations across 4 schemas** with a
"back up first — data unrecoverable" warning. A release procedure must state:

- **Upgrade policy:** upgrade **one minor at a time**; each release tests migrations
  from `N-1` (no version-skipping guarantee otherwise).
- **Rollback:** roll forward only; documented rollback = restore DB backup + pin the
  previous tag. Make a backup a **required pre-upgrade step**, not just a printed
  warning.
- **Support / hotfix:** `SECURITY.md` supports "latest only" and there are no release
  branches — define a minimal hotfix flow (cherry-pick to `release-0.X` → patch tag)
  so a critical fix can reach operators without dragging in all of `main`.
- **Deprecation:** one-minor notice window for config/API breaks.

### 4.9 Release governance & CI

- **Release authority:** merging the Release PR **is** the ship action — protect it
  (CODEOWNERS review + branch protection) and document who holds release authority.
- **CI on tags:** `ci.yml` triggers only on `main`, so the released tag gets **no
  CI**; and `cancel-in-progress: true` is unconditional (the exact concurrency bug the
  git-conventions rule already documents). Run a build+migrate+`/health` smoke gate on
  `refs/tags/v*` **before** the Release is marked published; scope
  `cancel-in-progress` to PR branches.
- **Failed-release recovery:** document "roll forward, never delete published
  artifacts" and per-step idempotency (the tag already exists if a later step fails).

### 4.10 Housekeeping

- Fix the three-way version drift (set pyproject + all charts to the chosen baseline).
- **Local** tag cleanup: delete `v0.*` (per §4.1) and relocate/delete `backup/*`.
  (Note: `backup/*` tags do **not** confuse release-please's version detection — it
  matches only `v${version}` — so this is hygiene, not a correctness blocker.)
- Add a Vector-style **`VERSIONING.md`**: while `0.x`, minor may break, patch never
  does, every breaking minor ships an upgrade guide.
- Reconcile the git-conventions rule wording with the re-established changelog tool,
  and confirm release-please/bot commits satisfy the DCO sign-off CONTRIBUTING
  requires (or exempt bots). Add a "Releases" section to `CONTRIBUTING.md`.
- **Decide the home** for the ratified procedure (this file is a proposal in
  `docs/plans/`, which otherwise holds per-issue implementation plans) — likely
  `docs/` or `deploy/`, superseding `deploy/README.md`'s versioning section.
- **Observability chart:** decide explicitly whether it's lockstep with the app or
  versioned independently (it's an optional, third-party-dependent umbrella).

## 5. End-to-end flow (proposed)

```
PR merged to main (Conventional Commit, squash)
        │
        ▼
release-please updates/opens the Release PR ──► maintainer reviews CHANGELOG + version
        │  (merge protected Release PR = the ship action)
        ▼
release-please: bump pyproject + umbrella chart + uv.lock, write CHANGELOG.md,
                tag vX.Y.Z, create GitHub Release
        │  (tag pushed via GitHub App token so downstream fires)
        ├──────────► CI-on-tag gate: build + migrate on fresh DB + /health smoke
        ├──────────► GoReleaser: CLI binaries + checksums.txt (cosign) → attach to Release
        ├──────────► Docker buildx: push ghcr.io/jentic/jentic-one/*:X.Y.Z (multi-arch, signed, SBOM, provenance)
        └──────────► Helm: umbrella + observability → oci://ghcr.io/jentic/... (not subcharts)
```

## 6. Open decisions (need sign-off before implementation)

1. **Version baseline (§4.1)** — clean `v0.1.0` (recommended for public honesty) vs
   continue `0.x` → `v0.14.0` (continuity, needs an explanatory note). Not `1.0.0`.
2. **Distribution (§4.7)** — install pulls the released image (default `JENTIC_REF` =
   tag) vs first release is source-built with the tag as a git marker. **Gates the
   rest.**
3. **Lockstep vs independent** versioning for the Go CLI / observability chart
   (recommend lockstep app+CLI; decide observability separately).
4. **App token** — reuse `ARAZZO_BUILDER_APP_ID`-style app, or provision a new one.
5. **`backup/*` + local `v0.*` tags** — delete vs relocate (local-only; no remote
   impact).
6. **Home for the ratified procedure** — `docs/`, `deploy/`, or a root `RELEASE.md`.

## 7. Suggested implementation order (once decisions land)

1. Fix version drift (pyproject + 9 charts) + add `VERSIONING.md`; local tag cleanup.
2. Reconcile the distribution decision (§4.7) — it gates whether published artifacts
   matter.
3. Add `release-please` config + workflow (manifest, lockstep, `bump-minor-pre-major`,
   seeded manifest + `bootstrap-sha`), including the Helm dependency-pin handling and
   `uv.lock` updater.
4. Cut the first Release PR; verify the proposed version + changelog (esp. that it
   doesn't replay pre-scrub history).
5. Add the tag-triggered publish workflow (CI-on-tag smoke gate → GoReleaser + Docker
   (signed/SBOM/provenance) + Helm-OCI umbrella-only) behind the App token.
6. Add `.github/release.yml`; reconcile the git-conventions rule + CONTRIBUTING;
   document upgrade/rollback/hotfix policy.
