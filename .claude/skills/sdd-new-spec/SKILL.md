---
name: sdd-new-spec
description: Scaffold a feature spec for a roadmap phase and open it as a PR for human review. Reads specs/roadmap.md, lets the user pick a phase (or accepts one as argument), runs preflight checks, cuts a feature branch, writes specs/YYYY-MM-DD-<slug>/ (requirements.md, plan.md, validation.md — grounded in specs/mission.md and specs/tech-stack.md), commits, pushes, and opens a PR. Makes zero code changes — the PR is for review of the spec itself; implementation follows in a separate PR once the spec is approved. Groups clarifying questions (one per output file) via AskUserQuestion before any disk write.
argument-hint: "[phase-number | \"phase title fragment\"] (optional)"
---

# /sdd-new-spec — scaffold a feature spec for a roadmap phase

You are operating within a Spec-Driven Development (SDD) workflow. See `.claude/rules/sdd-constitution.md`.

The **constitution** (mission / tech-stack / roadmap) already exists in `specs/`. This skill takes one **phase** from `specs/roadmap.md` and turns it into a workable **feature spec** — a dated directory under `specs/` with three files: requirements (what + why), plan (how), validation (done).

## Inputs

Argument in `$ARGUMENTS` (optional):

- empty → list candidate phases and ask the user to pick
- integer (`1`, `7`) → use that phase number directly
- title fragment (`"local service routing"`) → case-insensitive match against phase titles; if ambiguous, list matches and ask

## Hard constraints

- **Spec-only — zero code changes.** This skill never modifies `src/`, `ui/`, `tests/`, `alembic/`, `docs/`, `AGENTS.md`, `CLAUDE.md`, or any implementation/doc file outside `specs/<date>-<slug>/`. It does not run test suites or linters against code. The only files it touches are the three it scaffolds. The PR it opens is for review of the spec itself; the implementation that the spec describes happens in a follow-up PR.
- **Do not write to disk before the Phase 4 AskUserQuestion step completes.** The grouped questions exist to lock in decisions that shape the files; writing early wastes the call.
- **AskUserQuestion groups exactly three questions**, one per output file (requirements, plan, validation). Issue them in a single call.
- **Do not create a new roadmap phase.** If the user wants one, tell them edits to `specs/roadmap.md` are a separate explicit action — this skill only materializes existing phases.
- **Ground every requirement and constraint in `specs/mission.md`, `specs/tech-stack.md`, or the roadmap phase itself.** Do not invent scope the sources do not support.
- **PR contains the spec, nothing else.** Staged set must be exactly the three files under `specs/<date>-<slug>/` before committing. If anything else appears, stop and surface it.

## Phase 0 — Preflight

Run these checks in parallel. Stop with a clear message on any failure — do not auto-fix, do not stash, do not force.

- `git status --porcelain` is empty (working tree clean)
- Current branch is `main`
- `git fetch origin main` succeeds; local `main` is not behind `origin/main`. If behind and fast-forwardable, offer `git pull --ff-only` and wait for user confirmation. If diverged, stop.
- `gh auth status` succeeds — fail fast here if `gh` is not installed or not authenticated; Phase 8 depends on it.

Then load context in parallel:

- @specs/mission.md
- @specs/tech-stack.md
- @specs/roadmap.md
- @specs/lessons.md
- @.claude/rules/git-workflow.md
- @.claude/rules/conventional-commits.md
- @.claude/rules/testing.md
- @.claude/rules/karpathy-guidelines.md

After loading, scan `specs/lessons.md` for any lessons that apply to the phase being scaffolded. Carry the relevant ones into Phase 4 (use them to shape `AskUserQuestion` options where appropriate) and Phase 6 (apply them when writing requirements / plan / validation content). Mention in Phase 9 which lessons influenced this scaffold. The lessons file is operational, not load-bearing: a lesson that does not apply to this phase is fine to skip — do not force-fit guidance.

## Phase 1 — Pick a roadmap phase

Parse phases from `specs/roadmap.md` — the `## Phase N — <title>` blocks. Ignore the `Later Phases (Not Yet Planned)` section.

For each phase extract: number, title, `**Goal:**`, `**Depends on:**`, `**Priority:**`, and a `completed` flag set when the heading ends with ` ✅` (space + U+2705 checkmark). **Active phases** (no `✅`) are the candidate pool; **completed phases** (✅) are skipped from selection but used to resolve `Depends on:` references.

**Parsing `**Depends on:**`** — the value is one or more comma-separated dependency references; trim each.

