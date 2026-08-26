---
name: contribute-spec-fix
description: Fix a broken OpenAPI spec in jentic-public-apis with an OpenAPI Overlay, validate it (spectral lint + idempotency check), and contribute it back via a PR to the community catalog. Falls back to applying the same overlay to the local Jentic registry if the user can't wait for maintainer approval, and closes the loop by reacting when the upstream spec later changes (adopt update, or resolve an upstream-vs-overlay conflict). Use when an API spec is wrong/incomplete (bad server URL, missing enum, wrong content-type, missing param, etc.) and the fix should reach the community project, not just the local box.
metadata:
  kind: freeform
  argument-hint: "[vendor/api or path to the broken spec]"
---

# Contribute a Spec Fix (Overlay → PR → optional local apply)

Fix a spec in `jentic-public-apis` the right way: capture the fix as an **OpenAPI Overlay**,
validate it, and **open a PR so the whole community gets the fix**. This is the improvement
flywheel — fixes made by AI agents are contributed back, not siloed on one machine.

Target: `$ARGUMENTS` (a `vendor/api` id like `posthog.com/posthog-api`, or a path to a spec).

If the vendor is **not in the catalog at all**, this is the wrong skill — use `import-new-api`
instead; this skill only fixes specs that already exist.

## The flywheel — read this first

```
        broken spec found
                │
                ▼
     write overlay  ──►  validate (lint + idempotency)  ──►  apply to openapi.json
                │
                ▼
     ┌─────────────────────────────────────────────┐
     │  DEFAULT: PR to jentic/jentic-public-apis     │  ← community gets the fix
     └─────────────────────────────────────────────┘
                │
                ▼  (only if the user is blocked on maintainer approval time)
     ┌─────────────────────────────────────────────┐
     │  FALLBACK: apply the SAME overlay to the      │  ← unblocks the user now;
     │  local Jentic registry — PR stays open        │    PR is still the source of truth
     └─────────────────────────────────────────────┘
                │
                ▼  (later: the registry keeps watching upstream)
     ┌─────────────────────────────────────────────┐
     │  upstream spec changes  ──►  registry emits:  │
     │   • update_available      → re-import to adopt│  ← fix merged upstream? adopt + retire
     │   • update_conflicts_overlay → operator picks │  ← never silently overwrites the fix
     └─────────────────────────────────────────────┘
```

**Always open the PR.** The local apply is an impatience valve, not a substitute — it uses the
*same overlay document*, so nothing forks. Never skip the PR just because a local apply worked.

## Prerequisites

