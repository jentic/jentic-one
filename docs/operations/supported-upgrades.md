# Supported upgrades

How far back a direct upgrade is tested in CI, how that window relates to the
pre-1.0 [versioning policy](../../VERSIONING.md), and how intentional breaks are
recorded. For the step-by-step upgrade procedure itself, see
[Upgrades](upgrades.md#the-contract) and the per-release
[Upgrade notes](upgrade-notes.md).

## Support window (N-1 and N-2 minor series)

Direct, single-hop upgrades to a target `0.N.x` release are supported and
tested from the **previous two minor series (`N-1` and `N-2`)**, with each
minor series collapsed to its **latest published patch release**:

- Operators on an older patch within a supported minor series (`0.(N-1).0` when
  `0.(N-1).2` exists) should upgrade from the latest patch of that minor series,
  or upgrade directly to the latest release following the [upgrade notes](upgrade-notes.md).
- For the `0.45` target series, the two supported source releases are
  `0.44.0` (`N-1`) and `0.43.0` (`N-2`).
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

The machine-readable source of truth for CI is
[`tests/upgrade/supported-upgrades.yaml`](../../tests/upgrade/supported-upgrades.yaml),
validated on every unit-test run by
[`tests/unit/test_supported_upgrades.py`](../../tests/unit/test_supported_upgrades.py)
and [`tests/upgrade/matrix.py`](../../tests/upgrade/matrix.py):

- **Source images** are pinned by both release tag (`ghcr.io/jentic/jentic-one-app:X.Y.Z`)
  and multi-arch manifest digest (`sha256:…`) so tag re-pushes cannot silently
  alter the baseline under test.
- **PostgreSQL** (`postgres`) is the primary production backend (`blocking: true`).
- **SQLite** (`sqlite`, the quickstart topology) is tracked in the matrix as an
  advisory leg (`blocking: false`) while Phase 2 coverage is rolled out.

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
- `expires` — `YYYY-MM-DD` date (typically the next minor release cut) after
  which the entry fails `tests/unit/test_supported_upgrades.py` until retired.

When a `known_breaks` entry is active, the upgrade harness asserts the
*documented* failure mode or multi-hop route rather than skipping the leg.
