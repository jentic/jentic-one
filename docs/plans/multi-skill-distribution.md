> Epic: jentic/jentic-one — Phase 5 of the spec-flywheel
> (see `docs/plans/spec-flywheel-and-catalog-update-notify.md` §"Skill distribution").
> Prereq refs: internal#407 (`jentic skill update`), #825 (stale installed skills),
> #277/#651 (hosted-skill seam + `GET /skills/jentic.md`).

# Multi-skill distribution — ship `contribute-spec-fix` + `import-new-api` to agents

## Context

The community spec-flywheel has three flows. Flow-3 (upstream-change detection +
overlay reconciliation) is fully built, tested, and merged. Flows 1 and 2 —
**contribute a fix as an overlay** (`skills/contribute-spec-fix/SKILL.md`) and
**import a not-yet-catalogued API** (`skills/import-new-api/SKILL.md`) — have working
backend machinery (overlay submit/confirm/rollback, the ingest pipeline, the events
surface, all green in CI) and correct, merged skill runbooks.

**But those two skills reach no agent by any automated path.** We verified this at the
strongest level — a built wheel contains zero copy of them; the CLI binary embeds only
`content/jentic.md`; the backend serves only `GET /skills/jentic.md`. The repo-root
`skills/` tree is, in the design doc's words, "distribution-dead": it ships in neither
the wheel nor the CLI binary. Today an agent only uses these skills if a human manually
hands the agent the file. That is the last-mile gap between "the machinery works" and
"agents actually have the skills."

This plan closes that gap: extend the skill system from **one served skill** to a
**served skill set**, re-author the two flow skills to a conformant shape, and wire the
CLI + backend + packaging + drift tests to distribute the set — matching the
industry-standard Agent Skills layout so the files we install are natively discovered by
Claude Code, Cursor, and the rest.

This is a **cross-repo change** (public OSS `jentic-one`): Go CLI, Python backend, and
docs. Consult `jentic-one-rules/AGENTS.md` before touching CLI, web handlers, or tests.

---

## How this is normally done (external norms, and where we differ)

The **Anthropic Agent Skills** convention (now mirrored by Cursor and the wider
ecosystem) is deliberately minimal:

- A skill is **a directory containing a `SKILL.md`**. Bundled `scripts/`, `references/`,
  and `assets/` are optional siblings.
- `SKILL.md` starts with YAML frontmatter whose **only required fields are `name` and
  `description`** (`name`: ≤64 chars, `[a-z0-9-]`, no leading/trailing hyphen;
  `description`: 1–1024 chars, says *what it does and when to use it*). Everything else
  in the body is free-form Markdown. Optional keys: `license`, `compatibility`,
  `metadata`, `allowed-tools`.
- **Progressive disclosure / discovery**: an agent pre-loads only every skill's
  `name`+`description` at startup, then reads the full `SKILL.md` on demand. Installing
  N skills costs ~N descriptions of context, so "many skills" is the expected case.
- **Install = drop the folder** in `~/.claude/skills/<name>/` (personal) or
  `.claude/skills/<name>/` (project). No build step, no registry, no manifest required
  by the client. Plugins/marketplaces bundle several skills under one `skills/` dir.
- For **HTTP publication**, the emerging `llms.txt` convention is an H1 + one-line
  blockquote + H2 sections of `- [Title](absolute-url): description` links, served as
  `text/markdown`/`text/plain` at `/llms.txt`. A skill set is naturally an H2 section of
  such links.

**Where we differ today (the mismatch to fix):**

| Norm | jentic-one today |
| --- | --- |
| Skill = dir + `SKILL.md`, only `name`+`description` required | `parseCanonical` requires a rigid schema: `## When to Use / Prerequisites / Procedure / Quick Reference / Pitfalls / Verification`, and errors without `## Procedure` steps. |
| Many skills, auto-discovered | Exactly **one** skill (`jentic`), hardcoded end-to-end. |
| Drop the folder | We render a managed **block** into the operator's file (good for edit-preservation), but the target path hardcodes the literal `"jentic"`. |
| `name` drives the install path | Adapter `Target()` paths use literal `"jentic"`, not `c.Name`. |

