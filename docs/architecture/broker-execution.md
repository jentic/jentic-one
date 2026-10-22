# Broker execution

What happens between an agent's `jentic execute` and the upstream API's
response. The broker ([`src/jentic_one/broker/`](../../src/jentic_one/broker/)) is a credential-injecting
forward proxy with essentially one route: a catch-all
`/{upstream_url:path}` that accepts every HTTP method
([`broker/web/routers/execute.py`](../../src/jentic_one/broker/web/routers/execute.py)).
The vendor CLI facades, registered ahead of it, run the same pipeline for a
vendor's own CLI ([Vendor CLI facades](#vendor-cli-facades)).

## A brokered call, end to end

```mermaid
sequenceDiagram
    autonumber
    participant A as Agent (CLI / MCP / HTTP)
    participant B as Broker (web router)
    participant R as Registry resolver (in-process)
    participant C as Control DB
    participant P as Pipeline (runner stack)
    participant U as Upstream API
    participant D as Admin DB

    A->>B: METHOD /{full upstream URL} + bearer
    B->>B: authenticate (token/API key), require capabilities:execute
    B->>B: reconstruct + validate URL (SSRF gate)
    B->>R: resolve operation (api_vendor, api_name, api_version)
    B->>D: derive bound credentials (agent→credential bindings)
    B->>C: intersect with credentials covering the API (0 → 403)
    B->>C: select one credential (header pin → most specific, tie → 409)
    B->>B: evaluate the binding's permission rules (default-deny)
    B->>B: inject secret (header/query/cookie), re-validate URL
    B->>P: dispatch: Deadline → Retry → CircuitBreaker → SigV4 → HTTP
    P->>U: forward request (hop-by-hop + spoofable headers stripped)
    U-->>P: response
    alt streaming fast path (sync non-idempotent, the effective default)
        P-->>B: response streamed through
        B-->>A: response (secret never included)
        B--)D: record execution (post-response, best-effort)
    else buffered path (idempotent or passthrough disabled)
        P-->>B: outcome (upstream errors tagged, body verbatim)
        B->>D: record execution + audit events
        B-->>A: response (secret never included)
    end
```

## One pipeline, two callers — and a streaming fast path

The docstring rule in [`broker/services/execution/pipeline.py`](../../src/jentic_one/broker/services/execution/pipeline.py) is
"one pipeline, two callers": the execution use-case (`run_execution` in
[`broker/services/execution/service.py`](../../src/jentic_one/broker/services/execution/service.py)) is invoked by two paths, and
neither re-implements any step.

- **The sync router.** The catch-all route awaits `run_execution` and adapts
  the outcome to a FastAPI response. This is the buffered path — but note it
  is **not** what most sync requests take by default: with
  `broker.resilience.upstream.stream_passthrough_enabled` (default `true`), a sync
  request without an idempotency key is routed to the **streaming fast
  path** instead, which performs the same permission check, URL validation,
  and credential injection but streams the upstream body straight through
  and writes the execution record *after* the response, in a background
  task. Requests carrying an idempotency key, and installs that disable
  passthrough, take the buffered pipeline below.
- **The async worker.** A request carrying `Prefer: respond-async` is not
  executed inline: the router enqueues a `JobKind.EXECUTION` job and answers
  `202` with a `/jobs/{job_id}` link. The shared `WorkerLoop`
  ([`shared/jobs/worker.py`](../../src/jentic_one/shared/jobs/worker.py)) later hands the job to `ExecutionHandler`
  ([`shared/jobs/execution_handler.py`](../../src/jentic_one/shared/jobs/execution_handler.py)), which re-validates the URL, injects
  the credential, and dispatches through the same runner stack. The seam is
  two protocols (`UpstreamExecutor`, `CredentialInjector` in
  [`shared/jobs/protocols.py`](../../src/jentic_one/shared/jobs/protocols.py)) implemented broker-side by `PipelineExecutor`
  and `CredentialService` — so [`shared/jobs/`](../../src/jentic_one/shared/jobs/) never imports [`broker/`](../../src/jentic_one/broker/)
  (enforced by [`tests/arch/test_worker_no_inline_dispatch.py`](../../tests/arch/test_worker_no_inline_dispatch.py)).

Results of async executions are polled through the jobs API
(`GET /jobs/{job_id}`, `GET /jobs/{job_id}/result`) or the
`get_execution_result` MCP tool.