- A local checkout of `jentic-public-apis`. Find it (don't assume the path):
  `find ~ -maxdepth 4 -type d -name jentic-public-apis 2>/dev/null`. The repo has `origin`
  pointing at `jentic/jentic-public-apis`.
- `python3` — the format-preserving overlay applier (step 4) and the validation checks are Python.
- `spectral` on PATH (`spectral --version`) — used **only for linting** (step 6). Spectral is a
  linter/validator; it has no command to apply an overlay and emit a modified spec.
- `npx` available — `bump-cli` (fetched on demand via `npx --yes bump-cli`) is used **only as the
  idempotency cross-check** (step 5), never as the writer: it re-serializes and reorders keys, so
  it would reformat the whole file. Redocly 2.x can apply overlays too but only via a
  `redocly.yaml` config and it also reorders — same problem. No CLI applier preserves source
  formatting, which is why step 4 uses a small in-repo Python applier instead.
- `gh` authenticated (`gh auth status`) for the PR.
- For the local fallback only: a running Jentic control plane (default `http://127.0.0.1:8000`)
  and a registered agent (`jentic doctor` shows a resolvable identity with a valid token).

## Steps

### 1. Locate the spec and understand the fix

Resolve `$ARGUMENTS` to a spec at
`apis/openapi/<vendor>/<api>/<version>/openapi.json` in the jentic-public-apis checkout.
Read the relevant section and confirm with the user exactly what's wrong and what the fixed
value should be (e.g. "servers only has the US host; add an EU host"). Note the `<vendor>`,
`<api>`, and `<version>` **folder** path segments — you need them for the PR (they define where
the overlay file lives).

> **Folder path ≠ registry identity.** The `jentic-public-apis` folder segments are *not* the
> identity the registry serves under. On import the registry **slugifies** identity from the
> spec's own `info` block: `vendor` from `x-vendor`/`contact.name` and `name` from `info.title`,
> each lowercased with non-`[a-z0-9-]` → `-` (so `posthog.com` → `posthog-com`). Do **not** reuse
> the folder segments in `/apis/...` URLs — they will 404. For the local-apply steps (9–10) get
> the registry identity from the registry itself after import (shown there) and use *that* for
> `$V/$N/$VER`.

### 2. Branch off the latest main

Never work on a dirty tree or a stale base (see the no-direct-push-to-main rule).

```
git -C <repo> fetch origin main
git -C <repo> switch -c fix/<vendor>-<short-slug> origin/main
```

If the working tree has an unrelated in-progress state that blocks the checkout, prefer an
isolated `git worktree add -b fix/... <path> origin/main` so you don't disturb it. If you must
clear it in place, record the current branch tip first and confirm with the user.

Confirm the branch is at the tip of origin/main: `git -C <repo> log --oneline -1`.

### 3. Write the overlay

Create `apis/openapi/<vendor>/<api>/<version>/meta/overlay.json` (JSON for JSON specs). Use the
**OpenAPI Overlay 1.0.0** format. Prefer **remove-then-set** action pairs — they are
deterministic regardless of the starting state, which is what makes the overlay idempotent:

```json
{
  "overlay": "1.0.0",
  "info": { "title": "Overlay for the <Vendor> API", "version": "1.0.0" },
  "actions": [
    {
      "description": "Why the current value is wrong.",
      "target": "$.servers",
      "remove": true
    },
    {
      "description": "The corrected value.",
      "target": "$",
      "update": { "servers": [ /* corrected servers */ ] }
    }
  ]
}
```

`target` is JSONPath. For "a field that should offer a choice of hosts", use a single templated
server with a server **variable enum** (canonical OpenAPI), e.g.
`{"url":"https://{region}.posthog.com","variables":{"region":{"default":"eu","enum":["eu","us"]}}}`.
OAS 3.0.x requires every `{var}` in the URL to have a matching `variables` entry with a `default`.

### 4. Apply the overlay to `openapi.json`

Apply the overlay to produce the fixed `openapi.json`. Use a **format-preserving applier** so the
committed file keeps its original indentation and key order — this keeps the git diff scoped to
just the changed block, and (critically) lets the idempotency check in step 5 be a true
comparison rather than a formatting fight.

```
SPEC=apis/openapi/<vendor>/<api>/<version>/openapi.json
OVL=apis/openapi/<vendor>/<api>/<version>/meta/overlay.json
```

Do **not** use `npx bump-cli overlay` to produce the committed file: bump-cli minifies and
reorders keys, which reformats the whole document and moves `servers` to the end — a huge, noisy
diff. It's a fine cross-check (step 5), not the writer.

Apply the overlay in place with this format-preserving applier. It keeps 2-space indentation, the
trailing-newline convention, and — importantly — the **original position** of any key that a
`remove`+`update` pair re-creates (e.g. `servers`), so the diff stays scoped to the changed block.
It supports the JSONPath subset these overlays use (`$`, `$.servers`, `$.info`, `$.paths['/x'].get`,
etc.). It is deliberately small and covers the common fixes; step 5's idempotency check is the
gate that proves it applied your overlay correctly, so always run it rather than trusting the
applier blindly.

```
python3 - "$SPEC" "$OVL" <<'PY'
import sys, json, re
from collections import OrderedDict
spec_path, ovl_path = sys.argv[1], sys.argv[2]
raw = open(spec_path, encoding="utf-8").read()
had_final_nl = raw.endswith("\n")
spec = json.loads(raw, object_pairs_hook=OrderedDict)
overlay = json.load(open(ovl_path))

def resolve(root, target):
    if target == "$":
        return None, None, root
    node, parent, key = root, None, None
    for m in re.findall(r"\.([A-Za-z0-9_]+)|\['([^']*)'\]", target[1:]):
        k = m[0] or m[1]
        parent, key, node = node, k, node[k]
    return parent, key, node

orig_root_order = list(spec.keys())  # snapshot to restore positions after updates

for action in overlay.get("actions", []):
    parent, key, node = resolve(spec, action["target"])
    if action.get("remove"):
        if parent is not None:
            del parent[key]
    elif "update" in action:
        upd = action["update"]
        if parent is None:            # target "$": set each key on the root in place
            for k, v in upd.items():
                node[k] = v
        elif isinstance(node, dict) and isinstance(upd, dict):
            node.update(upd)
        else:
            parent[key] = upd

# Restore original root key order; genuinely new root keys keep insertion order at the end.
reordered = OrderedDict((k, spec[k]) for k in orig_root_order if k in spec)
for k in spec:
    reordered.setdefault(k, spec[k])
spec = reordered

out = json.dumps(spec, indent=2, ensure_ascii=False)
open(spec_path, "w", encoding="utf-8").write(out + ("\n" if had_final_nl else ""))
PY
git -C <repo> --no-pager diff --stat "$SPEC"   # expect only the changed block
```

If your fix touches JSONPath shapes beyond this subset (array-index targets, `remove` of a nested
sibling), extend `resolve`, or fall back to a surgical hand-edit of `$SPEC` that reproduces the
overlay's value exactly — then let step 5 (bump-cli) confirm the value matches.

### 5. Validation — idempotency check (REQUIRED)

Prove that applying the overlay again is a no-op. This guards CI re-runs and future improve
passes from drifting. Because bump-cli is a format-independent reference applier, compare the
**semantic** JSON (parsed values), not raw bytes:

```
npx --yes bump-cli overlay "$SPEC" "$OVL" -o /tmp/A.json     # apply to the committed (already-fixed) spec
npx --yes bump-cli overlay /tmp/A.json "$OVL" -o /tmp/B.json # apply again to the result
python3 -c "import json; \
spec=json.load(open('$SPEC')); A=json.load(open('/tmp/A.json')); B=json.load(open('/tmp/B.json')); \
print('overlay is a no-op on committed spec:', spec==A); \
print('re-applying is identical          :', A==B)"
```

`spec==A` compares parsed objects, so formatting/key-order differences don't matter — only the
values. Both lines must print `True`. `spec==A True` is the strongest form: the committed spec
already equals the overlaid result, so re-running the overlay changes nothing. If either is
`False`, the overlay is not idempotent (usually an `update` that merges into existing content
instead of a `remove`+`update` pair) — fix it before proceeding.

### 6. Validation — spectral lint (REQUIRED)

The resulting spec must be valid. There is no repo-specific ruleset, so extend the built-in OAS
rules.

```
printf 'extends: ["spectral:oas"]\n' > /tmp/ruleset.yaml
spectral lint "$SPEC" --ruleset /tmp/ruleset.yaml -f json --output.json /tmp/lint_new.json
```

Notes that will bite you if ignored:
- `spectral` **exits non-zero (13) on warnings** — run it as its own step, never chained with
  `&&`, or the chain aborts and later steps silently don't run.
- The `-f json` stdout has progress text before the JSON — use `--output.json <file>` and parse
  the file, not stdout.

Assert **zero errors**, and compare against the pre-change baseline so the overlay introduces no
new findings:

```
git -C <repo> show origin/main:"$SPEC" > /tmp/orig.json
spectral lint /tmp/orig.json --ruleset /tmp/ruleset.yaml -f json --output.json /tmp/lint_orig.json 2>/dev/null || true
python3 -c "import json; \
n=json.load(open('/tmp/lint_new.json')); o=json.load(open('/tmp/lint_orig.json')); \
err=[x for x in n if x.get('severity')==0]; \
print('errors:', len(err)); \
print('new codes vs baseline:', sorted(set(x['code'] for x in n)-set(x['code'] for x in o)) or 'NONE')"
```

`errors: 0` and `new codes vs baseline: NONE` is the pass condition. If there are errors, fix the
overlay and repeat steps 4–6.

### 7. Commit

Commit the two files together (overlay = record of the transform; `openapi.json` = applied
result — this matches existing overlay PRs in the repo).

```
git -C <repo> add "$SPEC" "$OVL"
git -C <repo> commit -m "improve(<vendor>-<api>-<version>): <what changed>

<why the original was wrong and what the fix does>

Recorded as meta/overlay.json (OpenAPI Overlay 1.0.0). Applied via bump-cli;
verified idempotent (re-apply is a no-op) and clean under spectral:oas."
```

### 8. DEFAULT — open the PR to the community catalog

This is the point of the skill: the fix reaches everyone.

```
git -C <repo> push -u origin fix/<vendor>-<short-slug>
gh pr create --repo jentic/jentic-public-apis --base main --head fix/<vendor>-<short-slug> \
  --title "improve(<vendor>-<api>-<version>): <what changed>" \
  --body-file /tmp/pr_body.md
```

The PR body **must** document all three of: the overlay applied to the spec, the idempotency
test, and the linting test. Paste the verbatim command output captured in steps 5–6. Template:

```markdown
## Summary
<what was broken, what the fix does>

## Overlay applied to the spec
New `meta/overlay.json` (OpenAPI Overlay 1.0.0); result committed to `openapi.json`.
Diff is scoped to the changed block:
<paste `git diff --stat` for openapi.json>

## Idempotency test
<paste step 5 output — both lines True>

## Linting test
<paste step 6 output — errors: 0, new codes vs baseline: NONE>
```

Report the PR URL to the user. **You are done with the default path** — the fix is now in front
of maintainers for the whole community.

### 9. FALLBACK — apply the same overlay locally (only if the user is blocked)

Offer this **only if the user says they can't wait for maintainer approval**. It submits the
*same overlay document* to the locally-registered API via the Jentic registry's native overlay
API. The PR stays open — do not close it; this is not a fork.

> **Platform behaviour (current):** confirming an overlay now **materializes** it — the registry
> re-ingests the base spec with the overlay applied and promotes the result to the API's current
> revision, so the served spec (`GET …/openapi`) reflects the fix once the (async) materialize
> job completes — usually a moment after confirm, not synchronously.
> Two things follow from this:
> - **Confirm is an operator action** and requires the `overlays:confirm` permission (not
>   `apis:write`). Contributors *submit* overlays; an operator reviews and *confirms*. Use a
>   token with `overlays:confirm` (an `org:admin` token also works — it implies the scope) for
>   the confirm call below, or ask an operator to confirm.
> - **Verify locally first.** Because confirm rewrites what the platform serves, treat the local
>   apply as the real verification of the fix: confirm, then re-download the spec and diff it
>   against your PR-branch `openapi.json` (they should match). If the overlay can't be applied
>   cleanly (drifted target, unsafe `servers[].url`), confirm is rejected and the overlay stays
>   `pending` — fix the overlay (steps 3–6) and retry.
> - **Stacking is cumulative, last-confirmed-wins, with no per-overlay unwind.** Confirming a
>   second overlay materializes it over the first; the only reversal is a rollback of the most
>   recent overlay (restoring the revision it superseded). See the
>   [overlay stacking contract](../../docs/guides/overlays.md) before authoring overlapping fixes.

The API must already exist in the local registry (import it from the catalog first if needed:
`jentic catalog import <api_id>`, where `<api_id>` is the catalog entry id — the dotted
`<vendor>/<api>` form, e.g. `posthog.com/posthog-api`). Then resolve the **registry** identity
(slugified — not the folder segments) and submit + confirm the overlay against the local control
plane (default `http://127.0.0.1:8000`).

```
# Run this block with bash (it uses `read ... < <(...)` process substitution, which
# POSIX `sh`/`dash` does not support).
#
# Every call goes through `jentic api` — the control-plane passthrough that
# reuses the CLI's active context, auth, and transport, so there is NO token to
# extract or export (the CLI attaches the bearer for you; V2 never exposes it).
# It targets whatever install your active context points at; use
# `--context <name>` to pick another. Submit needs apis:write; confirm needs
# overlays:confirm (an org:admin identity satisfies both) — if your agent
# identity lacks a scope, request it (`jentic access request`) or have an
# operator run the confirm step.

# Resolve the registry identity for the catalog entry you imported. The registry slugifies
# vendor/name from the spec's info block, so these differ from the jentic-public-apis folder
# segments (posthog.com → posthog-com; name = slug of info.title). Match the imported API by
# its catalog_api_id and read back the slugified (vendor, name, version) to use below.
API_ID=<api_id>   # the dotted catalog id, e.g. posthog.com/posthog-api
read V N VER SRC < <(jentic api GET /apis \
  | python3 -c "import json,sys; \
apis=json.load(sys.stdin).get('data', []); \
m=next((x for x in apis if x.get('catalog_api_id')=='$API_ID'), None); \
a=(m or {}).get('api', {}); \
print(a.get('vendor',''), a.get('name',''), a.get('version',''), (m or {}).get('source_url',''))")
echo "registry identity: $V/$N/$VER   source_url: $SRC"   # empty ⇒ not imported yet; import first

# Submit the overlay (document is the SAME overlay.json used for the PR)
jentic api POST "/apis/$V/$N/$VER/overlays" \
  -d "$(python3 -c "import json;print(json.dumps({'document':json.load(open('$OVL')),'contributed_by':'contribute-spec-fix skill'}))")"
# → note the returned overlay "id" and the "_links.confirm" URL

# Confirm it (pending → confirmed) — requires overlays:confirm. This materializes the
# overlay: it re-ingests the base spec with the overlay applied and serves the result.
# If your active identity lacks overlays:confirm, run this step under a context whose
# identity has it (`--context <operator>`; an org:admin identity also works).
jentic api POST "/apis/$V/$N/$VER/overlays/<overlay_id>:confirm" -d '{}'
```

Verify the fix actually landed on the served spec (this is the local verification of the fix):

```
# Give the ingest job a moment, then re-download and diff against your PR-branch spec.
# The served-spec route is `…/openapi` (JSON by content negotiation) — there is no
# `…/openapi.json` route on the registry.
jentic api GET "/apis/$V/$N/$VER/openapi" > /tmp/served.json
python3 -c "import json; print('served matches PR spec:', json.load(open('/tmp/served.json'))==json.load(open('$SPEC')))"
```

`served matches PR spec: True` confirms the local apply reproduces the PR-branch fix. The overlay
is now materialized locally and PR `<url>` is still the path for the community — nothing here
replaces it.

### 10. Close the loop — react when upstream changes later

The flywheel isn't "submit and forget". Once an overlay is materialized locally, the registry
keeps watching the API's **upstream** spec (the catalog `spec_url`) and, when it changes,
surfaces an actionable event so a human isn't lied to about state. Two outcomes, and they need
**different** reactions — the platform tells you which via the event **type**:

- **`catalog.update_available`** — upstream changed, but not in a way that collides with your
  overlay's base. Routine: re-import the upstream to adopt it. If your fix was already merged into
  `jentic-public-apis` (your PR landed), the upstream now *contains* the fix and the overlay is
  redundant — adopting upstream is the right move and the overlay can be retired.
- **`catalog.update_conflicts_overlay`** — upstream changed *against the very base your overlay was
  materialized over*. Adopting upstream would **supersede your fix**. This is deliberately an
  operator decision, not an automatic overwrite: the system will **not** silently discard the
  overlay.

Check for these — the class distinction lives in the **event type**, not in the API view (which
only carries a boolean `update_available`). Query the events surface and branch on which class is
pending for this API:

```
# Reuse the registry identity + source_url resolved in step 9 ($V/$N/$VER/$SRC). If you
# start fresh here, re-resolve them the same way (jentic api GET /apis, match on catalog_api_id).
# All calls go through `jentic api` — active context + auth attached automatically.
# Is there ANY pending update? (API view; boolean, no class):
jentic api GET "/apis/$V/$N/$VER" \
  | python3 -c "import json,sys; a=json.load(sys.stdin); \
print('origin          :', a.get('origin')); \
print('update_available:', a.get('update_available'))"

# WHICH class? Look for an actionable conflict event for this API. If this returns a row,
# it's the operator-decision path; if empty (but update_available is true), it's the
# routine adopt path. The conflict event's data carries the overlay_id to act on.
# (Events live on the admin/control plane; listing needs events:read — an org:admin/
# operator identity has it. Use `--context <operator>` if your agent identity lacks it.)
jentic api GET /events \
  --query event_type=catalog.update_conflicts_overlay \
  --query requires_action=true --query acknowledged=false \
  | python3 -c "import json,sys; \
evs=json.load(sys.stdin).get('data', []); \
mine=[e for e in evs if (e.get('data') or {}).get('spec_url')=='$SRC']; \
print('conflicts_overlay pending:', bool(mine)); \
print('overlay_id:', (mine[0]['data'].get('overlay_id') if mine else None))"
# Match on data.spec_url (the upstream URL) — it is present on BOTH the sweep-emitted conflict
# event and the refuse-path event (logged when an under-scoped caller attempts the adopt), and
# it equals the API's source_url you read in step 9. Do NOT filter on data.vendor: the sweep
# event carries the *slugified* vendor and the refuse-path event carries no vendor at all, so a
# vendor filter silently misses real rows. (data.api_id is the local UUID, not the catalog id.)
```

**Reacting to `catalog.update_available`** (adopt upstream — your fix is upstream now, or the
change is unrelated and you no longer need the overlay): re-import the catalog entry. A plain
re-import adopts the upstream spec and **settles the event** automatically. This needs
`catalog:import` (an `apis:write` scope implies it):

```
jentic api POST "/catalog/<api_id>:import" -d '{}'
```

**Reacting to `catalog.update_conflicts_overlay`** (upstream diverged from your fix's base): this
is an operator call with two clean options — **never** hand-edit around it:

1. **Keep your fix, ignore upstream for now.** Do nothing; the served spec stays your overlay. The
   event stays in the inbox as a truthful "there's a divergence" flag. If your PR is still open,
   the real resolution is landing it upstream.
2. **Adopt upstream and retire the overlay** (you've decided upstream wins). Re-importing the
   catalog entry over a *live confirmed overlay* is doubly gated: the `:import` route itself
   requires **`catalog:import`**, and superseding the overlay additionally requires
   **`overlays:confirm`** (because it discards an operator's fix). So the caller needs **both**
   scopes — an `org:admin` identity satisfies both by implication; `overlays:confirm` *alone* is
   rejected by the route guard before the supersede is even evaluated. An authorized re-import
   auto-deprecates the overlay and serves the fresh upstream in one step; a caller with
   `catalog:import` but **not** `overlays:confirm` is **refused** (403 `overlay_supersede_forbidden`)
   and the conflict is re-surfaced for someone who can decide — the fix is never silently reverted.

```
# Authorized adopt-upstream. Run under a context whose identity holds BOTH catalog:import
# and overlays:confirm (org:admin implies both) — e.g. `--context <operator>`, or ask an
# operator to run it if your agent identity lacks the scopes. The platform detects the live
# overlay and supersedes it because you hold overlays:confirm; the same call by a
# catalog:import-only identity returns 403.
jentic api POST "/catalog/<api_id>:import" -d '{}'
```

If instead you decide the overlay was the right answer and want to **undo** a materialization you
just made (e.g. confirmed the wrong overlay), roll it back — this restores the exact revision the
overlay superseded (also `overlays:confirm`):

```
jentic api POST "/apis/$V/$N/$VER/overlays/<overlay_id>:rollback" -d '{}'
```

The loop is closed when the served spec, the overlay's lifecycle status, and the action inbox all
agree: either upstream was adopted (overlay `deprecated`, event settled) or the fix is deliberately
retained (overlay `confirmed`, divergence flagged but not hidden).


## Guardrails

- **Never skip the PR.** The default and the whole reason this skill exists is contributing back.
  The local apply is opt-in and additive.
- **Never edit `openapi.json` by hand without the overlay.** The overlay is the auditable record;
  a hand-edit with no overlay breaks the flywheel and can't be re-derived or re-applied locally.
- **Both validations are gates, not formalities.** Do not open the PR if idempotency prints any
  `False` or lint shows any error / new finding code.
- **Reserve the local apply for genuine impatience.** If the user is fine waiting, the PR alone is
  the outcome.
- **Never hand-resolve an upstream conflict.** When the registry emits
  `catalog.update_conflicts_overlay`, adopt upstream via a scoped re-import or deliberately keep the
  overlay — never edit the served spec by hand to paper over the divergence. Adopting upstream over
  a live confirmed overlay requires **both** `catalog:import` (route) and `overlays:confirm`
  (supersede) — i.e. an `org:admin` token or both scopes; the platform refuses with a 403 (not a
  silent revert) if you lack `overlays:confirm`.
