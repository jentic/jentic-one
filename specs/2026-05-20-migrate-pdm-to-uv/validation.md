# Phase 29 Validation — Migrate Python Packaging from PDM to uv

## Definition of Done

All of the following must be true before this branch is merged.

### 1. `uv.lock` is consistent with `pyproject.toml`

```
uv lock --check
```

Exits 0. Confirms no stale lock was committed (`--check` is the alias for `--locked`; non-zero exit indicates `uv.lock` would change if regenerated).

### 2. Frozen sync installs cleanly

```
uv sync --frozen
```

Exits 0. The `--frozen` flag refuses to update `uv.lock`, so this doubles as a strict-reproducibility gate equivalent to PDM's `--frozen-lockfile`.

### 3. uv runs in application mode (no build backend)

```
grep -nE '^\[build-system\]' pyproject.toml
```

Exits 1 (no `[build-system]` block remains). jentic-mini is Docker-distributed and never wheelable, so no PEP 517 backend is needed.

```
grep -nE '^package = false' pyproject.toml
```

Exits 0. The `[tool.uv] package = false` declaration is present, telling uv this is an application — `uv sync` resolves and installs `[project.dependencies]` + `[dependency-groups] dev` into `.venv` without invoking any backend. This sidesteps the `src/`-layout / project-name mismatch entirely (uv would otherwise look for `src/jentic_mini/` based on the project name).

### 4. Lint passes under poe

```
uv run poe lint
```

Exits 0. Runs `ruff check` then `ruff format --check --diff` in sequence (matches the previous `pdm run lint` composite).

### 5. Backend test suite passes under poe

```
uv run poe test
```

Exits 0. Runs `pytest -v tests --tb=short` with `PYTHONPATH=.` (matches the previous `pdm run test`). Full suite, no marker filter, no exclude.

### 6. Dockerfile builds clean

```
docker build -t jentic-mini:phase29 .
```

Exits 0 on the host architecture. The build must complete without the previous `pipx install pdm` or `ensurepip --upgrade` layers.

### 7. `ci-backend.yml` workflow green on the PR

The GitHub Actions check `Backend / lint` and `Backend / backend-tests` must report success on the PR's head commit. Both jobs use `astral-sh/setup-uv@v8`, `uv sync --frozen`, and the new `uv run poe lint` / `uv run poe test` invocations; the `paths:` filter must trigger on the changed `pyproject.toml`/`uv.lock`/`Dockerfile`/`src/` paths.

### 8. `ci-docker.yml` workflow green on the PR

The GitHub Actions check `Docker Build & E2E` must report success. This builds the rewritten Dockerfile, runs `docker run -d -p 8900:8900 -v jentic-ci-data:/app/data -e JENTIC_TELEMETRY=off jentic-mini:latest`, polls `curl -sf http://localhost:8900/health` until ready, asserts `/health` returns one of `ok|setup_required|account_required`, asserts `/docs` contains `swagger-ui`, then runs `cd ui && npm run test:e2e:docker` (Playwright real-server suite).

### 9. `docker-security.yml` (Trivy) green on the PR

The GitHub Actions check for the Trivy scan must report success. The new image (no PDM-stage `ensurepip + pip/setuptools/wheel --upgrade`) must show no new HIGH/CRITICAL findings vs. `main`.

### 10. `codeql.yml` green on the PR

The GitHub Actions check for CodeQL (Python + JS/TS matrix) must report success. No new findings from the packaging swap.

### 11. No `pdm` or `PDM_IGNORE_ACTIVE_VENV` references survive in tracked code

```
git grep -nE "(pdm-project/setup-pdm|pdm install|pdm run|tool\.pdm)" -- ':!specs/2026-05-07-*' ':!specs/2026-05-08-*' ':!specs/2026-05-12-*'
```

Exits 1 (no matches). The exclude paths cover frozen completed-spec directories which are read-only history.

```
git grep -nE "PDM_IGNORE_ACTIVE_VENV" -- ':!specs/2026-05-07-*' ':!specs/2026-05-08-*' ':!specs/2026-05-12-*'
```

