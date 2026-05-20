# Phase 29 Plan — Migrate Python Packaging from PDM to uv

## Group 1 — Configure uv in application mode (no build backend)

1. Delete the entire `[build-system]` block from `pyproject.toml` (currently lines 37–39: `[build-system] / requires = ["pdm-backend"] / build-backend = "pdm.backend"`). The project is Docker-distributed, never wheelable; no PEP 517 backend is needed. `uv sync` honours `[tool.uv] package = false` (next step) and skips backend invocation entirely.
2. Append `[tool.uv]` with `package = false` to `pyproject.toml` (after the `[dependency-groups]` block). This declares jentic-mini an application, not a library — `uv sync` resolves and installs `[project.dependencies]` + `[dependency-groups] dev` into `.venv` without ever building or installing the project itself, which mirrors the Dockerfile's `--no-install-project` behaviour and means the `src/`-layout / project-name mismatch never surfaces.

## Group 2 — Add `poethepoet` and `[tool.poe.tasks]` (additive, no removals yet)

3. Append `poethepoet>=0.37,<0.47` to `pyproject.toml` `[dependency-groups] dev` (after the existing `ruff<0.16.0,>=0.14.1` entry).
4. Add `[tool.poe.tasks]` to `pyproject.toml` (after the existing `[tool.ruff.lint.isort]` block) with three tasks: `lint` as a sequence of `ruff check ${GITHUB_ACTIONS:+--output-format=github}` (shell form) followed by `ruff format --check --diff`; `lint:fix` as a sequence of `ruff check --fix` followed by `ruff format`; `test` with `env = { PYTHONPATH = "." }` and `cmd = "pytest -v ${args:tests} --tb=short"`. The `${args:tests}` placeholder must produce identical positional-argument behaviour to PDM's `{args:tests}` (default to `tests`, allow narrow overrides).
5. Smoke each task locally before flipping anything else: `uv sync` (writes a draft lockfile but is acceptable here as a probe — discard before committing); `uv run poe lint` exits 0; `uv run poe test tests/test_health.py -v` exits 0; `uv run poe lint:fix` exits 0 with no diff. Confirm `[tool.pdm.scripts]` is still present and untouched at this point — the additive flip lets us validate poe shape against a live ruff/pytest before discarding the PDM scripts.

## Group 3 — Generate `uv.lock`, delete PDM artefacts

6. Run `uv lock` to produce `uv.lock` deterministically (this regenerates rather than upgrades, since the resolver inputs in `pyproject.toml` `[project.dependencies]` and `[dependency-groups] dev` are unchanged); commit the resulting `uv.lock`.
7. `git rm pdm.lock`; remove the working-tree `.pdm-python` interpreter marker (`git rm .pdm-python` if tracked; otherwise `rm -f .pdm-python`).
8. Edit `.gitignore`: remove the `.pdm-python` line (currently line 23). Leave `__pypackages__/` (line 22) — harmless under uv but unused.

## Group 4 — Drop `[tool.pdm.scripts]` and update the husky pre-commit hook in lockstep

9. Remove the `[tool.pdm.scripts]` block from `pyproject.toml` (currently lines 59–63). After this edit, `pyproject.toml` has zero `[tool.pdm.*]` entries.
10. Edit `.husky/pre-commit` line 1: replace `pdm run lint` with `uv run poe lint`. Leave the `cd ui && npx lint-staged` line (UI-side hook) untouched. This commit must land atomically with the `[tool.pdm.scripts]` deletion so no developer commit fails between them.

## Group 5 — Rewrite the Dockerfile

