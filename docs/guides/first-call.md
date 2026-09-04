# Your first brokered call

Six steps from a running instance to a real API response, with the agent never
seeing your credentials. This page is the route map — each step is short and
links to the guide that owns the detail.

**No instance yet?** Install one first and come back — the
[installation guide](../installation/README.md) covers every path (Docker,
systemd, Helm, AWS Marketplace); the [README quickstart](../../README.md#quickstart)
is the fastest local trial. Everything below assumes the app
(`http://127.0.0.1:8000` on a local install) and the broker are running.

> **Run the agent on a different machine from Jentic One.** An agent running as
> the same OS user can read the credential database and encryption key off disk,
> whatever the API-level controls allow. Read the
> [security hardening guide](../security/security.md) before pointing an instance
> at a real credential — or use [`jentic run`](local-agent.md) to isolate a local
> coding agent behind its own Unix user.

## 1. Create your admin account

Open `/setup` in the web UI and create the first administrator — one-time; the
page redirects to login once the account exists. This account is the operator:
it imports APIs, stores credentials, and approves agents.

From the terminal instead: `jenticctl setup` creates the account;
`jenticctl wizard` sequences this whole page into one guided flow.

## 2. Import an API

Pull an API description into your local registry from the public
[Jentic API Directory](https://github.com/jentic/jentic-public-apis):

```bash
jentic catalog search httpbin   # find an API in the public directory (used in step 6)
```

`jentic catalog` run bare opens an interactive browser; `jentic apis` manages
what you've imported. You can also register your own OpenAPI description for a
private service — same custody, permissions, and audit trail. To correct an
imported description without editing the original, use [Overlays](overlays.md).

## 3. Store a credential

Store what the API needs (API key, bearer, basic, or an OAuth2 flow) in the UI.
It is encrypted at rest and never returned to a caller — it is decrypted only
inside the broker, at execution time. How credentials attach to toolkits and
their one-active-credential-per-API rule:
[Credentials and toolkits](credentials-and-toolkits.md).

## 4. Register the agent

From the machine that will run the agent:

```bash
jentic register
```

On a local install, confirm the prompted `http://127.0.0.1:8000` and the broker
is seeded automatically; a remote install needs `--url` and `--broker-url`
([CLI README](../../cli/README.md#usage)). `register` generates a keypair,
files a dynamic client registration, then **waits for an operator to approve
the agent** — approve it in the UI at `/app`. Re-running is idempotent.

Setting up a local *coding* agent (Claude Code, Cursor, …)? `jentic setup` does
identity + skills + isolation in one flow — see
[Local coding agents](local-agent.md). All the ways an agent can connect
(CLI + skill, MCP over stdio or HTTP, raw HTTP):
[Connecting an agent](connecting-agents.md).

## 5. Grant access

Access is **default-deny**: an approved agent is bound to nothing until an
operator grants it. Asking is a reviewable request, not a silent widening:

```bash
jentic access request --toolkit httpbin.org/httpbin   # prints an approve_url for the operator
jentic access status <request-id>                     # has it been granted?
```

## 6. Make the call

```bash
jentic search get             # find an imported operation
jentic inspect <operation>    # its method, params, and schemas
jentic execute GET:https://httpbin.org/get --json
```

`execute` takes the operation's full upstream URL (the form `search` and
`inspect` report) or its operation_id — the broker is a forward proxy, not a
path router. It checks the agent's permissions, attaches the stored credential
after the check, forwards the request, and writes an audit record.

## Where to go next

- **Have a coding agent do this for you.** The
  [agent runbooks](../agent/README.md) cover install through the
  discover → access → execute loop as directly executable steps.
- **Full CLI reference.** Every `jenticctl` and `jentic` command:
  [`cli/README.md`](../../cli/README.md).
- **In-app API reference.** A running deployment serves its own interactive
  API reference at `/docs`, generated from code.
- **Endpoint & scope reference.** Every HTTP route and the scope it requires:
  [endpoint reference](../reference/endpoints.md).
- **Run it somewhere real.** The [installation guides](../installation/README.md),
  then the [security hardening guide](../security/security.md).
