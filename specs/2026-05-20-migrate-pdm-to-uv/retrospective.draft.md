# Phase 29 Retrospective Draft — Migrate Python Packaging from PDM to uv

> **DRAFT** — written by `/sdd-implement-spec` from tracked implementation deviations. Promote to `retrospective.md` (edit + rename) or delete before merge if nothing here is worth capturing.

## Deviations from the spec

- `plan.md` Groups 1 and 2 are described as separate commits, but both touch interleaved sections of `pyproject.toml` (poethepoet dep sits between the `[build-system]` removal and the `[tool.poe.tasks]` addition); committed as one atomic `pyproject.toml` commit covering both groups rather than two separate commits.
- `plan.md` Group 2 task 4 specified `${args:tests}` as the pytest argument placeholder, but poe's `cmd` tasks require the native `args` config (`args = [{name = "target", default = "tests", positional = true}]` with `${target}` interpolation) — the shell-expansion form is not supported by poe's cmd executor.
- `validation.md` Check 11 (grep for PDM references) exits 0 with matches rather than 1 because the Phase 29 spec dir (`specs/2026-05-20-migrate-pdm-to-uv/`) and the Phase 29 roadmap body contain contextual PDM references describing the migration source; the exclusion list only covers prior completed-spec dirs (2026-05-07, -08, -12). The important invariant — no live tooling references outside the spec files — does hold.
- `specs/roadmap.md` Phase 29 body bullet described swapping `[build-system]` to `uv_build` (the decision to use application mode instead was captured in spec commit `7649e20` but not back-propagated to the roadmap body before implementation); corrected in a fix-up commit during pre-push review.

## Root cause

[ONE_OR_TWO_SHORT_PARAGRAPHS — why the spec missed this.]

## Lesson for future specs

- [LESSON_1]

## Promotion candidate

no — update if this lesson names a load-bearing invariant for `specs/tech-stack.md`.