11. Edit `Dockerfile` `py-deps` stage: replace lines 15–17 (`RUN pip install --no-cache-dir pipx && pipx install pdm==2.26.9` plus the `ENV PATH="/root/.local/bin:$PATH"`) with a single multi-stage copy `COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/`. Drop the `pipx` install entirely.
12. Edit `Dockerfile` line 19: change `COPY pyproject.toml pdm.lock ./` to `COPY pyproject.toml uv.lock ./`. Keep the `WORKDIR /app` (line 7) and the `RUN apt-get update … gcc libffi-dev` (lines 9–13) — these are still needed for any source-built wheels (cryptography, etc.) until proven otherwise by Group 7's CI green.
13. Edit `Dockerfile` lines 20–30: replace the entire `RUN /root/.local/bin/pdm install --prod --no-editable --no-self --frozen-lockfile && /app/.venv/bin/python -m ensurepip --upgrade && /app/.venv/bin/python -m pip install --upgrade --no-cache-dir pip setuptools wheel` block (and the comment at lines 20–27) with `RUN uv sync --frozen --no-install-project --no-dev`. The `ensurepip + pip/setuptools/wheel --upgrade` block goes away — uv's wheels avoid the bootstrap-pip CVE vector. Keep the runtime-stage `RUN python -m pip install --upgrade --no-cache-dir pip setuptools wheel` at line 40 (system pip, unrelated to PDM, with the existing comment block at lines 35–39 explaining why).
14. Confirm by inspection that lines 33+ (runtime stage) still consume the venv via `COPY --from=py-deps /app/.venv /app/.venv` (line 55) and `ENV PATH="/app/.venv/bin:$PATH"` (line 56). The `/app/.venv` shape is uv-default; no further runtime-stage edits required.

## Group 6 — Rewrite the CI workflow (`ci-backend.yml` only)

