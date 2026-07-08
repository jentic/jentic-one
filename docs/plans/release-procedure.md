# Release Procedure — Proposal

> **STATUS: PROPOSAL for review.** This documents where jentic-one's release
> process stands today, what we did before (jentic-mini), what mature OSS projects
> do, and a concrete recommended procedure. Nothing here is wired up yet — it's for
> discussion. Open decisions are called out as **DECISION** blocks.

## TL;DR

- jentic-one **inherited a full semantic-release → tag → GitHub Release → GHCR
  publish pipeline from jentic-mini** (reached `v0.13.2`), but the **2026-07-01 OSS
  scrub deleted all of it** and reset the version to `0.1.0`. Today there is **no
  versioning / tagging / release / changelog automation** — only CI.
- The *inputs* survive: **Conventional Commits + squash-merge are still enforced**
  (Python `cz` + lefthook). Nothing consumes them anymore, so the git-conventions
  rule still refers to an "auto-generated changelog" that no longer exists.
- Recommendation: adopt **release-please** (language-agnostic, reviewable Release
  PR, polyglot-native) → on its tag, run **GoReleaser + Docker + Helm-to-GHCR**.
  Continue the **0.x line** (resume from `v0.13.2`, next release `v0.14.0`); reserve
  `1.0.0` for a deliberate stability commitment.

## 1. Current state (facts)

| Concern | Today |
| --- | --- |
| Version source of truth | `pyproject.toml [project].version` → `scripts/version.sh` → Make `$(VERSION)` → Docker tag; Helm `appVersion` mirrored **manually** |
| Declared version | `0.1.0` in `pyproject.toml`, umbrella + 6 subchart `Chart.yaml`, observability chart; Go CLI defaults `version = "dev"` (stamped via ldflags) |
| Tags | Semver line `v0.1.0`…**`v0.13.2`** (2026-05-20), all **lightweight**; plus ~19 `backup/*` snapshot tags polluting the namespace |
| Version drift | Files say `0.1.0`; tags top out at `v0.13.2` — reset manually in `2654c92c` during the OSS cutover |
| Release automation | **None.** `.github/workflows/` has only `ci.yml`, `dependabot-auto-merge.yml`, `smoke-helm.yml`. No `tags:`/`release:` triggers |
| Docker/GHCR publish | **Commented-out TODO** scaffolding in `ci.yml` |
| Changelog | No `CHANGELOG.md`, no `.github/release.yml` |
| Commit hygiene | Conventional Commits enforced (`cz check` commit-msg hook + `tests/arch/test_commit_convention.py`), squash-merge mandated by the git-conventions rule |

## 2. What we did before (jentic-mini)

The old pipeline (still live in the sibling `jentic-mini/` checkout, `pyproject` at
`0.13.2`) was **Node semantic-release**, push-to-`main`, fully automated:

`.releaserc.json` → `@semantic-release/commit-analyzer` computes next `vX.Y.Z` from
Conventional Commits → `release-notes-generator` → `exec` stamps `pyproject.toml` +
`Dockerfile ARG APP_VERSION` → `git` commits `chore(release): cut the X.Y.Z release`
+ lightweight tag → `@semantic-release/github` publishes the Release → triggers
`docker-publish.yml` pushing to `ghcr.io/jentic/jentic-mini`. A **GitHub App token**
(`ARAZZO_BUILDER_APP_ID`) let the release commit's tag trigger the downstream publish
(the `GITHUB_TOKEN`-can't-trigger-workflows problem).

That whole stack (`.releaserc.json`, `release.yml`, `docker-publish*.yml`, husky,
commitlint, `.nvmrc`) was removed in the OSS scrub in favor of the Python hooks.

**Lesson:** we already proved the Conventional-Commit-driven release model works
here. We're not inventing a process — we're re-establishing one, adapted from
Node/semantic-release to a polyglot-friendly tool.

## 3. What mature OSS projects do (benchmark)

- **Conventional-commit auto-release:** `release-please` (Release-PR, gated,
  language-agnostic, polyglot/monorepo-native), `semantic-release` (push-to-main,
  auto, weak polyglot), `changesets` (JS-centric), `git-cliff` (notes only).
- **Polyglot artifacts (Py + Go + Helm + Docker):** prefer a **single lockstep
  version** across all artifacts for a self-hosted product (operators say "I run
  X.Y.Z"); move to independent versioning only if the CLI/chart drift.
- **Go CLI:** **GoReleaser** on tag push — cross-platform binaries + `checksums.txt`
  (+ cosign signing) attached to the GitHub Release.
- **Docker + Helm:** tag images to the release version, push to GHCR; publish the
  Helm chart to **GHCR OCI**; keep chart `version` == `appVersion` == image tag in
  beta.
- **Release notes:** categorized (feat/fix/breaking), not raw commit dumps —
  release-please changelog + `.github/release.yml` label categories; keep a
  `CHANGELOG.md`.
- **Pre-1.0:** SemVer says `0.x` = unstable; the common convention (and
  release-please `bump-minor-pre-major: true`) is breaking → **minor** bump while
  `0.x`. Vector's `VERSIONING.md` is the model: "minor may break; every breaking
  minor ships an upgrade guide."

## 4. Recommended procedure

### 4.1 Version baseline — **DECISION (needs ratification)**

**Recommendation: continue the 0.x line — resume from `v0.13.2`, first OSS release
`v0.14.0`.**

- **Not `1.0.0`:** the README says public beta, breaking changes without major bump,
  not production-recommended — that is the *opposite* of what 1.0 promises. 1.0 should
  be an earned milestone (production-ready + compatibility commitment).
- **Not a `0.1.0` reset:** the code at cutover is the mature mini codebase; `0.1.0`
  misrepresents maturity **and collides with the existing `v0.1.0`…`v0.13.2` tags**
  (release-please could compute an already-used tag).
- **Continue 0.x:** honest ("same product, now OSS, still beta"), no tag collisions,
  matches the beta breaking-change policy. Fix the `0.1.0`-in-files drift as part of
  adopting this.

Alternatives on the table: **(b)** clean break at `v1.0.0` for launch (old planning
doc leaned here); **(c)** genuine fresh start at `0.1.0` with a new tag lineage
(requires clearing/relocating the old semver tags to avoid collisions).

### 4.2 Tooling: release-please (manifest, single lockstep)

- `release-please` in **manifest mode**, one lockstep version across `pyproject.toml`,
  Helm umbrella + 6 subcharts (`version` + `appVersion`), observability chart, and the
  Go CLI ldflags default.
- `bump-minor-pre-major: true` → `feat!:` produces `0.(N+1).0` while beta.
- Merging the standing **Release PR** is the release action (reviewable changelog +
  version bump before anything ships — safer for a self-hosted product than
  push-to-main).

### 4.3 Tag → publish (re-establish, cleaner than mini)

On the tag release-please creates:

1. **GoReleaser** — `jentic` + `jenticctl` cross-platform binaries + `checksums.txt`,
   cosign-signed, attached to the GitHub Release.
2. **Docker buildx** — multi-arch images → `ghcr.io/jentic/jentic-one/*:X.Y.Z`.
3. **Helm** — patch chart `version`/`appVersion` to `X.Y.Z`, `helm push … oci://ghcr.io/jentic/...`,
   with a duplicate-version guard.

### 4.4 Trigger bridge — **DECISION: reuse a GitHub App token**

release-please's `GITHUB_TOKEN` **cannot** trigger the downstream publish workflows.
Reuse the mini approach: a **GitHub App token** (mini used `ARAZZO_BUILDER_APP_ID`) on
the release-please action (or a `release`-triggered publish workflow) so the tag fires
GoReleaser/Docker/Helm.

### 4.5 Release notes

- release-please Conventional-Commit `CHANGELOG.md` (committed) + GitHub Release body.
- `.github/release.yml` label categories as a backstop, with an explicit **Breaking
  Changes** section operators must see on upgrade.

### 4.6 Housekeeping

- Fix the `0.1.0` ↔ tag drift (set files to the chosen baseline).
- Move/delete the `backup/*` tags out of the release namespace (they're local
  snapshots, not releases).
- Add a Vector-style **`VERSIONING.md`**: while `0.x`, minor may break, patch never
  does, every breaking minor ships an upgrade guide.
- Reconcile the git-conventions rule wording with the re-established changelog tool.

## 5. End-to-end flow (proposed)

```
PR merged to main (Conventional Commit, squash)
        │
        ▼
release-please updates/opens the Release PR ──► maintainer reviews CHANGELOG + version
        │  (merge Release PR)
        ▼
release-please: bump all version files, write CHANGELOG.md, tag vX.Y.Z, create GitHub Release
        │  (tag push — via GitHub App token so downstream fires)
        ├──────────► GoReleaser: CLI binaries + checksums.txt (+cosign) → attach to Release
        ├──────────► Docker buildx: push ghcr.io/jentic/jentic-one/*:X.Y.Z (multi-arch)
        └──────────► Helm: set version/appVersion=X.Y.Z → push oci://ghcr.io/jentic/charts
```

## 6. Open decisions (need sign-off before implementation)

1. **Version baseline** — continue `0.x` from `v0.14.0` (recommended) / clean `v1.0.0`
   / fresh `0.1.0` new lineage.
2. **Lockstep vs independent** versioning for the Go CLI (recommend lockstep to start).
3. **App token** — reuse `ARAZZO_BUILDER_APP_ID`-style app, or provision a new one.
4. **Chart publish target** — GHCR OCI (recommended) vs classic `gh-pages` HTTP repo.
5. **`backup/*` tags** — delete vs relocate to a `refs/backup/*` namespace.

## 7. Suggested implementation order (once decisions land)

1. Fix version drift + add `VERSIONING.md` (safe, standalone).
2. Add `release-please` config + workflow (manifest, lockstep, bump-minor-pre-major).
3. Cut the first Release PR; verify the version + changelog it proposes.
4. Add the tag-triggered publish workflow (GoReleaser + Docker + Helm-OCI) behind the
   App token.
5. Add `.github/release.yml`; reconcile the git-conventions rule.
6. Tag namespace cleanup.
