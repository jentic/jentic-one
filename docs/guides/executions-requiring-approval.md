# Executions requiring approval

A permission rule with the **Ask** effect (`require-approval` on the wire)
holds an agent's matching call instead of running it. The call waits until a
human approves or denies it, or the approval expires. The agent is told the
call is held and where a reviewer decides it; it never re-sends the call.

Approval authority never comes from the agent: an agent cannot decide its own
calls (or any others), whatever scopes it holds.

## Write an Ask rule

Ask is a third effect beside Allow and Block (`allow` / `deny`). Pick **Ask**
in any rule editor — an agent's credential binding, a permission rule set, or
the rules an agent requests while connecting a credential.

- An Ask rule must constrain at least one of methods, path, or operations; a
  condition-less Ask rule is refused, since it would hold every call.
- Rules are evaluated first-match-wins, as for Allow and Block: a call matching
  an Ask rule is held, one matching a Block rule is refused, one matching an
  Allow rule runs, and one matching no rule is refused.
- The **Effective access** preview and the rule tester mark operations an Ask
  rule covers as **ask**.

Through the API, an Ask rule is an ordinary permission rule with
`"effect": "require-approval"`:

```json
{ "effect": "require-approval", "methods": ["POST"], "path": "/v1/charges", "match_mode": "prefix" }
```

## What the agent sees

### Over HTTP (CLI, SDKs, direct broker calls)

A held call answers **202 Accepted** with the held envelope:

```json
{
  "status": "held",
  "held_execution_id": "exap_…",
  "review_url": "https://<host>/app/agents/approvals/exap_…",
  "expires_at": "…",
  "agent_directive": "This call needs human approval. …",
  "_links": {
    "self": "https://<host>/executions/holds/exap_…",
    "result": "https://<host>/executions/holds/exap_…/result",
    "withdraw": "https://<host>/executions/holds/exap_…:withdraw"
  }
}
```

The agent shows the user `review_url`, then follows `_links.self`
(`GET /executions/holds/{held_execution_id}`) until `execution_status` says the
call has finished, and reads `_links.result`
(`GET /executions/holds/{held_execution_id}/result`):

| `execution_status` | Meaning | Result |
| --- | --- | --- |
| `held` | Waiting for a reviewer | — |
| `queued` / `running` | Approved; the call is about to run or running | — |
| `completed` | Approved and run | The upstream response |
| `failed` | Denied, expired, or the approved run failed | A problem: `approval_denied`, `approval_expired`, or the run's failure |
| `cancelled` | Withdrawn by the agent | — |

Following its own held call needs no permission beyond being the agent that
made it.

An identical call while one is pending joins the existing held execution (same
`held_execution_id`) instead of filing another, even when the agent is at its cap. Each
agent may have at most `execution_approvals.max_pending_per_agent` pending
approvals; past that a new held call is refused with **403**
`approval_pending_limit_reached`.

The held run sends the request as the agent did: its body, `Content-Type`,
`Accept`, conditional (`If-*`) and API version headers (`Notion-Version`,
`Stripe-Version`, `X-GitHub-Api-Version`, any `<name>-Version`). The agent's
own credentials and other headers are not kept; the run injects the bound
credential itself.

### From the CLI (`jentic execute`)

`jentic execute` prints the held envelope as its data, shows the review link
on stderr and exits **3** (`TIMEOUT_PENDING`).
`jentic executions poll <held_execution_id> [--timeout 30s]` then waits for the
decision and prints the outcome exactly as `execute` prints a call that ran at
once: the upstream response (exit 0) or the denial or expiry (exit 2).
`jentic execute --wait [--timeout 30s]` does both in one command. `--timeout`
defaults to 30 seconds and accepts up to 5 minutes (`90s`, `2m`, or bare
seconds); `0` checks once. Neither re-sends the call. If the call is still held
when the timeout lapses, either exits 3 again and prints the
`held_execution_id`, the `review_url` and the command to poll again.

### Over MCP (`/mcp` and `jentic mcp`)

Both MCP servers relay the held envelope unchanged, so the agent gets the same
`review_url` and directive:

- **Every client** gets the held result at once from `execute`. The directive
  asks the agent to show the user the review link and end its turn, then —
  once the user replies — call `get_execution_result` with the
  `held_execution_id`.