- A reference of the form `none` (optionally followed by a parenthetical rationale, e.g. `none (self-contained broker change)`) means the phase has no dependency; produce an empty list and skip resolution.
- For every other reference, strip any trailing ` (<rationale>)` parenthetical to recover the bare phase-title fragment, then resolve it case-insensitively against the headings parsed above via **substring match** (the fragment must appear inside one and only one heading). Substring matching handles short fragments like `Backend Unit Test Coverage` correctly even when the canonical heading is longer; if a fragment matches more than one heading, surface the ambiguity and stop.

A phase is a **candidate** when it is active and every resolved dependency carries `✅` on its heading (i.e. that dependency has shipped per the lifecycle rule). Active phases with still-pending dependencies (resolved phases whose heading lacks `✅`) are valid to pick but must be flagged. A reference that resolves to no phase at all — and is not the `none` form above — is a malformed roadmap; surface the mismatch and stop.

**If `$ARGUMENTS` identifies exactly one phase**, jump straight to showing that phase's full block and confirm with the user before continuing.

**Otherwise** present candidates as a compact list:

```
N. <title> — priority: <High|Medium|Low> — deps: <satisfied|pending: <names>>
   goal: <one-line goal>
```

Ask the user to pick one by number. Do not proceed until the user confirms.

If the user wants to change the phase's scope at this step, route them to update `specs/roadmap.md` first and re-run the skill.

## Phase 2 — Research in parallel (MANDATORY)

Launch **three `Explore` subagents in parallel** — a single message with multiple `Agent` tool calls, `subagent_type: Explore`, one per output file. Do not proceed to Phase 3 until all three return.

- **Thoroughness:** `very thorough` on every subagent. Feature specs are load-bearing; shallow grounding produces shallow specs.
- **Model:** pass `model: "opus"` in every `Agent` tool call. Max capability for grounding.
- **Briefs are self-contained.** Each subagent does not see this conversation. Include: phase number, phase title, the full roadmap-phase body copied verbatim from `specs/roadmap.md`, and a one-paragraph focus directive specific to its output file.

**Subagent A — Requirements research** (grounds `requirements.md`):
- Read `specs/mission.md` and `specs/tech-stack.md` for invariants / constraints this phase must preserve.
- Scan `docs/` for documents the phase directly relates to (e.g. `architecture.md`, `auth.md`, `workflows.md`, `decisions.md`).
- Scan recent git log for prior attempts at similar functionality or adjacent work.
- Return: constraints that MUST be preserved (one-line rationale each), relevant `docs/*.md` links, stakeholder-context signals, and any prior-context that reframes the phase.

**Subagent B — Plan research** (grounds `plan.md`):
- Map the `src/` modules, routers, migrations, and UI pages the phase will touch (based on the roadmap-phase body).
- Identify file-layout conventions (where new routers live, where tests live, where generated UI client code goes per `CLAUDE.md`).
- Find similar-shape features that have already shipped (git log + `src/`) as implementation references.
- Return: concrete file/module paths to touch or create, existing patterns to follow, the order in which layers depend on each other, and any gotchas (e.g. broker catch-all must remain last in `main.py`).

**Subagent C — Validation research** (grounds `validation.md`):
- Identify the test suites and CI workflows relevant to the areas touched (per `.claude/rules/testing.md` and `.github/workflows/`).
- Find existing endpoints / UI flows / trace-log inspections that will demonstrate the phase works end-to-end.
- Check whether `schemathesis` contract tests, `ui/openapi.json` regeneration, or Playwright E2E apply.
- Return: concrete test targets (`uv run poe test …`, `npm run test:run`, `npm run test:e2e`), curl / UI check commands with expected responses, and whether contract / E2E / migration gates apply.

**Rules:**
- Every brief must instruct the subagent to lead its summary with a `## Blockers` section (write `_none_` when there are none). Examples of blockers: the phase depends on an assumption that is false in the current code; prior work already landed and the phase is partly obsolete; a load-bearing file referenced by the phase body does not exist.
- Subagents are read-only (`Explore` type; they cannot edit, write, or commit).
- If any subagent surfaces a non-empty `## Blockers` section, stop and report to the user before Phase 3.
- Summaries from all three subagents feed Phase 4 (AskUserQuestion options) and Phase 6 (file content).

## Phase 3 — Derive slug, directory, and branch

Derive identifiers in order, checking for conflicts at each step **before** asking the user anything they could not act on.

