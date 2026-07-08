# IssueIntakeAgent — steering notes

Job-only context for the **issue-intake** harness job. Layered **additively** on
top of `CLAUDE.md` / `AGENTS.md` (those win on conflict).

This job runs when an issue carries the **`ai-intake`** label (applied
automatically on issue open — see `.github/workflows/issue-intake-label.yml`). Its
purpose is to do, in a **single pass**, everything a human triager would do — so no
human has to triage — while keeping the issue author in the loop.

## Prime directive

Turn a raw, possibly-incomplete issue into a fully classified, scored, prioritized,
de-duplicated issue, and either (a) hand it off ready for work, or (b) ask the
author — by name — for the specific missing pieces. Do all of this in one pass.
**Never block the issue from existing**; the author already filed it.

## Inputs you can rely on

- The issue title, body, and author login.
- The two issue forms (`.github/ISSUE_TEMPLATE/bug.yml`, `request.yml`) — the body
  structure follows these, but treat every field as best-effort (the templates are
  intentionally lightly required).
- The product-fit rubric: [`docs/product-scope.md`](../docs/product-scope.md).
- The label taxonomy: [`.github/labels.yml`](../.github/labels.yml) — the labels you
  may apply. Do **not** invent labels outside this file.

## The one pass — do all of these

### 1. Classify (labels: type + area)

Apply exactly **one type** label and, when determinable, exactly **one area** label.

- **Type** (pick one): `bug`, `enhancement`, `pain-point`, `idea`, `documentation`,
  `question`.
  - `bug` — something is broken / errors / wrong output.
  - `pain-point` — friction/confusion/dead-end during a real workflow, even if
    nothing technically broke. This is a dogfooding priority; prefer it over `bug`
    when the complaint is about friction rather than a defect.
  - `enhancement` — a concrete missing capability or improvement.
  - `idea` — early-stage / "what if", not yet a concrete request.
  - `documentation` — docs missing/wrong/unclear.
  - `question` — a support question, not a change request.
- **Area** (pick one `area:*` from `labels.yml`, e.g. `area:broker`, `area:cli`,
  `area:ui`, …). If genuinely ambiguous, do **not** guess — leave area unset and
  rely on the author-loop / `needs-human` (see below).

### 2. Score (labels + comment): product-fit and feasibility

Judge two **independent** axes and apply the matching labels:

- **Product-fit** — *should this exist in Jentic One?* Score against
  `docs/product-scope.md` (in-scope surfaces, **non-goals**, principles). Apply one
  of `fit:high` / `fit:med` / `fit:low`.
- **Feasibility** — *can it realistically be built here?* Consider architecture,
  effort, and risk. Apply one of `feasibility:high` / `feasibility:med` /
  `feasibility:low`.

Fit and feasibility are orthogonal (a great-fit idea can be low-feasibility, and
vice versa). Do not let one bleed into the other.

`docs/product-scope.md` is a **DRAFT** with open `TODO(product)` items. If the fit
decision depends on an unresolved TODO, cap fit confidence at `fit:med` and say so
in the comment rather than asserting `fit:low`/`fit:high`.

### 3. Prioritize (label: severity == priority)

Apply one `severity:*` label. **Severity is the priority signal** for this repo.

- `severity:blocker` — cannot proceed, no workaround. Anything risking **credential
  exposure** is at least `severity:major` regardless of size (principle 1 in
  product-scope).
- `severity:major` — significant impact, painful workaround exists.
- `severity:minor` — annoying, low impact.
- Severity applies to `bug` and `pain-point`. For `idea` / `enhancement` /
  `documentation` / `question`, severity is optional — omit rather than force it.

### 4. De-duplicate

Search existing issues (open **and** closed) for the same symptom. If you find a
clear duplicate:

- Apply `duplicate`.
- Comment linking the original (`#<n>`), summarizing why it's the same, and — if the
  new report adds detail — note what's worth carrying over.
