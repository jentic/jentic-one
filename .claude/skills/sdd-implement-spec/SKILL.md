---
name: sdd-implement-spec
description: Implement an existing feature spec end-to-end — pick an unprocessed feature spec (one whose `## Phase N — ...` heading in `specs/roadmap.md` does not yet carry the `✅` lifecycle marker), cut a feature branch, walk `plan.md` task groups in order with one primary atomic Conventional-Commits commit per group (plus optional small fix-up commits during verification or pre-push review), run `plan.md`'s Verify group plus every check in `validation.md`, then run a pre-push review pairing the built-in `/review` skill with three parallel deep-review subagents (spec-adherence, code-quality, risk-and-robustness) before pushing and opening the PR. The spec is read-only during implementation; if it is wrong or incomplete, the skill stops and surfaces the gap rather than patching the spec mid-flight. Reports implementation, review, and verification results at the end. When `$ARGUMENTS` is empty, enumerates unprocessed specs via AskUserQuestion.
argument-hint: "[phase-number | slug-fragment | spec-dir-path] (optional)"
---

# /sdd-implement-spec — implement a feature spec

You are operating within a Spec-Driven Development (SDD) workflow. See `.claude/rules/sdd-constitution.md`.

This skill takes one **unprocessed feature spec** (a `specs/YYYY-MM-DD-<slug>/` directory whose `## Phase N — ...` heading in `specs/roadmap.md` does not yet carry the `✅` lifecycle marker) and drives the work end-to-end: cuts the feature branch, walks `plan.md` task groups, runs the verification gates, commits atomically per group, runs a pre-push review (built-in `/review` plus three parallel deep-review subagents), pushes, opens a PR, and reports back on implementation, review, and verification.

The skill **drives** implementation — it is not merely scaffolding around it. The actual code changes happen in the main loop guided by `plan.md`. The spec itself is read-only.

## Inputs

Argument in `$ARGUMENTS` (optional):

- empty → enumerate unprocessed specs and pick via AskUserQuestion
- integer (`24`, `25`) → spec whose `requirements.md` H1 starts with `# Phase <N>`
- slug fragment (`"oauth-broker"`) → case-insensitive contains match against spec dir slugs (the portion after the date prefix); if ambiguous, list matches and ask
- relative path (`specs/2026-05-07-google-oauth-broker-token-mode`) → use directly; verify it exists and is a valid spec dir

## Hard constraints

