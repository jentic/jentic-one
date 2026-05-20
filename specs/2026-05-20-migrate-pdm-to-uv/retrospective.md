# Phase 29 Retrospective — Migrate Python Packaging from PDM to uv

## Deviations from the spec

- `plan.md` Groups 1 and 2 are described as separate commits, but both touch interleaved sections of `pyproject.toml` (poethepoet dep sits between the `[build-system]` removal and the `[tool.poe.tasks]` addition); committed as one atomic `pyproject.toml` commit covering both groups rather than two separate commits.
- `plan.md` Group 2 task 4 specified `${args:tests}` as the pytest argument placeholder, but poe's `cmd` tasks require the native `args` config (`args = [{name = "target", default = "tests", positional = true}]` with `${target}` interpolation) — the shell-expansion form is not supported by poe's cmd executor.
- `validation.md` Check 11 (grep for PDM references) exits 0 with matches rather than 1 because the Phase 29 spec dir and the Phase 29 roadmap body contain contextual PDM references describing the migration source; the exclusion list only covers prior completed-spec dirs (2026-05-07, -08, -12).
- `specs/roadmap.md` Phase 29 body bullet described swapping `[build-system]` to `uv_build`; what shipped was deleting the block with `[tool.uv] package = false`; corrected in a fix-up commit during pre-push review.

## Root cause

The spec was scaffolded in two commits — the second (`docs(spec): switch phase 29 to uv application mode`) revised the build-system decision from `uv_build` to application mode, but the roadmap phase body (which `/sdd-new-spec` does not rewrite) was not updated to match. This left the roadmap body and the feature spec contradicting each other before implementation even started.

The poe argument-syntax deviation (`${args:tests}` vs native `args` config) reflects the spec author's familiarity with PDM's interpolation syntax being carried over to poe without checking poe's actual docs. Similarly, the grep exclusion list omitting the current-phase spec dir is a spec-authoring oversight: the check was written for prior completed specs but not extended to cover the spec being implemented.

## Lesson for future specs

- When a spec decision changes between the scaffold and the implementation PRs (e.g. the build-system approach), update the roadmap phase body to match — `/sdd-new-spec` only scaffolds the spec dir; the roadmap body is not auto-synced.
- When writing a grep-based "no references remain" validation check for a migration, include the current-phase spec dir in the exclusion list — it will naturally contain the old names as descriptive context.
- Before specifying argument-passing syntax for a task runner (poe, just, make), verify the runner's actual interpolation syntax rather than carrying over syntax from the tool being replaced.

## Promotion candidate

no — these are empirical reminders for spec authoring, not load-bearing invariants for `specs/tech-stack.md`.
