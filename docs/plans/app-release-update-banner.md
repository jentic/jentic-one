> Issue: jentic/jentic-one — in-app "update available" banner
> PR: jentic/jentic-one#964

## Context

Operators self-hosting jentic-one had no in-product signal that a newer release
was available: they learned about updates only by remembering to run `jenticctl
update`, or from GitHub/Discord out of band. This feature adds a first-class
"you're behind" signal in two places, each matched to how that user actually
consumes the product:

- **Web console (UI):** a dismissible "update available" banner in the app shell,
  plus a persistent current-version line in the user menu.
- **CLI-only users:** a throttled one-line nudge on ordinary `jenticctl`/`jentic`
  commands (they may never open the console).

The running version is read from the package metadata; the **latest available**
release is resolved **server-side from GitHub** and exposed to the UI via an
authenticated `GET /system/version`.

## The decision that shaped everything: server-side check, not a CLI push

The first cut of this feature had the **CLI push** the latest release it
discovered (`jenticctl update` → `POST /admin/system/latest-release` → a
singleton `latest_releases` admin table → `GET /system/version` reads it back).
That design rested on the premise that *the backend must not egress to GitHub*,
so the CLI had to be the courier.

That premise is false. The backend **already** fetches from GitHub server-side,
on a daily cadence, through hardened machinery — the catalog update-notify sweep
(`registry/services/catalog/fetch.py::fetch_json`, driven by
`CatalogUpdateScanner`). Once that's true, the CLI-push design is solving a
problem that doesn't exist, at real cost:

- a new admin table + Alembic migration + ORM model + repo + service,
- a new `instance:write` permission scope (+ scope-catalog + operator-scope wiring),
- a new admin write endpoint + Go admin-client method + a best-effort CLI seam,
- and it only worked when a CLI that had run `update` also happened to have a
  cached admin token — a fragile, easily-missed path.

**Decision:** delete the entire CLI-push write half and have the backend resolve
the latest release itself, lazily, cached in-process. This is also the mainstream
pattern for self-hosted apps (Grafana holds it in a mutex-guarded field; Jellyfin
caches with a ~12h TTL; GitLab/Nextcloud expose an authenticated update-check).

We separately confirmed, by mapping every CLI/backend topology, that removing the
CLI→backend report loses **no** update signal the product actually had.

## What the backend does now

`GET /system/version` (authenticated) returns:

```json
{ "current": "0.26.0", "latest": "0.27.0", "update_available": true }
```

- `current` — the running build, from `jentic_one.__version__`.
- `latest` — newest published release, resolved by `ReleaseChecker`
  (`shared/release_check.py`) from
  `https://api.github.com/repos/{repo}/releases/latest` via the existing hardened
  `fetch_json` (SSRF guard, DNS-pinned transport, redirect + size caps, descriptive
  User-Agent). `null` whenever it can't be determined.
- `update_available` — `is_update_available(current, latest)` from
  `shared/version_compare.py`, a dependency-free port of the CLI's semver rules so
  the backend's verdict is identical to what `jenticctl update` prints.

**Fetch-on-read + in-process TTL cache, no background job.** `ReleaseChecker` is
held on the application `Context`, so its cache and single-flight lock are shared
across every request on the process: the first read after the TTL expires makes
one GitHub request, concurrent readers coalesce behind an `asyncio.Lock`, and a
burst of UI pollers still makes at most one request per TTL. We deliberately did
*not* re-introduce a background scanner — that's ~90 lines of lifecycle
boilerplate for a value the UI already polls for.

**Gating & kill switches** (`ReleaseCheckConfig` in `shared/config.py`):
- runs only when `release_check.enabled` (default `true`) **and**
  `server.backend == "local"` — a hosted/remote backend isn't something the
  operator can self-update, so it never phones GitHub;
- `cache_ttl_seconds` default `21600` (6h); `0` is a full kill switch (air-gapped
  installs, no egress), mirroring the catalog scanner's `interval <= 0` convention;
- `repo` defaults to `jentic/jentic-one`.

**Never breaks the app.** Every failure path (disabled, remote, air-gapped,
offline, rate-limited, private repo, unparseable/pre-release tag) degrades to
`latest = null` / `update_available = false` — the UI just shows the current
version with no banner. The probe cannot 500 the version endpoint.

## Why `GET /system/version` is authenticated (not public)