1. **Slug** — kebab-case from the phase title, lowercase, alphanumerics and hyphens only. Drop leading `Phase N — `. Target ≤ 40 chars; strip filler words (`the`, `and`, `of`) only if over. Example: `Phase 1 — Local Service Routing` → `local-service-routing`.
2. **Date** — today's date as `YYYY-MM-DD` from the current environment (do not ask).
3. **Directory** — `specs/<date>-<slug>/`.
4. **Directory idempotence check** — if `specs/<date>-<slug>/` already exists, stop, show what's there, ask whether to reuse / rename / abort. Do this **before** the issue-number question so the user does not waste effort on a scaffold that cannot proceed.
5. **Branch prefix** — follow `.claude/rules/git-workflow.md`:
   - `feature/` by default
   - `fix/` if the phase goal describes a defect or starts with "Fix"
   - `chore/` for tooling / maintenance / dependency phases
   - `docs/` if the phase is purely documentation
6. **Issue number** — ask the user once: "Is there a GitHub issue for this phase? (issue number or 'no')". If yes, branch is `<prefix>/<issue>-<slug>`; else `<prefix>/<slug>`.
7. **Branch idempotence check** — if the target branch exists locally or on `origin`, stop, ask whether to switch to it / rename / abort.

## Phase 4 — AskUserQuestion (MANDATORY, before any disk write)

Issue a single `AskUserQuestion` call containing three questions, one per output file. Each question offers 3–5 concrete options plus a freeform "other" so the user can redirect. Frame each as a decision the scaffold needs locked-in.

**Question 1 — Requirements** (shapes `requirements.md`):
  - Prompt: "Any scope to explicitly exclude, external constraints, or up-front decisions to record?"
  - Options should cover common shapes (e.g. "no exclusions, follow roadmap bullets exactly", "exclude UI changes for now", "lock a specific naming choice", "external deadline or dependent team", "other").

**Question 2 — Plan** (shapes `plan.md`):
  - Prompt: "How should the plan be structured — ordering and granularity?"
  - Options should cover common shapes (e.g. "test-first", "skeleton-first then flesh out", "risky-bits-first to de-risk", "3–4 large groups", "6–10 small groups", "other").

**Question 3 — Validation** (shapes `validation.md`):
  - Prompt: "What is the primary acceptance signal, and are there merge gates beyond green CI?"
  - Options should cover common shapes (e.g. "integration test passing", "contract test (`schemathesis`) passing", "UI flow verified manually", "endpoint reproducible via curl", "docs + AGENTS.md must update", "other").

Only after the user answers do you proceed.

## Phase 5 — Cut the branch

```
git checkout -b <branch>
```

Do not push yet — Phase 8 handles push after the commit.

## Phase 6 — Write the three files

Create `specs/<date>-<slug>/`. Write three files using these templates as structural scaffolds — fill in the placeholders from the phase content and the user's answers. **Use templates for structure only; do not copy text verbatim.** Files are plain markdown — no YAML frontmatter.

- @.claude/templates/sdd/feature-spec/requirements.example.md
- @.claude/templates/sdd/feature-spec/plan.example.md
- @.claude/templates/sdd/feature-spec/validation.example.md

All three share an H1 of `# Phase <N> <Requirements|Plan|Validation> — <Phase Title>` (the phase title is the human-readable title from `specs/roadmap.md`, not the kebab slug).

### requirements.md

Sections in this order:

- **Scope** — one or two short paragraphs naming what this phase delivers. Written in plain language, informed by the user's Question 1 answer. Not a copy of the roadmap bullet list.
- **Out of Scope** — explicit exclusions the user confirmed. Empty is allowed — do not invent exclusions.
- **Decisions** — one `### <Decision Title>` subsection per decision from the user's answers. Body is a short paragraph: what was chosen, why, and any consequence for implementation (alternatives noted if weighed).
- **Constraints** — load-bearing invariants from `specs/mission.md` and `specs/tech-stack.md` that this phase must preserve. Only what is actually relevant (e.g. "broker catch-all must be registered last" only if the work touches router registration). Do not copy every invariant.
- **Context** — one to three short paragraphs: why this phase exists now, what it enables, how it fits the roadmap. Cross-reference any relevant `docs/*.md`.
- **Stakeholder Notes** — `- **<Name or Role>** — <need; how satisfied>`. Omit the whole section if the user named no stakeholders.

### plan.md

Numbered task groups. Granularity per the user's Question 2 answer.

**Task numbering is sequential across all groups** (1, 2, 3, … N), not reset per group:

```
## Group 1 — <Title>
1. <Concrete task>
2. <Concrete task>

## Group 2 — <Title>
3. <Concrete task>
4. <Concrete task>
```