- **`get_execution_result`** takes an optional `timeout` in seconds (default
  30, maximum 60; `0` checks once): the server re-polls the held execution and
  returns as soon as the call has finished, or where it stands when the timeout
  lapses. Every answer names the `held_execution_id`, the `review_url` and
  `expires_at`; one that comes back before the call has finished also carries
  an `agent_directive` to show the user the review link again and end the turn.
- **Clients declaring URL-mode elicitation** (`elicitation.url`) are instead
  asked to open the review page for the user. The client's accept only means
  the link was opened — it never decides the approval. The retry reads the
  job, waiting briefly for the decision.

## Review a held call

Held calls are listed under **Agents → Approvals** (`/app/agents/approvals`);
each opens its review page at `/app/agents/approvals/{id}` — the
`review_url` the agent relays. The page shows the agent and its owner, the
matched rule, and the exact request that runs if approved (method, URL and
body). The page needs the reviewer's own sign-in; the URL carries no token.

- **Who reviews:** the agent's owner, or any org admin. An agent without an
  owner is reviewable by org admins only. Anyone else is told who can review.
- **Approve and run** releases the call; it runs once, re-checked against the
  agent's current permissions. **Deny** (with an optional reason) fails it
  with a permission-denied result. The first decision wins; later ones are
  refused.
- A held execution in the **Monitor**'s jobs view links to its review page
  (**Review**). It has no Cancel action: a held call settles only through its
  approval.

The same actions are available over the admin API. None of these routes
needs a `jobs:*` permission: each caller sees the held executions of agents it
owns (an org admin sees all, an agent sees its own), and who may act is
checked per call.

| Route | Who | Effect |
| --- | --- | --- |
| `GET /executions/holds` | The filing agent, its owner, or an org admin | List, filterable by `state` and `agent_id` |
| `GET /executions/holds/{id}` | The filing agent, its owner, or an org admin | The decision, where the call stands, and the held request |
| `GET /executions/holds/{id}/result` | The filing agent, its owner, or an org admin | The outcome once finished (**409** before) |
| `POST /executions/holds/{id}:decide` | The owner or an org admin (never an agent) | `{"decision": "approve" \| "deny", "reason": "…"}` |
| `POST /executions/holds/{id}:withdraw` | The agent that filed it | Withdraw a pending held execution; it never runs |

Deciding or withdrawing a settled held execution answers **409**. Each decision,
withdrawal and expiry is audit-logged and emits an event
(`execution.approval_requested` when filed, `execution.approval_decided`,
`execution.approval_withdrawn`, `execution.approval_expired`).

## Lifecycle

| `state` | `execution_status` | How |
| --- | --- | --- |
| `pending` | `held` | The broker files the hold |
| `approved` | `held` → `queued` → `running` → `completed` / `failed` | A reviewer approves |
| `denied` | `held` → `failed` | A reviewer denies |
| `expired` | `held` → `failed` | Nobody decided within `execution_approvals.ttl_seconds` |
| `withdrawn` | `held` → `cancelled` | The agent withdraws |

An approved call runs at most once: it gets a single attempt, and a run cut
off mid-flight is failed (`approval_resume_failed`) rather than re-run. Its
result stays readable for `execution_approvals.result_retention_seconds`. The
held request is stored encrypted until it runs.

## Configuration

| Key | Default | Env var |
| --- | --- | --- |
| `execution_approvals.ttl_seconds` | `86400` (24 h) | `JENTIC__EXECUTION_APPROVALS__TTL_SECONDS` |
| `execution_approvals.max_pending_per_agent` | `10` | `JENTIC__EXECUTION_APPROVALS__MAX_PENDING_PER_AGENT` |
| `execution_approvals.result_retention_seconds` | `86400` (24 h) | `JENTIC__EXECUTION_APPROVALS__RESULT_RETENTION_SECONDS` |

When the broker runs as its own service (without the admin app), set
`broker.jobs_api_base_url` to the admin API and web UI origin. Otherwise the
`review_url` and `_links` point at the broker, which serves neither; the broker
logs `broker_jobs_api_base_url_unset` at startup when this applies.

See the [configuration reference](../reference/config.md) for every key.