Both UI consumers (the banner and the user-menu version line) render only inside
the signed-in shell, so gating the endpoint costs nothing — every caller already
holds a session. In return we keep the **exact running build off unauthenticated
fingerprinting** (OWASP ASVS 14.3.3). The closest self-hosted precedents gate
their update-check data the same way or stricter (GitLab's `/admin/version_check`,
Nextcloud's updater). No special permission is required — *any* valid session.

(`/health` already exposes the running version to authenticated liveness checks;
`/system/version` adds the *latest* + the verdict, which is the new signal.)

## Why the UI banner is admin-only (the version line is not)

Acting on the banner means running `jenticctl update` on the host — an operator
action. A non-admin can't do it, so a banner would be pure noise for them.
**Decision:** the banner renders only for `org:admin` (`usePermission(ORG_ADMIN)`);
the current-version line in the user menu still shows for everyone (it's
informational, not a call to action). Dismissal persists the dismissed version
(`localStorage`), so dismissing 0.26 still surfaces 0.27 later.

## The CLI-only nudge

CLI-only users never see the console, so the backend endpoint doesn't reach them.
Following the mainstream CLI pattern (gh, npm/`update-notifier`, glab, brew — all
"check at most once/day, cache the result, print a short **stderr** nag, suppress
when non-interactive/CI"), the root `PersistentPreRun` now runs a nudge
(`cli/internal/cmd/updatenudge.go`):

- throttled to **once per `updateNudgeInterval` (24h)** via a small cache at
  `~/.jentic/update-check.json`, so the common path is a cheap file read with no
  network at all; a stale cache triggers one `git ls-remote` probe, **bounded by
  `updateNudgeTimeout` (2s)** so a slow/offline network never delays the command;
- printed to **stderr** so piped/captured stdout stays clean;
- **suppressed** for non-interactive stderr, for `JENTIC_NO_UPDATE_NOTIFIER` /
  `CI` / `JENTIC_NO_BANNER`, and for the commands that own their own messaging or
  output (`update`, `execute`, `help`, `completion`, `install`) — the same skip
  set the brand banner uses;
- **best-effort**: any error (no manifest, offline, unparseable) is swallowed;
  a failed probe still advances the throttle timestamp but keeps any prior tag so
  it can still nudge from cache.

## Files changed

### Removed (the CLI-push write half)
- `src/jentic_one/admin/web/routers/system.py`, `admin/services/latest_release_service.py`,
  `admin/repos/latest_release_repo.py`, `admin/core/schema/latest_releases.py`,
  `migrations/admin/versions/e1f2a3b4c5d6_add_latest_releases.py`,
  `tests/web/admin/test_system.py`.
- Reverted the `instance:write` scope wiring in `admin/core/permissions.py`,
  `shared/web/endpoint_scopes.py`, `shared/web/scope_catalog.py`, and the model
  registration in `admin/core/schema/__init__.py` + router wiring in
  `admin/web/app.py`.
- Reverted the CLI reporter: `adminclient.ReportLatestRelease` (+ its test) and
  the `reportLatestRelease` seam/call in `cli/internal/cmd/update.go` (+ test).

### Backend — server-side check
- `src/jentic_one/shared/release_check.py` — **new** `ReleaseChecker` (TTL cache +
  single-flight, gated fetch-on-read).
- `src/jentic_one/shared/config.py` — **new** `ReleaseCheckConfig` on `AppConfig`.
- `src/jentic_one/shared/context.py` — `release_checker` property (per-process cache).
- `src/jentic_one/shared/web/system.py` — `GET /system/version` now sources `latest`
  from `ctx.release_checker` (authenticated; `operation_id="getVersion"`).
- `src/jentic_one/shared/version_compare.py` — retained (semver parity with the CLI).
- `src/jentic_one/shared/web/{app_factory,openapi_meta}.py` — router wiring + tag rule.

### CLI — nudge
- `cli/internal/cmd/updatenudge.go` — **new** throttled stderr nudge.
- `cli/internal/cmd/root.go` — call it from `PersistentPreRun`.
- `cli/internal/config/paths.go` — `UpdateCheckPath()` (`~/.jentic/update-check.json`).

### UI
- `ui/src/shared/hooks/useVersionInfo.ts` — React Query hook over `getVersion`.
- `ui/src/shared/ui/Banner.tsx` — presentational dismissible banner (`role="status"`).
- `ui/src/shared/app/UpdateBanner.tsx` — admin-only shell banner + dismissal persistence.
- `ui/src/shared/app/UserMenu.tsx` — current-version line (all users).
- `ui/src/shared/app/Layout.tsx` — mount the banner.
- `ui/src/mocks/handlers.ts` — root MSW handler for `/system/version`.

### Generated (regenerated, not hand-edited)
- `openapi/control/control.openapi.yaml`, `ui/openapi.json`,
  `docs/reference/endpoints.{md,json}`, `ui/src/shared/api/generated/**`.

## Tests
- `tests/unit/shared/test_release_check.py` — gating (disabled / TTL 0 / remote),
  tag normalisation, degrade-on-failure, TTL cache hit, single-flight coalescing.
- `tests/unit/shared/test_system_version.py` — reports current; surfaces a newer
  release (stubbed fetch) and caches; degrades on GitHub failure; skips on remote
  backend; requires auth; not mounted on broker; schema/tags.
- `tests/unit/shared/test_version_compare.py` — retained semver-parity cases.
- `cli/internal/cmd/updatenudge_test.go` — probe-then-cache, refresh-when-stale,
  failure-keeps-prior-tag, first-run-offline stays silent, env opt-outs, skip set.
- `ui/.../UpdateBanner.test.tsx` — shows/dismisses/re-shows, and **hidden for a
  non-admin**; `useVersionInfo.test.tsx` — reports/surfaces/degrades.

## Out of scope
- Backend push from the CLI (removed — see above).
- A background release scanner (unnecessary; the UI polls, the CLI nudges).
- Catalog/API-spec update-notify (a separate, existing feature).