15. Edit `.github/workflows/ci-backend.yml` line 11 `paths:` filter: change `'pdm.lock'` to `'uv.lock'` so backend CI re-triggers on Dependabot's lock updates.
16. Edit `.github/workflows/ci-backend.yml` lint job (lines 28–32): replace `uses: pdm-project/setup-pdm@v4` with `uses: astral-sh/setup-uv@v8` (latest stable major as of 2026-05-20; tag pin matches the repo's existing `actions/checkout@v4` convention); pass `python-version: '3.11'` and `enable-cache: true`. Replace `run: pdm install --dev --frozen-lockfile` (line 35) with `run: uv sync --frozen`. Replace `run: pdm run lint` (line 38) with `run: uv run poe lint`.
17. Edit `.github/workflows/ci-backend.yml` test job (lines 46–50, 53, 58): apply the identical setup-uv swap, replace the install with `uv sync --frozen`, and replace `run: pdm run test` with `run: uv run poe test`. No marker filter, no exclude list — preserve current behaviour.

## Group 7 — Flip Dependabot ecosystem

18. Edit `.github/dependabot.yml` Python entry (currently lines 13–21): change `package-ecosystem: pip` to `package-ecosystem: uv`. Leave every other field unchanged (`directory: "/"`, `schedule.interval: daily`, `schedule.time: "23:00"`, `commit-message.prefix: "chore"`, `commit-message.include: "scope"`, `open-pull-requests-limit: 3`). Leave the `npm /ui` and `github-actions /` entries entirely untouched.

## Group 8 — Update developer-facing docs

19. Edit `DEVELOPMENT.md` Prerequisites section: remove the **PDM** install line (`curl -sSL https://pdm-project.org/install-pdm.py | python3 -`) and replace with the uv install line (`curl -LsSf https://astral.sh/uv/install.sh | sh`).
20. Edit `DEVELOPMENT.md` install section: replace `pdm venv create` and `pdm install --dev` with `uv sync` (uv creates the project-local `.venv` automatically; no separate `venv create` step).
21. Edit `DEVELOPMENT.md` "Running Tests" section: replace every `pdm run test …` invocation (currently lines 84–89) with the `uv run poe test …` equivalent, preserving the `--` argument-passthrough examples.
22. Edit `DEVELOPMENT.md` "Linting" section: replace `pdm run lint` and `pdm run lint:fix` (lines 104–110) with `uv run poe lint` and `uv run poe lint:fix`. Add a one-line note showing how to discover available poe tasks (`uv run poe`).

## Group 9 — Update agent-facing rules and SDD templates

23. Edit `.claude/rules/worktrees.md`: remove the `PDM_IGNORE_ACTIVE_VENV=1` invariant (lines 7–12 step 2 Python install paragraph and the lines 41–44 "Two env vars beyond port selection" bullet). Rewrite the host-mode launch backend block (lines 49–65) for `mkdir -p data; JENTIC_INTERNAL_PORT=8901 DB_PATH="$(pwd)/data/jentic-mini.db" uv run uvicorn src.main:app --port 8901 --reload` (no `PDM_IGNORE_ACTIVE_VENV=1` prefix). Step 2's Python install becomes `uv sync` at the worktree root.
24. Edit `.claude/rules/testing.md` line 21: replace `pdm run test …` with `uv run poe test …`.
25. Edit `.claude/rules/python-code-style.md` line 8: replace `pdm run lint:fix` with `uv run poe lint:fix`.
26. Edit `.claude/rules/update-tech-stack-on-deps.md` line 14 example list: replace `PDM` with `uv`.
27. Edit `.claude/skills/sdd-implement-spec/SKILL.md` (lines that mint `pdm run lint`/`pdm run test` into spec examples) and `.claude/skills/sdd-new-spec/SKILL.md` (same): replace each with the `uv run poe …` equivalent. Edit `.claude/templates/sdd/feature-spec/plan.example.md` line 22: replace `pdm run test tests/broker` with `uv run poe test tests/broker`.
28. Edit `.claude/settings.local.json`: replace the `Bash(pdm run *)` allowlist entry with `Bash(uv run *)` and replace the `Bash(PDM_IGNORE_ACTIVE_VENV=1 pdm update *)` entry with `Bash(uv sync *)` plus `Bash(uv lock*)`. Leave every other allowlist entry untouched.

## Group 10 — Update constitution and roadmap

29. Edit `specs/tech-stack.md` Core Stack table line 35: change `| Python packaging | PDM |` to `| Python packaging | uv |`.
30. Edit `specs/tech-stack.md` formatting/linting prose at line 92: change `PDM scripts: \`lint\`, \`lint:fix\`.` to `Poe tasks (\`[tool.poe.tasks]\`): \`lint\`, \`lint:fix\`, \`test\`.` Confirm by `grep -in pdm specs/tech-stack.md` — expect zero matches.
31. Edit `specs/roadmap.md` Phase 15 (Pyright) body bullets only: replace `[tool.pdm.dev-dependencies]` with `[dependency-groups] dev`; replace `[tool.pdm.scripts]` with `[tool.poe.tasks]`; replace `pdm run typecheck` with `uv run poe typecheck`. Do not change Phase 15's goal, priority, depends-on, or any other content.
32. Edit `specs/roadmap.md` Phase 29 heading at line 431: append ` ✅` (single space + U+2705) so the heading reads `## Phase 29 — Migrate Python Packaging from PDM to uv ✅`. Leave the rest of the Phase 29 block intact per the lifecycle rule (`specs/roadmap.md:35-40`); do not delete or renumber.

## Group 11 — Verify

33. `uv lock --check` exits 0 (lockfile is consistent with `pyproject.toml`).
34. `uv sync --frozen` exits 0 (clean install from frozen lockfile).
35. `uv run poe lint` exits 0 (ruff check + ruff format --check --diff both pass).
36. `uv run poe test` exits 0 (full backend pytest suite passes; equivalent to today's `pdm run test`).
37. `docker build -t jentic-mini:phase29 .` exits 0 (Dockerfile rewrite builds clean on the host architecture).
38. `git grep -nE "(pdm-project/setup-pdm|pdm install|pdm run|tool\.pdm)" -- ':!specs/2026-05-07-*' ':!specs/2026-05-08-*' ':!specs/2026-05-12-*'` exits 1 (no matches) — completed-spec history is excluded; everything else must be PDM-free.
39. `git grep -nE "PDM_IGNORE_ACTIVE_VENV" -- ':!specs/2026-05-07-*' ':!specs/2026-05-08-*' ':!specs/2026-05-12-*'` exits 1 (no matches outside frozen historical specs).
40. `test ! -f pdm.lock` and `test -f uv.lock` and `test ! -f .pdm-python` all exit 0.
41. `grep -nE '^\[build-system\]' pyproject.toml` exits 1 (no `[build-system]` block remains; uv runs in application mode per `[tool.uv] package = false`).
42. `grep -nE '^package = false' pyproject.toml` exits 0 (the `[tool.uv] package = false` declaration is present).
43. `grep -F "## Phase 29 — Migrate Python Packaging from PDM to uv ✅" specs/roadmap.md` exits 0 (phase-completion lifecycle marker present, single space before ✅).
