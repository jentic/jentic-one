# Spike 4 — Cloudflare `cf`: base-URL override, dry-run, sandbox

Date 2026-10-19. Image: `Dockerfile` (`node:22.20.0-bookworm-slim` +
`cf@1.0.0-beta.18`, Cloudflare's package from `github.com/cloudflare/cf`, with
npm checking its sha512 integrity on install). The container runs with
`--network none`, and an in-container recording mock (`mock.py`) is reachable
only through the base URL. Commands are in `run.sh`; raw output is in
`results-raw.txt`.

Env: `CLOUDFLARE_API_BASE_URL=http://127.0.0.1:18801/client/v4`,
`CLOUDFLARE_API_TOKEN=<dummy>`, `CF_SEND_TELEMETRY=false`, `DO_NOT_TRACK=1`,
`CI=1`.

## Results

| Case | Result |
|---|---|
| `cf zones list` | ✅ exactly 1 call: `GET /client/v4/zones` to the mock, with `Authorization: Bearer <dummy>`. No other connection attempts (no telemetry, no update check) |
| `cf dns records create -z … --body '{…}'` | ✅ 1 call: `POST /client/v4/zones/<id>/dns_records`, with the body as given |
| `cf auth whoami` | ✅ 2 calls (`/accounts`, `/memberships`). It reports `authSource: CLOUDFLARE_API_TOKEN` and doesn't read any stored profile |
| `--dry-run` (list, create, `accounts tokens create`) | ✅ prints `{command, method, url, pathParams, query, bodyKind, body}` with **no network**. The `url` is **canonical** (`https://api.cloudflare.com/client/v4/…`), not the overridden base URL. `method` + `url` + `body` match the real request exactly |
| `cf schema zones list` | ✅ `{operationId: "zones.list", httpMethod: "GET", path: "/zones", pathParams, queryParams, …}` |
| `cf cli search "create a dns record"` | ✅ works offline. Returns ranked `{command, summary}` JSON |
| Destructive command without `--force` under `CI=1` | ✅ aborts: "non-interactive; pass --force to confirm" |
| Output format | JSON by default. There is no `--json` flag (passing one prints help and exits 1) |

## Sandbox (bwrap, chart posture + `../sandbox/seccomp-cli-runner.json`, no fresh `/proc`)

| Runtime | Works | Startup sandboxed vs bare |
|---|---|---|
| `cf` (Node 22) | ✅ `--version`, `--dry-run`, `cli search` | 77 vs 74 ms (`--version`), 118 vs 102 ms (`--dry-run`) |
| `aws` (Python 3.14) | ✅ `--version`; `s3 ls` fails with `NoCredentials`, as expected with a clean env | **541 vs 254 ms**. Cause not investigated |
| `gh` (Go) | ✅ (spike 1) | 15 vs 13–14 ms |

Image sizes (arm64, uncompressed):
- `cf` image: **864 MB** (Node + the CLI).
- `gh` + `aws` image: 599 MB.
- `gh`-only image: 260 MB.

## What this means

1. **`cf` needs no routing tricks.** The documented base-URL override covers
   every call made in this spike. It sends a bearer token, which the broker
   must strip and replace.
2. **`--dry-run` is an exact, credential-free, network-free preview** of the
   request, with the canonical URL. A runner can evaluate policy against it
   before running the real command. `cf schema` supplies the operationId for
   mapping to the registry.
3. **Credential-minting commands are plain API calls** (`accounts tokens create`
   → `POST /accounts/{id}/tokens`). They're visible in dry-run, so they can be
   blocked by operation.
4. **Not covered here:**
   - commands that need a local project (`cf deploy`, `cf dev`);
   - streaming (`tunnels tail`);
   - whether every `cf` call honours the base URL. This spike only exercised
     API commands.
