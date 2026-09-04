# Contributing to Jentic One

Thanks for your interest in contributing. This guide covers **filing issues**
(bugs, feedback, ideas) and **contributing code** (local setup, workflow, and the
conventions the project enforces).

---

## Filing an issue

The fastest way to help is to tell us what broke, confused you, or is missing.

1. **Search first** — check [open and closed issues](https://github.com/jentic/jentic-one/issues?q=is%3Aissue)
   for a duplicate. If you find one, add your context as a comment instead of
   opening a new one.
2. **Open a new issue** at [New issue](https://github.com/jentic/jentic-one/issues/new/choose)
   and pick one of the two forms:
   - 🐛 **Bug** — something broken / errored / wrong output.
   - 💡 **Request or feedback** — a feature, an improvement, a rough idea, a pain
     point (friction/confusion during real use), or a docs gap. One form covers all
     of these — you don't need to categorize it.
3. **Fill in the template.** Only the first text field is required — but concrete
   beats vague: exact commands, exact errors, what you expected vs. what happened.
   You'll also tick a couple of quick confirmations to submit (you searched for a
   duplicate, removed any secrets, and accept the
   [Code of Conduct](https://github.com/jentic/.github/blob/main/CODE_OF_CONDUCT.md)).
4. **Redact secrets** — never paste API keys, tokens, OAuth secrets, or passwords.
   Replace them with `***`.
5. **Security vulnerabilities do not go here** — follow
   [SECURITY.md](https://github.com/jentic/jentic-one/blob/main/SECURITY.md) for
   private disclosure.

### What happens next — automated intake, no manual triage

You don't need to know Jentic One's internals or apply any labels. When you open an
issue, an **automated intake assistant** reads it and, in one pass:

- **classifies** it (type + which part of the product it touches),
- **scores** it for product fit and feasibility,
- **prioritizes** it (severity, for bugs and pain points), and
- **checks for duplicates**.

If something important is missing, the assistant will **comment and @-mention you**
with a short checklist of what would help (e.g. the exact error text, or which
command you ran) — you're never blocked from filing, and you stay in the loop. A
maintainer steps in only when the assistant flags an issue as needing a human. You
never have to set labels yourself; in fact, on a public repo you can't.

## Filing an issue with an AI agent

If you use an AI coding agent to file issues on your behalf, keep it simple: have it
write a clear, faithful issue through one of the two forms above, with the **exact
error/output pasted verbatim** and secrets redacted (`***`). Don't have it pass
`--label` — on a public repo GitHub silently drops labels from non-maintainers, and
our intake assistant applies the labels anyway. If the agent knows the type/area/
severity, it can mention that in the body; the assistant will confirm it.

---

## Contributing code

The rest of this guide covers contributing code: local setup, the development
workflow, and the checks a pull request has to pass.

## Getting started

1. Install [uv](https://docs.astral.sh/uv/) — `brew install uv` (or see the uv docs).
2. Clone the repository.
3. Install dependencies and git hooks:

   ```bash
   make install
   ```

4. Run the checks to confirm your environment is set up:

   ```bash
   make check
   ```

5. Start the app locally:

   ```bash
   make start-app
   ```

See [deploy/README.md](deploy/README.md) for the build architecture and
common build tasks.

## Development workflow

### Branching

- Create feature branches from `main`.
- Use descriptive branch names, prefixed by type: `feat/…`, `fix/…`, `docs/…`,
  `refactor/…`, `chore/…`.
- Keep branches focused on a single change.

### Making changes

1. Write code following the conventions enforced by the architecture tests
   (`make test-arch`); [docs/architecture/surfaces-and-layering.md](docs/architecture/surfaces-and-layering.md)
   maps each rule to its test.
2. Add or update tests for your change.
3. Run `make check` (lint + type check + secrets audit + architecture tests)
   before pushing. `make fix` auto-fixes formatting and lint issues.

### Commits

Commits follow [Conventional Commits](https://www.conventionalcommits.org/) with
a **mandatory scope**, enforced by a `commit-msg` hook:

```
type(scope): concise description
```

Reserve `fix` for real bug fixes to shipped behaviour. Use `feat`, `refactor`,
`docs`, `test`, `chore`, `ci`, `build`, etc. as appropriate.

### Sign-off (DCO)

Contributions are accepted under the [Developer Certificate of Origin](https://developercertificate.org/).
Sign off every commit with `git commit -s`, which adds a `Signed-off-by` line
certifying you have the right to submit the change under the project's license.

### Pull requests

- Describe *what* changed and *why*.
- CI must pass: lint, type check (mypy strict), tests, and architecture tests.
- Keep PRs focused and reviewable — prefer smaller, incremental changes.

## Code of conduct

This project follows the Jentic
[Code of Conduct](https://github.com/jentic/.github/blob/main/CODE_OF_CONDUCT.md).
By participating, you agree to uphold it.

## Tooling

| Tool | Purpose |
| ---- | ------- |
| [uv](https://docs.astral.sh/uv/) | Dependency management (`uv.lock` is committed) |
| [ruff](https://docs.astral.sh/ruff/) | Linting + formatting (`make lint` / `make fix`) |
| mypy (strict) | Static type checking (`make typecheck`) |
| lefthook | Git hooks (pre-commit lint + secrets, commit-msg lint) |
| detect-secrets | Secrets scanning (`make detect-secrets`) |

## Testing

Tests are split into tiers:

```bash
make test-unit          # logic with no external services
make test-integration   # database lifecycle against PostgreSQL fixtures
make test-arch          # layering and convention enforcement
make test-smoke         # liveness against running services
```

- Unit tests must not require external services.
- Integration tests run against PostgreSQL fixtures (`make start-fixtures` /
  `make stop-fixtures`).
- Use synthetic/fabricated data in tests — never real credentials or data.

## Architecture & conventions

The codebase is a modular monolith with **AST-enforced module boundaries**
(`make test-arch`). Generated artifacts
(OpenAPI spec, endpoint reference, CLI reference) are produced by `make openapi`
/ `make endpoints` / `make cli-reference` and should not be hand-edited.

## Documentation

- New docs live under `docs/` — [docs/README.md](docs/README.md) maps the
  sections (install, operate, use, secure, understand, develop, reference).
- Link every new doc from its section README or `docs/README.md`; an
  architecture test fails on docs the index cannot reach.
- `docs/reference/` is **generated — never hand-edit**. Regenerate with
  `make config-reference` (config.md), `make endpoints` (endpoints.md/.json);
  the CLI reference is `make cli-reference` (ui/public/cli-reference.json).
- Any referential fact in prose — a command, path, port, or URL — should be
  covered by an arch gate (`tests/arch/`) or quoted from a generated file, so
  it fails CI when the code changes instead of silently going stale.

## Security

Never commit secrets. See [SECURITY.md](SECURITY.md) for responsible-disclosure
instructions and the operator security model.
