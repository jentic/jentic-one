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

## Findings

- **Matching and injection work for a GitHub-shaped GraphQL call.** The broker
  matched the operation, evaluated the binding's rules, injected the
  credential and recorded an execution with no changes to the code. Every
  piece the CLI path needs on the broker side exists.
- **Promotion is a required step.** Inline import leaves a draft, and drafts
  don't route. Profile packaging must import **and promote** its operations.
- **The vendor is stored slugified** (`github.com` → `github-com`). Profile
  manifests have to use the slug in API references.
- **Local-run gotcha:** with `server.reload` on, file writes in the worktree
  restart the servers and issued tokens stop working. `e2e.env` sets
  `JENTIC__SERVER__RELOAD=false`.

## Not covered

Connecting a sandboxed CLI's traffic to the broker (the in-sandbox → broker
hop) isn't part of this spike. A full `gh` → sandbox → broker → upstream run
needs that piece first. The rest of the chain above is ready to be reused for
that test.