Our managed-block approach (edit-preserving, hash-annotated, `--force`-gated) is *better*
than a raw folder drop for the always-in-context `AGENTS.md` operators and worth keeping.
The fix is to make it **parametric over a skill set**, and to relax `Canonical` so a
runbook-style skill (frontmatter + free-form body) is a first-class citizen rather than
being forced into the `jentic`-shaped schema.

---

## Design decisions (resolve before coding)

> These decisions incorporate a three-lens plan review (Go/skillgen, Python/backend,
> Agent-Skills-norms). The most consequential change from the first draft: **dir-skill
> operators must get a clean, spec-conformant `SKILL.md` with no managed-block markers
> inside it** (decision 2) — the earlier "keep the managed block everywhere" idea was
> non-conformant and defeated the goal of native discovery.

1. **Two output modes by operator class, not one managed block everywhere.**
   - **Owned-file operators (`.claude`, `.cursor`, `.hermes`) → clean spec `SKILL.md`.**
     The whole file *is* the skill: YAML frontmatter (`name`, `description`, optional
     `metadata`) followed by the verbatim body — **no `<!-- BEGIN/END JENTIC MANAGED
     SKILL -->` markers inside it.** These tools discover a skill by reading the file's
     frontmatter and load the *entire body* on trigger; foreign HTML-comment markers and
     a "do not edit / regenerated by Jentic / hash=…" line injected into the body are
     off-spec noise the model would see as skill content. Idempotency/edit-preservation
     for these files is handled at the **whole-file** level (we already compute this via
     `wholeFileSurroundEdited`): rewrite when our generated content changed, refuse when
     the user edited it unless `--force`. To detect *our* content without an in-file
     marker, record provenance in a **sidecar** `.jentic-skill.json` next to the
     `SKILL.md` (name, rendered-body sha256, source, base-url) — never inside the served
     file.
   - **Shared-file operators (`codex`, `generic`) → named managed block in `AGENTS.md`.**
     Here splicing into foreign content is the actual requirement, so the managed block
     stays — but becomes **per-skill named** (`BEGIN JENTIC MANAGED SKILL: <name>` /
     `END …: <name>`) so several skills coexist without collision.

