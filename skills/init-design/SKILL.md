# Skill: init-design

Set up and carry out a new design in `docs/design/designs/<name>/`. Follows the conventions established in `platform-actors` — a brief README index, problem statement, scope, architecture decisions, open questions, and per-concern sub-documents. Ends with scaffolded stubs for each planned document.

---

## Trigger

Invoked when the user asks to start a design for a new feature, module, or cross-cutting concern. Examples:
- "start a design for X"
- "set up the design docs for X"
- "run init-design on X"

---

## Steps

### 1. Clarify scope (ask the user)

Before writing anything, ask two questions in a single message:

> **a. Pull in related context?**
> Should I scan the repo for existing code, specs, or design docs relevant to `<feature>`? (Relevant modules, OpenAPI specs, existing ORM models, etc.) This shapes the problem statement and identifies constraints before you commit to an approach.
>
> **b. Documents to create?**
> Besides README.md, which concern-level docs do you want scaffolded now?
> Suggest a default list based on the feature — the platform-actors design used:
> `actors.md`, `access-scenarios.md`, `endpoints.md`, `openapi-sketch.yaml`, per-actor subdirs (each with `README.md`, `auth.md`, `endpoints.md`), `authorization-server/`.
> Say which you want, or say "suggest" and I'll propose a list with rationale.

Wait for the user's answers before proceeding.

### 2. Pull in related context (if yes)

Search for:
- Existing source modules that the feature touches (grep for the feature noun in `src/`, `openapi/`)
- Existing design docs that mention the feature or its entities
- OpenAPI specs that will need extending
- ORM models and migrations relevant to the new entities
- Existing permission scopes in `admin/core/permissions.py`

Summarise findings in a short bullet list to the user before writing any docs. This becomes the raw material for the "Relationship to existing work" section of the README.

### 3. Write the README

`docs/design/designs/<name>/README.md` — the index and anchor doc for the design. Structure:

```
# <Name> — Design

One-paragraph purpose statement.

## Problem statement
What is broken or missing today? Why does it matter?

## Scope
### In scope
- Bullet list
### Out of scope (deferred)
- Bullet list

## Design documents
Table: | Document | Status | Purpose |
Status values: Draft | TBD | Settled

## Architecture decisions
Key settled decisions with rationale. Use ### subsections per decision.
Name each decision with what was chosen and why, not just what the alternatives were.

## Key questions
Open questions that must be resolved before or during implementation.
Table: | ID | Question | Resolves in |
IDs: OQ-1, OQ-2, … (sequential; carry over if any are already settled)

## Request / interaction flow (optional)
Mermaid sequence or flowchart diagram if there is a clear primary flow.

## Relationship to existing work
Bullet list: what this design extends, replaces, or depends on.
```

### 4. Scaffold sub-documents

Create each planned sub-document as a stub. Each stub contains:
- A `# <Title>` heading
- A one-sentence purpose statement
- `## TBD` section with the key questions or concerns it will address
- A `## Reference` section pointing back to the README and peer docs

Do not write content that is not yet designed. Stubs are intentionally thin — they establish the document's existence and purpose so the README table is not broken, and so each doc can be filled in incrementally.

### 5. Confirm with the user

List the files created (with one-line summaries) and the open questions that need answers before implementation can start. Ask:

> Ready to fill in any of these documents now, or shall we commit the skeleton first?

---

## Conventions (enforced throughout)

These come from feedback on the platform-actors design work:

- **No padding.** Every sentence must earn its place. No meta-commentary ("this section will cover…"), no restatements of what the heading already says.
- **Resolve stale OQ refs.** When an open question is answered, move it out of the open-questions table and into a `### Resolved` subsection below it. Each resolved entry uses the format `#### OQ-N: <full original question text>` followed by a one-paragraph resolution summary and a link to the sub-document where the full detail lives. The full question is the heading — do not shorten it to a topic label. The open table stays clean — only genuinely unresolved questions; resolved ones stay visible with their answers. Add a matching `## OQ-N: <title> — resolved` section in the sub-document itself.
- **Relative paths only.** All cross-document links use relative paths. Verify paths on file creation and on any file rename/move.
- **Mermaid for flows.** Use `sequenceDiagram` for request flows, `flowchart TD` for entity/registration graphs. No ASCII flowcharts.
- **Status column stays current.** The README's document table has a Status column. Update it as documents graduate from TBD → Draft → Settled.
- **Scope = decisions + open questions.** The design is done when every OQ is resolved and every in-scope item has a settled document. Nothing else.

---

## Reference pattern (platform-actors)

The `platform-actors` design is the canonical example of this pattern in this repo:

```
docs/design/designs/platform-actors/
  README.md                         — index, problem, scope, arch decisions, OQs
  access-scenarios.md               — concrete end-to-end access patterns (input to auth design)
  endpoints.md                      — shared OAuth/identity endpoint surface
  openapi-sketch.yaml               — design-time OpenAPI sketch (not production spec)
  actors/
    README.md                       — index for actor-type subdirs
    actors.md                       — entity model: Users, Agents
    agent/
      README.md
      auth.md                       — DCR + JWT Bearer flow; token model; key rotation
      endpoints.md                  — agent-specific HTTP surface
    users/
      README.md
      auth.md
      endpoints.md
  authorization-server/
    README.md                       — OAuth surface ownership, pluggable IDP scope
    implementation-guide.md         — endpoints, concerns, risks, phased delivery
```

The README is always the entry point. All other documents are reachable from it, and all cross-references use relative paths.
