# CLI reference

<!-- GENERATED FILE — do not hand-edit. Regenerate with `make cli-reference`
     (cli/cmd/clidocs). Drift-gated by TestCommittedCLIMarkdownUpToDate in
     cli/pkg/clitree and by the CI cli job. -->

Every command, flag, and example of the `jentic` and `jenticctl` binaries,
generated from the cobra command tree they ship with (the same source as
`--help` and the in-app docs at `/app/docs`). Hidden commands and flags are
left out. For an overview of the binaries see [`cli/README.md`](../../cli/README.md).

- [`jentic`](#jentic) — discover, inspect, and run against the Jentic API catalog
- [`jenticctl`](#jenticctl) — install and operate jentic-one locally

## jentic

jentic: discover, inspect, and run against the Jentic API catalog

```text
jentic is the command-line companion for working with the Jentic API
catalog. Register and switch agent identities, browse and import APIs from
the public catalog into your local registry, inspect operations, and execute
against them.

New here? If you're a person setting up a local agent, run `jentic setup`
to set one up end to end (identity + skills). If you're an agent
that just needs tokens for an existing identity, run `jentic register`. Then
browse the public catalog with `jentic catalog`. To install and operate
jentic-one locally, use the `jenticctl` CLI (e.g. `jenticctl install`). Use
`jentic <command> --help` for details.
```

**Commands**

*Identity & access*

| Command | Description |
|---|---|
| [`jentic setup`](#jentic-setup) | Set up a new local agent (identity + skills) — start here if you're a person |
| [`jentic register`](#jentic-register) | Obtain tokens for an existing agent identity (identity only, no skills) — agents run this |
| [`jentic logout`](#jentic-logout) | Revoke and clear the active identity's cached token |
| [`jentic context`](#jentic-context) | Manage and switch contexts (environment + identity + mode) |
| [`jentic env`](#jentic-env) | Manage Control Plane environments (deployment targets) |
| [`jentic identity`](#jentic-identity) | Manage identities (agents and users) and their credentials |
| [`jentic whoami`](#jentic-whoami) | Show who you are: identity, status, permissions, and credential bindings |
| [`jentic migrate`](#jentic-migrate) | Migrate legacy ~/.jentic profiles to the XDG context model |

*APIs*

| Command | Description |
|---|---|
| [`jentic catalog`](#jentic-catalog) | Browse, search, and import APIs from the public catalog |
| [`jentic apis`](#jentic-apis) | Browse and manage APIs in the local registry |
| [`jentic endpoints`](#jentic-endpoints) | Browse the API endpoint + permission reference |
| [`jentic credentials`](#jentic-credentials) | List and inspect credentials (secrets are server-masked) |
| [`jentic connect`](#jentic-connect) | Start connecting a credential for a vendor or a registry API (a human approves it) |
| [`jentic access`](#jentic-access) | Show and change what your credential bindings let you do |

*Find and run operations*

| Command | Description |
|---|---|
| [`jentic search`](#jentic-search) | Search for operations by natural-language query |
| [`jentic inspect`](#jentic-inspect) | Inspect an operation's contract (schema, parameters, examples) |
| [`jentic execute`](#jentic-execute) | Execute an operation through the Jentic broker |
| [`jentic executions`](#jentic-executions) | Follow your held executions (calls awaiting human approval) |
| [`jentic history`](#jentic-history) | Query and export execution history |
| [`jentic events`](#jentic-events) | Observe control-plane events |
| [`jentic api`](#jentic-api) | Authenticated passthrough to the control plane |

*Local agent client*

| Command | Description |
|---|---|
| [`jentic skill`](#jentic-skill) | Generate the Jentic skill set into your agent's native layout |
| [`jentic reset`](#jentic-reset) | Wipe this machine's jentic identity state and remove any leftover agent account |
| [`jentic doctor`](#jentic-doctor) | Diagnose this agent's local jentic setup (read-only) |
| [`jentic mcp`](#jentic-mcp) | Serve Jentic tools to a local MCP client over stdio |

*Administration*

| Command | Description |
|---|---|
| [`jentic admin`](#jentic-admin) | Administer the jentic-one platform |
| [`jentic theme`](#jentic-theme) | Set the persistent color theme |

### jentic global flags

Accepted by every command (inherited from the root).

| Flag | Shorthand | Type | Default | Description |
|---|---|---|---|---|
| `--context` |  | `string` |  | Context to act on (overrides the active context; \$JENTIC_CONTEXT) |
| `--mode` |  | `string` |  | Interaction mode: human\|agent (\$JENTIC_MODE) |
| `--theme` |  | `string` |  | Color theme: dark\|light\|no-color (\$JENTIC_THEME) |

### Identity & access

#### jentic setup

Set up a new local agent (identity + skills) — start here if you're a person

```text
jentic setup [flags]
```

```text
setup takes a fresh machine from nothing to ready: it connects this
machine to a Jentic install (creating the environment, identity and context
if needed), registers the agent (Dynamic Client Registration), prints an
approval link and waits for a human to approve it, mints and saves tokens,
and generates the Jentic CLI-usage skill into your agent runtime's native
layout, plus the `jentic mcp` server entry for each detected MCP runtime.

It is a thin orchestration of `jentic register` and `jentic skill`: nothing
here you can't do by hand, just sequenced so you can start playing right
away. Re-running refreshes everything idempotently.
```

**Flags**

| Flag | Shorthand | Type | Default | Description |
|---|---|---|---|---|
| `--all` |  | `bool` | `false` | target every supported operator |
| `--broker-url` |  | `string` |  | broker (data plane) URL for the environment — execute needs it on a remote install; seeded automatically for loopback |
| `--dry-run` |  | `bool` | `false` | show what would happen without registering or writing |
| `--env` |  | `string` |  | environment name for --url (default: derived from the URL host) |
| `--force` |  | `bool` | `false` | re-register the agent even if the identity already has one (does not overwrite an edited skill block) |
| `--name` |  | `string` |  | agent client name shown to the approver |
| `--operator` |  | `stringSlice` | `[]` | operators to target (repeatable or comma-separated) |
| `--scope` |  | `string` |  | skill placement scope: user or project (default: per-operator) |
| `--skip-skill` |  | `bool` | `false` | provision identity only; do not write skill files |
| `--timeout` |  | `duration` | `5m0s` | how long to wait for approval |
| `--url` |  | `string` |  | Jentic install URL to connect to (creates environment/identity/context on first run) |
| `--yes` | `-y` | `bool` | `false` | non-interactive: no prompts; with no --operator/--all, the skill targets the detected operators |

Also accepts the [jentic global flags](#jentic-global-flags).

**Examples**

```sh
jentic setup
jentic setup --url https://jentic.example.com --broker-url https://broker.jentic.example.com
jentic setup --url https://jentic.example.com --operator claude --yes
jentic setup --skip-skill   # identity only
jentic setup --dry-run
```

#### jentic register

Obtain tokens for an existing agent identity (identity only, no skills) — agents run this

```text
jentic register [flags]
```

```text
register connects this machine to a Jentic install: it generates an
Ed25519 keypair (if absent), performs Dynamic Client Registration, waits
for an operator to approve the agent, then mints and saves tokens.

With an active context (`jentic context use`), it registers that context's
identity with its environment. On a fresh machine, pass --url (or answer
the prompt) and register creates the environment, identity and context in
one step, activates them, and registers.

Local install: use http://127.0.0.1:8000 (NOT http://localhost:8000) — the
token-exchange audience is matched exactly against the backend's
canonical_base_url, and a local backend uses 127.0.0.1. For a loopback URL,
register also seeds the environment's broker_url (http://127.0.0.1:8100) so
`jentic execute` works without extra flags.

Remote install: pass --broker-url too. The broker usually lives on its own
host and is never derived from the control-plane URL; without it the
environment has no broker and `jentic execute` fail-closes until one is set
(ask your operator for the broker URL). Re-running register with
--broker-url fills it in on an existing environment.
```

**Flags**

| Flag | Shorthand | Type | Default | Description |
|---|---|---|---|---|
| `--broker-url` |  | `string` |  | broker (data plane) URL for the environment — execute needs it on a remote install; seeded automatically for loopback |
| `--env` |  | `string` |  | environment name for --url (default: derived from the URL host) |
| `--force` |  | `bool` | `false` | re-register even if this identity already has a registration |
| `--name` |  | `string` |  | agent name shown to the approving operator (default: hostname) |
| `--timeout` |  | `duration` | `5m0s` | how long to wait for approval |
| `--url` |  | `string` |  | Jentic install URL to connect to (creates environment/identity/context on first run) |
| `--yes` | `-y` | `bool` | `false` | skip the interactive prompt; use flags + defaults |

Also accepts the [jentic global flags](#jentic-global-flags).

**Examples**

```sh
jentic register --url http://127.0.0.1:8000   # local install (use 127.0.0.1, not localhost)
jentic register --url https://jentic.example.com --broker-url https://broker.jentic.example.com
jentic register --url https://jentic.example.com --name crawler --env prod
jentic register                      # active context (or interactive setup)
jentic register --force              # re-register the active identity
```

#### jentic logout

Revoke and clear the active identity's cached token

```text
jentic logout [flags]
```

Also accepts the [jentic global flags](#jentic-global-flags).

#### jentic context

Manage and switch contexts (environment + identity + mode)

```text
jentic context [flags]
```

```text
context binds an Environment, an Identity, and an interaction mode into a
named context, and switches which one commands act on. It is the
successor to the old `jentic profile` (see `jentic migrate`).
```

Also accepts the [jentic global flags](#jentic-global-flags).

**Subcommands**

| Command | Description |
|---|---|
| [`jentic context create`](#jentic-context-create) | Create a context binding an environment + identity + mode |
| [`jentic context use`](#jentic-context-use) | Set the active context |
| [`jentic context list`](#jentic-context-list) | List contexts and mark the active one |
| [`jentic context view`](#jentic-context-view) | Show the active context |
| [`jentic context rename`](#jentic-context-rename) | Rename a context |
| [`jentic context delete`](#jentic-context-delete) | Delete a context |

##### jentic context create

Create a context binding an environment + identity + mode

```text
jentic context create <name> [flags]
```

Aliases: `add`

```text
Create a context — a named binding of an environment (--env), an identity
(--identity), and a mode (--context-mode, default human). Contexts are what
commands act through; --use activates the new one immediately. --context-mode
is the mode SAVED in the context; the global --mode is this invocation's own
session mode, and agent mode fences this command. Most people never run this
directly — `jentic register --url …` creates the trio for you.
```

**Flags**

| Flag | Shorthand | Type | Default | Description |
|---|---|---|---|---|
| `--context-mode` |  | `string` |  | Interaction mode to save in this context: human\|agent (default human) |
| `--env` |  | `string` |  | Environment this context targets |
| `--force` |  | `bool` | `false` | Replace an existing context of the same name |
| `--identity` |  | `string` |  | Identity this context acts as |
| `--use` |  | `bool` | `false` | Set this as the active context after creating it |

Also accepts the [jentic global flags](#jentic-global-flags).

**Examples**

```sh
jentic context create prod --env prod --identity crawler --use
jentic context create local --env local --identity laptop --context-mode agent
```

##### jentic context use

Set the active context

```text
jentic context use <name> [flags]
```

```text
Set the active context — the environment + identity + mode that every
command acts through when you don't pass --context. The named context must
already exist (`jentic context list`); this only switches which one is active.
```

Also accepts the [jentic global flags](#jentic-global-flags).

**Examples**

```sh
jentic context use prod
jentic context use local   # then: jentic catalog / jentic execute …
```

##### jentic context list

List contexts and mark the active one

```text
jentic context list [flags]
```

Aliases: `ls`

Also accepts the [jentic global flags](#jentic-global-flags).

##### jentic context view

Show the active context

```text
jentic context view [flags]
```

Also accepts the [jentic global flags](#jentic-global-flags).

##### jentic context rename

Rename a context

```text
jentic context rename <old> <new> [flags]
```

```text
rename changes a context's name (e.g. an auto-derived `qa1-crawler` to
something friendlier) without delete+recreate. If the renamed context is
active, the active selection follows the new name.
```

Also accepts the [jentic global flags](#jentic-global-flags).

**Examples**

```sh
jentic context rename qa1-crawler qa
```

##### jentic context delete

Delete a context

```text
jentic context delete <name> [flags]
```

Aliases: `rm`

```text
delete removes a context (the equivalent of removing a legacy profile).
It refuses to delete the ACTIVE context
(switch first) to avoid a dangling active_context. With --identity it also
removes the referenced identity iff no other context uses it.
```

**Flags**

| Flag | Shorthand | Type | Default | Description |
|---|---|---|---|---|
| `--identity` |  | `bool` | `false` | Also delete the referenced identity if unused elsewhere |
| `--yes` | `-y` | `bool` | `false` | Skip the confirmation prompt |

Also accepts the [jentic global flags](#jentic-global-flags).

#### jentic env

Manage Control Plane environments (deployment targets)

```text
jentic env [flags]
```

```text
env manages the deployment targets commands talk to — each an
Environment with a Control Plane base URL and an optional Broker URL.
Environments are referenced by Contexts (`jentic context`). Migrated
from legacy ~/.jentic profiles by `jentic migrate`.
```

Also accepts the [jentic global flags](#jentic-global-flags).

**Subcommands**

| Command | Description |
|---|---|
| [`jentic env add`](#jentic-env-add) | Add a Control Plane environment |
| [`jentic env list`](#jentic-env-list) | List configured environments |
| [`jentic env delete`](#jentic-env-delete) | Delete an environment |

##### jentic env add

Add a Control Plane environment

```text
jentic env add <name> [flags]
```

```text
Add a Control Plane environment. --broker-url is optional and is NEVER
derived from --url (Control Plane and Broker often live on different
domains). `jentic register` seeds broker_url automatically for a loopback
install; set it explicitly here for a remote broker or to fix an existing
environment (use --force to replace one of the same name). Replacing an
environment with a different Control Plane or Broker origin purges its
cached credentials and identity registrations before saving.
```

**Flags**

| Flag | Shorthand | Type | Default | Description |
|---|---|---|---|---|
| `--broker-url` |  | `string` |  | Data Plane / Broker URL (optional; used by execute and history) |
| `--ca-cert` |  | `string` |  | Path to a custom CA bundle for this environment (optional) |
| `--force` |  | `bool` | `false` | Replace an existing environment of the same name |
| `--url` |  | `string` |  | Control Plane base URL |

Also accepts the [jentic global flags](#jentic-global-flags).

**Examples**

```sh
# Local install: point both the control plane and broker at loopback (http).
jentic env add local --url http://127.0.0.1:8000 --broker-url http://127.0.0.1:8100 --force
# Remote: broker on its own host.
jentic env add prod --url https://jentic.example.com --broker-url https://broker.example.com
```

##### jentic env list

List configured environments

```text
jentic env list [flags]
```

Aliases: `ls`

Also accepts the [jentic global flags](#jentic-global-flags).

##### jentic env delete

Delete an environment

```text
jentic env delete <name> [flags]
```

**Flags**

| Flag | Shorthand | Type | Default | Description |
|---|---|---|---|---|
| `--yes` | `-y` | `bool` | `false` | Skip the confirmation prompt |

Also accepts the [jentic global flags](#jentic-global-flags).

#### jentic identity

Manage identities (agents and users) and their credentials

```text
jentic identity [flags]
```

```text
identity manages the actors commands act as. Bind an identity to an
environment through a Context (`jentic context`). API-key identities carry
a jak_* credential; agent identities register via `jentic register`.
Migrated from legacy ~/.jentic profiles by `jentic migrate`.
```

Also accepts the [jentic global flags](#jentic-global-flags).

**Subcommands**

| Command | Description |
|---|---|
| [`jentic identity add`](#jentic-identity-add) | Add an identity (optionally with a jak_\* API key credential) |
| [`jentic identity list`](#jentic-identity-list) | List configured identities |
| [`jentic identity register`](#jentic-identity-register) | Register the active identity's key with the active environment |
| [`jentic identity claim`](#jentic-identity-claim) | Claim ownership of a self-registered agent with its one-time claim token |
| [`jentic identity delete`](#jentic-identity-delete) | Delete an identity |

##### jentic identity add

Add an identity (optionally with a jak_\* API key credential)

```text
jentic identity add <name> [flags]
```

```text
add records an identity in the config. With --api-key it stores a jak_*
credential for the given --env (the successor to the old `jentic profile
add-key`). Agent identities obtain tokens later via `jentic register`.
```

**Flags**

| Flag | Shorthand | Type | Default | Description |
|---|---|---|---|---|
| `--api-key` |  | `string` |  | A jak_\* API key credential to store for --env |
| `--env` |  | `string` |  | Environment the --api-key credential is scoped to |
| `--force` |  | `bool` | `false` | Replace an existing identity of the same name |
| `--type` |  | `string` |  | Identity type: agent\|user (default agent) |

Also accepts the [jentic global flags](#jentic-global-flags).

##### jentic identity list

List configured identities

```text
jentic identity list [flags]
```

Aliases: `ls`

Also accepts the [jentic global flags](#jentic-global-flags).

##### jentic identity register

Register the active identity's key with the active environment

```text
jentic identity register [flags]
```

```text
register generates an environment-scoped Ed25519 key (if absent),
performs RFC 7591 Dynamic Client Registration, and records the returned
client_id + status. The agent is 'pending' until an operator approves it;
token exchange then succeeds automatically. This is the successor to the
legacy `jentic register` and operates on the resolved active context.
```

**Flags**

| Flag | Shorthand | Type | Default | Description |
|---|---|---|---|---|
| `--name` |  | `string` |  | Client name shown to the approving operator (default: identity name) |

Also accepts the [jentic global flags](#jentic-global-flags).

##### jentic identity claim

Claim ownership of a self-registered agent with its one-time claim token

```text
jentic identity claim <agent-id> [flags]
```

```text
claim binds a self-registered agent to you (the human) using the
single-use token shown once at `jentic register` time. Present it with
--token; an agent cannot claim itself. Requires an active human context
(`jentic context use`).
```

**Flags**

| Flag | Shorthand | Type | Default | Description |
|---|---|---|---|---|
| `--token` |  | `string` |  | The one-time claim token returned at registration (required) |

Also accepts the [jentic global flags](#jentic-global-flags).

##### jentic identity delete

Delete an identity

```text
jentic identity delete <name> [flags]
```

**Flags**

| Flag | Shorthand | Type | Default | Description |
|---|---|---|---|---|
| `--yes` | `-y` | `bool` | `false` | Skip the confirmation prompt |

Also accepts the [jentic global flags](#jentic-global-flags).

#### jentic whoami

Show who you are: identity, status, permissions, and credential bindings

```text
jentic whoami [flags]
```

```text
whoami answers with your identity as the control plane sees it — the
authenticated GET /me response, verbatim. For an agent that is the id,
status, permissions, and credential bindings with the APIs each one serves;
a user token renders its own /me variant. Check it before executing
anything new — access is decided from your bindings, never by probing
with execute.
```

Also accepts the [jentic global flags](#jentic-global-flags).

#### jentic migrate

Migrate legacy ~/.jentic profiles to the XDG context model

```text
jentic migrate [flags]
```

```text
migrate copies each legacy profile into an Environment + Identity +
Context under ~/.config/jentic, and copies key/token material (including
cached access and refresh tokens) into the XDG layout. It is idempotent and
non-destructive by default so the legacy tree remains available for recovery
or downgrade. Verify that a migrated context authenticates (for example,
`jentic whoami`) before passing --purge-legacy to delete the legacy tree.
```

**Flags**

| Flag | Shorthand | Type | Default | Description |
|---|---|---|---|---|
| `--purge-legacy` |  | `bool` | `false` | Delete the legacy ~/.jentic profile tree (run only after verifying migrated authentication) |
| `--yes` | `-y` | `bool` | `false` | Proceed without interactive confirmation |

Also accepts the [jentic global flags](#jentic-global-flags).

### APIs

#### jentic catalog

Browse, search, and import APIs from the public catalog

```text
jentic catalog [flags]
```

```text
catalog explores the Jentic public API catalog and imports specs into
this deployment's local registry. Run bare on a terminal to open an
interactive browser (search, preview operations, import in place); the
subcommands (list/search/show/import/outdated/refresh) are script-friendly.
Requires a registered agent (run `jentic register` first).
```

Also accepts the [jentic global flags](#jentic-global-flags).

**Subcommands**

| Command | Description |
|---|---|
| [`jentic catalog list`](#jentic-catalog-list) | List catalog entries |
| [`jentic catalog search`](#jentic-catalog-search) | Search the catalog by keyword |
| [`jentic catalog show`](#jentic-catalog-show) | Show a catalog entry and preview its operations |
| [`jentic catalog import`](#jentic-catalog-import) | Import a catalog entry into the local registry (auto-promotes to live) |
| [`jentic catalog outdated`](#jentic-catalog-outdated) | List registered entries with an upstream update available |
| [`jentic catalog refresh`](#jentic-catalog-refresh) | Refresh the catalog manifest from upstream (requires org:admin) |

##### jentic catalog list

List catalog entries

```text
jentic catalog list [flags]
```

**Flags**

| Flag | Shorthand | Type | Default | Description |
|---|---|---|---|---|
| `--all` |  | `bool` | `false` | follow pagination and list every matching entry |
| `--json` |  | `bool` | `false` | emit JSON instead of formatted output |
| `--limit` |  | `int` | `50` | page size (1-200) |
| `--registered` |  | `bool` | `false` | only entries already imported locally |
| `--unregistered` |  | `bool` | `false` | only entries not yet imported |

Also accepts the [jentic global flags](#jentic-global-flags).

##### jentic catalog search

Search the catalog by keyword

```text
jentic catalog search <query> [flags]
```

**Flags**

| Flag | Shorthand | Type | Default | Description |
|---|---|---|---|---|
| `--all` |  | `bool` | `false` | follow pagination and list every matching entry |
| `--json` |  | `bool` | `false` | emit JSON instead of formatted output |
| `--limit` |  | `int` | `50` | page size (1-200) |
| `--registered` |  | `bool` | `false` | only entries already imported locally |
| `--unregistered` |  | `bool` | `false` | only entries not yet imported |

Also accepts the [jentic global flags](#jentic-global-flags).

##### jentic catalog show

Show a catalog entry and preview its operations

```text
jentic catalog show <api_id> [flags]
```

**Flags**

| Flag | Shorthand | Type | Default | Description |
|---|---|---|---|---|
| `--json` |  | `bool` | `false` | emit JSON instead of formatted output |
| `--limit` |  | `int` | `0` | max operations to preview (default 200) |
| `--tag` |  | `string` |  | only preview operations with this tag |

Also accepts the [jentic global flags](#jentic-global-flags).

##### jentic catalog import

Import a catalog entry into the local registry (auto-promotes to live)

```text
jentic catalog import <api_id> [flags]
```

**Flags**

| Flag | Shorthand | Type | Default | Description |
|---|---|---|---|---|
| `--json` |  | `bool` | `false` | emit JSON instead of formatted output |
| `--no-promote` |  | `bool` | `false` | leave imported revisions as draft (do not promote to live) |
| `--no-wait` |  | `bool` | `false` | enqueue the import and return the job id without waiting |
| `--timeout` |  | `duration` | `2m0s` | how long to wait for the import job |

Also accepts the [jentic global flags](#jentic-global-flags).

##### jentic catalog outdated

List registered entries with an upstream update available

```text
jentic catalog outdated [flags]
```

```text
outdated lists locally-registered catalog entries whose upstream spec has
a notified update the local revision hasn't adopted yet. Review the change,
then re-import to adopt it. By default, entries an operator has snoozed/muted
are hidden; pass --include-snoozed to list them too.
```

**Flags**

| Flag | Shorthand | Type | Default | Description |
|---|---|---|---|---|
| `--include-snoozed` |  | `bool` | `false` | also list entries whose update notification has been snoozed/muted |
| `--json` |  | `bool` | `false` | emit JSON instead of formatted output |
| `--limit` |  | `int` | `50` | page size (1-200) |

Also accepts the [jentic global flags](#jentic-global-flags).

##### jentic catalog refresh

Refresh the catalog manifest from upstream (requires org:admin)

```text
jentic catalog refresh [flags]
```

**Flags**

| Flag | Shorthand | Type | Default | Description |
|---|---|---|---|---|
| `--json` |  | `bool` | `false` | emit JSON instead of formatted output |

Also accepts the [jentic global flags](#jentic-global-flags).

#### jentic apis

Browse and manage APIs in the local registry

```text
jentic apis [flags]
```

```text
apis inspects and manages the APIs imported into this deployment's local
registry — the other half of `jentic catalog import`. Run bare on a
terminal to open an interactive browser (list, preview operations, manage
revisions); the subcommands (list/show/revisions/operations/inspect/
promote/archive/rm/spec) are script-friendly.
Requires a registered agent (run `jentic register` first).
```

Also accepts the [jentic global flags](#jentic-global-flags).

**Subcommands**

| Command | Description |
|---|---|
| [`jentic apis list`](#jentic-apis-list) | List locally registered APIs |
| [`jentic apis show`](#jentic-apis-show) | Show an API and preview its operations |
| [`jentic apis revisions`](#jentic-apis-revisions) | List the revisions of an API |
| [`jentic apis operations`](#jentic-apis-operations) | List the operations of an API (current revision by default) |
| [`jentic apis inspect`](#jentic-apis-inspect) | Inspect an operation's structural detail |
| [`jentic apis auth`](#jentic-apis-auth) | Show how a connection to an API would be made, and the scopes you can ask for |
| [`jentic apis promote`](#jentic-apis-promote) | Promote a draft revision to live |
| [`jentic apis archive`](#jentic-apis-archive) | Archive a draft revision |
| [`jentic apis rm`](#jentic-apis-rm) | Delete an API (or an archived revision) |
| [`jentic apis spec`](#jentic-apis-spec) | Download the OpenAPI spec for an API |
| [`jentic apis import`](#jentic-apis-import) | Import an OpenAPI document into the catalog |

##### jentic apis list

List locally registered APIs

```text
jentic apis list [flags]
```

Aliases: `ls`

**Flags**

| Flag | Shorthand | Type | Default | Description |
|---|---|---|---|---|
| `--all` |  | `bool` | `false` | follow pagination and list every matching API |
| `--json` |  | `bool` | `false` | emit JSON instead of formatted output |
| `--limit` |  | `int` | `50` | page size (1-200) |
| `--vendor` |  | `string` |  | only APIs from this vendor |

Also accepts the [jentic global flags](#jentic-global-flags).

##### jentic apis show

Show an API and preview its operations

```text
jentic apis show <vendor/name/version> [flags]
```

**Flags**

| Flag | Shorthand | Type | Default | Description |
|---|---|---|---|---|
| `--json` |  | `bool` | `false` | emit JSON instead of formatted output |
| `--limit` |  | `int` | `0` | max operations to preview (default 50) |

Also accepts the [jentic global flags](#jentic-global-flags).

##### jentic apis revisions

List the revisions of an API

```text
jentic apis revisions <vendor/name/version> [flags]
```

Aliases: `revs`

```text
revisions lists an API's revisions, newest first, with each revision's
state and whether it is the current (live) one.
A listed revision_id pins broker calls to that revision (e.g. to try a
draft before promoting it): send the header
`Jentic-Revision: <vendor>:<name>:<version>=<revision_id>`.
```

**Flags**

| Flag | Shorthand | Type | Default | Description |
|---|---|---|---|---|
| `--all` |  | `bool` | `false` | follow pagination and list every revision |
| `--json` |  | `bool` | `false` | emit JSON instead of formatted output |
| `--limit` |  | `int` | `50` | page size (1-200) |
| `--state` |  | `stringSlice` | `[]` | filter by state (draft, published, archived); repeatable |

Also accepts the [jentic global flags](#jentic-global-flags).

##### jentic apis operations

List the operations of an API (current revision by default)

```text
jentic apis operations <vendor/name/version> [flags]
```

Aliases: `ops`

**Flags**

| Flag | Shorthand | Type | Default | Description |
|---|---|---|---|---|
| `--all` |  | `bool` | `false` | follow pagination and list every operation |
| `--json` |  | `bool` | `false` | emit JSON instead of formatted output |
| `--limit` |  | `int` | `50` | page size (1-200) |
| `--revision` |  | `string` |  | list operations for a specific revision id |

Also accepts the [jentic global flags](#jentic-global-flags).

##### jentic apis inspect

Inspect an operation's structural detail

```text
jentic apis inspect <METHOD:url> [flags]
```

**Flags**

| Flag | Shorthand | Type | Default | Description |
|---|---|---|---|---|
| `--format` |  | `string` |  | output format: markdown, json, or openapi (default: json in agent mode or when piped, else markdown) |
| `--json` |  | `bool` | `false` | emit JSON (shorthand for --format json) |
| `--revision` |  | `string` |  | pin to a specific revision id |

Also accepts the [jentic global flags](#jentic-global-flags).

##### jentic apis auth

Show how a connection to an API would be made, and the scopes you can ask for

```text
jentic apis auth <vendor/name/version> [flags]
```

```text
auth answers with each security scheme the API declares, how a connection
would collect it (an OAuth flow, a credential a human enters, or waiting for
an OAuth app), and for OAuth the scopes a request may name (requestable).
Use it before `jentic connect --api` or `jentic access edit --add-scopes`.
```

**Flags**

| Flag | Shorthand | Type | Default | Description |
|---|---|---|---|---|
| `--registration-id` |  | `string` |  | pin a shared OAuth app, as `jentic connect --registration-id` would |

Also accepts the [jentic global flags](#jentic-global-flags).

##### jentic apis promote

Promote a draft revision to live

```text
jentic apis promote <vendor/name/version> <revision_id> [flags]
```

**Flags**

| Flag | Shorthand | Type | Default | Description |
|---|---|---|---|---|
| `--json` |  | `bool` | `false` | emit JSON instead of formatted output |

Also accepts the [jentic global flags](#jentic-global-flags).

##### jentic apis archive

Archive a draft revision

```text
jentic apis archive <vendor/name/version> <revision_id> [flags]
```

**Flags**

| Flag | Shorthand | Type | Default | Description |
|---|---|---|---|---|
| `--json` |  | `bool` | `false` | emit JSON instead of formatted output |

Also accepts the [jentic global flags](#jentic-global-flags).

##### jentic apis rm

Delete an API (or an archived revision)

```text
jentic apis rm <vendor/name/version> [revision_id] [flags]
```

Aliases: `delete`

**Flags**

| Flag | Shorthand | Type | Default | Description |
|---|---|---|---|---|
| `--json` |  | `bool` | `false` | emit JSON instead of formatted output |
| `--yes` | `-y` | `bool` | `false` | skip the confirmation prompt |

Also accepts the [jentic global flags](#jentic-global-flags).

##### jentic apis spec

Download the OpenAPI spec for an API

```text
jentic apis spec <vendor/name/version> [flags]
```

**Flags**

| Flag | Shorthand | Type | Default | Description |
|---|---|---|---|---|
| `--out` |  | `string` |  | write to this file instead of stdout |
| `--revision` |  | `string` |  | download a specific revision id (default: current/live) |
| `--yaml` |  | `bool` | `false` | request YAML instead of JSON |

Also accepts the [jentic global flags](#jentic-global-flags).

##### jentic apis import

Import an OpenAPI document into the catalog

```text
jentic apis import <file|url> [flags]
```

```text
import submits an OpenAPI spec to the catalog. The argument is either a
local file path (read and sent inline) or an http(s) URL (sent as a
fetchable source). Import is asynchronous; the command prints the job id.
```

**Flags**

| Flag | Shorthand | Type | Default | Description |
|---|---|---|---|---|
| `--dry-run` |  | `bool` | `false` | Validate and print the request that WOULD be sent, without sending it |
| `--export-plan` |  | `bool` | `false` | Emit a machine-readable execution plan (implies --dry-run) |
| `--name` |  | `string` |  | Override the API name |
| `--submitted-by` |  | `string` |  | Attribute this import to another principal (org:admin only; otherwise the server attributes it to the caller) |
| `--vendor` |  | `string` |  | Override the vendor for the imported API |
| `--version` |  | `string` |  | Override the API version |

Also accepts the [jentic global flags](#jentic-global-flags).

#### jentic endpoints

Browse the API endpoint + permission reference

```text
jentic endpoints [flags]
```

```text
endpoints prints every control-plane API endpoint grouped by its typical
caller and the permission(s) it requires. The grouping is an advisory hint —
the permission is the real gate. It reads the server's public endpoint
reference (/reference/endpoints.json, no token needed) — the same join
published in docs/reference/endpoints.md.
Filter with --permission or --actor, or use --json for a machine-readable dump.
```

**Flags**

| Flag | Shorthand | Type | Default | Description |
|---|---|---|---|---|
| `--actor` |  | `string` |  | only endpoints callable by this actor type (user, agent) |
| `--json` |  | `bool` | `false` | emit JSON instead of formatted output |
| `--permission` |  | `string` |  | only endpoints requiring this permission |

Also accepts the [jentic global flags](#jentic-global-flags).

#### jentic credentials

List and inspect credentials (secrets are server-masked)

```text
jentic credentials [flags]
```

Aliases: `creds`

Also accepts the [jentic global flags](#jentic-global-flags).

**Subcommands**

| Command | Description |
|---|---|
| [`jentic credentials list`](#jentic-credentials-list) | List credentials |
| [`jentic credentials show`](#jentic-credentials-show) | Show a credential (secret-masked) |

##### jentic credentials list

List credentials

```text
jentic credentials list [flags]
```

**Flags**

| Flag | Shorthand | Type | Default | Description |
|---|---|---|---|---|
| `--cursor` |  | `string` |  | Fetch the page at this opaque cursor |
| `--limit` |  | `int` | `0` | Fetch a single page of at most N credentials |
| `--vendor` |  | `string` |  | Filter by vendor |

Also accepts the [jentic global flags](#jentic-global-flags).

##### jentic credentials show

Show a credential (secret-masked)

```text
jentic credentials show <credential_id> [flags]
```

Also accepts the [jentic global flags](#jentic-global-flags).

#### jentic connect

Start connecting a credential for a vendor or a registry API (a human approves it)

```text
jentic connect [<vendor>] [--api <vendor/name/version>] [flags]
```

```text
connect starts a connect session and prints the approval_url. Name a verified
vendor from the deployment's vendor registry (e.g. `jentic connect github`), or
any registry API with --api <vendor/name/version> (where the deployment takes
API connect requests): its spec decides the credential — pass --auth-type when
it declares several schemes — and --rules proposes the permission rules you need.
Relay the URL to your human operator: they approve the connection (or enter the
API key) in the browser — this command never completes an approval by itself.
Once they confirm, check your new binding with `jentic whoami` and retry the
call that was blocked. --wait polls the session until it connects or ends
(rejected, expired, or cancelled), printing the approval_url and heartbeats
on stderr and a single JSON result on stdout. A session a human finishes by
entering a credential can take hours: --wait returns at once for it unless
--timeout is set — relay the URL, end your turn, and retry later.
When several shared OAuth apps serve the vendor, the error lists them
(details.candidates): ask your user which one to use and re-run with
--registration <registration_id>. Choosing the app is your user's call.
```

**Flags**

| Flag | Shorthand | Type | Default | Description |
|---|---|---|---|---|
| `--api` |  | `string` |  | registry API to connect a credential for, as vendor/name/version (instead of a vendor key) |
| `--auth-type` |  | `string` |  | with --api: the declared scheme name or kind to use (api_key, bearer, basic, oauth2) when the spec declares several |
| `--reason` |  | `string` |  | why you need this connection (shown to the approver; max 1024 chars) |
| `--registration` |  | `string` |  | registration_id of the shared OAuth app to connect through, when several serve the vendor (your user picks it) |
| `--rules` |  | `string` |  | permission rules you ask for on the binding, as a JSON list (the approver reviews them; a denial's suggested_rules fit as-is) |
| `--scopes` |  | `stringSlice` | `[]` | vendor scope names to request (the human approver confirms the final set) |
| `--timeout` |  | `duration` | `5m0s` | how long --wait polls before exiting 3 (TIMEOUT_PENDING) |
| `--wait` |  | `bool` | `false` | poll the session until it connects or ends (rejected, expired, or cancelled) |

Also accepts the [jentic global flags](#jentic-global-flags).

#### jentic access

Show and change what your credential bindings let you do

```text
jentic access [flags]
```

Also accepts the [jentic global flags](#jentic-global-flags).

**Subcommands**

| Command | Description |
|---|---|
| [`jentic access show`](#jentic-access-show) | Show your access through one credential: rules, rule set, scopes, suspension |
| [`jentic access edit`](#jentic-access-edit) | Ask for a change to your access through one credential (a human approves it) |
| [`jentic access amend`](#jentic-access-amend) | Change an open request you made, before it is approved |

##### jentic access show

Show your access through one credential: rules, rule set, scopes, suspension

```text
jentic access show <credential_id> [flags]
```

```text
show answers with what your binding to the credential grants: its
permission rules with their ids (data), the shared rule set it points at
(whose rules apply instead of data), the credential's granted OAuth scopes,
and whether the binding is suspended. Credential ids come from whoami.
```

Also accepts the [jentic global flags](#jentic-global-flags).

##### jentic access edit

Ask for a change to your access through one credential (a human approves it)

```text
jentic access edit <credential_id> [flags]
```

```text
edit asks your human user to change what your binding to the credential lets you
do: rules to add (--add, optionally --before a rule id), remove (--remove) or
replace (--set), OAuth scopes to add, remove or replace (--add-scopes,
--remove-scopes, --set-scopes), or resuming a suspended binding
(--resume). Read your access first with `jentic access show`: rule ids come from
there, and a denial's suggested_rules fit --add as-is. Put every change you need
in one request. It prints the approval_url to relay; nothing changes until they
approve. If a request for this binding is already open, amend that one with
`jentic access amend <session_id>` instead. --wait polls until it is applied or ends.
```

**Flags**

| Flag | Shorthand | Type | Default | Description |
|---|---|---|---|---|
| `--add` |  | `string` |  | rules to add, as a JSON list of rules or {"rule": …, "before": "&lt;rule id&gt;"} (a denial's suggested_rules fit as-is) |
| `--add-scopes` |  | `stringSlice` | `[]` | OAuth scopes to add (the approver signs in to the vendor again) |
| `--before` |  | `string` |  | rule id to insert the added rules before (first match wins); appended when omitted |
| `--reason` |  | `string` |  | why you need the change (shown to the approver; max 1024 chars) |
| `--remove` |  | `stringSlice` | `[]` | rule ids to remove (from `jentic access show`) |
| `--remove-scopes` |  | `stringSlice` | `[]` | OAuth scopes to remove |
| `--resume` |  | `bool` | `false` | switch a suspended binding back on |
| `--scope-comment` |  | `stringToString` | `[]` | why you ask for a scope change, as scope=reason (repeatable; shown to the approver) |
| `--set` |  | `string` |  | the full rule list to replace the current one with, as JSON (not combined with --add/--remove) |
| `--set-scopes` |  | `stringSlice` | `[]` | the full OAuth scope list to replace the grant's with (not combined with --add-scopes/--remove-scopes) |
| `--timeout` |  | `duration` | `5m0s` | how long --wait polls before exiting 3 (TIMEOUT_PENDING) |
| `--wait` |  | `bool` | `false` | poll the request until it is applied or ends |

Also accepts the [jentic global flags](#jentic-global-flags).

##### jentic access amend

Change an open request you made, before it is approved

```text
jentic access amend <session_id> [flags]
```

```text
amend changes what an open request asks for; the reviewer sees the new version and
approves it once. The fields you pass replace those parts of the ask and the rest
stay. For a change to your access (`jentic access edit`) pass --add/--remove/--set,
--add-scopes/--remove-scopes/--set-scopes, --resume or --reason; for a new
connection (`jentic connect`) pass --requested-rules, --requested-scopes or
--reason. A request that was already approved or ended cannot be amended: ask
again instead.
```

**Flags**

| Flag | Shorthand | Type | Default | Description |
|---|---|---|---|---|
| `--add` |  | `string` |  | rules to add, as a JSON list of rules or {"rule": …, "before": "&lt;rule id&gt;"} (a denial's suggested_rules fit as-is) |
| `--add-scopes` |  | `stringSlice` | `[]` | OAuth scopes to add (the approver signs in to the vendor again) |
| `--before` |  | `string` |  | rule id to insert the added rules before (first match wins); appended when omitted |
| `--reason` |  | `string` |  | why you need the change (shown to the approver; max 1024 chars) |
| `--remove` |  | `stringSlice` | `[]` | rule ids to remove (from `jentic access show`) |
| `--remove-scopes` |  | `stringSlice` | `[]` | OAuth scopes to remove |
| `--requested-rules` |  | `string` |  | for a new connection: the rules to ask for, as a JSON list (replaces the ask) |
| `--requested-scopes` |  | `stringSlice` | `[]` | for a new connection: the scopes to ask for (replaces the ask) |
| `--resume` |  | `bool` | `false` | switch a suspended binding back on |
| `--scope-comment` |  | `stringToString` | `[]` | why you ask for a scope change, as scope=reason (repeatable; shown to the approver) |
| `--set` |  | `string` |  | the full rule list to replace the current one with, as JSON (not combined with --add/--remove) |
| `--set-scopes` |  | `stringSlice` | `[]` | the full OAuth scope list to replace the grant's with (not combined with --add-scopes/--remove-scopes) |

Also accepts the [jentic global flags](#jentic-global-flags).

### Find and run operations

#### jentic search

Search for operations by natural-language query

```text
jentic search <query> [flags]
```

```text
search finds API operations whose descriptions, names, or paths match a
query. Results are ranked by lexical (full-text) relevance. The
query can also be passed via -q for piping.

Each hit carries a `target` to pass verbatim to inspect/execute:
its METHOD:url pair, or its operation_id when the url is host-relative
(the spec declares no absolute server, e.g. /pets). Such an
operation_id target is inspect-only — execute refuses it.

Output defaults to JSON when stdout is not a TTY (agent-friendly);
use --json to force JSON on a terminal.
```

**Flags**

| Flag | Shorthand | Type | Default | Description |
|---|---|---|---|---|
| `--all` |  | `bool` | `false` | follow pagination and return all results |
| `--api` |  | `stringSlice` | `[]` | restrict to these APIs (vendor\[/name\[/version\]\], as shown in search hits and `jentic apis list`; repeatable) |
| `--cursor` |  | `string` |  | pagination cursor from a previous response |
| `--json` |  | `bool` | `false` | force JSON output (default when stdout is not a TTY) |
| `--limit` |  | `int` | `10` | max results per page (1-100) |
| `--query` | `-q` | `string` |  | search query (alternative to positional arg) |

Also accepts the [jentic global flags](#jentic-global-flags).

**Examples**

```sh
jentic search "list users"
jentic search -q "create issue" --api github-com/api-github-com --limit 5
jentic search "list pets" --all --json | jq -r '.data[].target'
```

#### jentic inspect

Inspect an operation's contract (schema, parameters, examples)

```text
jentic inspect <METHOD:url> [flags]
```

```text
inspect resolves an operation to its full structural detail: HTTP
method, URL, parameters, request/response schemas, and examples. The
target is a discovered operation's METHOD:url (the same form
`jentic search` prints, e.g. GET:https://rest.coincap.io/v3/markets).
An opaque registry operation_id also still resolves, for compatibility
— prefer the METHOD:url form. The output format is negotiated with the
server: JSON for machine consumption, Markdown for human reading.

The space form (`GET <url>`) is also accepted, but the colon form is
canonical (it needs no shell quoting).

Default format: JSON when stdout is not a TTY, Markdown when it is.
```

**Flags**

| Flag | Shorthand | Type | Default | Description |
|---|---|---|---|---|
| `--format` |  | `string` |  | output format: json, markdown, openapi (default: json for non-TTY, markdown for TTY) |
| `--json` |  | `bool` | `false` | shorthand for --format json |
| `--revision` |  | `string` |  | pin to a specific revision ID |

Also accepts the [jentic global flags](#jentic-global-flags).

**Examples**

```sh
jentic inspect GET:https://rest.coincap.io/v3/markets --format json | jq .method
jentic inspect POST:https://api.example.com/v1/users --format markdown
jentic search "list users" --json | jq -r '.data[0].target' | xargs jentic inspect
```

#### jentic execute

Execute an operation through the Jentic broker

```text
jentic execute <METHOD:url | METHOD:/path> [flags]
```

```text
execute sends an HTTP request through the Jentic broker. The broker
authenticates the caller with their agent token and injects the stored
upstream credential, so the agent token is never sent to the upstream
API directly. The target is the operation's method + URL:

  1. METHOD:url — a discovered operation's full URL, the same form
     `jentic search`/`jentic inspect` print (e.g.
     GET:https://rest.coincap.io/v3/markets). Resolved via inspect, then
     routed through the broker. The space form (`GET <url>`) is also
     accepted, but the colon form is canonical (it needs no shell quoting).
  2. METHOD:/path — a broker-relative path sent to --broker-host
     verbatim (e.g. GET:/v1/pets); the caller supplies the broker path.

(An opaque registry operation_id also still resolves, for compatibility
with older scripts — prefer the METHOD:url form. A search hit whose
target is an operation_id has a host-relative url — its spec declares no
absolute server — so it is inspect-only: execute refuses it with
RESOLVE_FAILED, since there is no upstream host to proxy to.)

Path parameters, query parameters, headers, and a request body can be
supplied via flags.

Multipart uploads: --form key=value adds a text field and --form-file
key=@path adds a file part (both repeatable; a repeated key sends several
parts under one name). A file part may carry curl-style suffixes:
key=@path;type=image/png forces the part Content-Type (otherwise it is
inferred from the extension, falling back to application/octet-stream)
and ;filename=name sets the uploaded file name (default: the path's base
name). Suffixes are parsed from the right, so any other ';' stays part of
the path; quote a path that itself ends in ;type=… or ;filename=… as
key=@"path" (a leading " counts as quoting only when a closing " is
followed by the end or a ';'). Option values cannot contain ';'. Field
names and file names must not contain CR/LF. A file part must be a
regular file: @- is refused, and so are non-regular files such as
pipes, FIFOs and devices (including /dev/stdin or /dev/fd/N when stdin
is a pipe or terminal; stdin redirected from a regular file is accepted).
The assembled body is checked against the broker's default 50 MiB
multipart/form-data limit before anything is sent (a broker cap raised
above 50 MiB is not usable from the CLI); an oversize body is refused
with MISSING_ARGUMENT and details.reason=body_too_large (plus
details.total_bytes/limit_bytes). --dry-run prints a parts summary
(names, kinds, filenames, content types, per-part byte lengths, total
bytes) — never field values or file contents.

When the broker denies the call (e.g. you have no credential binding
for the API, or no credential is provisioned), it returns an
agent_directive describing how to recover. execute surfaces that
directive on stderr and exits 2 so a script can branch on the denial.

When a permission rule requires approval, the broker holds the call
for a human instead of running it and answers 202 with the held
execution and a review_url. execute prints that envelope, shows the
review link on stderr, and exits 3 (TIMEOUT_PENDING). Show the user the
link, then collect the outcome with `jentic executions poll
<held_execution_id>` — never re-send the call. With --wait, execute
follows the held call itself (up to --timeout: default 30s, max 5m)
and then prints the result exactly as if the call had run at once, or
the denial/expiry (exit 2). Every held outcome names the
held_execution_id.

Exit codes:
  0 — broker returned a non-denial HTTP response (incl. 2xx and upstream errors)
  1 — local/transport failure (DNS, TLS, timeout, connection refused)
  2 — denied by the broker (carries an agent_directive) or resolve failure
      (inspect error, e.g. an unknown operation); also a held call a
      reviewer denied or that expired undecided
  3 — held for human approval and not yet decided (TIMEOUT_PENDING)

Broker target: resolved as built-in default (https://127.0.0.1:8100) <
the active environment's broker_url < --broker-scheme/--broker-host. A
local install serves the broker over plain HTTP, so `jentic register`
seeds broker_url=http://127.0.0.1:8100 for a loopback environment. A TLS
error like "server gave HTTP response to HTTPS client" means the target
resolved to https against a local http broker — set the environment's
broker_url (`jentic env add <env> --broker-url http://127.0.0.1:8100
--force`) or override per call with --broker-scheme http. For a REMOTE
control plane you MUST set broker_url (or JENTIC_BROKER_URL); execute
refuses with RESOLVE_FAILED rather than dialing the local default.
```

**Flags**

| Flag | Shorthand | Type | Default | Description |
|---|---|---|---|---|
| `--broker-host` |  | `string` | `127.0.0.1:8100` | broker target host as host\[:port\] (no scheme; use --broker-scheme) |
| `--broker-scheme` |  | `string` | `https` | broker target scheme (http or https) |
| `--data` | `-d` | `string` |  | request body JSON (use - for stdin) |
| `--data-file` |  | `string` |  | read request body from this file |
| `--dry-run` |  | `bool` | `false` | Validate and print the request that WOULD be sent, without sending it |
| `--export-plan` |  | `bool` | `false` | Emit a machine-readable execution plan (implies --dry-run) |
| `--form` |  | `stringArray` | `[]` | multipart/form-data text field as key=value (repeatable; cannot be combined with --data/--data-file; a piped stdin body is ignored) |
| `--form-file` |  | `stringArray` | `[]` | multipart/form-data file part as key=@path\[;type=&lt;media-type&gt;\]\[;filename=&lt;name&gt;\] (repeatable; cannot be combined with --data/--data-file; a piped stdin body is ignored) |
| `--header` |  | `stringArray` | `[]` | extra header as key=value (repeatable) |
| `--idempotency-key` |  | `string` |  | caller-supplied Idempotency-Key so a retried POST/PUT is de-duplicated by the broker |
| `--json` |  | `bool` | `false` | force JSON envelope output |
| `--path` |  | `stringArray` | `[]` | path parameter as key=value (repeatable) |
| `--query` |  | `stringArray` | `[]` | query parameter as key=value (repeatable) |
| `--raw` |  | `bool` | `false` | stream response body directly to stdout |
| `--revision` |  | `string` |  | pin to a specific revision ID for inspect |
| `--timeout` |  | `duration` | `30s` | how long --wait follows a held call before exiting 3 (TIMEOUT_PENDING): a duration (30s, 1m) or seconds (30); max 5m, 0 checks once |
| `--wait` |  | `bool` | `false` | when the call is held for human approval, wait for the decision and print the result |

Also accepts the [jentic global flags](#jentic-global-flags).

**Examples**

```sh
jentic execute GET:https://rest.coincap.io/v3/markets --json
jentic execute GET:https://api.example.com/v1/pets --query limit=10 --json
jentic execute GET:/v1/pets/{petId} --path petId=123 --raw
echo '{"name":"Bob"}' | jentic execute POST:/v1/users --json
# A call a rule holds for approval: follow it for up to 2 minutes:
jentic execute POST:https://api.example.com/v1/orders -d '{"n":1}' --wait --timeout 2m --json
jentic execute POST:https://api.example.com/v1/pics --form demo=true --form-file images=@face.jpg --json
jentic execute POST:https://api.example.com/v1/docs --form-file 'doc=@scan;type=application/pdf;filename=scan.pdf' --dry-run
# Local broker over http, one-off (usually unnecessary — register seeds broker_url):
jentic execute GET:https://api.example.com/v1/pets --broker-scheme http --broker-host 127.0.0.1:8100
```

#### jentic executions

Follow your held executions (calls awaiting human approval)

```text
jentic executions [flags]
```

```text
executions follows your held executions: calls a permission rule held for
a human to approve or deny. `jentic execute` prints a held call's
held_execution_id; `jentic executions poll <held_execution_id>` collects
its outcome.
```

Also accepts the [jentic global flags](#jentic-global-flags).

**Subcommands**

| Command | Description |
|---|---|
| [`jentic executions poll`](#jentic-executions-poll) | Collect a held execution's outcome, waiting up to --timeout |

##### jentic executions poll

Collect a held execution's outcome, waiting up to --timeout

```text
jentic executions poll <held_execution_id> [flags]
```

```text
poll reads a held execution — a call the broker held for human
approval — and, while it has not finished, re-reads it for up to
--timeout (default 30s, max 5m; 0 checks once). A finished call prints
exactly as `jentic execute` prints a call that ran at once: the upstream
response (exit 0, even an upstream 4xx/5xx), or the denial when a
reviewer denied the call or it expired undecided (exit 2). A call still
held prints its held_execution_id, review_url and this command again
(exit 3): show the user the link, end your turn, and poll once more when
they reply. It never re-sends the call.

--timeout takes a duration (30s, 1m) or a number of seconds (30).

Exit codes:
  0 — the call ran; the upstream response is the data
  1 — local/transport failure, or the run got no upstream answer
  2 — the call never ran (denied, expired, withdrawn, refused at run
      time), an unknown held execution id, or a result past retention
  3 — still held, queued or running when --timeout lapsed (TIMEOUT_PENDING)
```

**Flags**

| Flag | Shorthand | Type | Default | Description |
|---|---|---|---|---|
| `--json` |  | `bool` | `false` | force JSON envelope output |
| `--raw` |  | `bool` | `false` | stream the response body directly to stdout |
| `--timeout` |  | `duration` | `30s` | how long to follow the held call before exiting 3 (TIMEOUT_PENDING): a duration (30s, 1m) or seconds (30); max 5m, 0 checks once |

Also accepts the [jentic global flags](#jentic-global-flags).

**Examples**

```sh
jentic executions poll held_2mXk... --json
jentic executions poll held_2mXk... --timeout 2m
jentic executions poll held_2mXk... --timeout 0
```

#### jentic history

Query and export execution history

```text
jentic history [flags]
```

```text
history reads the control plane's execution records. `export` walks a
trace's executions (cursor-paginated) and emits a versioned JSON document an
agent can template a workflow from.
```

Also accepts the [jentic global flags](#jentic-global-flags).

**Subcommands**

| Command | Description |
|---|---|
| [`jentic history export`](#jentic-history-export) | Export the execution history of a trace |

##### jentic history export

Export the execution history of a trace

```text
jentic history export [flags]
```

```text
export walks every execution for --trace (following cursor pagination to
completion) and renders a versioned export document. Failed executions
(HTTP >= 400) are excluded unless --include-failures is set. Use --limit/
--cursor to fetch a single page instead of the full walk.
```

**Flags**

| Flag | Shorthand | Type | Default | Description |
|---|---|---|---|---|
| `--api` |  | `string` |  | Filter by API id |
| `--cursor` |  | `string` |  | Fetch the page at this opaque cursor instead of the full walk |
| `--from` |  | `string` |  | Only executions at/after this RFC3339 time |
| `--include-failures` |  | `bool` | `false` | Include failed executions (HTTP &gt;= 400) |
| `--limit` |  | `int` | `0` | Fetch a single page of at most N records instead of the full walk |
| `--output` | `-o` | `string` |  | Write the export to this file instead of stdout |
| `--to` |  | `string` |  | Only executions at/before this RFC3339 time |
| `--trace` |  | `string` |  | Trace ID to export (required) |

Also accepts the [jentic global flags](#jentic-global-flags).

#### jentic events

Observe control-plane events

```text
jentic events [flags]
```

Also accepts the [jentic global flags](#jentic-global-flags).

**Subcommands**

| Command | Description |
|---|---|
| [`jentic events watch`](#jentic-events-watch) | Tail the control-plane event stream as NDJSON |

##### jentic events watch

Tail the control-plane event stream as NDJSON

```text
jentic events watch [flags]
```

```text
watch connects to the control plane's Server-Sent Events stream and emits
one JSON event per line (NDJSON) until interrupted. Use --last-event-id to
resume after a previously seen event (the stream also advertises the id of
each event so a wrapper can persist it). Ctrl-C stops cleanly.
```

**Flags**

| Flag | Shorthand | Type | Default | Description |
|---|---|---|---|---|
| `--last-event-id` |  | `string` |  | Resume after this event id |
| `--trace` |  | `string` |  | Only events for this trace id |
| `--type` |  | `stringArray` | `[]` | Only events of these types (repeatable) |

Also accepts the [jentic global flags](#jentic-global-flags).

#### jentic api

Authenticated passthrough to the control plane

```text
jentic api <METHOD> <PATH> [flags]
```

```text
api sends an arbitrary request to the control plane, reusing the same
auth, session, and transport as every other command. The PATH must match a
route in the CLI's embedded OpenAPI spec (use --live to allow routes the
connected server serves that this binary predates). Discover routes with
`jentic api ops` and their contract with `jentic api describe`.

Request body (mirrors `jentic execute`): -d/--data inline JSON, -d - for
stdin, --data-file <path>, or auto-stdin when a body is piped/redirected.

By default any transport-successful response (2xx or 4xx/5xx) exits 0 and
the body is emitted as-is; --fail-on-error maps non-2xx to exit 1.
```

**Flags**

| Flag | Shorthand | Type | Default | Description |
|---|---|---|---|---|
| `--data` | `-d` | `string` |  | request body JSON (use - for stdin) |
| `--data-file` |  | `string` |  | read request body from this file |
| `--fail-on-error` |  | `bool` | `false` | exit 1 on a non-2xx response |
| `--header` |  | `stringArray` | `[]` | extra header as key=value (repeatable) |
| `--live` |  | `bool` | `false` | fetch the server's /openapi.json instead of the embedded spec |
| `--query` |  | `stringArray` | `[]` | query parameter as key=value (repeatable) |
| `--raw` |  | `bool` | `false` | skip the spec path allowlist (loopback hosts only, for dev) |

Also accepts the [jentic global flags](#jentic-global-flags).

**Examples**

```sh
jentic api GET /credentials
jentic api POST /agents -d '{"name":"clarity"}'
jentic api POST /apis < api.json
jentic api GET "/apis?limit=10"
```

**Subcommands**

| Command | Description |
|---|---|
| [`jentic api ops`](#jentic-api-ops) | List control-plane operations (METHOD, path, id, summary) |
| [`jentic api describe`](#jentic-api-describe) | Describe an operation's params and request/response schema |

##### jentic api ops

List control-plane operations (METHOD, path, id, summary)

```text
jentic api ops [flags]
```

**Flags**

| Flag | Shorthand | Type | Default | Description |
|---|---|---|---|---|
| `--filter` |  | `string` |  | only operations whose path/id/summary contains this substring |
| `--live` |  | `bool` | `false` | list the server's live operations instead of the embedded set |

Also accepts the [jentic global flags](#jentic-global-flags).

##### jentic api describe

Describe an operation's params and request/response schema

```text
jentic api describe <METHOD> <PATH> [flags]
```

**Flags**

| Flag | Shorthand | Type | Default | Description |
|---|---|---|---|---|
| `--live` |  | `bool` | `false` | describe against the server's live spec instead of the embedded one |

Also accepts the [jentic global flags](#jentic-global-flags).

### Local agent client

#### jentic skill

Generate the Jentic skill set into your agent's native layout

```text
jentic skill [flags]
```

```text
skill writes the Jentic skill set into each supported agent runtime's
native layout — a clean, spec-conformant SKILL.md per skill for
claude-code, cursor, and hermes, or a named pointer block in AGENTS.md
for codex and generic — so the agent discovers how to use Jentic and the
spec-flywheel skills without you hand-writing anything.

The shipped set is jentic (how to use the CLI), contribute-spec-fix, and
import-new-api; pass --skill to install a subset.

Writes are idempotent: owned SKILL.md files carry provenance in a sidecar
and AGENTS.md content lives in named managed blocks, so re-running never
clobbers your own edits around them.
```

Also accepts the [jentic global flags](#jentic-global-flags).

**Subcommands**

| Command | Description |
|---|---|
| [`jentic skill init`](#jentic-skill-init) | Generate the Jentic skill set for one or more operators |
| [`jentic skill list`](#jentic-skill-list) | Show supported operators and where each skill is installed |
| [`jentic skill update`](#jentic-skill-update) | Re-render installed skills with the current base URL |
| [`jentic skill remove`](#jentic-skill-remove) | Remove installed Jentic skills from one or more operators |

##### jentic skill init

Generate the Jentic skill set for one or more operators

```text
jentic skill init [flags]
```

```text
init detects which agent runtimes you have, lets you pick the targets
and placement (or pass --operator/--scope), and writes the Jentic skill
set into each one's native layout.

Passing --operator or --all skips every prompt, including the placement
one: each operator uses its default scope unless --scope is given
(preview with --dry-run). --skill limits the set (default: all shipped
skills); there is no interactive skill picker.

Non-interactively (--yes, agent mode, pipes, agent sessions) it
defaults to the detected operators, echoing each resolved path before
writing.

With --json (the default off-TTY and in agent mode) stdout carries one
{schema_version, results, mcp} document with a per-(operator, skill,
scope) outcome and the MCP registration outcome; side reports go to
stderr. --json changes only the output shape: on a terminal the
pickers still run (on stderr).

An install recording a newer skill version than this CLI bundles is
left alone (skipped_newer) unless --force.
```

**Flags**

| Flag | Shorthand | Type | Default | Description |
|---|---|---|---|---|
| `--all` |  | `bool` | `false` | target every supported operator |
| `--base-url` |  | `string` |  | Jentic control-plane base URL |
| `--dry-run` |  | `bool` | `false` | print target paths without writing |
| `--force` |  | `bool` | `false` | overwrite content you have manually edited, or downgrade an install newer than this CLI's copy |
| `--json` |  | `bool` | `false` | force the JSON outcome envelope (default when stdout is not a TTY or in agent mode) |
| `--operator` |  | `stringSlice` | `[]` | operators to target (repeatable or comma-separated) |
| `--scope` |  | `string` |  | placement scope: user or project (default: per-operator) |
| `--skill` |  | `stringSlice` | `[]` | skills to install (repeatable or comma-separated; default: all shipped) |
| `--yes` | `-y` | `bool` | `false` | non-interactive: no pickers; with no --operator/--all, target the detected operators |

Also accepts the [jentic global flags](#jentic-global-flags).

**Examples**

```sh
jentic skill init
jentic skill init --operator claude,cursor
jentic skill init --skill jentic --all --yes
jentic skill init --operator generic --dry-run
```

##### jentic skill list

Show supported operators and where each skill is installed

```text
jentic skill list [flags]
```

```text
list reports, per operator, whether its runtime is detected and where each
shipped skill is installed (user and project scope), whether it has
manual edits, and whether it is older than the copy bundled in this CLI
(stale: vX installed, vY available). Refresh stale installs with
`jentic skill update`.
```

**Flags**

| Flag | Shorthand | Type | Default | Description |
|---|---|---|---|---|
| `--json` |  | `bool` | `false` | force JSON output |

Also accepts the [jentic global flags](#jentic-global-flags).

##### jentic skill update

Re-render installed skills with the current base URL

```text
jentic skill update [flags]
```

```text
update re-renders every installed (skill, operator, scope) with the
currently resolved base URL and rewrites it when the recorded content
hash differs. A base-URL change therefore legitimately rewrites the
jentic skill (its body carries the interpolated URL). Manually-edited
content, and installs recording a newer skill version than this CLI
bundles, are reported and left alone unless --force.
```

**Flags**

| Flag | Shorthand | Type | Default | Description |
|---|---|---|---|---|
| `--base-url` |  | `string` |  | Jentic control-plane base URL |
| `--dry-run` |  | `bool` | `false` | print what would change without writing |
| `--force` |  | `bool` | `false` | overwrite content you have manually edited, or downgrade an install newer than this CLI's copy |
| `--json` |  | `bool` | `false` | force the JSON outcome envelope (default when stdout is not a TTY or in agent mode) |
| `--operator` |  | `stringSlice` | `[]` | operators to update (repeatable or comma-separated; default: all) |
| `--skill` |  | `stringSlice` | `[]` | skills to update (repeatable or comma-separated; default: all shipped) |

Also accepts the [jentic global flags](#jentic-global-flags).

**Examples**

```sh
jentic skill update
jentic skill update --operator claude
jentic skill update --skill jentic --force
```

##### jentic skill remove

Remove installed Jentic skills from one or more operators

```text
jentic skill remove [flags]
```

Aliases: `delete`, `rm`

**Flags**

| Flag | Shorthand | Type | Default | Description |
|---|---|---|---|---|
| `--all` |  | `bool` | `false` | remove from every supported operator |
| `--dry-run` |  | `bool` | `false` | print what would be removed without deleting anything |
| `--force` |  | `bool` | `false` | remove even content you have manually edited |
| `--json` |  | `bool` | `false` | force the JSON outcome envelope (default when stdout is not a TTY or in agent mode) |
| `--operator` |  | `stringSlice` | `[]` | operators to clean up (repeatable or comma-separated) |
| `--scope` |  | `string` |  | placement scope to remove from: user or project (default: every scope where the skill is installed) |
| `--skill` |  | `stringSlice` | `[]` | skills to remove (repeatable or comma-separated; default: all shipped) |

Also accepts the [jentic global flags](#jentic-global-flags).

**Examples**

```sh
jentic skill remove --operator cursor
jentic skill remove --skill import-new-api --operator cursor
jentic skill remove --all --force
```

#### jentic reset

Wipe this machine's jentic identity state and remove any leftover agent account

```text
jentic reset [flags]
```

```text
reset is the clean-slate hatch for this machine's jentic identity state.

If an older jentic release left a local agent account on this machine, reset
tears it down first — the dedicated Unix user (`<you>-local-agent`), the
named-user ACLs stamped across your home (both the leaf read/write grants and
the execute-only ancestor traverse grants), and the passwordless-launch
sudoers drop-in — plus any MCP-isolation service accounts (`_jentic-<runtime>`,
their sudoers lines, their exported key material, and the root-owned jentic
copy they ran). Current releases never create these. Then it wipes
your OWN jentic identity state: the store
(contexts, environments, identities, keys, tokens under ~/.config/jentic and
~/.local/state/jentic) and any legacy V1 profile tree under ~/.jentic. The
account's home is PRESERVED by default (re-owned to you); deleting it takes
a separate, explicit confirmation.

To remove a single context/identity instead of everything, use
`jentic context delete` / `jentic identity delete`.

Deleting an agent account and stripping ACLs are privileged, so reset
requires sudo for those steps: run it as yourself and you'll be prompted for your
password when it reaches the privileged steps. It shows the full plan before
touching anything. It only ever removes the agent's own named-user ACLs and
never touches another user's files.
```

**Flags**

| Flag | Shorthand | Type | Default | Description |
|---|---|---|---|---|
| `--delete-home` |  | `bool` | `false` | also delete the agent's home directory (non-interactive opt-in; pairs with --force) |
| `--force` |  | `bool` | `false` | skip the typed-name confirmation (the only non-interactive escape hatch; use with care) |

Also accepts the [jentic global flags](#jentic-global-flags).

**Examples**

```sh
jentic reset                              # full clean slate: any agent account + your identity state
jentic reset --force                      # non-interactive; keeps the home
jentic reset --force --delete-home        # non-interactive; deletes the home
```

#### jentic doctor

Diagnose this agent's local jentic setup (read-only)

```text
jentic doctor [flags]
```

```text
doctor is the agent-side self-check. It verifies the things an agent needs to
work — its config/state directories, a resolvable identity, a usable token or
API key, control-plane reachability, local clock skew, and whether any
installed skill is older than the bundled copy — and prints a
pass/warn/fail report. It is read-only (never mints tokens or writes config)
and, unlike `jenticctl doctor`, needs no operator tooling, so it runs where an
agent actually lives. It exits non-zero when any check fails (warnings keep a
zero exit), so it is safe to wire into a health probe.

Output defaults to JSON when stdout is not a TTY (agent-friendly).
```

**Flags**

| Flag | Shorthand | Type | Default | Description |
|---|---|---|---|---|
| `--json` |  | `bool` | `false` | force JSON output (default when stdout is not a TTY) |

Also accepts the [jentic global flags](#jentic-global-flags).

#### jentic mcp

Serve Jentic tools to a local MCP client over stdio

```text
jentic mcp [flags]
```

```text
mcp runs a Model Context Protocol server on stdin/stdout for a local MCP
client (Claude Code, Cursor, …) to spawn. It serves the Jentic tool surface
pre-auth: the session always boots — `tools/list` works with no or invalid
configuration — and the `get_started` tool diagnoses this machine's setup
state (config, registration, instance reachability) with the exact operator
instruction for each gap. `whoami` reports the agent identity the control
plane sees. Every tool result carries a top-level `instance` key identifying
the Jentic One instance it came from.

Context selection uses the root --context flag / $JENTIC_CONTEXT; broker
targeting comes from the context's environment. stdout is the JSON-RPC wire —
server logs go to --log-file (default under the XDG state dir), never stdout.
The command never prompts and never starts or stops the instance.

--http serves the same tool surface as a stateless Streamable HTTP daemon
(the isolated-local-daemon mode): a unix socket with OS-identity
(peer-credential) checks by default, or TCP with --listen (token required;
non-loopback additionally requires --allow-non-loopback + TLS). The daemon
holds exactly one context's keys and idle-exits for socket activation — see
deploy/mcp-daemon/ for the systemd/launchd templates.

--connect <url> turns this process into the credential-less stdio relay for
stdio-only clients: frames are pumped to the daemon over Streamable HTTP.
The relay reads no config and touches no state dir — its log goes to stderr
unless --log-file is set. Against a local unix socket it holds no key
material at all; against a remote https daemon it forwards the short-lived
bearer from $JENTIC_MCP_BEARER or --bearer-file, never persisting it.
```

**Flags**

| Flag | Shorthand | Type | Default | Description |
|---|---|---|---|---|
| `--allow-non-loopback` |  | `bool` | `false` | explicitly allow a non-loopback --listen bind (still refuses without --tls-cert/--tls-key and --token-file) |
| `--allow-origin` |  | `stringSlice` | `[]` | Origin values allowed on --http requests besides loopback (repeatable); anything else is refused with 403 |
| `--allow-uid` |  | `intSlice` | `[]` | peer uids allowed to connect to the --http unix socket (repeatable); the daemon's own uid and root always are |
| `--allow-unauthenticated` |  | `bool` | `false` | serve loopback --listen without a token — every local user may then act as this context; loopback-only, mutually exclusive with --token-file |
| `--bearer-file` |  | `string` |  | file holding a short-lived bearer for a remote --connect target (mode 0600; \$JENTIC_MCP_BEARER is the env alternative; never persisted) |
| `--connect` |  | `string` |  | relay stdio to a Streamable HTTP daemon at this URL (unix:///path/mcp.sock, http://&lt;loopback&gt;, or https://…) instead of serving |
| `--exclude-tools` |  | `stringSlice` | `[]` | tool names to withhold from the client (repeatable or comma-separated) |
| `--from-launchd` |  | `bool` | `false` | inherit the launchd inetd-wait listening socket on fd 0 (set by the LaunchDaemon template) |
| `--http` |  | `bool` | `false` | serve stateless Streamable HTTP instead of stdio (unix socket by default; see --socket/--listen) |
| `--idle-timeout` |  | `duration` | `15m0s` | exit after this long without a request in --http mode (0 disables) — pairs with socket activation |
| `--listen` |  | `string` |  | TCP host:port to serve --http on; TCP callers must present the --token-file token |
| `--log-file` |  | `string` |  | server log file (default: &lt;XDG state dir&gt;/jentic/logs/mcp.log; with --connect the default is stderr — the relay touches no state dir unless this is set) |
| `--max-result-bytes` |  | `int64` | `131072` | hard cap on a relayed response body in a tool result; larger bodies are truncated with {truncated, total_bytes, execution_id} |
| `--read-only` |  | `bool` | `false` | serve only tools annotated read-only |
| `--socket` |  | `string` |  | unix socket path to serve --http on; callers are checked by OS identity (default: &lt;XDG state dir&gt;/jentic/mcp.sock) |
| `--tls-cert` |  | `string` |  | TLS certificate for --listen (required for a non-loopback bind) |
| `--tls-key` |  | `string` |  | TLS private key for --listen (required for a non-loopback bind) |
| `--token-file` |  | `string` |  | file holding the bearer token TCP callers must present (mode 0600; required for --listen) |

Also accepts the [jentic global flags](#jentic-global-flags).

### Administration

#### jentic admin

Administer the jentic-one platform

```text
jentic admin [flags]
```

```text
admin groups operator-facing platform administration. Subcommands
require an identity with admin privileges (e.g. org:admin).

`admin config providers` manages runtime credential provider
configuration (e.g. Pipedream) without hand-editing backend YAML or
restarting the server.
```

Also accepts the [jentic global flags](#jentic-global-flags).

**Subcommands**

| Command | Description |
|---|---|
| [`jentic admin config`](#jentic-admin-config) | Manage runtime platform configuration |

##### jentic admin config

Manage runtime platform configuration

```text
jentic admin config [flags]
```

Also accepts the [jentic global flags](#jentic-global-flags).

**Subcommands**

| Command | Description |
|---|---|
| [`jentic admin config providers`](#jentic-admin-config-providers) | Manage credential provider configuration |

###### jentic admin config providers

Manage credential provider configuration

```text
jentic admin config providers [flags]
```

```text
providers configures credential providers (e.g. pipedream) at runtime.
A successful `set` rebuilds the server's provider registry so the change
takes effect without a restart. Secret fields are encrypted at rest and
redacted on read.
```

Also accepts the [jentic global flags](#jentic-global-flags).

**Subcommands**

| Command | Description |
|---|---|
| [`jentic admin config providers set`](#jentic-admin-config-providers-set) | Create or update a provider config |
| [`jentic admin config providers get`](#jentic-admin-config-providers-get) | Show a provider config (secrets redacted) |
| [`jentic admin config providers list`](#jentic-admin-config-providers-list) | List provider configs (secrets redacted) |

###### jentic admin config providers set

Create or update a provider config

```text
jentic admin config providers set <name> [flags]
```

```text
set validates the provider-specific fields, encrypts the secret, and
activates the config at runtime. For `pipedream` the recognised flags are
--project-id, --client-id, --environment, and --connect-base-url; the
client_secret is prompted for without echo (or read from stdin with
--secret-stdin).
```

**Flags**

| Flag | Shorthand | Type | Default | Description |
|---|---|---|---|---|
| `--client-id` |  | `string` |  | provider client id |
| `--connect-base-url` |  | `string` |  | provider connect base URL |
| `--environment` |  | `string` |  | provider environment (e.g. production) |
| `--json` |  | `bool` | `false` | emit JSON output |
| `--project-id` |  | `string` |  | provider project id |
| `--secret-stdin` |  | `bool` | `false` | read client_secret from stdin instead of prompting |

Also accepts the [jentic global flags](#jentic-global-flags).

###### jentic admin config providers get

Show a provider config (secrets redacted)

```text
jentic admin config providers get <name> [flags]
```

**Flags**

| Flag | Shorthand | Type | Default | Description |
|---|---|---|---|---|
| `--json` |  | `bool` | `false` | emit JSON output |

Also accepts the [jentic global flags](#jentic-global-flags).

###### jentic admin config providers list

List provider configs (secrets redacted)

```text
jentic admin config providers list [flags]
```

**Flags**

| Flag | Shorthand | Type | Default | Description |
|---|---|---|---|---|
| `--json` |  | `bool` | `false` | emit JSON output |

Also accepts the [jentic global flags](#jentic-global-flags).

#### jentic theme

Set the persistent color theme

```text
jentic theme <dark|light|no-color> [flags]
```

```text
theme persists the global color theme in ~/.config/jentic/config.yaml.
Override per-invocation with --theme or $JENTIC_THEME. Agent mode
always uses no-color regardless of this setting.
```

Also accepts the [jentic global flags](#jentic-global-flags).

## jenticctl

jenticctl: install and operate jentic-one locally

```text
jenticctl is the installer and lifecycle companion for jentic-one. It
stands up a local deployment (from source via `uv`, or in Docker via
`docker compose`) and manages the running app: health checks, start/stop,
log tailing, updates, and teardown.

New here? Run `jenticctl install` to set up locally. Once installed, use the
`jentic` CLI to register an agent and run against the API catalog. Use
`jenticctl <command> --help` for details on any command.
```

**Commands**

*Setup & lifecycle*

| Command | Description |
|---|---|
| [`jenticctl install`](#jenticctl-install) | Interactive wizard to configure and onboard jentic-one |
| [`jenticctl wizard`](#jenticctl-wizard) | Guided first-run: create your admin account, connect an operator, and start using Jentic |
| [`jenticctl setup`](#jenticctl-setup) | Create the first admin account (one-time first-run setup) |
| [`jenticctl reset-password`](#jenticctl-reset-password) | Set a temporary password for a user who is locked out or forgot theirs |
| [`jenticctl doctor`](#jenticctl-doctor) | Diagnose the local jentic environment |
| [`jenticctl status`](#jenticctl-status) | Show install, server, and identity health |
| [`jenticctl start`](#jenticctl-start) | Start the configured jentic-one app |
| [`jenticctl stop`](#jenticctl-stop) | Stop the background app or Docker stack |
| [`jenticctl logs`](#jenticctl-logs) | View the jentic-one app logs |
| [`jenticctl update`](#jenticctl-update) | Update the jentic CLIs (and check the stack) to the latest release |
| [`jenticctl uninstall`](#jenticctl-uninstall) | Remove everything under ~/.jentic (config files are backed up to \*-old) |

### Setup & lifecycle

#### jenticctl install

Interactive wizard to configure and onboard jentic-one

```text
jenticctl install [flags]
```

```text
install walks you through choosing a deployment path (from source or
Docker, SQLite or Postgres) and configuration, generates a jentic-one.yaml,
then builds the stack (local venv or Docker image), applies migrations, and
starts the app. Use --skip-build to only generate the config, or --no-start
to build without launching.

Docker path: by default this PULLS the published, signed app image
(ghcr.io/jentic/jentic-one-app, version-matched to the CLI) — no local build.
Use --build-local to build from a checkout instead (auto-selected inside a
jentic-one source tree or with $JENTIC_SRC), --ref to build a specific
branch/tag/commit, or --image-tag to pin a specific tag or @sha256: digest.

Advanced: --preset and the generated --<section>-<field> flags (e.g.
--server-public-base-url, --logging-file-enabled) overlay schema-driven
configuration onto the generated jentic-one.yaml, on top of the wizard.
```

**Flags**

| Flag | Shorthand | Type | Default | Description |
|---|---|---|---|---|
| `--answers` |  | `string` |  | non-interactive: skip the wizard and take answers from a YAML file (unlisted fields keep the wizard defaults; implies --defaults for the rest) |
| `--build-local` |  | `bool` | `false` | Docker path: build the app image from a local checkout instead of pulling the published image (auto-selected inside a jentic-one checkout, with \$JENTIC_SRC, or --ref) |
| `--config-form` |  | `bool` | `false` | after the wizard, show an extra schema-derived form to review/adjust config sections (interactive only) |
| `--defaults` |  | `bool` | `false` | non-interactive: skip the wizard and take its defaults (Docker + Postgres, loopback) as-is |
| `--fresh-secrets` |  | `bool` | `false` | rotate every generated secret instead of reusing an existing config's (default: reuse from jentic-one.yaml or jentic-one-old.yaml so encrypted data stays readable) |
| `--fresh-venv` |  | `bool` | `false` | wipe any existing local venv before building (recovers a wedged/half-populated ~/.jentic/venv from a prior failed install; local path only) |
| `--image-tag` |  | `string` |  | Docker path: pull this app image tag or @sha256: digest (or a full ghcr.io/…@sha256 ref) instead of the version-matched tag; ignored with --build-local |
| `--no-start` |  | `bool` | `false` | don't start the app in the background after a local install |
| `--no-wizard` |  | `bool` | `false` | don't offer the guided first-run wizard after the stack starts |
| `--out` |  | `string` | `jentic-one.yaml` | path to write the generated config to |
| `--preset` |  | `string` |  | apply an embedded config preset over schema defaults (local-dev, production); empty means none |
| `--ref` |  | `string` |  | git ref (branch/tag/commit) to build the stack from; implies --build-local (default: the ref this CLI was installed from, then the CLI version) |
| `--skip-build` |  | `bool` | `false` | only generate the config; don't build, migrate, or start |

#### jenticctl wizard

Guided first-run: create your admin account, connect an operator, and start using Jentic

```text
jenticctl wizard [flags]
```

```text
wizard is the hand-held onboarding that runs after `jenticctl install`. It
waits while you create your first admin account in the browser (so you get
to know the product through the UI), then offers to connect your AI operator
(Claude, Cursor, …): it registers an agent, points you at the console to
approve it, waits for the approval, and finally suggests example prompts you
can try.

Everything it does you can also do by hand (open the UI, `jenticctl setup`,
`jentic setup`); the wizard just sequences it into one continuous flow.
Re-run it any time — each step is idempotent.
```

**Flags**

| Flag | Shorthand | Type | Default | Description |
|---|---|---|---|---|
| `--base-url` |  | `string` |  | Jentic control-plane base URL (default: config / http://127.0.0.1:8000) |
| `--no-open` |  | `bool` | `false` | do not auto-open the browser to the setup page |
| `--no-wait` |  | `bool` | `false` | do not wait for the admin account; assume it already exists |
| `--operator` |  | `string` |  | operator to connect (claude, cursor, …); default: auto-detect |
| `--skip-operator` |  | `bool` | `false` | stop after the account exists; do not connect an operator |
| `--timeout` |  | `duration` | `15m0s` | how long to wait for account creation and agent approval at each step |
| `--yes` | `-y` | `bool` | `false` | skip confirmation prompts; connect the detected operator automatically |

**Examples**

```sh
jenticctl wizard
jenticctl wizard --operator claude --yes
jenticctl wizard --no-open   # don't auto-launch the browser
```

#### jenticctl setup

Create the first admin account (one-time first-run setup)

```text
jenticctl setup [flags]
```

```text
setup creates the first administrator for a freshly-installed jentic-one.
It drives the app's one-time create-admin path against the running stack
(Docker compose) or the local install (venv), so the credential never
leaves the operator boundary — there is no default password to rotate.

This succeeds only while no users exist; once an admin is created, manage
further users from the admin UI. Run `jenticctl install` first if you have
not set up locally yet.
```

**Flags**

| Flag | Shorthand | Type | Default | Description |
|---|---|---|---|---|
| `--config` |  | `string` |  | path to the generated config (default: ~/.jentic/jentic-one.yaml) |
| `--email` |  | `string` |  | admin email (prompted if omitted) |
| `--password` |  | `string` |  | admin password (prompted if omitted; prefer the prompt to keep it out of shell history) |
| `--yes` | `-y` | `bool` | `false` | skip prompts; require --email and --password |

#### jenticctl reset-password

Set a temporary password for a user who is locked out or forgot theirs

```text
jenticctl reset-password [flags]
```

```text
reset-password sets a one-time temporary password for an existing user and
forces them to choose a new one at their next sign-in. The operator never
learns the user's standing password — you hand over the temporary one, the
user changes it themselves, and the temporary credential dies on first use.

This is the operator recovery path: there is no email-based self-service
reset. It also clears any login lockout. Drives the app's reset path against
the running stack (Docker compose) or the local install (venv).
```

**Flags**

| Flag | Shorthand | Type | Default | Description |
|---|---|---|---|---|
| `--config` |  | `string` |  | path to the generated config (default: ~/.jentic/jentic-one.yaml) |
| `--email` |  | `string` |  | user email (prompted if omitted) |
| `--password` |  | `string` |  | temporary password (prompted if omitted; prefer the prompt to keep it out of shell history) |
| `--yes` | `-y` | `bool` | `false` | skip prompts; require --email and --password |

#### jenticctl doctor

Diagnose the local jentic environment

```text
jenticctl doctor [flags]
```

```text
doctor runs an exhaustive set of health checks across the local jentic
setup — filesystem and config, the recorded install, required tooling, the
control-plane server, and the agent profile — and prints a pass/warn/fail
report with remediation hints.

It is read-only and never mints tokens. It exits non-zero when any check
fails (warnings keep a zero exit), so it is safe to wire into CI.
```

**Flags**

| Flag | Shorthand | Type | Default | Description |
|---|---|---|---|---|
| `--base-url` |  | `string` |  | Jentic control-plane base URL |
| `--json` |  | `bool` | `false` | emit the check results as JSON |

#### jenticctl status

Show install, server, and identity health

```text
jenticctl status [flags]
```

```text
status is a health dashboard for the local jentic setup. It reports the
recorded install (mode/db/source), whether the control-plane server is
reachable and its version, and the active context's identity/token
state. It degrades gracefully: missing pieces are reported, not fatal.
```

**Flags**

| Flag | Shorthand | Type | Default | Description |
|---|---|---|---|---|
| `--base-url` |  | `string` |  | Jentic control-plane base URL |

#### jenticctl start

Start the configured jentic-one app

```text
jenticctl start [flags]
```

```text
start launches the locally-installed jentic-one app in the background.
It requires a completed `jenticctl install` (a generated config and a built
venv); if either is missing it tells you to run `jenticctl install` first.
```

**Flags**

| Flag | Shorthand | Type | Default | Description |
|---|---|---|---|---|
| `--config` |  | `string` |  | path to the generated config (default: ~/.jentic/jentic-one.yaml) |

#### jenticctl stop

Stop the background app or Docker stack

```text
jenticctl stop [flags]
```

```text
stop terminates the background jentic-one process recorded in
~/.jentic/app.pid (started by `jenticctl install` or `jenticctl start`), or tears
down the Docker stack for a containerized install. It sends SIGTERM, waits
for a graceful exit, then escalates to SIGKILL if needed.

For a Docker install, --volumes also removes the stack's data volumes
(SQLite data or the managed Postgres data dir). Use it to recover from an
incompatible Postgres image upgrade. This destroys the database.
```

**Flags**

| Flag | Shorthand | Type | Default | Description |
|---|---|---|---|---|
| `--timeout` |  | `duration` | `10s` | how long to wait for graceful shutdown before SIGKILL |
| `--volumes` |  | `bool` | `false` | also remove the Docker stack's data volumes (destroys the database) |
| `--yes` | `-y` | `bool` | `false` | skip the confirmation prompt for --volumes |

#### jenticctl logs

View the jentic-one app logs

```text
jenticctl logs [flags]
```

```text
logs shows the captured output of the locally-installed app
(~/.jentic/logs/app.log). Use --follow to stream new lines as they are
written, or --json to read the structured JSON-lines sink configured by
`jenticctl install`.
```

**Flags**

| Flag | Shorthand | Type | Default | Description |
|---|---|---|---|---|
| `--follow` | `-f` | `bool` | `false` | stream new log lines as they are written |
| `--json` |  | `bool` | `false` | read the structured JSON-lines sink instead of console output |
| `--lines` | `-n` | `int` | `200` | number of trailing lines to show |
| `--path` |  | `bool` | `false` | print the resolved log file path and exit |

#### jenticctl update

Update the jentic CLIs (and check the stack) to the latest release

```text
jenticctl update [flags]
```

```text
update reports the installed CLI and server versions, compares the
installed version against the latest release tag on GitHub, and (unless
--check) rebuilds and replaces the jenticctl and jentic binaries in place,
then refreshes the installed stack. A Docker stack running a published
image pulls the image for the target release; one built from source is
rebuilt. Use --cli-only or --stack-only to update just one half. A CLI
already at the latest release is not swapped again, so a re-run after a
failed stack half refreshes only the stack.

A Homebrew-installed CLI is never swapped in place: the CLI half runs
`brew upgrade jentic` instead, so brew's bookkeeping stays consistent
(--ref cannot pin a Homebrew-managed CLI).
```

**Flags**

| Flag | Shorthand | Type | Default | Description |
|---|---|---|---|---|
| `--base-url` |  | `string` |  | Jentic control-plane base URL (for the server probe) |
| `--check` |  | `bool` | `false` | only report status; don't apply any update |
| `--cli-only` |  | `bool` | `false` | update only the CLI binary |
| `--ref` |  | `string` |  | git ref to update to, pinning a specific tag/branch/commit (default: the latest release tag) |
| `--stack-only` |  | `bool` | `false` | update only the stack (not the CLI binary) |
| `--yes` | `-y` | `bool` | `false` | skip the confirmation prompt |

#### jenticctl uninstall

Remove everything under ~/.jentic (config files are backed up to \*-old)

```text
jenticctl uninstall [flags]
```

```text
uninstall deletes the contents of ~/.jentic (venv, source, data, logs,
etc.). Config files are preserved: each is renamed to a
'-old' backup so you can restore your settings later. Your agent
identity (~/.config/jentic, ~/.local/state/jentic, and any legacy
~/.jentic/profiles) is ALSO preserved — reinstalling keeps you
authenticated; run `jentic reset` to remove it. The ~/.jentic
directory itself is kept. A local agent account an older release
created (Unix user, ACLs, sudoers entry) is not touched either;
`jentic reset` removes it too.

For a Docker install the database lives in a named volume (SQLite data or
the managed Postgres data dir), not under ~/.jentic. uninstall tears the
stack down but PRESERVES that volume by default, so a later `install`
reattaches your data. The credential-encryption key for that data lives
in the jentic-one-old.yaml backup — install auto-reuses it so encrypted
credentials stay readable; deleting the backup by hand strands them.
Use --purge to also delete the data volume (this destroys the database),
or --keep-data to force preservation. With no flag and an interactive
terminal, uninstall asks before deleting it.

On a local (source) install it stops the background app/broker processes
first so they aren't orphaned when their files are removed.

Agent skills installed by `jentic skill init` live outside ~/.jentic
(e.g. ~/.claude/skills/jentic, AGENTS.md managed blocks). By default
uninstall leaves them in place and lists any that remain, with the
command to remove them (`jentic skill remove --all`).
--remove-skills (implied by --purge) also removes the user-scope
installs under your home directory. Project-scope installs (./AGENTS.md
blocks, ./.claude|.cursor|.hermes/skills/jentic) are found relative to
the current directory and are only removed with --remove-project-skills.
Uninstall lists the exact files first and asks before removing them
unless --yes is given. Manually edited skills, and files at a skill path
that Jentic did not write, are kept and reported. --dry-run shows what
would be removed and changes nothing. If a skill cannot be removed,
uninstall still finishes the teardown, reports the failure, and exits
non-zero.
```

**Flags**

| Flag | Shorthand | Type | Default | Description |
|---|---|---|---|---|
| `--dry-run` |  | `bool` | `false` | list what uninstall would remove, including agent skills, and change nothing |
| `--keep-data` |  | `bool` | `false` | preserve the Docker stack's data volume (the default; reinstall reattaches it and reuses jentic-one-old.yaml's encryption key) |
| `--purge` |  | `bool` | `false` | also remove the Docker stack's data volume (destroys the database) and the user-scope Jentic agent skills (never touches the current directory) |
| `--remove-project-skills` |  | `bool` | `false` | also remove project-scope Jentic agent skills under the current directory (./AGENTS.md blocks, ./.claude\|.cursor\|.hermes/skills/jentic); implies --remove-skills |
| `--remove-skills` |  | `bool` | `false` | also remove the user-scope Jentic agent skills under your home directory (manually edited ones are kept) |
| `--yes` | `-y` | `bool` | `false` | skip the confirmation prompts |

**Examples**

```sh
jenticctl uninstall
jenticctl uninstall --dry-run --purge
jenticctl uninstall --yes --keep-data
jenticctl uninstall --yes --remove-skills
jenticctl uninstall --yes --remove-project-skills
jenticctl uninstall --yes --purge
```
