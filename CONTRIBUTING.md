# Contributing to Jentic One

Thanks for your interest in contributing. This guide covers **filing issues**
(bugs, feedback, ideas) and **contributing code** (local setup, workflow, and the
conventions the project enforces).

---

## Filing an Issue

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
3. **Fill in the template.** Only the first field is required — but concrete beats
   vague: exact commands, exact errors, what you expected vs. what happened.
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
- **prioritizes** it (severity), and
- **checks for duplicates**.

If something important is missing, the assistant will **comment and @-mention you**
with a short checklist of what would help (e.g. the exact error text, or which
command you ran) — you're never blocked from filing, and you stay in the loop. A
maintainer steps in only when the assistant flags an issue as needing a human. You
never have to set labels yourself; in fact, on a public repo you can't.

## Filing an Issue with an AI Agent

If you use an AI coding agent to file issues on your behalf, keep it simple: have it
write a clear, faithful issue through one of the two forms above, with the **exact
error/output pasted verbatim** and secrets redacted (`***`). Don't have it pass
`--label` — on a public repo GitHub silently drops labels from non-maintainers, and
our intake assistant applies the labels anyway. If the agent knows the type/area/
severity, it can mention that in the body; the assistant will confirm it.

---

## Contributing Code

Ready to open a pull request? The rest of this guide covers local setup, the
development workflow, and the conventions the project enforces.

## Getting Started

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

See the [Build & Deploy Guide](deploy/README.md) for the full setup and
common tasks.

## Development Workflow

### Branching

- Create feature branches from `main`.
- Use descriptive branch names, prefixed by type: `feat/…`, `fix/…`, `docs/…`,
  `refactor/…`, `chore/…`.
- Keep branches focused on a single change.

### Making Changes

1. Write code following the conventions enforced by the architecture tests (`make test-arch`).
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

### Pull Requests

- Describe *what* changed and *why*.
- CI must pass: lint, type check (mypy strict), tests, and architecture tests.
- Keep PRs focused and reviewable — prefer smaller, incremental changes.

## Code of Conduct

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

## Architecture & Conventions

The codebase is a modular monolith with **AST-enforced module boundaries**
(`make test-arch`). Generated artifacts
(OpenAPI spec, endpoint reference, CLI reference) are produced by `make openapi`
/ `make endpoints` / `make cli-reference` and should not be hand-edited.

## Security

Never commit secrets. See [SECURITY.md](SECURITY.md) for responsible-disclosure
instructions and the operator security model.