## The Broker protocol and `DefaultBroker`

[`shared/broker/broker.py`](../../src/jentic_one/shared/broker/broker.py) defines the `Broker` protocol — `execute()`
(buffered) and `execute_streaming()` (passthrough). `DefaultBroker`
([`broker/default_broker.py`](../../src/jentic_one/broker/default_broker.py)) is the stock implementation wrapping the
`BrokerExecutionPipeline`; a deployment (or the enterprise overlay) can
inject its own via `app.state.broker` / `app.state.broker_factory`, and
`BaseBrokerComplianceTest` in `jentic_one.testing` pins the contract.

## The resilience stack

The outbound call is a decorator chain over one shared `httpx.AsyncClient`
(the runners live in [`broker/adapters/runners/`](../../src/jentic_one/broker/adapters/runners/)), composed in two stages:
the broker app lifespan ([`broker/web/app.py`](../../src/jentic_one/broker/web/app.py)) wraps the transport with the
SigV4 signer and the optional circuit breaker, then `build_runner`
([`broker/services/execution/pipeline.py`](../../src/jentic_one/broker/services/execution/pipeline.py)) adds the retry loop and the
outermost deadline:

```
DeadlineRunner( RetryRunner( CircuitBreakerRunner( SigV4SigningRunner( HttpRunner ))))
```

- **`DeadlineRunner`** — outermost whole-call wall-clock budget, distinct
  from per-attempt transport timeouts; expiry is a `504` with a `wait`
  directive.
- **`RetryRunner`** — retries only when safe: the failure happened before
  the request was sent, or the method is idempotent, or the caller supplied
  an idempotency key. Exponential backoff with full jitter; honors
  `Retry-After` on 429/503; never retries a streaming response.
- **`CircuitBreakerRunner`** — a per-upstream-host breaker over the shared
  state backend (memory or Redis), so one failing vendor cannot burn the
  budget of every call.
- **`SigV4SigningRunner`** — innermost signer for AWS-shaped credentials;
  re-signs on every retry attempt; a no-op otherwise.
- **`HttpRunner`** — the only place a request actually leaves the process
  (pinned by [`tests/arch/test_broker_runner_seam.py`](../../tests/arch/test_broker_runner_seam.py)); per-host concurrency
  bulkhead, mid-stream response-size cap, raw-byte streaming.

At the web edge, admission control sheds load when too many requests are in
flight ([`broker/web/middleware.py`](../../src/jentic_one/broker/web/middleware.py); a request counts until its
response is complete, not through any post-response background work) and a per-caller rate limiter answers
`429` with `RateLimit-*` headers ([`broker/web/deps.py`](../../src/jentic_one/broker/web/deps.py)).

## Egress controls

Two layers keep the proxy from being used against the network it runs in:

- **Pre-request validation** ([`shared/url_validation.py`](../../src/jentic_one/shared/url_validation.py)) — `http`/`https`
  only; private, loopback, and link-local ranges refused; cloud metadata
  endpoints are a hard, non-overridable deny. Operators can exempt specific
  private subnets/domains via `EgressConfig`.
- **Connection-time DNS pinning** ([`shared/egress.py`](../../src/jentic_one/shared/egress.py)) — re-resolves and
  re-validates at connect time and pins the connection to the validated IP,
  closing DNS-rebinding races. Redirects are not followed
  (`follow_redirects=False`); the egress layer owns redirect policy.

Validation runs again after server-variable substitution, in both the sync
and async callers, so an injected variable cannot smuggle in a new host.

The platform's own OAuth calls (credential and OAuth app `token_url` /
`authorize_url` checks, the endpoints an approver confirms for their own
client on a connect session, token exchange and refresh, device authorization,
the identity probe) use the same two layers under `control.egress`, and always
pin DNS. `control.egress` holds only `allowed_private_subnets` and
`allowed_internal_domains`; each one left unset is taken from `broker.egress`,
so an in-network token endpoint allowlisted for the broker is reachable for
these calls too. Set a key to give these calls a different value (`[]` blocks
every private range for them even when the broker allows one).

The policy is read by whichever process makes the call. The control surface
makes the credential and OAuth app writes, the connect-session confirm, the
code exchange, device authorization and the identity probe; the **broker
surface** makes the token refresh it runs before injecting an OAuth2
credential, under its own `control.egress` (inherited from its own
`broker.egress`). In a combined deployment that is one config. With one process
per surface, set `control.egress`, and the `broker.egress` keys it inherits,
identically on the control and broker surfaces; otherwise a token endpoint
accepted at create time can be refused at refresh (or the reverse), and a
narrower `control.egress` set on the control surface alone does not constrain
the refreshes the broker sends.