- **The spec is read-only during implementation.** Do not edit `specs/<dir>/requirements.md`, `plan.md`, or `validation.md`. If the spec is wrong, incomplete, or contradicts current code, stop and surface the gap; the user owns spec edits and may re-run the skill after revising.
- **`plan.md` is the source of truth for ordering and scope.** Walk groups sequentially. Each non-Verify group produces one primary atomic commit; verification (Phase 7) may add small fix-up commits if a check fails. Tasks within a group can interleave as needed for the change to make sense.
- **`validation.md` is the source of truth for done.** Every numbered check must pass before opening the PR. If a check fails and cannot be fixed without changing the spec, stop and surface it.
- **Roadmap completion marking is part of `plan.md`.** The convention is for `plan.md` to include "Append `✅` to the `## Phase N — <Title>` heading in `specs/roadmap.md`" as a numbered task in its final docs/lifecycle group; respect it. If `plan.md` does not include the completion-marking task, surface the gap before starting — do not improvise it.
- **Conventional Commits + DCO sign-off.** Per `.claude/rules/conventional-commits.md` and `.claude/rules/git-workflow.md`. Every commit `git commit -s`; header ≤ 69 chars; type+scope reflect the group's primary subject; lowercase imperative description, no trailing period.
- **Atomic, surgical commits.** Per `.claude/rules/git-workflow.md` and `.claude/rules/karpathy-guidelines.md`: one logical change per commit (applies equally to primary group commits and Phase 7 verification fix-ups), touch only what the change requires, do not improve adjacent code.
- **No destructive git.** No `--force`, no `reset --hard`, no `--no-verify`. If a pre-commit hook rejects the commit, no commit was created — fix the underlying issue, restage, and retry the same `git commit` (don't add a duplicate). The "never amend" rule applies *after* a commit already exists and you discover a problem; in that case add a fix-up commit on top instead of amending.
- **Stage explicit paths.** Never `git add -A` or `git add .` — name the files the group touched. After staging, verify `git diff --cached --name-only` lists only those paths.
- **Ask the user when work surfaces a real decision.** During Phase 6 (implementation) and Phase 7 (verification), if a task surfaces a choice the spec doesn't lock down — multiple valid approaches a reasonable engineer would weigh, an adjacent change the spec didn't anticipate, drift between spec and code that has more than one reasonable resolution, ambiguous validation expectations — use `AskUserQuestion` to surface it inline rather than picking silently or halting outright. Halting is for genuine blockers; questions are for genuine choices. The user is the source of truth when the spec isn't.
- **Track spec deviations and draft a retrospective.** Maintain a running list across Phases 6, 7, and 8 of any concrete divergence between what the spec said and what the implementation had to do. Sources of deviations: file/line targets in `plan.md` that drifted, scope the spec missed but the work required, validation checks that needed clarification, fix-up commits that revealed a gap. Each tracked item is one sentence naming the spec file (`requirements.md` / `plan.md` / `validation.md`), the section, and what changed in practice. Phase 9 emits this list in the PR body's `## Spec deviations` section; Phase 8.5 commits a `specs/<date>-<slug>/retrospective.draft.md` on the branch so the reviewer has a structured starting point. The draft is provisional — the reviewer promotes it to `retrospective.md` (edit + rename) or deletes it before merge. Root cause and lessons are left as placeholders; the human fills them in. See `.claude/rules/sdd-constitution.md` (post-implementation feedback loop).

## Phase 0 — Preflight

Run in parallel:

- `git status --porcelain` empty
- Current branch is `main`
- `git fetch origin main` succeeds; local `main` not behind `origin/main`. If behind and fast-forwardable, offer `git pull --ff-only` and wait for confirmation. If diverged, stop.
- `gh auth status` succeeds — fail fast here; Phase 9 depends on it.

Load context in parallel:

- @specs/mission.md
- @specs/tech-stack.md
- @specs/roadmap.md
- @.claude/rules/sdd-constitution.md
- @.claude/rules/git-workflow.md
- @.claude/rules/conventional-commits.md
- @.claude/rules/testing.md
- @.claude/rules/karpathy-guidelines.md

## Phase 1 — Enumerate unprocessed specs and pick

A spec is **unprocessed** when:

1. `specs/YYYY-MM-DD-<slug>/` exists with `requirements.md`, `plan.md`, and `validation.md` present
2. Its `# Phase <N> Requirements — ...` H1 names a phase number
3. The matching `## Phase <N> — ...` heading in `specs/roadmap.md` does **not** end with `✅` (lifecycle: shipped phases keep their block but get `✅` appended to the heading)

**`$ARGUMENTS` is matched only against unprocessed specs.** If a fragment or path resolves to a spec dir whose phase heading in `specs/roadmap.md` already carries `✅`, surface it explicitly ("`<dir>` matched but Phase <N> has already shipped — heading marked `✅` in roadmap") and stop. If a fragment or path resolves to a spec dir whose phase is missing from `specs/roadmap.md` altogether, surface that separately ("`<dir>` matched but Phase <N> has no heading in roadmap — roadmap may be malformed") and stop. Do not silently fall back to fresh enumeration.

**If `$ARGUMENTS` resolves to exactly one unprocessed spec**, show it (phase number + title + dir + one-line goal from roadmap) and ask the user to confirm before continuing.

**If `$ARGUMENTS` resolves to multiple unprocessed specs** (e.g. ambiguous slug fragment), issue an `AskUserQuestion` call with one option per match — same shape as the empty-args enumeration below (`header: "Spec"`, options carrying `label` + `description`). Do not silently choose the first.

**If `$ARGUMENTS` resolves to zero unprocessed specs** (e.g. integer doesn't match a spec dir, slug fragment matches nothing, path doesn't exist), surface the mismatch with the candidate list and stop. Do not silently fall through to enumeration.

**If `$ARGUMENTS` is empty**, enumerate all unprocessed specs and present them by count:

- 0 → stop with a one-line summary; nothing to implement
- 1 → show it (phase + title + dir + goal) and ask the user to confirm
- 2–4 → issue a single `AskUserQuestion` call with one option per spec
- 5+ → issue `AskUserQuestion` with the 3 lowest-numbered specs (oldest pending work first); the automatic freeform write-in lets the user specify any other phase number or slug

The `AskUserQuestion` call uses `header: "Spec"` (max 12 chars, per the schema) and question text like `"Which unprocessed spec should I implement?"`. Each option provides only `label` and `description`:

- `label`: `Phase <N> — <Title>` (truncate the title if needed; full title appears in the description)
- `description`: `<spec-dir>; <one-line goal from roadmap>`

**Freeform write-in handling.** Every `AskUserQuestion` call gets an automatic freeform "Other" option. If the user types a phase number or slug there (whether in the multi-match, the 2–4-spec, or the 5+-spec branch above), treat the freeform value as a fresh `$ARGUMENTS` and re-run the matching rules at the top of this phase. If it still doesn't resolve to exactly one unprocessed spec, ask again.

If the chosen phase has unsatisfied dependencies (per `Depends on:` in `specs/roadmap.md` — any named phase whose heading does NOT yet carry `✅` is unshipped), warn with the specific dependencies and ask the user to confirm before continuing.

**Spec age check.** Run `git log -1 --format=%ai -- specs/<dir>` for the chosen spec. If the spec was last touched more than ~14 days ago, warn the user that `plan.md`'s file/line references may have drifted from current `main` and ask whether to proceed, refresh the spec first, or abort. Heuristic only — never block on age alone.

## Phase 2 — Load and parse the spec

Load in parallel:

- `specs/<dir>/requirements.md`
- `specs/<dir>/plan.md`
- `specs/<dir>/validation.md`

Parse `plan.md`:

- Extract `## Group <N> — <Title>` blocks in order
- Extract numbered tasks under each (sequential numbering across all groups, per the scaffolding convention)
- Identify the final group as the Verify group. Per `sdd-new-spec`'s template the last group is always named `Verify` and contains command-style verification tasks (no code changes). If a future spec uses a different name but the content is still command-style, treat it as Verify. If the final group contains code-change tasks the spec is malformed against the SDD convention — stop and report; do not improvise alternative control flow for Phases 6 and 7.
- Locate the **roadmap-completion task** — a numbered task in the final docs/lifecycle group whose body says to append ` ✅` (a single space followed by the U+2705 checkmark) to the `## Phase N — <Title>` heading in `specs/roadmap.md`. The space matters: Verify assertions `grep -F` for the exact ` ✅` suffix, so `Title✅` (no space) would silently fail.

Parse `validation.md`:

- Extract `### <N>. <Check Title>` numbered subsections under `## Definition of Done`
- Each subsection contains either a fenced command + expectation, or a structural assertion (file contents, presence of a row, etc.)
- Note the trailing `## Not Required` section — surfaced in the PR body's "Out of scope" subsection so reviewers see what's deliberately deferred

If parsing fails (no groups, no validation checks, missing roadmap-completion task in `plan.md`, malformed structure), stop and report what couldn't be parsed. Do not improvise around a malformed spec.

## Phase 3 — Derive branch name and check idempotence

Slug: the trailing portion of the spec dir name after the `YYYY-MM-DD-` prefix (e.g. `2026-05-08-reverse-proxy-path-prefix-support` → `reverse-proxy-path-prefix-support`).

Branch prefix per `.claude/rules/git-workflow.md`:

- `feature/` (default)
- `fix/` if the phase title starts with "Fix" or the goal describes a defect
- `chore/` for tooling / maintenance phases
- `docs/` for pure documentation phases
- `test/` for phases that are purely test coverage with no production-code changes (e.g. "Backend Unit Test Coverage")

Ask the user once: "Is there a GitHub issue for this phase? (issue number, or 'no')". If yes, branch = `<prefix>/<issue>-<slug>`; else `<prefix>/<slug>`.

Branch idempotence: if the target branch already exists locally or on `origin`, stop and ask whether to switch to it / rename / abort. Resuming a partially-implemented branch is out of scope for this skill — surface the situation and let the user decide.

## Phase 4 — Cut the branch

```
git checkout -b <branch>
```

Do not push yet — Phase 9 handles push after the final commit (Phase 8 runs the pre-push review first).

## Phase 5 — Seed TaskCreate from plan.md groups

Use `TaskCreate` to create one task per `## Group <N> — <Title>` block (**including** the final Verify group), plus one trailing synthetic task labeled `Pre-push review` for Phase 8. Each plan-group task's description is the group title.

This gives the user a live progress view while the skill walks groups. Phase 6 (non-Verify groups), Phase 7 (Verify group), and Phase 8 (pre-push review) own their own `in_progress` / `completed` transitions; do not pre-mark them in Phase 5.

## Phase 6 — Implement: walk non-Verify groups

For each `## Group <N> — <Title>` in order, **excluding** the final Verify group:

1. `TaskUpdate` the group task → `in_progress`.
2. Implement the numbered tasks within the group:
   - Match existing code patterns; surgical changes only (per `.claude/rules/karpathy-guidelines.md`).
   - Use the file/line references in `plan.md` as authoritative starting points. Minor drift (line numbers off by a few from intervening commits) is fine and silent; if a referenced file or symbol is clearly absent or has been replaced wholesale, stop and surface — do not improvise around the spec.
   - Do not add scope the spec did not call for. If the work obviously requires an adjacent change the spec missed, surface it and ask before adding.
3. After the group's tasks are implemented, run any group-relevant local gates per `.claude/rules/testing.md` (scoped to the area touched if practical):
   - Backend changes: `uv run poe lint` and `uv run poe test` (target a subset like `uv run poe test tests/<area>` when the change is narrow)
   - UI changes: `cd ui && npm run lint && npx tsc --noEmit && npm run test:run` — chained so all three run inside `ui/`. Use `test:run`, not `test` (`test` is watch mode and won't exit).
4. Stage the files this group touched by explicit path. Run `git diff --cached --name-only` and confirm only the expected paths appear; if anything else is staged, stop and surface.
5. Commit per `.claude/rules/conventional-commits.md`:
   - Header `<type>(<scope>): <description>` ≤ 69 chars; type/scope reflect the group's primary subject (e.g. `feat(broker): support reverse-proxy path prefix`, `test(root_path): add three-mode integration coverage`)
   - Body: short paragraph naming what shipped. Include `Refs #<issue>` if the user provided one. **Do not** use GitHub close-keywords (`Closes`, `Fixes`, `Resolves`) here — they belong only in the PR body.
   - `git commit -s` (DCO sign-off)
6. `TaskUpdate` the group task → `completed`.

If a pre-commit hook rejects the commit, no commit was created — fix the underlying issue, restage, and re-run the same `git commit` command. Don't add a duplicate. Never `--amend`, never `--no-verify`.

If implementation hits an obstacle mid-group, decide first whether it's a **genuine blocker** (no path forward without spec changes) or a **decision** (more than one reasonable resolution exists). For decisions, use `AskUserQuestion` per the hard constraint and continue based on the answer. For genuine blockers (a task is impossible against current code, an external dependency is missing, an assumption in `plan.md` is false), stop immediately and report:

- Which group / numbered task halted
- The specific error or unmet condition
- Last successful commit SHA on the branch
- Recommended next step (typically: revise `plan.md`, fix locally outside the spec, or abort and re-plan)

The roadmap-completion task identified in Phase 2 lives inside one of the groups (typically the final docs/lifecycle group, before Verify). Implement it as part of that group's commit — do not split it into its own commit unless the spec instructs otherwise. The change is mechanical: append ` ✅` (space, then `✅`) to the `## Phase N — <Title>` heading in `specs/roadmap.md`; leave the rest of the block in place.

## Phase 7 — Run plan.md Verify group + validation.md

`TaskUpdate` the Verify group task → `in_progress`.

`plan.md`'s Verify group and `validation.md`'s Definition-of-Done often overlap (e.g. both say `uv run poe lint`). For overlapping commands, run each unique command **once** and mark both gates satisfied for that command — don't re-run a passing test just because it's listed in two places.

**Run `plan.md`'s Verify group commands** in order. These are local-gate verifications, not code changes — they produce no commit. Capture exit codes and key output lines for the Phase 10 report.

**Run `validation.md` numbered checks** sequentially (some depend on prior state — do not parallelise):

- Fenced command + expectation → execute, compare exit code / status / output substring against the stated expectation
- Non-command check (file content, structural assertion) → inspect and confirm

For any failure:

- If the cause is obviously trivial and within the spec's scope (lint nit, missing import, doc-row gap, typo) → fix it and commit the fix as its own atomic commit. Conventional Commits header reflects the fix (typically `fix(<scope>): ...` or `docs(<scope>): ...`). `git commit -s`.
- If the fix isn't trivial, stop and report — do not iterate on guesses, and do not patch the spec to make a check pass.

After every check passes, `TaskUpdate` the Verify group → `completed` and assemble a verification summary for Phase 10:

- Each `plan.md` Verify command + result (pass/fail + key line)
- Each `validation.md` check + result (pass/fail + the asserted condition that held)

## Phase 8 — Pre-push review

After Phase 7 passes and before pushing, run two reviews of the branch diff. Both fire **before `git push`** (Phase 9) so any fixes can land as cheap fix-up commits on the local branch.

If the run halts mid-phase (user dismisses the synthesis question, tool error during a fix-up commit, etc.), surface the situation per Phase 3's branch-idempotence policy and stop — partial-resume of Phase 8 is out of scope. A re-run starts Phase 8 from scratch on the same branch; the existing per-group commits and any landed fix-ups are preserved.

`TaskUpdate` the `Pre-push review` task → `in_progress`.

### Capture diff context once

Run these once and pass the output to every reviewer:

- `git log main..HEAD --oneline` — the commit list
- `git diff --stat main...HEAD` — the file-level summary
- `git diff main...HEAD` — the full diff

For very large diffs (≥ ~2000 lines or ≥ ~30 files), pass each subagent the commit list, the file-level summary, and the list of paths to read directly via `Read` — sending a multi-megabyte diff inline to four reviewers wastes tokens and can blow the context window.

### A. Built-in `/review` skill

Invoke the `Skill` tool with `skill: "review"` and ``args: "branch changes against main (`git diff main...HEAD`)"``. The string is best-effort — `/review` is built around PR URLs and a "local changes" working-tree mode, so it may interpret a branch-vs-main scope fluidly or report it has nothing concrete to review; either result is fine. Surface whatever it returns verbatim, do not narrate the invocation mechanism, do not retry. The load-bearing reviewer is the three-perspective deep review in **B** below; `/review` here is a sanity-check pass. If the invocation itself fails (tool error, unreachable), surface the error and continue to B.

### B. Three-perspective deep review

Spawn three `Agent` calls **in a single message** (parallel) using `subagent_type: "general-purpose"`. Each subagent gets a perspective-specific brief, the diff context captured above, and the spec/rule files relevant to its lens. Cap each response at ~400 words — the goal is a structured findings list, not narrative.

The shared question for every perspective: *from this lens, is anything in the diff wrong, surprising, missing, or obviously improvable?* Each finding line takes the shape:

`<finding-type>: <one-line summary> — <file:line if applicable> — <suggested fix, or "surface to user">`

**Perspective 1 — Spec adherence.** Inputs: `specs/<dir>/requirements.md`, `plan.md`, `validation.md`, the commit list, the diff. Look for: groups/tasks in `plan.md` not visible in any commit; commits introducing work outside the spec's scope; deviations from `plan.md`'s prescribed file/line targets without surfacing; the roadmap-completion task missing from its expected group commit (the diff should show ` ✅` appended to the `## Phase N — <Title>` heading in `specs/roadmap.md`). Finding-types: `missing-task`, `extra-scope`, `silent-deviation`, `roadmap-completion-missing`.

**Perspective 2 — Code quality and simplicity.** Inputs: the diff, the commit list, `.claude/rules/karpathy-guidelines.md`, `.claude/rules/git-workflow.md`, `.claude/rules/conventional-commits.md`. Look for: speculative features the spec doesn't require; abstractions for single-use code; error handling for impossible scenarios; "improvements" to adjacent code beyond the change scope; comments explaining WHAT instead of WHY (especially comments referencing the current task / fix / caller); non-atomic commits; CC type/scope that misrepresents the commit's content. Finding-types: `bloat`, `abstraction`, `adjacent-edit`, `dead-comment`, `commit-shape`.

**Perspective 3 — Risk and robustness.** Inputs: the diff, `specs/tech-stack.md`, and any load-bearing invariants documented in the project's `CLAUDE.md` files anywhere in the tree (repo root, `.claude/`, or nested per-directory) — the subagent reads what's actually present at runtime; the skill itself does not bake in domain knowledge or project-specific invariants. Look for: security concerns (auth bypass, credential exposure, secrets in logs, injection); validation gaps (edge cases `validation.md` doesn't cover but the diff plausibly hits); regression risks to invariants the subagent surfaces from `tech-stack.md` or `CLAUDE.md`; observability gaps (a new code path with no trace/log); performance or scaling concerns (N+1 queries, unbounded growth, blocking I/O on the request path). Finding-types: `security`, `regression`, `edge-case`, `observability`, `performance`.

### Synthesize

Combine findings from `/review` + the three subagents into one grouped list:

- **Blockers** — in-scope issues that should be fixed before PR (broken validation, missing spec coverage, security regression, architectural violation, commit-shape error that would survive the squash-merge)
- **Suggestions** — optional improvements (simpler approach, better naming, additional test case, observability gap)
- **Nits** — cosmetic only (typos, formatting)

Deduplicate findings raised by more than one reviewer; keep the strongest framing. Demote out-of-scope findings (changes adjacent to the spec but not within it) to **Suggestions** with an `(out-of-scope, optional)` tag — never silently promote them to blockers, and never silently apply them.

### Resolve

Surface the grouped report to the user. If there are blockers, use a single `AskUserQuestion` (multi-select) to choose which to fix. For suggestions/nits, list them and ask once whether to apply any (default: skip).

For each accepted fix:

- Apply as a small atomic fix-up commit per Phase 6's commit rules — Conventional Commits header (typically `fix(<scope>): ...`, `refactor(<scope>): ...`, or `docs(<scope>): ...`), `git commit -s`, stage explicit paths, no `--amend`, no `--no-verify`
- Unrelated fixes → multiple commits

For blockers the user declines to fix, confirm explicitly that you should proceed and record a one-liner for the Phase 10 report (`Proceeded over blocker: <one-liner>`).

After fix-ups:

- Re-run the `validation.md` numbered checks whose covered area intersects the fix-up diff (don't re-run the whole Verify group unless every check is plausibly affected)
- Re-invoke `/review` at most **once**, and only if any fix-up commit touched code (not docs/config-only paths). Do **not** re-spawn the three subagents — they fire once per skill run.

`TaskUpdate` the `Pre-push review` task → `completed`.

## Phase 8.5 — Draft retrospective (conditional)

Skip this phase entirely if the deviation list is empty.

If the deviation list is non-empty, write `specs/<date>-<slug>/retrospective.draft.md` and commit it before pushing. The draft gives the reviewer a structured starting point — they promote it to `retrospective.md` (edit + rename) or delete it before merge; nothing flows into `/sdd-distill-lessons` until a human confirms it.

**File structure** — follow `.claude/templates/sdd/feature-spec/retrospective.example.md`:

- H1: `# Phase <N> Retrospective Draft — <Phase Title>`
- Opening note (blockquote): `> **DRAFT** — written by \`/sdd-implement-spec\` from tracked implementation deviations. Promote to \`retrospective.md\` (edit + rename) or delete before merge if nothing here is worth capturing.`
- `## Deviations from the spec` — one bullet per item in the tracked deviation list (same content as the PR body's `## Spec deviations`).
- `## Root cause` — leave the `[ONE_OR_TWO_SHORT_PARAGRAPHS …]` placeholder; do not speculate.
- `## Lesson for future specs` — leave the `[LESSON_1 …]` placeholder; do not speculate.
- `## Promotion candidate` — write `no — update if this lesson names a load-bearing invariant for \`specs/tech-stack.md\`.`

**Commit:**

- Stage only the draft file: `git add specs/<date>-<slug>/retrospective.draft.md`
- Verify with `git diff --cached --name-only` (must list exactly that one path)
- `git commit -s -m "docs(spec): draft retrospective for phase <N>"` (≤ 69 chars; body: one sentence — "Agent-generated draft from implementation deviations; reviewer should promote to retrospective.md or delete.")

## Phase 9 — Push and open the PR

```
git push -u origin <branch>
```

**Search for related issues** before drafting the PR body. Run `gh issue list --search "<keyword from phase title>"` and try one or two close synonyms (e.g. for Phase 24, search "oauth", "broker", "credentials"). Per `.claude/rules/git-workflow.md`, link related issues in the PR body. If any open issues plausibly relate to the phase and weren't named in Phase 3, surface them to the user and ask whether to link any with `Closes #<issue>` (full resolution) or `Refs #<issue>` (partial). If nothing relevant turns up, proceed with only the Phase 3 issue (if any).

Open the PR with `gh pr create`. The PR title shape is the Conventional Commits header that will become the squash-merge commit (commitlint enforces this on the merge):

- Pick the type+scope of the headline change for the phase. Often this is the same shape as the largest non-Verify group's commit (e.g. `feat(broker): support reverse-proxy path prefix`).
- If the phase spans multiple modules (backend + UI + docs is common), prefer the most user-facing scope — the squash-merge commit must read like a release-note line for the phase as a whole, not a description of one slice.
- ≤ 69 chars; lowercase imperative; no trailing period.

Body via HEREDOC:

```
## Summary

Implements **Phase <N>: <Title>** from the SDD roadmap.

Spec: `specs/<date>-<slug>/`

### What changed

- <one bullet per non-Verify group — short noun phrase, e.g. "Config plumbing for `JENTIC_ROOT_PATH`". The group whose commit performs the roadmap-completion task should mention it inline (e.g. "Docs + roadmap entry marked ✅ per lifecycle"); do not add a separate trailing bullet for the marker.>

## Validation

All gates from `specs/<date>-<slug>/validation.md` passed:

- <check 1 title> — pass
- <check 2 title> — pass
…

### Out of scope (from `validation.md` "Not Required")

- <one bullet per item under `## Not Required` in `validation.md` — short paraphrase, not a verbatim copy>

## Spec deviations

- <one bullet per tracked deviation — name the spec file + section + what changed in practice. Plain past-tense sentences, e.g. "`plan.md` Group 3 referenced `src/foo.py` but the function had already moved to `src/bar.py`; implemented against the current path.">

A `specs/<date>-<slug>/retrospective.draft.md` has been committed on this branch as a structured starting point — promote it to `retrospective.md` (edit + rename) or delete before merge. Once merged, `/sdd-distill-lessons` can roll confirmed retrospectives into `specs/lessons.md` so future specs absorb the lesson. See `.claude/rules/sdd-constitution.md` (post-implementation feedback loop).

## Test plan

- [ ] CI green
- [ ] Manual smoke per `validation.md` (only if `validation.md` has manual-smoke checks)

Closes #<issue>
Refs: `specs/<date>-<slug>/`
```

PR body rules:

- `## Spec deviations` is included **only** when the deviation list maintained through Phases 6–8 is non-empty; omit the heading entirely otherwise.
- `Closes #<issue>` is included **only** when the user provided an issue number in Phase 3 — the implementation PR for a phase typically fully resolves its tracking issue. If no issue was given, omit the line entirely (do not write `Closes #` with no number).
- `Refs:` always points at the spec dir.
- **No `🤖 Generated with [Claude Code]` trailer.** Sibling SDD skills (`sdd-new-spec`, `sdd-new-phase`) follow the same convention — the PR is the final step of an SDD workflow whose spec was reviewed and merged separately, so the "generated by Claude" framing doesn't fit. Do not add it back.

## Phase 10 — Report back

Return to the user in this shape:

- **Spec**: `Phase <N> — <Title>` (`specs/<date>-<slug>/`)
- **Branch**: name
- **Commits** (in order): each line as `<sha-short> <type>(<scope>): <description> — <group title>`. Include verification fix-up commits and pre-push review fix-up commits, each tagged with the group/finding they amend.
- **PR URL**
- **Implementation summary**: one short paragraph naming what was built and where (modules / files / migrations / UI surfaces touched). Aim for the shape a reviewer would skim before opening the diff.
- **Verification summary**:
  - `plan.md` Verify group: each command + pass/fail
  - `validation.md`: each numbered check + pass/fail
- **Pre-push review outcome**: one of `clean` (no findings), `findings addressed` (had findings, applied fixes — list their SHAs and what they addressed), or `proceeded over blocker` (had blockers, user accepted as-is — include the one-liner). Suggestions or nits the user declined to apply get one short bullet each as optional follow-ups, not regressions. If `/review` couldn't run (tool error), say so on this line instead.
- **Deviations from the spec** (if any): the same running list emitted in the PR body's `## Spec deviations` section — tasks that were split or merged, file/line targets that drifted, validation checks that needed clarification, fix-up commits that revealed gaps. Each item is one sentence naming the spec file (`requirements.md` / `plan.md` / `validation.md`), the section, and what changed in practice. If deviations existed, also note that `specs/<date>-<slug>/retrospective.draft.md` was committed on the branch — the reviewer should promote it to `retrospective.md` (edit + rename) or delete before merge.
- **Next step**: human review on the PR. The spec dir stays as history per the lifecycle rule; the roadmap entry was marked `✅` by the relevant commit in this PR.

If the run halted before completion, replace the post-completion sections with:

- **Halted at**: Group / numbered task + the specific reason
- **Last successful commit**: SHA + label
- **Verification status so far**: any checks that did pass; checks that were skipped or failed
- **What to do next**: concrete options (revise the spec, fix locally, re-run skill once the blocker is unblocked)
