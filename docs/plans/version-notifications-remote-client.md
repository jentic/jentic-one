# Design: version notifications + remote-client UX

> **STATUS: design — feature split out of the release procedure.** The
> update-notification is a firm requirement; the remote-client UX is small and related.

## Update / version notifications

Both the CLI and UI nudge when a newer version exists. Privacy-respecting (Grafana/Gitea
model): a **one-way GET** to the GitHub Releases API (or a static `versions.json` on our
own domain — avoids leaking operator IP/version-cadence metadata to GitHub), compared to
the running version.

**Two skews to surface:** (a) running version **vs latest release**; (b) CLI **vs the
remote server it targets** (`serverinfo.Probe` already fetches the server version from
`/health` — today only *displayed*, never compared). Make the skew message **actionable**
and say *where* to run the fix: *"CLI v0.15 vs server v0.13 — upgrade the server with
`jenticctl update` on the server host"* (the agent-dev on host B can't upgrade host A).

**Privacy:** pure pull, sends no data; **default-on, one flag off** (`check_for_updates=false`
/ `JENTIC_CHECK_FOR_UPDATES=false`, in the config file + `--help`); **separate from
telemetry** (opt-in); `--offline` silences all outbound. Note: even a bodyless GET leaks
the requester IP + version cadence to whoever hosts the check — a static `versions.json`
on our domain is the higher-privacy option.

### Surfaces + effort (~3 days across 3 codebases)

- **Server (~0.5d):** background poller + cache; expose `latest_version` / `update_available`
  on the root `/health` (already a free-form JSONResponse — no schema change needed there).
  Don't block the request path on the GitHub call.
- **CLI (~1d):** add `golang.org/x/mod/semver`; compute both skews; surface via the existing
  `VersionPanel` + the `update` "Update available: X → Y" phrasing; wire the flags/`--offline`.
- **UI (~1.5d — the soft spot):** the SPA reads **`/admin/health`**, whose `HealthResponse`
  schema has **no `version` field**; the root `/health` (which has version) is typed `any`
  in the generated client. So the banner requires: add `version`/`latest_version`/
  `update_available` to the **admin** `HealthResponse` Python schema + endpoint → regenerate
  the OpenAPI spec + TS client (**CI's `openapi-client-drift` gate enforces this**) → *then*
  a dismissible banner in the app shell. It's a cross-codebase, CI-gated change, not a
  UI-only task.

## Remote-client UX (~0.5–1d)

The `jentic` CLI already targets a remote via `--base-url` (control plane) +
`--broker-scheme`/`--broker-host` (broker), per-profile. Smooth the VPC/different-host case:

- **`JENTIC_BASE_URL` env var** — only `JENTIC_HOME`/`JENTIC_PROFILE` exist today; insert an
  env layer into base-URL resolution (`flag → env → config → default`). Document precedence.
- **Single instance URL** that derives both control-plane + broker (there's already
  `CanonicalBaseURL` derivation to build on), so users set one address, not two knobs.

## Related fix

Stale `broker.jentic.ai` default appears in `skillgen/content/jentic.md`, `execute.go`, and
`install.go` comments vs the real `127.0.0.1:8100` — fix before the CLI goes public (it's in
the exact doc an agent-dev copies from).