2. **`AGENTS.md` blocks are pointer blocks, not full bodies** (resolves OQ-6 + bloat).
   `AGENTS.md` is always-in-context with no progressive disclosure, and the flow skills
   are long (`contribute-spec-fix` ≈ 500 lines, `import-new-api` ≈ 260). Splicing full
   bodies would add ~750 lines of permanent prompt to every codex/generic run. Instead,
   each named block carries the skill's **`name` + `description` + a
   `GET {base}/skills/<name>.md` fetch-on-demand link** — the honest analogue of
   progressive disclosure for a format that lacks it. The short `jentic` guide MAY splice
   its full body (it's the onboarding guide); the flow skills are pointer-only in
   `AGENTS.md`. (Rejected: full-body splice for every skill — unbounded context cost.)

3. **Two skill kinds now; retire `Canonical` templating as a fast-follow.** Add a
   **`freeform`** kind (frontmatter + verbatim body) for the flow skills, alongside the
   existing structured `Canonical` (`jentic`). **The kind is explicit via frontmatter
   (`metadata.kind: freeform`), never inferred from which H2 headings are present** — a
   runbook that happens to contain `## Prerequisites`/`## Procedure` must not be
   mis-parsed as `Canonical`. Track a follow-up to convert `jentic` itself to `freeform`
   and delete `parseCanonical`/`renderBody`/section-templating (incl. the lossy 60-char
   hermes description shortening): one verbatim-body code path is strictly more
   spec-conformant. Not required for this PR.

4. **Content stays byte-mirrored, not symlinked.** Keep the two-copy model
   (`cli/internal/skillgen/content/<name>.md` for `go:embed` +
   `src/jentic_one/shared/web/content/<name>.md` for the wheel) because `go:embed` can't
   escape the Go module and the wheel can't ship outside `src/jentic_one`. The
   `skills/<name>/SKILL.md` repo-root tree is the **single human-authored source**; a
   `make skills` target regenerates both `content/` dirs from it. **Invariant (enforced
   by the validator): source `SKILL.md` files MUST NOT contain BaseURL placeholders** —
   BaseURL is a render-time concern (CLI-only), never in the file — so the two mirrors
   stay byte-identical and the backend can serve the raw bytes safely.

5. **`index.json.sha256` is the hash of the RAW served file bytes.** The backend serves
   `content/<name>.md` verbatim (no BaseURL interpolation); the CLI interpolates BaseURL
   only for the `Canonical` `jentic` render. So a CLI-installed *rendered* hash is **not**
   comparable to the manifest's raw hash for `jentic`. Any hosted-update comparison
   (decision 7 / phase F) must **re-render locally then hash** (render-then-hash), never
   compare an installed block hash to the manifest hash. For freeform skills the body is
   emitted verbatim, so raw == rendered and the hashes do coincide.

6. **Which skills ship in the set (v1):** `jentic` (Canonical), `contribute-spec-fix`
   (freeform), `import-new-api` (freeform). `init-design` is **excluded** from the served
   set (internal design-workflow doc); it stays under `skills/` unserved and is not
   iterated by `BundledNames`/the validator/the drift test. The **canonical name set has
   a single source of truth** — derived by globbing `content/*.md` — shared by the CLI
   embed loader, the backend allowlist, and the drift test, so they cannot diverge.

7. **`jentic skill update` is singular** (match the existing `list`/`init`/`remove`
   tree; internal#407). Update re-renders each installed skill **with the currently
   resolved BaseURL** and rewrites when the recorded (sidecar / named-block) hash differs;
   a BaseURL change therefore legitimately rewrites. Defer hosted-source refresh (phase F).

8. **`argument-hint` moves under `metadata:`.** It is a Cursor/slash-command field, not a
   recognized top-level Agent-Skills key. Nest it as `metadata.argument-hint` so strict
   frontmatter validators pass; the command surfaces that consume it read it there.

---

## Work breakdown

### A. Re-author the flow skills to a conformant shape (docs)

- Move the human-authored source of truth to `skills/<name>/SKILL.md` and normalize
  frontmatter to the Agent Skills spec: `name` (validate `^[a-z0-9]([a-z0-9-]*[a-z0-9])?$`,
  ≤64 chars), `description` (**1–1024 chars** — `import-new-api`'s current description is
  ~1050 chars and MUST be trimmed; drop the exhaustive "user says …" phrase list, keep
  what+when), `metadata.kind: freeform`, and `metadata.argument-hint` (moved off the
  top level).
- **`init-design` is excluded** from the served set. Leave it under `skills/` unserved
  (not globbed into `content/`, not iterated by the validator/drift test).
- Files: `skills/contribute-spec-fix/SKILL.md`, `skills/import-new-api/SKILL.md`.

### B. skillgen core — make it a skill *set* (Go)

`cli/internal/skillgen/`:

- **`bundled.go`**
  - Change the embed from a single-file `string` to a multi-file FS: `import "embed"`,
    `//go:embed content/*.md` into `var bundledContent embed.FS` (a `string`/`[]byte`
    target only permits one file — the FS is mandatory). Add `BundledNames() []string`
    (reads + **sorts** the FS dir entries — no hand-maintained list) and
    `Bundled(name, baseURL)`.
  - Add `Kind` (`canonical` | `freeform`) and `Body string` to `Canonical`. Rename
    `parseCanonical` → `parseSkill`: parse frontmatter; **read the kind explicitly from
    `metadata.kind`** (default `canonical` for back-compat) — never infer it from which
    H2s are present. Freeform requires only `name`+`description` and keeps the verbatim
    body; canonical keeps the section parse. Drop the `Name:"jentic"` default (`name` is
    required frontmatter now). Add a build-time validator (name regex, description 1–1024,
    frontmatter parses, **no BaseURL placeholder in the body**) run by the drift test.
- **`adapters.go` — two output modes (decision 1):**
  - Owned-file operators (`dirSkillAdapter` claude/cursor, `hermesAdapter`): `Target()`
    uses `c.Name` (`.../skills/<name>/SKILL.md`, `.../skills/api/<name>/SKILL.md`), and
    **`renderDedicated` emits a clean `frontmatter + verbatim body` file with NO managed
    markers.** Provenance goes in a sidecar `.jentic-skill.json` (name, rendered sha256,
    source, base-url) beside the `SKILL.md`.
  - `hermesFrontmatter` tags + `titleFor` special-case: derive from `c.Name`/frontmatter,
    not the literal `jentic`. Drop the lossy 60-char hermes description shortening for
    freeform (emit the real description).
  - `agentsAdapter` (codex/generic): render a **named pointer block**
    (`BEGIN JENTIC MANAGED SKILL: <name>` … name + description + `GET {base}/skills/<name>.md`
    link … `END …: <name>`) — **not** the full body for flow skills (decision 2). Splice
    inserts missing blocks in stable `BundledNames()` order for idempotent diffs.
- **`skillgen.go` / `apply.go` / `status.go` — the splice/find/apply engine is the real
  work, not a one-liner:**
  - `managedBlock(name, …)` / `findBlock(name)`: `findBlock` must scan for the *named*
    begin marker among possibly several blocks in one `AGENTS.md` and return that one;
    `splice` must replace/insert exactly that block without disturbing siblings.
  - `wholeFileSurroundEdited` no longer applies to `AGENTS.md` multi-block (prelude of
    block N includes block N−1); scope whole-file edit detection to the **owned-file
    sidecar** path, and use per-named-block hash comparison for `AGENTS.md`.
  - **Legacy migration:** an existing install has either the old un-named `AGENTS.md`
    block or an old marker-wrapped dir `SKILL.md`. Read/extract/hash the legacy form
    **using the legacy marker constants** (so `currentBlockBody` strips the right end
    marker and the body hash still matches → no false "user-edited"), then rewrite to the
    new form (named block for AGENTS.md; clean file + sidecar for dir operators). This is
    the highest-risk path — cover it with a dedicated test.
  - `Apply`/`Outcome` become **per-skill** so one user-edited block never freezes updates
    to its siblings; for a shared `AGENTS.md`, render all of a file's set blocks in **one
    read-modify-write** (avoid N clobbering cycles / partial-set-on-error).
  - `pruneEmptyDirs`: add `.cursor` to the boundary set (a latent gap today), prune
    now-empty `skills/<name>/` but treat the parent `skills/` and each operator dir
    (`.claude`/`.cursor`/`.hermes`) as hard boundaries so the walk never climbs into user
    config.
  - `InstallState` grows a `Skill string` field; `InstallStates` probes per
    (skill, adapter, scope). This changes the `jentic skill list --json` row shape — a
    compat surface, call it out in the PR.
- Tests (see the expanded test list at the end of this section).

### C. CLI surface (Go)

`cli/internal/cmd/skill.go` + `bootstrap.go`:

- Add optional **`--skill <name>` (repeatable)**, validated against `BundledNames()`
  (like `--operator` is against `reg.Names()`); default = the full bundled set. **No
  interactive skill picker for v1** — the picker still chooses operators/scope only.
- `writeSkill` renders per skill and loops **skills × operators × scopes**; for a shared
  `AGENTS.md` it batches that file's blocks into one write (per decision above).
- Add **`jentic skill update`**: for each installed (skill, operator, scope), re-render
  **with the currently resolved BaseURL**, rewrite when the recorded hash differs, report
  per-skill status. A BaseURL change legitimately rewrites. Chips at #825.
- `jentic skill list`: per-skill install state (installed/detected) across operators.
- `bootstrap`: install the full set by default (still gated by `--skip-skill`).
- Tests: set install/update/list/remove, `--skill` filtering + validation, bootstrap set
  install, idempotent re-run (no spurious diffs).

### D. Backend HTTP serving (Python)

`src/jentic_one/shared/web/agent_discovery.py`:

- **Name validation is mandatory and layered** (defense-in-depth): validate `{name}`
  against **both** the Agent-Skills regex `^[a-z0-9]([a-z0-9-]*[a-z0-9])?$` (≤64) **and**
  membership in the shipped set (globbed `content/*.md`, single source of truth). Validate
  **before** calling the loader; miss → **404** (matches existing unknown-doc posture).
  Route uses `{name}` (default `str` converter, not `{name:path}`) so slashes/traversal
  can't match.
- `load_skill_markdown(name)` reads `content/{name}.md` via `importlib.resources` and
  returns raw bytes. Keep `@cache` — but only reached with a validated name, so the key
  space is the finite shipped set.
- **Route ordering:** register the literal `GET /skills/jentic.md` + `/SKILL.md` alias
  **before** the parameterized `GET /skills/{name}.md` (Starlette matches in registration
  order), or fold `jentic` into the param route. The router is already mounted before the
  broker catch-all — keep that.
- Add `GET /skills/index.json` → `[{name, description, version, sha256, url}]` where
  **`sha256` is `hashlib.sha256(raw_file_bytes)`** (decision 5), `url` is base-stamped
  absolute, `version` defaults to `"1"` when frontmatter omits it (freeform skills have
  no `version`). `JSONResponse`, `include_in_schema=False`.
- `render_llms_txt`: add a **`## Skills`** H2 listing the set as
  `- [<name>]({base}/skills/<name>.md): <one-line description>` — **collapse each
  description to a single line** (freeform descriptions are multi-clause). Absolute URLs;
  keep the existing H1 + blockquote.
- All new routes `include_in_schema=False`.
- Tests (extend `tests/unit/shared/web/test_agent_discovery.py`): each shipped skill
  fetchable; **unknown name → 404**; **explicit traversal attempt** (`/skills/a/b.md`,
  `%2e%2e` sequences) → 404; `index.json.sha256 == sha256(GET /skills/<name>.md bytes)`;
  `index.json.url` absolute + base-stamped; **OpenAPI exclusion** for `/skills/{name}.md`
  and `/skills/index.json`; **broker catch-all doesn't shadow** a non-`jentic` skill;
  `llms.txt` `## Skills` lists exactly the shipped set.

### E. Content mirror + drift + packaging

- **Generator**: a `make skills` target regenerates both
  `cli/internal/skillgen/content/<name>.md` and
  `src/jentic_one/shared/web/content/<name>.md` from each `skills/<name>/SKILL.md`
  (excluding `init-design`). Single human edit point.
- **`tests/arch/test_skill_drift.py`**: iterate the shipped name set (globbed, shared
  source of truth); for each assert `cli/.../content/<name>.md` ==
  `src/.../content/<name>.md` (byte-identical) **and** both mirror
  `skills/<name>/SKILL.md`. Run the frontmatter validator (name regex, description
  1–1024, no BaseURL placeholder). Fails on any single-sided add/edit.
- **Packaging**: backend needs **no** `pyproject` change — `content/` ships as a dir
  inside `src/jentic_one` via `packages=["src/jentic_one"]`; new `content/<name>.md`
  ship automatically. CLI: `//go:embed content/*.md` covers new files. Verify by building
  the wheel and asserting all three `content/*.md` are present.

### F. Hosted-source seam (optional, follow-up)

- Wire the stubbed `SourceHosted`: `jentic skill update --from-deployment` fetches
  `/skills/index.json` + `/skills/<name>.md` and refreshes installed skills. Comparison is
  **render-then-hash** locally (decision 5) — never installed-hash vs manifest-hash
  directly, since `jentic`'s rendered body carries the interpolated BaseURL. Chips further
  at #825/#277. Lands after A–E.

---

## Phasing

1. **A + E-source** — re-author the two skills under `skills/`, add the mirror generator,
   copy content into both `content/` dirs. (No behavior change yet; sets the source.)
2. **B** — skillgen set support (freeform kind, per-name targets, named blocks, migration).
   Fully unit-tested in isolation.
3. **C** — CLI `--skill`, set install in `init`/`bootstrap`, new `update`, `list` per-skill.
4. **D** — backend `/skills/{name}.md`, `/skills/index.json`, `llms.txt` skills section.
5. **E-drift/packaging** — iterate the drift test; wheel-content assertion in CI.
6. **F** — hosted-refresh seam (optional).

Phases 2–4 are independent enough to review as separate PRs; Phase 1 must land first
(it's the source the others render).

---

## Risks & mitigations

- **Non-conformant install (highest risk, now designed out):** putting managed-block
  markers inside a `.claude`/`.cursor`/`.hermes` `SKILL.md` is off-spec and injects
  "do not edit / hash=…" noise into the model-facing body. Mitigation: owned-file
  operators get a clean `frontmatter + verbatim body` file; provenance lives in a sidecar
  `.jentic-skill.json`. The managed block is reserved for `AGENTS.md`.
- **Legacy migration:** existing users have an old un-named `AGENTS.md` block (and,
  pre-fix, marker-wrapped dir files). The migration reader must extract/hash the legacy
  form using the **legacy marker constants** before rewriting, or the body hash mis-matches
  and the block is falsely flagged user-edited (refused without `--force`). Dedicated
  upgrade regression test.
- **Single-block splice engine:** `findBlock`/`splice`/`wholeFileSurroundEdited`/
  `InstallStates` all assume exactly one block per file. Multi-skill `AGENTS.md` needs
  named-block scan-and-replace-one-of-many, per-skill `Apply`/`Outcome` (one edited block
  must not freeze siblings), and one read-modify-write per file for a set. This is
  substantive engineering, not a rename.
- **BaseURL-in-hash:** the recorded hash covers the rendered (BaseURL-interpolated) body
  for `Canonical`. `update` re-renders with the resolved BaseURL and accepts that a
  BaseURL change legitimately rewrites; the manifest `sha256` is the **raw** file hash and
  is never compared directly to an installed hash.
- **`AGENTS.md` bloat:** resolved (decision 2) — codex/generic get **pointer blocks**
  (name + description + fetch link), not the ~750-line full bodies.
- **Schema relaxation:** a malformed skill could ship. Mitigation: build-time validator
  (name regex, description **1–1024**, frontmatter parses, explicit `metadata.kind`, no
  BaseURL placeholder in body) run in the drift/generator test.
- **`skill list --json` shape change:** adding a per-skill dimension changes the row
  shape (a compat surface). Call it out in the PR body.
- **Public/private boundary:** all public `jentic-one`; do not inline private rule text.
  Follow `jentic-one-rules` for CLI/web/test conventions.

## Definition of done

- `jentic skill init` (and `bootstrap`) installs `jentic` + `contribute-spec-fix` +
  `import-new-api` into detected operators; `jentic skill list` shows all three per
  operator; `jentic skill update` refreshes them; re-running init is idempotent (no
  spurious diffs).
- **Owned-file operators receive a clean spec `SKILL.md`** (frontmatter + verbatim body,
  **no managed markers inside**); provenance is in the sidecar. **An installed `SKILL.md`
  is verified to parse as a valid Agent Skill** (frontmatter loads; `name` regex;
  `description` 1–1024) — the closest CI-checkable proxy for "actually triggers in
  Claude/Cursor". codex/generic get named **pointer** blocks in `AGENTS.md`.
- A built wheel contains `content/jentic.md`, `content/contribute-spec-fix.md`,
  `content/import-new-api.md`; `GET /skills/<name>.md` serves each; unknown/traversal names
  404; `/skills/index.json` lists them with raw-bytes `sha256`; `llms.txt` has a Skills
  section; all new routes are hidden from OpenAPI.
- The drift/generator test iterates the shipped set (single-source name list) and fails on
  any single-sided change; the frontmatter validator passes for every served skill.
- No hardcoded `"jentic"` skill name remains in adapter `Target()` paths, the embed, or
  the backend loader (grep test, scoped to exclude legitimate brand strings like the
  `AGENTS.md` marker text and hermes tags).
