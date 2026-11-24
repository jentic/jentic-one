# Supported upgrades

How far back a direct upgrade is tested in CI, how that window relates to the
pre-1.0 [versioning policy](../../VERSIONING.md), and how intentional breaks are
recorded. For the step-by-step upgrade procedure itself, see
[Upgrades](upgrades.md#the-contract) and the per-release
[Upgrade notes](upgrade-notes.md).

## Support window (N-1 and N-2 minor series)

Direct, single-hop upgrades to a target `0.N.x` release are supported from the
**previous two minor series (`N-1` and `N-2`)**, with each minor series
collapsed to its **latest published patch release** (see
[Backends and CI enforcement](#backends-and-ci-enforcement) for Phase 1 CI leg
gating):

- Operators on an older patch within a supported minor series (`0.(N-1).0` when
  `0.(N-1).2` exists) should upgrade from the latest patch of that minor series,
  or upgrade directly to the latest release following the [upgrade notes](upgrade-notes.md).
- The window rolls forward automatically with each release: CI derives the
  two source releases from the published `v*` tags, so nothing is edited by
  hand when a release is cut (see [Backends and CI enforcement](#backends-and-ci-enforcement)).
- Downgrades are not supported. Rolling back requires restoring the
  pre-upgrade database and keyset [backup](backup-restore.md)
  (see [Upgrades → The contract](upgrades.md#the-contract)).

### How this relates to `VERSIONING.md`

During the `0.x` public beta, [`VERSIONING.md`](../../VERSIONING.md#pre-10-policy-public-beta)
sets two rules:

1. **Bug fixes land on the latest release only** — there are no maintained
   backport branches for `N-1` or `N-2`.
2. **Minor releases (`0.N` → `0.(N+1)`) may include breaking changes** to APIs,
   database schemas, CLI flags, or configuration.

Supporting upgrades *from* `N-1` and `N-2` means that **data and state written
by those two releases migrate forward cleanly** when you follow the
[upgrade contract](upgrades.md#the-contract) and any required steps in
[`upgrade-notes.md`](upgrade-notes.md). It is a forward-migration guarantee,
not a promise to backport fixes onto older release lines.

## Historical waypoints (`0.39.x` and older → `0.40.x` → `0.41.1`)

Deployments running **0.39.x or older** cannot jump directly to a current
release. They must step through **`0.40.x`** and then **`0.41.1`** before
upgrading further:

- `0.40.x` performs the one-shot toolkit flattening and service-account
  successor creation that `0.41+` no longer carries.
- `0.41.1` drops the retired toolkit and service-account tables after the
  explicit export, verification, and acknowledgement steps in
  [Upgrade notes → 0.41.0](upgrade-notes.md#0410).
- **Do not use `0.41.0` on PostgreSQL** — use `0.41.1` (see
  [0.41.0](upgrade-notes.md#0410) for the `allowed_scopes` array/JSONB fix).

The `0.41.1` waypoint is a documented historical route for pre-`0.40`
deployments, outside the rolling `N-1` / `N-2` CI window.

## Backends and CI enforcement

The upgrade *policy* lives in
[`tests/upgrade/supported-upgrades.yaml`](../../tests/upgrade/supported-upgrades.yaml)
(window size, which roles run, backends, known breaks). It holds no version
numbers, and its validation in
[`tests/unit/test_supported_upgrades.py`](../../tests/unit/test_supported_upgrades.py)
does not depend on the current date or release, so cutting a release never
fails it. [`tests/upgrade/matrix.py`](../../tests/upgrade/matrix.py) resolves
the concrete window when CI runs:

- **Target series.** An unreleased `pyproject.toml` version (a release PR)
  targets its own minor. A version that is already released (`main` after a
  release) targets the next minor, so the release just cut becomes `N-1`. The
  tagged release commit targets its own series.
- **Sources** are the latest released patch of each of the previous two minors,
  read from the `v*` git tags. A minor with no release is omitted.
- **Which legs run (`enabled_roles`).** Both `N-1` and `N-2` are in the
  supported window, but CI runs a leg only for the roles listed in
  `window.enabled_roles` and only on `enabled: true` backends. During Phase 1
  that is `enabled_roles: ["n-1"]` on PostgreSQL; the `N-2` leg is held pending
  Phase 2 matrix expansion.
- **Source images** are pulled by the multi-arch manifest digest GHCR reports
  for the tag when the matrix is built, so every leg in a run tests the same
  image. A tag whose image is not published yet (the minutes between tagging
  and `publish-image`) is skipped with a warning.
- **Seeders.** The harness uses the newest `tests/upgrade/seed/seed_v<major>_<minor>.py`
  that is not newer than the source, so a release that keeps the seeded API
  needs no new seeder.
- **What fails CI.** A leg fails only when the upgrade path fails: the candidate
  migration, `migrations.run --check`, the idempotent re-run, candidate startup
  on the migrated data, or the post-upgrade verifier. A leg that never reaches
  the upgrade step (image pull, source-release startup, seeding against the
  source API) is reported as *inconclusive* with a warning.
- **PostgreSQL** (`postgres`) is the primary production backend (`blocking: true`,
  `enabled: true`).
- **SQLite** (`sqlite`, the quickstart topology) is declared in the policy as an
  advisory leg (`blocking: false`, `enabled: false`) pending Phase 2 rollout.

## Refresh-token survival policy

The matrix records `refresh_token_policy: survive_unless_documented_in_upgrade_notes`:

- By default, OAuth refresh tokens issued by a supported source release must
  continue to refresh after upgrading to the candidate build.
- If a release intentionally revokes existing refresh tokens or rewrites token
  scopes/families (for example, the scope retirement in
  [0.43.0](upgrade-notes.md#0430)), that revocation must be documented in the
  release's section of [`upgrade-notes.md`](upgrade-notes.md) so operators know
  clients will re-authenticate.

## Intentional breaks (`known_breaks`)

Because pre-1.0 minor releases may introduce breaking changes, a change may
intentionally refuse an in-place upgrade unless an operator performs a manual
pre-upgrade action or steps through an intermediate release. Blanket skips and
`continue-on-error` are not permitted on upgrade legs. Instead, an intentional
break within the `N-1` / `N-2` window must be recorded under `known_breaks` in
[`tests/upgrade/supported-upgrades.yaml`](../../tests/upgrade/supported-upgrades.yaml)
with four required fields:

- `source` — the affected source release tag (for example `"0.43.0"`).
- `reason` — concise explanation of why direct migration is refused or requires
  operator action.
- `upgrade_notes_link` — path and heading anchor in
  [`docs/operations/upgrade-notes.md`](upgrade-notes.md) (for example
  `docs/operations/upgrade-notes.md#0450`; never `#unreleased`).
- `expires` — `YYYY-MM-DD` date (typically the next minor release cut). Until
  then, a failing leg for `source` is reported as a warning. After it, the leg
  gates again, and the matrix summary flags the entry as expired so it can be
  retired.

A `known_breaks` entry never skips a leg: the leg still runs and its result is
reported, so a break that is fixed before it expires is visible.
