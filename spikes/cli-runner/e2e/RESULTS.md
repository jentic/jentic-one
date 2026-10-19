# Spike 5 — broker leg end to end on a real local Jentic

Date 2026-10-19. A real combined app (:58000) and broker (:58100) from this
branch, on an isolated Postgres (compose project `jentic-spike-cli`, port
55432, `e2e.env`), with a mock GitHub upstream on :58200 (`upstream.py`).
Everything is provisioned through public APIs by `setup.py`:

1. Bootstrap the admin (`POST /users:create-admin`).
2. Inline-import a GitHub-shaped spec (`POST /graphql`,
   `GET /repos/{owner}/{repo}/pulls`) under vendor `github.com`.
3. Register, approve and token-exchange an agent (DCR + Ed25519).
4. Create a static `bearer_token` credential with a **fake** token, and bind
   it to the agent.
5. Set the binding rules: allow `POST /graphql` (exact) and `GET /repos/.*`
   (regex).

Start-up:

```
JENTIC_PG_PORT=55432 docker compose -p jentic-spike-cli -f docker/local-setup/docker-compose.yaml up -d --wait
set -a; . spikes/cli-runner/e2e.env; set +a; uv run python -m jentic_one.migrations.run
JENTIC__SERVER__PORT=58000 uv run python -m jentic_one &
JENTIC__APPS=broker JENTIC__SERVER__PORT=58100 JENTIC__BROKER__JOBS_API_BASE_URL=http://127.0.0.1:58000 uv run python -m jentic_one &
python3 spikes/cli-runner/e2e/upstream.py 58200 spikes/cli-runner/e2e/logs/upstream.log &
PYTHONPATH=. uv run python spikes/cli-runner/e2e/setup.py
```

## Results

| Step | Result |
|---|---|
| Inline import | ✅ job `completed`, but the revision is **`draft`** |
| Broker call before promotion | ❌ `404 operation_not_found` ("unregistered upstream URL") |
| `POST /apis/github-com/github-spike/1.0.0/revisions/{id}:promote` | ✅ 200 |
| `POST http://127.0.0.1:58100/http://127.0.0.1:58200/graphql` with the agent token | ✅ `200`, `jentic-execution-id: exec_…`, gh-shaped `pullRequests` body |
| What the upstream received | `Authorization: Bearer ghp_SPIKE_FAKE_…`, the **stored credential**. The agent's Jentic token was not forwarded |

### Policy and audit

| Check | Result |
|---|---|
| `GET /repos/cli/cli/pulls` (allowed by regex rule) | ✅ 200, credential injected |
| `GET /repos/cli/cli/readme` (not in the spec) | ✅ 404 `operation_not_found`, **never reaches upstream** |
| Rules narrowed to `GET /repos/.*`, then `POST /graphql` | ✅ 403 `action_denied` with `agent_directive.strategy: prompt_human` ("ask your operator to adjust the binding's permission rules"). **Zero upstream calls** |
| `GET /executions` (admin) | allowed calls recorded with operation, credential name and agent. **Denied calls are not execution records** |
| `GET /events` (admin) | allowed: `execution.completed` + `credential.accessed`. Denied: `broker.pbac_denied`. Rule edits: `credential.permission_rule_set` |
| Re-running `setup.py` | the re-import dead-letters as `job.failed_permanently` (identical revision). Harmless, but noisy |

## Findings

- **Matching and injection work for a GitHub-shaped GraphQL call.** The broker
  matched the operation, evaluated the binding's rules, injected the
  credential and recorded an execution with no changes to the code. Every
  piece the CLI path needs on the broker side exists.
- **Promotion is a required step.** Inline import leaves a draft, and drafts
  don't route. Profile packaging must import **and promote** its operations.
- **The vendor is stored slugified** (`github.com` → `github-com`). Profile
  manifests have to use the slug in API references.
- **Denials only show up in the event stream.** A CLI invocation view that
  lists "what this command did" must merge `/executions` with
  `broker.pbac_denied` events, or a denied child call won't appear.
- **Local-run gotcha:** with `server.reload` on, file writes in the worktree
  restart the servers and issued tokens stop working. `e2e.env` sets
  `JENTIC__SERVER__RELOAD=false`.

## Not covered

Connecting a sandboxed CLI's traffic to the broker (the in-sandbox → broker
hop) isn't part of this spike. A full `gh` → sandbox → broker → upstream run
needs that piece first. The rest of the chain above is ready to be reused for
that test.

### CLI → forwarder → broker hop

