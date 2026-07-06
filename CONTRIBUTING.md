# Contributing to Jentic One

Thanks for your interest in contributing. This guide covers local setup, the
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
3. Run `make check` (lint + OpenAPI score + secrets audit + architecture tests)
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
