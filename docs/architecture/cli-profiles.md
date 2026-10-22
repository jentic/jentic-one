# CLI profile manifests

A **CLI profile** is one manifest per vendor CLI that Jentic governs (`gh`,
`cf`). It declares everything Jentic needs to run that CLI through the Broker:
the vendor's APIs and where each comes from, how the Broker's facade and the
egress proxy of `jentic run --container` route the CLI's calls, the agent-side
environment, the hard-deny lists that apply, and rule presets that expand into
concrete permission rules. Enabling a vendor's facade and connecting its APIs
then needs no hand-made spec and no hand-typed rules.

The shipped profiles live in
[`src/jentic_one/shared/cli_profiles/`](../../src/jentic_one/shared/cli_profiles/)
(`github.yaml`, `cloudflare.yaml`), next to the specs they ship (`specs/`) and
the JSON Schema editors validate them with
([`cli-profile.schema.json`](../../src/jentic_one/shared/cli_profiles/cli-profile.schema.json),
regenerated from the Pydantic model with `make cli-profile-schema`). The
operator-facing guide is [CLI profiles in the local agent guide](../guides/local-agent.md#cli-profiles).

## What reads a profile

| Consumer | Reads |
| -------- | ----- |
| Broker facades (`broker/core/facades.py`) | `routing.facade` (the GitHub layout's `/api/v3`, `/api/graphql`, `/api/uploads`; Cloudflare's path facade), `vendor.registry_domains` and `vendor.error_shape` (the proxy route's vendor check and error bodies) |
| `GET /cli-facades` (control) | `cli`, `vendor.display_name`, the API roles (as `protocols`), `agent.wrapper`, `agent.container`, `routing.egress.governed_domains` |
| `jentic <cli>`, `jentic cli run` | the served `binary`, `display_name` and `wrapper` (built-in `cf` wiring for a server that serves none) |
| `jentic run --container` | the served `container` wiring and `governed_domains` (built-in GitHub defaults for a server that serves none) |
| Provisioning (registry, `POST /cli-profiles/{profile}:provision`, and every connect for one of the profile's APIs) | `apis[].source`, `apis[].api_id`, `apis[].identity` |
| `POST /integrations:connect` with `preset` | `apis[].connect`, `apis[].role`, `presets` |
| `GET /cli-profiles` | everything above, for operators and agents |

Loading is strict and happens at import: an unknown key, an identity that is
not the one an import registers, a missing or malformed shipped spec, an
unknown rule set, a hard-deny reference of the wrong shape or a preset that does
not expand into valid rules fails the process (and the CI test
`tests/unit/shared/test_cli_profiles.py`, which also resolves every hard-deny
reference and checks every `connect` key against `config/local.yaml`).

## Format

```yaml
format_version: 1            # required; the only version
profile: github              # the profile's name; the file is <profile>.yaml
display_name: GitHub
cli:
  binary: gh                 # executable looked up on PATH
  supported_range: ">=2.40.0,<3"   # comma-separated comparators (informational)
vendor:
  id: github                 # the cli_facades.vendors key and invocation-token vendor
  display_name: GitHub
  registry_domains: [github.com]   # registry vendors this vendor's tokens reach
  error_shape: github        # github | cloudflare | unset (RFC 9457)
credential:
  mode: per_api              # per_api; profile_scoped is reserved
connect:                     # the single profile-wide connect (profile_scoped only)
  vendor_entry: github
  flow: device_authorization
apis: [...]                  # see below
routing: {...}
agent: {...}
hard_deny: [...]
presets: {...}
```

### `apis`

Each API has a **role**, unique within the profile, that decides which rule
kind its binding takes:

| Role | Rules (`match_mode`) |
| ---- | -------------------- |
| `rest`, `uploads` | path rules: `regex`, `prefix`, `exact` |
| `graphql` | GraphQL top-level-field rules: `graphql` |
| `git` | git repository/ref rules: `git` |

```yaml
apis:
  - role: rest
    source: catalog:github.com/api.github.com     # the public API catalog
    identity: {vendor: github-com, name: api-github-com}
    connect: github                                # vendors.entries key
  - role: graphql
    source: shipped:specs/github-graphql.openapi.yaml   # a spec in this package
    api_id: github.com/github-graphql              # required for shipped
    identity: {vendor: github-com, name: github-graphql, version: "1"}
    connect: github-graphql
  - role: git
    source: builtin:github.com/github-git          # served by the Broker itself
    identity: {vendor: github-com, name: github-git, version: "1"}
    connect: github-git
```

- `source` is `catalog:<catalog api_id>`, `shipped:specs/<file>` or
  `builtin:<catalog api_id>`.
- `api_id` is the id a vendor entry names the API by (`vendors.entries.<key>.vendor`);
  for `catalog` and `builtin` sources it is the source's own id.
- `identity` is the registry identity the API resolves to, in slug form. The
  loader checks it against what an import registers (the registrable domain of
  the api_id and its sub segment), so a credential the connect flow stamps
  covers the imported API exactly. A `catalog` API has no version (the
  imported spec decides it); `shipped` and `builtin` APIs declare theirs, and a
  shipped spec's `info.version` must match it.
- `connect` is the `vendors.entries` key whose connect creates this API's
  credential (`jentic connect <key>`). Two APIs never share one.

### `routing`

```yaml
routing:
  facade:
    mode: host               # must match cli_facades.vendors.<vendor>'s mode
    routes:
      - {api: rest, prefix: /api/v3, upstream: "https://api.github.com"}
      - {api: graphql, path: /api/graphql, upstream: "https://api.github.com/graphql"}
      - {api: rest, prefix: /api/uploads, upstream: "https://uploads.github.com"}
  egress:
    governed_domains: [github.com]
```

A route takes a `prefix` (`<prefix>/<rest>` maps to `<upstream>/<rest>`) or an
exact `path`. In `path` mode a prefix is below the facade's own
`/_facade/<vendor>`, and `/` is that root. `governed_domains` are the domains
the egress proxy TLS-terminates and relays to the Broker; `jentic run
--container` refuses a vendor whose served domains its proxy does not govern.

### `agent`

```yaml
agent:
  wrapper:                   # jentic <cli> on the agent's machine
    env_set: {GH_HOST: "{address_host}", GH_ENTERPRISE_TOKEN: "{token}"}
    env_strip: [GH_TOKEN, GITHUB_TOKEN, GITHUB_ENTERPRISE_TOKEN]
  container:                 # jentic run --container
    env_set: {GH_TOKEN: "{placeholder}", GIT_TERMINAL_PROMPT: "0"}
    env_strip: [GITHUB_TOKEN]
    tunnel_hosts: []         # hosts tunnelled, never decrypted
```

An `env_set` value is a literal or exactly one placeholder: `{address}` (the
facade address), `{address_host}` (its hostname) and `{token}` (the command's
invocation token) for the wrapper, which must hand the CLI `{token}`;
`{placeholder}` (the egress proxy's fixed non-secret token, which the proxy
replaces) for the container. A CLI that meets a placeholder it does not know
refuses to run the vendor CLI.

### `hard_deny`

References to the lists no rule can override. The lists stay in code, one
definition each; the manifest only names them, as `<module>:<attribute>`:

```yaml
hard_deny:
  - {api: graphql, kind: graphql_mutation,
     source: "jentic_one.shared.permissions.github_graphql:HARD_DENIED_MUTATIONS"}
  - {api: graphql, kind: graphql_held_mutation,
     source: "jentic_one.shared.permissions.github_graphql:HELD_MUTATIONS"}
  - {api: git, kind: git_ref,
     source: "jentic_one.broker.core.git_protocol:ref_hard_deny_reason"}
```

### `presets`

A preset is a named rule list across the profile's APIs:

```yaml
presets:
  push_branches:
    description: Clone, fetch and push branches matching a pattern ...
    params:
      repo: {kind: repo_list, description: ...}
      branches: {kind: ref_glob, description: ...}
      force: {kind: flag, description: ..., default: "false"}
    rules:
      - {api: git, for_each: repo, effect: allow, match_mode: git,
         path: "push:${repo}:refs/heads/${branches}"}
      - {api: git, for_each: repo, when: force, effect: allow, match_mode: git,
         path: "force:${repo}:refs/heads/${branches}"}
  read_and_prs:
    rules:
      - {api: graphql, include: "github_graphql:pr_write"}
```

- A rule has the fields of a permission rule (`effect`, `methods`, `path`,
  `match_mode`, `operations`) and the `api` role whose binding it goes on; its
  `match_mode` must be one that role takes.
- `${name}` in `path` is filled with parameter `name`. (`{owner}`-style
  OpenAPI placeholders in a REST path are left as they are.)
- `for_each: <repo_list param>` repeats the rule once per listed repository;
  `when: <flag param>` keeps it only when the flag is true.
- `include: <set>:<name>` inserts a rule set kept in code
  ([`shared/cli_profiles/rule_sets.py`](../../src/jentic_one/shared/cli_profiles/rule_sets.py);
  `github_graphql:read_only`, `pr_write`, `pr_write_with_approval` are the
  GitHub GraphQL presets).
- Parameter kinds: `repo_list` (comma-separated `<owner>/<repo>` globs, at most
  50), `ref_glob` (a branch glob below `refs/heads/`, e.g. `agent/*`), `flag`
  (`true`/`false`).

Expansion is server-side, per API role: `jentic connect github-git --preset
push_branches --preset-param repo=octo-org/app --preset-param branches='agent/*'`
sends the preset to `POST /integrations:connect`, which expands it for the
git API only (asking only for the parameters those rules use), checks every
rule with the validators a hand-written rule goes through, appends them to the
rules the agent listed itself, and stores them on the session. The approver
reviews those concrete rules; the response lists them.

The shipped GitHub presets:

| Preset | REST (`github`) | GraphQL (`github-graphql`) | git (`github-git`) |
| ------ | --------------- | -------------------------- | ------------------ |
| `read_only` | `GET` prefix `/` | every query; no mutation | `fetch:<repo>` |
| `read_and_prs` | `read_only` + `POST` pull requests and issue comments | every query, the PR mutations, any other mutation held | `fetch:<repo>` |
| `push_branches` | — | — | `push:<repo>:refs/heads/<branches>`, plus `force:` on the same pattern with `force=true` |

## Provisioning

`POST /cli-profiles/{profile}:provision` (`catalog:import`; `jentic admin
cli-profiles provision <profile>`) makes every API of a profile exist and be
live:

| Source | What happens | Status |
| ------ | ------------ | ------ |
| `catalog` | the catalog import (an import job), unless the entry is registered | `import_queued`, `live`, `not_in_catalog` |
| `shipped` | an inline import with origin `cli_profile`, live in the same transaction, unless the current revision already has the shipped spec's digest | `imported`, `live`, `held_for_review` |
| `builtin` | nothing | `builtin` |

It is idempotent and safe on several replicas at once: a registered catalog
entry is skipped, and a replica that loses the race to import a shipped spec
reads it back as live (the registry's uniqueness on the API identity and on
`(api, spec digest)` decides the race). Nothing provisions at boot. The same
provisioning runs on demand whenever a connect starts or finishes for one of
the profile's APIs (the catalog auto-import seam): the connected API always,
and the profile's other APIs too while the vendor's facade is enabled.

## Credentials today, and a profile-scoped credential

`credential.mode: per_api` keeps one credential per API: each API's
`connect` entry runs its own flow and the credential covers that API alone,
with its own binding and rules. `profile_scoped` is reserved for one
credential per profile connect (the profile's `connect` block: one flow, one
stored secret) covering exactly the APIs the profile declares and nothing else
of the vendor, with each rule kind applying only to the API of the matching
role. The manifest already carries what that needs: a stable `role` and
resolved `identity` per API, and the single `connect` entry.
