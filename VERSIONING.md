# Versioning

Jentic One follows [Semantic Versioning](https://semver.org) with the pre-1.0
conventions below. The version is the single source of truth in
[`pyproject.toml`](pyproject.toml); every other surface derives from it
(`src/jentic_one/__init__.py` reads it via `importlib.metadata`, and the Helm
charts and Go CLI are stamped to match at release time).

## Pre-1.0 policy (public beta)

While Jentic One is in the `0.x` line it is in **public beta**, as stated in the
[README](README.md):

> APIs, database schemas, and CLI commands are subject to breaking changes
> without a major version bump.

Concretely, on the `0.MINOR.PATCH` line:

- **MINOR** (`0.13 → 0.14`) — may include breaking changes (API, DB schema, CLI
  flags, config) as well as features. Read the release notes and
  [`UPGRADING.md`](UPGRADING.md) before upgrading.
- **PATCH** (`0.13.2 → 0.13.3`) — bug fixes and non-breaking changes only.

We will not cut `1.0.0` until the public API, database schema, and CLI surface
are stable enough to promise the usual SemVer backward-compatibility guarantee.

## Where the version lives

| Surface | Source | How it's kept in sync |
| --- | --- | --- |
| Python package | `pyproject.toml` `[project].version` | canonical |
| `jentic_one.__version__` / `/health` / OpenAPI | package metadata | `importlib.metadata.version("jentic-one")` — never hand-edited |
| Helm charts (umbrella + observability + subcharts) | each `Chart.yaml` | bumped in lockstep by the release tooling |
| Go CLI (`jenticctl`, `jentic`) | build-time ldflag | stamped from the git tag by GoReleaser (`version = "dev"` in source) |

## Lockstep versioning

For beta, all artifacts share **one version** (the server, the Helm charts, and
the CLI binaries all carry the same `X.Y.Z`). The CLI is published as standalone
signed binaries but is versioned in lockstep with the server. Decoupling the CLI
and server version lines is deferred until there is a concrete need (e.g.
independent CLI release cadence for remote-client users).

## How releases are cut

Releases are automated via [release-please](https://github.com/googleapis/release-please):
a standing **Release PR** (`chore(main): release X.Y.Z`) accumulates
Conventional-Commit changes into a proposed version bump + `CHANGELOG.md`.
**Merging that PR is the release** — it tags `vX.Y.Z`, creates the GitHub
Release, and triggers the tag pipeline (build/migrate/`/health` gate →
signed CLI binaries). See [`docs/release-procedure.md`](docs/release-procedure.md)
for the full procedure and [`CHANGELOG.md`](CHANGELOG.md) for the history.

The baseline is the restored `v0.1.0`…`v0.13.2` tag line; the next release is
`v0.14.0`. We continue the `0.x` line rather than reset — the tags and GitHub
Releases are real and publicly visible, so continuing is the honest choice.
