# Phase 29 Requirements — Migrate Python Packaging from PDM to uv

## Scope

Replace PDM with [astral-sh/uv](https://docs.astral.sh/uv/) as the project's Python package manager and replace `[tool.pdm.scripts]` with `[tool.poe.tasks]` (run via `poethepoet`) so task invocation becomes `uv run poe <task>`. jentic-mini is Docker-distributed and never wheelable, so delete the `[build-system]` block entirely and add `[tool.uv] package = false` to declare the project an application — uv resolves and installs dependencies into `.venv` without invoking any PEP 517 backend. Replace `pdm.lock` with `uv.lock` and rewrite every consumer of PDM commands across the repo: `Dockerfile`, CI workflows, `.github/dependabot.yml`, `DEVELOPMENT.md`, the `.husky/pre-commit` hook, every `.claude/rules/*.md` rule that names PDM, the SDD skill templates that mint future-spec examples, the agent-harness allowlist, and `specs/tech-stack.md`. Mark Phase 29 ✅ in `specs/roadmap.md` on completion.

The migration is a hard cutover — no PDM-also-works fallback, no shim layer. The single behavioural change a user can observe is that `package-ecosystem: uv` in `.github/dependabot.yml` re-enables Dependabot's daily Python bumps, which have been silently inert for ~4 weeks because Dependabot's pip parser short-circuits on `pdm.lock` and the upstream PDM-side fix in dependabot-core ([PR #12435](https://github.com/dependabot/dependabot-core/pull/12435)) was abandoned on 2026-05-19.

## Out of Scope

- Adding new tests, behavioural changes to `src/**` or `ui/**`, or any backend/UI feature work
- Switching the runtime base image (`python:3.11-slim`), the non-root `jentic` user model, or the multi-stage Dockerfile shape
- Introducing Pyright / `pdm run typecheck` (Phase 15 territory — Phase 29 only neutralizes Phase 15's stale PDM references, it does not implement type-checking)
- Adding a workspace layout (`[tool.uv.workspace]`) — `jentic-mini` remains a single-package project
- PyPI publication wiring — the project is Docker-distributed; application mode (`[tool.uv] package = false`) means no wheel is ever built. Future PyPI publishing would re-introduce a `[build-system]` block, but that is not in scope here.
- Reintroducing any lock-sync workflow (the previous `dependabot-pdm-lock.yml` was deleted by #417 and must stay deleted; uv updates `uv.lock` atomically inside the same Dependabot PR)
- Retroactive edits to `specs/2026-05-07-…`, `specs/2026-05-08-…`, `specs/2026-05-12-…` — completed-spec history is read-only
- Updating `docs/decisions.md` — there is no PDM ADR there; only an unrelated `auth_type` entry

## Decisions

### Application mode, no PEP 517 backend

jentic-mini is Docker-distributed and never wheelable — no PyPI publish, no sdist, no consumer of `uv build` exists today. The Dockerfile already runs `uv sync --frozen --no-install-project --no-dev` (Group 5), which installs `[project.dependencies]` + `[dependency-groups] dev` without invoking any PEP 517 backend. Adopt `[tool.uv] package = false` to declare jentic-mini an application and delete the entire `[build-system]` block; uv treats the project as application-only and never asks a backend for help. This sidesteps the `src/`-layout / project-name mismatch entirely (default `uv_build` discovery looks for `src/jentic_mini/` based on the project name, which does not exist; modules actually import as `from src.foo import bar`). The simpler shape removes a load-bearing risk from the migration: no wheel ever has to be built or proven, no `[tool.uv.build-backend]` override is needed, no hatchling fallback is needed. If a future phase adds PyPI publishing, flip `package = false` back and choose a backend at that time — that's a one-line change.

### Maximalist sweep over the roadmap-body's bullet list

The phase body in `specs/roadmap.md` enumerates the obvious targets but omits PDM consumers that would silently break post-migration: `.husky/pre-commit:1` invokes `pdm run lint` on every commit; `.claude/rules/python-code-style.md` and `.claude/rules/update-tech-stack-on-deps.md` reference PDM; `.claude/skills/sdd-implement-spec/SKILL.md`, `.claude/skills/sdd-new-spec/SKILL.md`, and `.claude/templates/sdd/feature-spec/plan.example.md` mint stale `pdm run …` examples into every future spec; `.claude/settings.local.json` allowlists `Bash(pdm run *)`; `.pdm-python` lives at the repo root and `.gitignore` carries a stale `.pdm-python` entry. All in-scope. The validation Verify group greps the tracked tree to confirm none survive.

### Phase 15's roadmap entry must be neutralized

`specs/roadmap.md` Phase 15 (Pyright) currently instructs the future implementer to add pyright to `[tool.pdm.dev-dependencies]` and create a `pdm run typecheck` script. Leaving these references stale would ship contradictory instructions in the same `roadmap.md` that this PR rewrites elsewhere. Phase 15's body is rewritten in-place here — only the bullets that name PDM mechanics (`[tool.pdm.dev-dependencies]` → `[dependency-groups] dev`; `[tool.pdm.scripts]` → `[tool.poe.tasks]`; `pdm run typecheck` → `uv run poe typecheck`). Phase 15's goal, priority, and substantive content stay identical; this is a mechanical translation, not a re-scoping.

### `.python-version` is not added in this phase

uv resolves the interpreter floor from `[project] requires-python = ">=3.11"`; `astral-sh/setup-uv` accepts an explicit `python-version` input and the Dockerfile's `python:3.11-slim` base image pins 3.11 directly. Adding a `.python-version` file would be optional symmetry with the sister-repo and is reserved for a follow-up if uv-managed Python provisioning becomes useful.

### Recommended uv installation method per context

uv ships in three forms; this phase picks one per context to match where the install actually runs:

- **Developer machines (`DEVELOPMENT.md` Prerequisites)** — the official standalone installer, `curl -LsSf https://astral.sh/uv/install.sh | sh` (Linux/macOS) or `powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"` (Windows). Recommended by Astral as the default path; no Python prerequisite, no `pipx`/`pip` bootstrap, self-updates via `uv self update`. Plan task 19 wires this into `DEVELOPMENT.md`.
- **Docker `py-deps` stage** — `COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/`. Pulls the static binary out of the official OCI image — no curl, no shell installer, no extra layer for `pipx`. This is the pattern Astral documents for Dockerfiles and matches what jentic-openapi-tools uses. Plan task 11 wires this in.
- **GitHub Actions** — `astral-sh/setup-uv@v8` with `enable-cache: true`. The official Action handles tool-cache placement and lets uv reuse the GHA cache across runs. Plan tasks 16–17 wire this in.

Homebrew (`brew install uv`) and `pipx install uv` are valid alternatives for developer machines but are not recommended here — the standalone installer is the documented default and is what the rest of the migration assumes.

`DEVELOPMENT.md`, `.claude/rules/worktrees.md`, `.claude/rules/testing.md`, and the other doc edits rewrite cleanly — no "previously PDM, now uv" footnotes. Git history is the searchability backstop. This matches the team's `karpathy-guidelines.md` rule "no backwards-compatibility hacks".

## Constraints

Load-bearing invariants from `specs/mission.md` and `specs/tech-stack.md` that this phase must preserve.

- **Python 3.11+ floor** (`specs/tech-stack.md:18,32`; `pyproject.toml requires-python = ">=3.11"`) — the new `astral-sh/setup-uv` step and Dockerfile must keep targeting 3.11. uv must not silently bump the interpreter.
- **Container is the deployment shape** (`specs/tech-stack.md:20,38`; `specs/mission.md` Current State) — the Dockerfile's three-stage layout (UI build → py-deps → runtime) and runtime user (`jentic`) must survive intact; uv replaces PDM **inside** the existing stage, not by collapsing stages.
- **Multi-arch publish (`amd64`, `arm64`)** (`specs/tech-stack.md:99`) — uv's official static binaries cover both arches, but the Dockerfile change must be exercised on `linux/arm64` (recent commit `567e28c` shows arm64 is historically fragile to packaging-tool changes).
- **`semantic-release` mutates `[project] version`** (`.releaserc.json` `prepareCmd`) — the regex `s/^version = .*/version = "${nextRelease.version}"/` must still match. Application mode (`[tool.uv] package = false`) leaves `[project] version` untouched, but the migration must not move it into a `[tool.uv]` block or similar.
- **Ruff config and `tests/conftest.py` per-file-ignores** (`pyproject.toml:41-58`) — the lint/test invocations under poe must produce identical ruff behaviour and preserve `[tool.ruff.lint.per-file-ignores]` verbatim.
- **`[tool.poe.tasks].test` env must set `PYTHONPATH = "."`** — preserves the `from src.foo import bar` test-import contract that `tests/conftest.py` relies on (carries over from PDM's `test.env = { PYTHONPATH = "." }`).
- **No reintroduction of a lock-sync workflow** — `.github/workflows/dependabot-pdm-lock.yml` was deleted by #417; uv updates `uv.lock` inside the same Dependabot PR, so the migration must not add a new sync workflow.
- **Trivy/CodeQL security scans must stay green** (`.github/workflows/docker-security.yml`, `codeql.yml`) — the Dockerfile rewrite drops the PDM-stage `ensurepip + pip/setuptools/wheel --upgrade` block (which existed to clear CVE noise inside the PDM-managed venv). Trivy must show no regression on the new image; the runtime-stage `pip install --upgrade pip setuptools wheel` (system pip, unrelated to PDM, comment block at `Dockerfile:35-39`) survives.
- **`tests/conftest.py` must set `DB_PATH` before any `src.*` import** (`specs/tech-stack.md:83`) — unaffected by this phase but listed to confirm we're not touching test bootstrap.
- **Roadmap lifecycle: completed phases retain their block, append ` ✅` (single space + U+2705)** (`specs/roadmap.md:35-40`) — the leading space is load-bearing because the Verify grep is `grep -F "## Phase 29 — Migrate Python Packaging from PDM to uv ✅"`; `Title✅` (no space) silently fails.

## Context

Phase 29 exists because Dependabot has been silently un-bumping every Python dependency for ~4 weeks. Dependabot's pip parser sees `pdm.lock` alongside PEP 621 fields and short-circuits — no PRs are opened. The two Python CVE bumps in the silent window (`fix(deps): bump idna from 3.13 to 3.15` (#415, May 20) and `chore(deps): bump urllib3 from 2.6.3 to 2.7.0` (#387, May 12)) were both human-authored, and the upstream PDM-ecosystem fix in `dependabot/dependabot-core#12435` was abandoned by its author on 2026-05-19. uv has a first-class Dependabot ecosystem that reads `[project.dependencies]` and `[dependency-groups]`, updates `uv.lock` atomically in the same PR, and does not require a custom lock-sync workflow.

The migration was preceded by #417 (`ci(dependabot): align auto-merge with app token and drop dead lock-sync`), which deleted the never-triggered `dependabot-pdm-lock.yml`, and #418 (`docs(roadmap): add phase 29 — migrate from pdm to uv`), which froze the scope. After Phase 29 lands, Dependabot's daily 23:00 UTC schedule resumes opening Python PRs against `pyproject.toml`, and they auto-merge through the existing ecosystem-agnostic `dependabot-merge.yml` (which uses `dependabot/fetch-metadata@v3` and works the same for `package-ecosystem: uv`).

The phase touches no documentation outside the developer-and-agent-facing surfaces it explicitly names. `docs/architecture.md`, `docs/auth.md`, `docs/oauth-broker.md`, `docs/decisions.md`, `docs/versioning.md`, `AGENTS.md`, and `README.md` have zero PDM mentions and are not edited.

## Stakeholder Notes

- **Dependabot** — the migration's positive stakeholder. After merge, the next daily cycle should open the first Python bump PR; absence of one within ~24h of merge is a regression to investigate. Not a merge gate (Dependabot only fires after the config lands on `main`).
- **Self-hosters pulling `jentic/jentic-mini:latest` / `ghcr.io/jentic/jentic-mini:latest`** — change is opaque: same port, env-vars, volumes, entrypoint. Image is smaller (no pipx + pdm bootstrap layer).
- **Contributors with PDM-managed `.venv`** — `uv sync` reuses `.venv/` cleanly; `DEVELOPMENT.md` rewrite is the canonical signal. Worktree contributors lose the `PDM_IGNORE_ACTIVE_VENV=1` invariant entirely (uv does not inherit `VIRTUAL_ENV` the same way).
- **Agents running `pdm run …` inline** — the `.claude/settings.local.json` allowlist is updated in-scope; agents will not hit permission prompts post-merge.
- **CI maintainers** — `setup-pdm`'s GHA cache key changes to `setup-uv`'s; one-time cache miss; subsequent runs benefit from uv's per-module wheel cache.