## Credential injection

`CredentialService` ([`broker/services/credentials/orchestrator.py`](../../src/jentic_one/broker/services/credentials/orchestrator.py)) is the
one place a secret meets a request:

1. **Resolve** against the control DB by the API identity tuple
   (`api_vendor`, `api_name`, `api_version`; `NULL` = wildcard,
   most-specific match wins), restricted to the caller's bound credentials
   and honouring the `Jentic-Credential-Id`/`-Name` header pins. No usable
   secret → `424` with a *provision this credential* pointer; a genuine tie
   → `409 ambiguous_credential_binding` listing the candidates. See
   [How credential resolution works](../guides/credentials-and-toolkits.md).
2. **Refresh** OAuth tokens when expired (transient failures and
   needs-reconnect are distinct errors).
3. **Inject** per credential type ([`broker/core/injection.py`](../../src/jentic_one/broker/core/injection.py)): bearer or
   basic `Authorization` headers, API keys into header/query/cookie, SigV4
   as signing material for the runner. The response path never carries the
   secret back.
4. **Audit** — every resolve/decrypt emits a `CREDENTIAL_ACCESSED` event.

Before any of that, the binding deriver
([`broker/repos/credential_binding_resolver.py`](../../src/jentic_one/broker/repos/credential_binding_resolver.py), wrapped by the
short-TTL cache in [`caching_credential_deriver.py`](../../src/jentic_one/broker/repos/caching_credential_deriver.py)) works out which
credentials the caller may use for the call — a single cross-DB lookup
intersecting the agent's credential bindings (admin DB) with the active
credentials covering the API (control DB); none → `403
no_credential_binding` (or `403 credential_identity_mismatch` when a bound
credential is a near-miss, `403 credential_binding_suspended` when the
agent's binding covering the API is suspended), several → resolution disambiguates (or asks for
`Jentic-Credential-Id` with a `409`) — and the winning binding's permission
rules are evaluated **default-deny**: no matching rule, no call
([`broker/repos/agent_rule_evaluator.py`](../../src/jentic_one/broker/repos/agent_rule_evaluator.py)).
Rule paths match the request path **relative to the API's server URL** — the
spec's path (`/widgets/42`), normalized, never the full upstream path with
the server's base path (`/eu/widgets/42` for a server `http://host/{region}`).
Discovery rebuilds that path from the matched operation
(`OperationInfo.relative_path`), so the rule editor's preview, the
`permissions:test` dry-run and enforcement all judge the same path; the
evaluation loop itself is shared
([`shared/permissions/evaluation.py`](../../src/jentic_one/shared/permissions/evaluation.py)).
Normalization never decodes an encoded separator (`%2F`, `%5C`) — the raw URL
is forwarded, so rules must not see a different segment structure than the
upstream gets — and a path that spells a dot segment with escapes or a
backslash is denied
(`ambiguous_path`)
([`shared/url_path.py`](../../src/jentic_one/shared/url_path.py)).
Rules written with the base path baked in can be fixed with
`jentic_one rewrite-rule-base-paths` (see
[upgrades](../operations/upgrades.md#binding-rules-written-against-the-full-upstream-path));
until then the broker logs `rule_written_with_server_base_path` whenever such
a rule changes a verdict, in either direction.
Authorization is direct-only: the retired toolkit path and its
`broker.direct_bindings_enabled` fallback were removed with the phase-6b
table drops (setting the flag to `false` now fails config validation).

### Cache invalidation and staleness bounds

The broker's hot path caches three kinds of authorization lookups in memory per
process:

- **Token and actor-status verdicts** ([`broker/core/token_validation.py`](../../src/jentic_one/broker/core/token_validation.py),
  bounded by `broker.resolve_cache_ttl_seconds`, default `30` s).
- **Direct credential-binding derivations** ([`broker/repos/caching_credential_deriver.py`](../../src/jentic_one/broker/repos/caching_credential_deriver.py),
  bounded by `broker.toolkit_cache_ttl_s`, default `30` s).
- **Permission-rule lists** ([`broker/repos/agent_rule_evaluator.py`](../../src/jentic_one/broker/repos/agent_rule_evaluator.py),
  bounded by `broker.rule_cache_ttl_s`, default `30` s).

`install_broker_auth` ([`broker/core/setup.py`](../../src/jentic_one/broker/core/setup.py)) registers a
`BrokerCacheInvalidator` on the shared `Context` (`ctx.invalidate_broker_caches(...)`).
When an agent's credential binding is bound/unbound/resumed, an agent is
disabled/enabled/archived/purged, an API key or OAuth token is rotated or
revoked, or a credential or permission rule set is updated/deactivated/deleted
in the **same process** (e.g. single-process harnesses and in-process tests),
the hook immediately evicts the affected deriver, rule-evaluator, and
token/actor-status cache entries with zero wait for the TTL.

In **multi-process deployments** (where the broker runs in its own process or
across multiple stateless replicas), each broker instance holds its own
in-memory LRU caches and does not share the control plane's `Context`. Cross-process
propagation is bounded by the configured TTLs above (default `30` s; set the
respective TTL to `0` to disable caching). Queued async executions bypass these
caches and re-authorize against live database state when the worker claims the
job (`build_queued_execution_authorizer`).

## Vendor CLI facades

A facade lets a vendor's own CLI, pointed at the broker with its base-URL
override, run through this pipeline
([`broker/web/routers/facades.py`](../../src/jentic_one/broker/web/routers/facades.py),
[`broker/core/facades.py`](../../src/jentic_one/broker/core/facades.py)). Each facade is a
real route registered before the catch-all, under the reserved `/_facade`
prefix: its first segment starts with an underscore, which no hostname can
contain, so it never shadows a URL the catch-all would proxy. Any other path
under `/_facade` answers 404. Facades are off unless `cli_facades.enabled`
and the vendor's own `enabled` are both set, checked on every request.

The Cloudflare facade (`cf`, `CLOUDFLARE_API_BASE_URL=<broker>/_facade/cloudflare`)
maps `/_facade/cloudflare/<path>?<query>` to
`https://api.cloudflare.com/client/v4/<path>?<query>`, with the path and query
kept exactly as the client encoded them. A call differs from a proxied one in
these ways:

- **Authentication.** Only a `cit_` CLI invocation token minted for the
  vendor is accepted ([identity and authorization](identity-and-authorization.md)).
  The call spends the agent's rate limit as a proxied call does.
- **Trace and credential.** The call is recorded under the token's
  server-chosen trace id, so a command's calls group together, and runs in a
  span on that trace whatever `traceparent` the client sent. The token's
  credential name takes the place of the `Jentic-Credential-Name` header in
  credential selection; the `Jentic-Credential-*` request headers are not read.
- **Headers.** Inbound headers carrying an invocation token, and inbound
  trace context, are never forwarded. Response headers go through an
  allowlist, so an upstream `Set-Cookie`, `Location` or `WWW-Authenticate`
  never lands on the broker's origin.
- **Errors.** The CLI cannot read Problem Details, so the facade's route class
  sets an error-response hook that the central exception handlers apply to
  the response they built (`apply_route_error_response_hook` in
  [`shared/web/errors.py`](../../src/jentic_one/shared/web/errors.py)): the error keeps its status and is
  rendered as Cloudflare's envelope,
  `{"success": false, "errors": [{"code": <status>, "message": "Jentic: …"}], "messages": [], "result": null}`,
  with the problem's detail and agent directive in the message.
- **Holds.** A call a rule holds for approval is filed as usual and answered
  with a 409 error naming the held execution, its review URL and its result
  link, instead of the 202 held envelope, which a CLI would misread as the
  operation's response. A facade call always runs synchronously.

Agents never hard-code a facade's address. The control plane lists the facades
that are switched on and served, with the address to point each CLI at, on
`GET /cli-facades`
([`control/services/cli_facades/service.py`](../../src/jentic_one/control/services/cli_facades/service.py)):
the vendor's `public_address` when set, otherwise the broker URL
`GET /instance` advertises (`server.mcp.broker_url`) plus the facade prefix.
`jentic cf …` reads it, mints an invocation token, runs the local `cf` with
`CLOUDFLARE_API_BASE_URL` and `CLOUDFLARE_API_TOKEN` set (and any other
Cloudflare credential variables removed), renews the token while `cf` runs,
revokes it on exit and passes `cf`'s exit code through.

### Proxy route

`/_facade/proxy/<full upstream URL>` is the broker end of an egress proxy that
relays a vendor CLI's calls with the command's invocation token. It takes the
upstream URL from the raw path as the catch-all does (`%2F` and the query kept;
a URL with a `.` or `..` segment answers 404) and runs the facade pipeline
above, with three differences:

- **Any served vendor's token.** A `cit_` token of any vendor is accepted
  while that vendor's facade is switched on (else 404 `cli_facade_not_found`);
  an `at_` token, API key or JWT answers 401.
- **The URL must be the token's vendor's.** After discovery, the resolved
  API's vendor must belong to the token's vendor (`github` covers
  `github-com`, `cloudflare` covers `cloudflare-com`, `aws` covers
  `amazonaws-com`; `PROXY_API_VENDORS` in
  [`broker/core/facades.py`](../../src/jentic_one/broker/core/facades.py)).
  Any other API answers 403 `cli_invocation_vendor_mismatch` before rules or
  credentials are looked at; an unregistered URL answers 404.
- **Errors in the URL's vendor's shape.** The CLI behind the egress proxy
  believes it talks to the vendor, so errors take the shape of the vendor the
  URL's host belongs to: GitHub's REST error for `*.github.com`, Cloudflare's
  v4 envelope for `*.cloudflare.com` (the same bodies as the GitHub and
  Cloudflare facades), standard RFC 9457 Problem Details for any other host.
  Keyed on the URL rather than the token, so a missing or invalid token gets
  the same shape. Successful responses keep the facade header allowlist, or
  the GitHub facade's for a GitHub URL (`Link`, `X-GitHub-*`), unrewritten.
  The egress proxy of `jentic run --container` is this route's client (see
  [Local agent isolation](../security/same-host/local-agent-isolation.md)).

### GraphQL top-level-field rules

Every GitHub GraphQL operation travels as one `POST https://api.github.com/graphql`,
so a rule on that path alone would allow every mutation. A call to that
endpoint, through the GitHub facade or the forward proxy, is inspected before
discovery
([`shared/permissions/graphql_document.py`](../../src/jentic_one/shared/permissions/graphql_document.py)):
the body is read once, under the usual body cap, and parsed by `graphql-core`
into its operation type and top-level **field names** (aliases resolved, root
fragments and inline fragments expanded). A request that cannot be judged that
way (not `POST`, a URL query, a batch, a persisted query, duplicate JSON keys,
more than one operation, a subscription, an unparsable document, a root
fragment on another type) is refused with 400 `graphql_request_rejected`.

Rules then run once per top-level field and the most restrictive field
verdict is the call's. A rule with `match_mode: graphql` carries a selector in
`path` (`query`, `mutation`, `mutation:createPullRequest,addComment`) and
matches only such a field
([`shared/permissions/graphql_rules.py`](../../src/jentic_one/shared/permissions/graphql_rules.py));
a reader that does not know the mode compiles the selector as a path pattern
of an unknown mode, which never matches. On top of the rules
([`shared/permissions/github_graphql.py`](../../src/jentic_one/shared/permissions/github_graphql.py)),
mutations that create lasting access or credentials or change repository
protections are always denied (403 `graphql_field_denied`, naming the field),
and mutations that move Git refs are held for approval even when a rule
allows them. The same module holds the GitHub GraphQL rule presets
(`read_only`, `pr_write`, `pr_write_with_approval`). A queued or approved call
is re-inspected on the worker from the body it will send.

### Governed git

git's own clones, fetches and pushes to github.com run through a pipeline of
their own
([`broker/services/git/pipeline.py`](../../src/jentic_one/broker/services/git/pipeline.py)),
not the execute pipeline, which needs a registry operation, buffers bodies and
caps them at a few MiB. A git smart-HTTP URL at the proxy route,
`/_facade/proxy/https://github.com/<owner>/<repo>[.git]/info/refs?service=git-upload-pack|git-receive-pack`,
`…/git-upload-pack` or `…/git-receive-pack`, is matched by a route ahead of the
general proxy route (`GitProxyRoute`). It takes a GitHub invocation token
(another vendor's answers 403 `cli_invocation_vendor_mismatch`) and is served
while the GitHub facade and `cli_facades.git.enabled` are on (else 404). Errors
are one `text/plain` line, which git prints as `remote: Jentic: …`; a 401
carries `WWW-Authenticate: Basic`, so a git holding the token in a credential
helper or URL offers it. The egress proxy of `jentic run --container`, which
injects the token itself, passes a 401 on to git as a 403 without the
challenge, so git prints the line instead of prompting. The execute pipeline refuses every github.com git URL (400
`git_request_rejected`), whatever the registry holds, so a REST path rule can
never decide a push.

**API identity and credential.** The pipeline runs under a built-in API,
`github-com` / `github-git` / `1`
([`broker/core/git_api.py`](../../src/jentic_one/broker/core/git_api.py)), with
operations `github-git.info-refs`, `github-git.upload-pack` and
`github-git.receive-pack`. It is not a registry row: nothing needs discovering
(three fixed endpoints on a fixed upstream), and a registry entry would let the
buffered execute pipeline route these URLs. Bindings and credential selection
work as for any API: a GitHub credential covers the git API when its API scope
is the vendor only (`github.com`, no API name), or when it is scoped to
`github-git`; a credential scoped to the REST API alone (`api.github.com`) does
not. The normal connect flow creates a `github-git` credential from a vendor
entry whose `vendor` names the built-in API's catalog id,
`github.com/github-git`
([`shared/builtin_apis.py`](../../src/jentic_one/shared/builtin_apis.py); no
catalog import is attempted for it): `jentic connect github-git`. It covers the
git API only, and its binding carries its own rules, apart from the REST
binding's. A vendor-wide credential covers git too, but also every other
GitHub API under one rule list (jentic/jentic-one#1908); only the rule kinds
stay separate (a REST path rule never decides a git request, a git rule never
a REST one). The stored token (bearer or OAuth) is injected as git's
`Authorization: Basic base64("x-access-token:<token>")`; a credential that
injects anything else answers 424 `git_credential_unsupported`.

**Rules.** A rule with `match_mode: git` carries a selector in `path`,
`<fetch|push|force>:<owner>/<repo>[:refs/heads/<glob>|refs/tags/<glob>]`
([`shared/permissions/git_rules.py`](../../src/jentic_one/shared/permissions/git_rules.py)).
Only a git rule or a condition-less `deny` decides a git request; git rules are
`allow` or `deny` only and take no methods or operations. An `allow` covers its
level and below (a push rule also allows the fetch and advertisement a push
needs, `push.negotiate` included); a `deny` covers its level and above. A
binding that may fetch `octo-org/app` and push only to
`refs/heads/jentic-e2e/*` holds two rules, `allow fetch:octo-org/app`
and `allow push:octo-org/app:refs/heads/jentic-e2e/*`; everything
else is default-denied. A reader that does not know the mode never matches the
selector (fail closed). The info/refs and upload-pack requests are checked as
a fetch (or, for `git-receive-pack` info/refs, a push with no ref) and refused
with 403 before any credential is decrypted.

**Pushes.** The receive-pack body is spooled to a bounded temporary file
(`spool_memory_bytes` in memory, then disk, up to `max_push_bytes`) and read
before anything reaches GitHub: optional `shallow` lines, the ref commands
(capabilities on the first), push options, then the pack. A body that is just
a flush packet is git's authentication probe before a large push and is
answered locally. Each command is then checked
([`broker/core/git_push.py`](../../src/jentic_one/broker/core/git_push.py)):

- **Hard limits.** Only well-formed `refs/heads/*` and `refs/tags/*` refs; `refs/jentic/*`
  is reserved. No rule lifts these.
- **Levels.** A create needs `push`; a deletion needs `force`; any update or
  deletion of an existing tag needs `force`, even a fast-forward (tags are
  immutable); a branch update needs `push`, and `force` unless it is shown to
  be a fast-forward.
- **The force-push check.** The pack is indexed for its commit objects
  ([`broker/core/git_pack.py`](../../src/jentic_one/broker/core/git_pack.py),
  a minimal reader: zlib, `OFS_DELTA` and `REF_DELTA` against in-pack bases,
  trailing checksum verified, commit bytes capped by
  `max_inspected_commit_bytes`). The walk goes from the new commit through
  in-pack parents, first parent first; reaching the old commit proves a
  fast-forward, otherwise each boundary commit (outside the pack) is compared
  on GitHub (`GET /repos/{owner}/{repo}/compare/{old}...{boundary}` with the
  bound credential) until one answers `ahead` or `identical`, at most
  `max_compare_requests` calls per push. None does: a non-fast-forward, denied.
  A boundary GitHub does not have, in a pack holding deltas against objects
  outside it, is a thin pack hiding a commit: denied with the directive to
  retry with `git -c pack.useThin=false push`.

Any refused ref refuses the whole push, so the pipeline forwards the client's
original body once or not at all, and GitHub keeps its own old-SHA
compare-and-swap, `--atomic`, push protection and rulesets. A refusal is
answered as git's report-status, `unpack ok` and `ng <ref> jentic: <reason>`
for every ref (the others with "not applied"), on side-band 1 when the client
asked for side-band, with a `remote:` progress line on band 2; git prints
`! [remote rejected] <ref> (jentic: <reason>)`. Each refused ref also records
`broker.pbac_denied` (`mode: git`, with `repository`, `ref`, `git_action` and
`detail`). An allowed push streams upstream and GitHub's report-status streams
back unchanged.

**Streaming and limits.** Both bodies stream; the execute body caps, upstream
timeout, retries and circuit breaker do not apply. The upstream is always
`https://github.com` (and `https://api.github.com` for compare); redirects are
never followed, so the credential is only sent there. `Git-Protocol` is not
forwarded, so GitHub answers protocol v0, whose ref advertisement the
pipeline filters as it streams: `refs/jentic/*` refs are dropped and the
`push-cert` capability is withheld. Timeouts and sizes live under
`cli_facades.git` (`connect_timeout_s`, `idle_timeout_s`, `transfer_timeout_s`,
`compare_timeout_s`, `max_push_bytes`, `max_ref_updates`). Every served request
leaves one execution record under the invocation token's trace id, written
after its response ends; a refused push is recorded as failed.

## What gets recorded

Executions land as append-only `ExecutionRecord` rows in the admin DB
([`shared/executions/ingest.py`](../../src/jentic_one/shared/executions/ingest.py)): status, duration, operation and API
identity, HTTP status, actor, origin, and the credential used (by id and
name, never the secret). On the buffered and async paths the write happens
before the response. On the streaming fast path every outcome — completed,
failed partway through the body, or cut off by a client disconnect — writes
exactly one record, and the write is **best-effort** and never holds the
response: it is handed to the app's background-task registry
([`shared/web/background_tasks.py`](../../src/jentic_one/shared/web/background_tasks.py))
and bounded by `broker.streaming_persist_timeout_s`; on shutdown the registry
drains for up to `services.background_task_drain_timeout_s` before the
databases close. A database failure, timeout, or shutdown cancellation is
logged and counted (`broker.streaming_execution.persist_failures`,
`app.background_tasks`, [monitoring.md](../operations/monitoring.md)) but does
not fail the call, so a call can, rarely, lack its record. Concurrent streaming
persists are capped per process (`broker.streaming_persist_max_concurrency`,
clamped to `databases.admin.pool_max - 1`, the admin base pool less one;
SQLAlchemy's default overflow can open up to 10 connections beyond `pool_max`).
An excess persist waits for a slot for at most
`broker.streaming_persist_acquire_timeout_s` and is then abandoned and counted
(`reason="backlog_timeout"`); the queue is visible as
`broker.streaming_execution.persist_pending`. The wait never touches admission:
a request releases its admission slot as soon as its response is complete or its
client disconnects, so persists queued behind a stalled admin database count
neither toward `broker.admission.in_flight` nor toward `/ready` saturation. The
acquire timeout bounds how many records such a stall keeps queued in memory and
how long a *queued* record holds up shutdown; a persist that already holds a
slot and is writing runs under the task registry's
`broker.streaming_persist_timeout_s` budget and the admin pool's checkout
timeout. When the upstream fails partway through a streamed body, the status
line has already been sent: the broker logs one `upstream_network_failure` (or
`streaming_response_aborted` for a size-cap or transfer-deadline cut-off) and
closes the connection without the final chunk, so an HTTP/1.1 chunked or HTTP/2
client sees a truncated body (an HTTP/1.0 client reads a close-delimited body
and cannot tell). Lifecycle events (`EXECUTION_COMPLETED` /
`EXECUTION_FAILED`), permission denials (`PBAC_DENIED`), and credential
incidents each emit typed events into the monitor.

## Related

- [Composition and processes](composition-and-processes.md) — why the broker
  always runs alone, and how its runner stack is assembled at startup.
- [Identity and authorization](identity-and-authorization.md) — the token
  kinds the broker's composite validator accepts.
- [`docs/reference/endpoints.md`](../reference/endpoints.md) — the permission
  each route requires.