Exits 1 (no matches outside historical specs).

### 12. `pdm.lock` and `.pdm-python` are gone; `uv.lock` is present

```
test ! -f pdm.lock && test -f uv.lock && test ! -f .pdm-python
```

Exits 0.

### 13. No PDM backend reference remains

```
grep -nE 'pdm-backend|pdm\.backend' pyproject.toml
```

Exits 1. The full `[build-system]` block (which previously named `pdm-backend`) is gone — see #3.

### 14. `.github/dependabot.yml` Python entry uses uv

```
grep -nE 'package-ecosystem:\s*"?uv"?' .github/dependabot.yml
```

Exits 0 (matches either YAML quoting style in a single regex). `grep -nE 'package-ecosystem:\s*"?pip"?' .github/dependabot.yml` exits 1 (no `pip` ecosystem entry remains).

### 15. `specs/tech-stack.md` Core Stack reflects the swap

```
grep -nE '^\| Python packaging \| uv \|' specs/tech-stack.md
```

Exits 0. `grep -in pdm specs/tech-stack.md` exits 1.

### 16. Phase 29 lifecycle marker present

```
grep -F "## Phase 29 — Migrate Python Packaging from PDM to uv ✅" specs/roadmap.md
```

Exits 0. The single space before U+2705 is load-bearing per the lifecycle rule (`specs/roadmap.md:35-40`); a heading rendered as `Title✅` (no space) silently fails this assertion.

### 17. Phase 15's body no longer mints PDM commands into a future spec

```
sed -n '/^## Phase 15 /,/^## Phase 16 /p' specs/roadmap.md | grep -inE "tool\.pdm|pdm run"
```

Exits 1 (no matches inside the Phase 15 block). Phase 15's goal, depends-on, priority, and substantive content are otherwise unchanged — verified by reviewer inspection of the diff.

### 18. Husky hook runs lint via uv

```
grep -nF 'uv run poe lint' .husky/pre-commit
```

Exits 0 on line 1. `grep -nF 'pdm run' .husky/pre-commit` exits 1.

## Not Required

- **Adding new tests.** This phase is a tooling swap; the existing pytest suite re-run under `uv run poe test` is the equivalence proof.
- **Schemathesis CLI gate.** Schemathesis remains in dev-deps but is not wired into CI today (per Phase 24 / Phase 25 validation precedent — known FastAPI-TestClient incompatibility in `tests/test_openapi_contract.py`); not a Phase 29 concern.
- **Vitest / mocked Playwright runs.** Phase 29 doesn't touch `ui/src/**`; `ci-ui.yml`'s path filter naturally skips on this PR. The Docker E2E (`ci-docker.yml`'s `npm run test:e2e:docker`) is the only browser-driven gate that's load-bearing.
- **Manual security review.** Trivy and CodeQL CI gates cover the security surface.
- **`docs/`, `AGENTS.md`, `README.md`, `CLAUDE.md` edits.** These files have zero PDM mentions today; nothing to rewrite.
- **`docs/decisions.md` ADR.** No PDM ADR exists; only an unrelated `auth_type` entry.
- **Backwards-compat "PDM also works" mode.** Hard cutover per `karpathy-guidelines.md`.
- **Adding `.python-version`.** Optional symmetry with the sister-repo; deferred unless uv-managed Python provisioning becomes useful.
- **Verifying the first Dependabot Python PR pre-merge.** Dependabot only opens PRs after the new config lands on `main` (daily 23:00 UTC schedule), so this cannot be a merge gate. Recorded as a post-merge confirmation in the PR body: within ~24h of merge, a `chore(deps): bump …` PR should open and flow through `dependabot-merge.yml` cleanly. Absence is a regression to investigate, not a reason to revert.
- **Rebuilding worktrees in CI.** The `.claude/rules/worktrees.md` rewrite is verified by reading the file post-edit.
- **Reintroducing a lock-sync workflow.** `dependabot-pdm-lock.yml` was deleted in #417 and must stay deleted; uv updates `uv.lock` atomically inside the same Dependabot PR.