- Tasks are plain numbered items, **not checkboxes**. One concrete change each (file / function / test / route / migration / CLI command).
- **The last group is always `Verify`**, listing the concrete checks that confirm the whole plan succeeded: command + expected result (e.g. `uv run poe test tests/broker` exits 0; `curl localhost:8900/…` returns 200 with JSON containing `{ … }`).
- **Include the roadmap-completion task** as a concrete numbered task in the final non-Verify group (typically a docs/lifecycle group). The task says to append ` ✅` — a single space followed by the U+2705 checkmark — to the `## Phase N — <Title>` heading in `specs/roadmap.md` and leave the rest of the block untouched, per the lifecycle rule in `specs/roadmap.md`. The leading space is load-bearing: the Verify assertion `grep -F`s for the exact ` ✅` suffix, so a heading rendered as `Title✅` (no space) silently fails the gate. Phase-completion marking is a phase-specific markdown edit that ships with the work, not generic meta-workflow — `/sdd-implement-spec` halts on plans that omit it. Pair it with an assertion in the Verify group that confirms the heading now ends with ` ✅` (e.g. `grep -F "## Phase <N> — <Title> ✅" specs/roadmap.md` exits 0).
- **Do not include meta-workflow** in the plan (test-suite runs belong in `Verify`; squash/commit/PR creation are governed by `.claude/rules/git-workflow.md` and `.claude/rules/conventional-commits.md` — no need to repeat per phase).

Do not pad. If three groups plus `Verify` cover the work, that is correct.

### validation.md

Structure:

- **Definition of Done** — opening sentence: "All of the following must be true before this branch is merged."
- Numbered subsections `### <N>. <Check Title>` — each contains either a fenced code-block command and an exact expectation (HTTP status, exit code, output substring, file contents), or a short description of a non-command check (e.g. `tsconfig.json` must contain `"strict": true`). Be concrete: "HTTP 200, body contains `<h1>Jentic</h1>`" — not "endpoint works".
- **Not Required** — final section. Explicit list of what this phase does **not** need (no automated tests for this phase, no CI pipeline required, no browser check, etc.) — matches what the user flagged in Question 3. Prevents scope creep and reviewer confusion.

## Phase 7 — Commit the spec files

Atomic commit per `.claude/rules/git-workflow.md`.

- Stage **only** the three files under `specs/<date>-<slug>/`. Do **not** use `git add -A` or `git add .` — name the three paths explicitly.
- Safety check: `git diff --cached --name-only` must list exactly three paths, all inside `specs/<date>-<slug>/`. If anything else appears, abort and surface it — the skill does not commit code.
- Commit with `git commit -s` (DCO sign-off) and a Conventional Commits header per `.claude/rules/conventional-commits.md`:
  - Type `docs`, scope `spec`
  - Header: `docs(spec): scaffold phase <N> — <short-slug>` (≤ 69 chars total; truncate the slug if needed)
  - Body: one short paragraph naming what the spec covers and linking the roadmap phase. Include `Refs #<issue>` if the user provided an issue number. **Do not** use GitHub close-keywords (`Closes`, `Fixes`, `Resolves`) — this PR does not resolve the phase.

## Phase 8 — Push and open the PR

- `git push -u origin <branch>`
- Open a PR with `gh pr create`. Pass the body via a HEREDOC to preserve formatting. Title is the commit header. Body shape:

  ```
  ## Summary

  Spec scaffold for **Phase <N>: <title>** from `specs/roadmap.md`.

  This PR adds **spec files only** — no code changes. Implementation follows in a separate PR once this spec is approved.

  ### Scope
  [one-paragraph summary pulled from the Scope section of requirements.md]

  ### Out of Scope
  - [bullets from requirements.md, or "none declared"]

  ### Key decisions
  - [one bullet per `###` decision title in requirements.md]

  ## Files

  - `specs/<date>-<slug>/requirements.md`
  - `specs/<date>-<slug>/plan.md`
  - `specs/<date>-<slug>/validation.md`

  ## Review guidance

  Reviewers should verify:
  - The phase goal is captured faithfully (see `specs/roadmap.md` Phase <N>).
  - Scope, constraints, and decisions match intent.
  - The plan's task groups are appropriately granular and each has a concrete verification step.
  - Validation gates are realistic for the phase.

  Refs: `specs/roadmap.md` Phase <N>[; #<issue> if applicable].
  ```

- Do **not** include `Closes #<issue>` / `Fixes #<issue>` in the PR body — only `Refs`. The phase closes on the implementation PR, not this one.

## Phase 9 — Report back

Return to the user in a few lines:

- Phase chosen (number + title)
- Branch, commit SHA, PR URL
- Files created (full paths)
- Next step: human review on the PR. Once merged, the implementation work described in `plan.md` happens in a separate branch/PR — this skill does not execute it.