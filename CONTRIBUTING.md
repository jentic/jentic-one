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
2. **Open a new issue via the template picker** at
   [New issue](https://github.com/jentic/jentic-one/issues/new/choose) and pick:
   - 🐛 **Bug** — something broken / errored / wrong output
   - 🤕 **Pain point** — friction, confusion, dead ends, confusing wording or flow
   - ✨ **Feature** — a capability that's missing or could be improved
   - 💡 **Idea / Suggestion** — an early-stage "what if", not yet a concrete request
   - 📖 **Docs** — missing / wrong / unclear documentation
3. **Fill in the template.** Only the first field is required — but concrete beats
   vague: exact commands, exact errors, what you expected vs. what happened.
4. **Redact secrets** — never paste API keys, tokens, OAuth secrets, or passwords.
   Replace with `***`.

### Labels — how they work here

The **type** of an issue (`bug`, `enhancement`, `documentation`, `pain-point`,
`idea`) is applied **automatically by the template** when you file through the web
picker, along with `needs-triage`. You don't need to — and, without write access,
**cannot** — set these yourself.

Two further label groups are added by a **maintainer during triage**:

| Group | Examples | Meaning |
| ----- | -------- | ------- |
| `area:*` | `area:cli`, `area:broker`, `area:ui`, `area:control`, `area:auth`, `area:deploy`, `area:docs`, … | Which part of Jentic One the issue is about |
| `severity:*` | `severity:blocker`, `severity:major`, `severity:minor` | Impact on you (for bugs & pain points) |

If you know the area or severity, **mention it in the issue body** (e.g. "this is in
the broker, felt like a blocker") — a maintainer will apply the matching labels.
You don't need to know Jentic One's internals to file a good issue.

> **Note on `--label`:** creating an issue with `gh issue create --label ...` or the
> REST API `labels` field **only works if you have Triage access or above.** For
> everyone else, GitHub **silently drops** the labels — the issue is created without
> them and with no error. File through the **web template picker** so the template's
> labels apply, or just describe the classification in the body.

### What happens next

New issues are `needs-triage`. A maintainer will reproduce and add `area:` /
`severity:` labels, mark `confirmed`, ask for more info, or close as `duplicate`.

## Filing an Issue with an AI Agent

If you use an AI coding agent (Claude Code, Cursor, Codex, …) to file issues on your
behalf, this section is the protocol for it. (Agents: [AGENTS.md](AGENTS.md) points
here.)

### The one hard constraint — an agent cannot apply labels

`jentic/jentic-one` is public, and applying a label needs **Triage access or above**.
GitHub enforces this **silently**: `gh issue create --label ...` and the REST
`labels` field are **dropped without error** for a non-maintainer. So:

> **Do not pass `--label`.** Instead, write a **`Suggested labels`** block into the
> issue body (type / area / severity) for a maintainer to apply at triage. The type
> and `needs-triage` are applied automatically **only** when a human files through
> the web template picker — the CLI does not apply them.

### Protocol

1. **Interview the user, one question at a time.** Don't ask them to classify
   anything ("is this the broker?"). Pull out: what they tried, the exact steps /
   command, the **exact error or output pasted verbatim**, what they expected, and
   where they were (terminal vs. website). "unknown" is an acceptable answer — never
   block on a field.
2. **Search for duplicates** before filing:
   `gh issue list -R jentic/jentic-one --state all --search "<symptom keywords>" --limit 20`.
   Found one? Comment on it with the user's context (anyone can comment) instead of
   opening a duplicate.
3. **Infer the classification** (see the label table above) and put it in a
   `Suggested labels` block at the top of the body. Suggest exactly one **type**; one
   **area** (or `unknown` — never guess); a **severity** for bugs/pain points.
4. **Write the body** — concrete and faithful. Redact secrets (`***`). Paste real
   errors verbatim in fenced code blocks. One problem per issue. Title:
   `[type] short imperative summary`.
5. **Confirm, then file.** Present the draft (title + suggested labels + body) and
   ask a single yes/no question before filing. Prefer directing the user to the web
   template picker (labels apply); if filing via CLI, use `--body-file` with **no
   `--label`**, then tell the user a maintainer still needs to apply the labels.

### Body template for an agent-filed issue

```markdown
### Suggested labels (for maintainer triage)
- type: bug            <!-- bug | pain-point | enhancement | idea | documentation -->
- area: broker         <!-- area:* value, or "unknown" -->
- severity: major      <!-- severity:blocker | major | minor (bugs & pain points) -->

---

## What happened
<Plain-language summary of the user's experience.>

## Where
Command / page: <exact command, page/button, or "unknown">

## Steps to reproduce
1. …

## Expected
<What the user expected.>

## Actual
<Exact error/output, verbatim, in a code block.>

## Environment
- Jentic One version / commit: <or "unknown">
- OS: <or "unknown">
- Install method: <install.sh | source | Docker | unknown>
```

---

# Contributing Code

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