`e2e/forwarder.py` is a loopback plain-HTTP forwarder (no CA, no TLS interception).
The CLI gets `HTTP_PROXY` and `GH_HOST`; the forwarder accepts absolute-form requests,
allows only hosts in its map, drops the CLI's own `Authorization`, and relays to the
broker as the agent. The spike holds the agent token in the forwarder process, which
stands in for the runner's out-of-sandbox forwarder.

| Check | Result |
|---|---|
| `gh pr list -R cli/cli` (host `gh`, dummy `GH_TOKEN`) | ✅ prints both PRs. GraphQL `POST` → broker → upstream, `jentic-execution-id` returned |
| `gh api /repos/cli/cli/pulls` | ✅ 200 through the broker |
| Upstream auth header | `Bearer ghp_SPIKE_FAKE_…` (stored credential). The CLI's own token never arrives |
| Host outside the map (`evil.example`) | ✅ 403 at the forwarder, nothing reaches the broker |
| `CONNECT` | ✅ refused (403) |
| Request carrying its own `Authorization` | ✅ replaced with the agent's |

Not covered: the sandboxed variant (netns + unix socket hop), `aws`/`cf`, and the
wiring gaps from spike 3 (absolute `Link` URLs, redirects to other hosts), which still
need every outbound host in the map.

### Review of `forwarder.py` (2026-10-19)

Tested against a separate instance of the unmodified forwarder (fake token,
capture server in place of the broker):

| # | Finding | Evidence | Severity |
|---|---|---|---|
| R1 | **The agent token follows redirects to other hosts.** `urllib` follows a 3xx itself and keeps `Authorization`, so the token is sent to the `Location` host. The broker deliberately never follows redirects, and the forwarder undoes that | 302 → `127.0.0.1:58303/landing` received `Bearer <agent token>`; the client saw 200 | blocker |
| R2 | **Broker-control headers are forwarded** from the CLI side: `Jentic-Revision`, `Jentic-Credential-Name`, `Prefer`, `Idempotency-Key`. The sandboxed process could pin revisions, choose a credential, or force async | all four arrived at the capture server | major |
| R3 | **The agent token is in argv**, so it's visible to every local process via `ps` | `ps` shows the token argument for the running instances | major |
| R4 | **Chunked request bodies are dropped silently.** Only `Content-Length` is read | chunked upload → broker saw `content-length=0`, client got 200 | major |
| R5 | **Response headers are reduced to `Content-Type` + `jentic-execution-id`.** `Link`, `ETag`, `X-RateLimit-*` and `Location` are lost, so `gh api --paginate` silently stops after page 1 and conditional requests can't work | `Link`/`ETag` absent in the client response | major |
| R6 | `HEAD` isn't handled | 501 | minor |
| R7 | **Broker unreachable** → unhandled exception, connection closed with no response | curl exit 52 | minor |
| R8 | No per-invocation correlation (session/trace id), so child calls can't be grouped into one invocation record | no such header sent | minor |

Required behaviour for the production version:
- never follow redirects; hand 3xx back to the CLI, and only through the
  profile's host allowlist;
- forward headers from an allowlist, never `Jentic-*`, `Prefer` or
  `Idempotency-Key`;
- hold the token outside argv and env, and outside the sandbox;
- support chunked bodies or reject them explicitly;
- pass response headers through, minus hop-by-hop;
- return explicit errors.

### Review fixes to the forwarder (R1–R8)

Re-tested against a fake broker that records requests, then again with the real `gh` chain.

| # | Fix | Verified |
|---|---|---|
| R1 | `http.client`, so redirects are never followed; 3xx goes back to the CLI | ✅ 302 + `Location` returned, no second broker call |
| R2 | Request headers forwarded from an allowlist only | ✅ `Jentic-Revision`, `Jentic-Credential-Name`, `Prefer`, `Idempotency-Key` and the CLI's `Authorization` do not reach the broker |
| R3 | Token read from a mode-0600 file | ✅ not in argv. It is still in the forwarder's memory; production keeps it outside the sandbox |
| R4 | Chunked request bodies decoded | ✅ broker received `hello-chunked` |
| R5 | Response headers passed through minus hop-by-hop | ✅ `Link` and `ETag` reach the client |
| R6 | `HEAD` supported | ✅ 200, no body |
| R7 | Broker unreachable | ✅ explicit `502 broker_unreachable` |
| R8 | Per-process invocation ID | ✅ logged on every line; not yet sent to the broker |

Real chain after the fixes: `gh pr list` and `gh api` still return 200 with the stored
credential at the upstream.
