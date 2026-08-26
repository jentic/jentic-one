# Quickstart — your first brokered call

This is the full walkthrough behind the six steps in the
[README](../../README.md#first-brokered-call): from a running Jentic One instance
to a response from a real API, with the agent never seeing your credentials.

If you do not have an instance yet, install one first — see
[Quickstart → install options](../../README.md#quickstart) (signed release binary,
bootstrap script, or from source). This guide assumes the App control plane and
the Broker are running and reachable (the local default is
`http://127.0.0.1:8000`).

> **Run the agent on a different machine from Jentic One.** An agent running as
> the same OS user can read the credential database and encryption key off disk,
> whatever the API-level controls allow. Read the
> [security hardening guide](../security/security.md) before pointing an instance
> at a real credential.

## 1. Create your admin account

Open `/setup` in the operator UI and create the first administrator. This is a
one-time step: once the account exists, this page redirects to login. The account
you create here is the operator: it configures the instance, imports APIs, stores
credentials, and approves agents.

To do this from the terminal instead, run `jenticctl setup`.

## 2. Import an API

Import an API description into your local registry. Browse and import from the
public [Jentic API Directory](https://github.com/jentic/jentic-public-apis) with
the catalog browser:

```bash
jentic catalog search httpbin   # find an API in the public directory (used in step 6)
```

`jentic catalog` opens an interactive browser to search the public directory and
import an API into your local registry; `jentic apis` manages the ones you have
imported. See [`cli/README.md`](../../cli/README.md) for the full command surface.

Or register your own OpenAPI description for a private or internal service — the
same credential custody, per-agent permissions and audit trail apply whether the
upstream is a third-party API or one that exists only inside your network. If an
imported description needs correcting without editing the original, see
[Overlays](overlays.md).

## 3. Store a credential

Store the credential the API needs (an API key, bearer token, basic auth, or an
OAuth2 flow). It is encrypted at rest and is **never** returned to a caller,
logged in cleartext, or exposed to the agent — it is decrypted only inside the
Broker, at execution time. See
[Credentials and toolkits](credentials-and-toolkits.md) for how a credential is
stored and how one credential is shared across an API's operations.

## 4. Register the agent

Give the agent its own identity. From the machine that will run the agent:

```bash
jentic register
```

`register` defaults to the local install (`http://127.0.0.1:8000`) and seeds the
local broker for you, so on a local setup you can just confirm the prefilled URL
with Enter. (Prefer to be explicit, or scripting it? `jentic register --url
http://127.0.0.1:8000` is equivalent.)

This generates an Ed25519 keypair and registers the agent through dynamic client
registration. **`register` then blocks, waiting for an operator to approve the
agent.** On a single-operator install you are the operator: approve the pending
agent in the UI at `/app`, and the command completes automatically once the
agent is active. Re-running `register` is idempotent. (Registering with a
**remote** deployment instead? Pass `--url` and `--broker-url` — see the
[CLI README](../../cli/README.md#usage).)

## 5. Grant access

Bind the agent to a toolkit so it can reach specific operations. Access is
**default-deny**: a rule-less binding blocks everything, and permissions are
first-match. An agent reaches only the operations it has been approved for, and
asking for more is a reviewable request rather than a silent widening:

```bash
jentic access request --toolkit httpbin.org/httpbin   # file a request the operator can review
jentic access status <request-id>                     # check whether it has been granted
```

## 6. Make the call

Find an operation, inspect it, and run it through the Broker:

```bash
jentic search get             # find an imported operation
jentic inspect <operation>    # see its method, params and schemas
jentic execute GET:https://httpbin.org/get --json
```

The `execute` target is the operation's full upstream URL (the same form `search`
and `inspect` report) or its operation_id — the Broker is a forward proxy, not a
path router.

The Broker checks the agent's permissions, attaches the stored credential after
the permission check, forwards the request, and writes an audit record. The
credential is added inside the Broker and is never returned to the caller.

## Where to go next

- **Have a coding agent do this for you.** [AGENTS.md](../../AGENTS.md) has an
  install-and-use section a coding agent can follow to install Jentic One and
  register itself against it.
- **Full CLI reference.** Every `jenticctl` and `jentic` command:
  [`cli/README.md`](../../cli/README.md).
- **In-app API reference.** On a running deployment, `/docs` serves interactive
  Swagger and `/redoc` the rendered reference — both generated from code.
- **Endpoint & scope reference.** Every HTTP route and the scope it requires:
  [endpoint reference](../reference/endpoints.md).
- **Run it somewhere real.** [Build & deploy](../../deploy/README.md) and the
  [security hardening guide](../security/security.md).