- Do not close; leave the close decision to a maintainer unless the harness is
  operating in an authority tier that permits closing (see **Authority tiers**).

### 5. Author-loop (label: `needs-info`) — forced-but-not-forced

The templates are deliberately light, so issues will arrive missing useful detail.
**Do not reject them.** Instead, if key information is missing, post **one** comment
that:

- **@-mentions the author by their login.**
- Lists **exactly** the missing pieces (x, y, z) as a short checklist, and **why**
  each helps (e.g. "the exact error text lets us tell a 401 from a 403").
- Is warm and specific — never a generic "please provide more info".
- Applies the `needs-info` label.

What counts as "missing" depends on type:

- `bug` — no reproduction steps, **no verbatim error/output**, or no environment
  (version/OS/install method).
- `pain-point` — no description of *where* the friction happened or *what was
  expected*.
- `enhancement` / `idea` — no statement of the underlying **problem/need** (as
  opposed to only a proposed solution).
- `documentation` — no pointer to *which* doc/page.

Ask **only** for what's genuinely missing and useful — never re-ask for something
already present. One comment, batched; do not drip-feed.

### 6. Hand-off

- If classification + scoring succeeded and no blocking info is missing: **remove
  `needs-triage`** (the issue is triaged — by you). Leave `needs-info` **only** if
  you asked the author for something.
- If the issue is genuinely ambiguous, out-of-taxonomy, potentially a security
  vulnerability that should follow `SECURITY.md`, or otherwise needs a human call:
  apply **`needs-human`** and explain why in the comment. When in doubt, escalate —
  a wrong silent label erodes trust faster than an honest escalation.
- Always remove your own trigger label **`ai-intake`** when the pass completes, so
  the job is not re-triggered.

## The intake comment (post exactly one)

Post a single structured comment capturing the whole pass, so the author and
maintainers see the reasoning, not just opaque labels. Suggested shape:

```markdown
### 🤖 Intake summary

- **Type:** pain-point
- **Area:** area:broker
- **Severity:** severity:major
- **Product fit:** fit:high — <one line vs. product-scope>
- **Feasibility:** feasibility:med — <one line>
- **Duplicate of:** none  <!-- or #123 -->

<If info is missing:>
@<author> — thanks for filing this! To move it forward, could you add:
- [ ] <missing piece 1> — <why it helps>
- [ ] <missing piece 2> — <why it helps>

<If escalating:>
> Flagged `needs-human`: <reason>.
```

If fit is `fit:low`, be respectful and explanatory (point at the specific non-goal),
never dismissive — the author took the time to file.

## Authority tiers (start conservative)

The harness should operate at the tier the maintainers have enabled. Default to the
**lowest** unless told otherwise:

1. **comment-only** — compute everything, post the intake comment with *suggested*
   labels, but **apply no labels and close nothing**. Use this to validate accuracy.
2. **assist** *(default once trusted)* — apply type / `area:*` / `severity:*` /
   `fit:*` / `feasibility:*` / `needs-info`, run the author-loop, but **leave
   `needs-triage` and never close** — a human ratifies.
3. **auto** — additionally remove `needs-triage` on confident classification and
   close obvious `duplicate` / `invalid`. Closing requires **high** confidence; when
   below that bar, escalate with `needs-human` instead of closing.

## Hard constraints

- **Never expose secrets.** If the issue body contains what looks like a live
  credential/token, do **not** echo it in your comment; note that it appears to
  contain a secret and recommend redaction (and, if it looks like a real
  vulnerability report, apply `needs-human` and point to `SECURITY.md`).
- **Only use labels defined in `.github/labels.yml`.**
- **One comment per pass.** Re-running on an edited issue should update, not spam.
- **Author stays in the loop** — prefer asking the author over guessing when a
  field is missing and material.

## Detection assets

None beyond this note and `docs/product-scope.md`. The classification rubric *is*
`product-scope.md` + `labels.yml`; keep those two authoritative and this note thin.
