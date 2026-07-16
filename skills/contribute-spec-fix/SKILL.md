---
name: contribute-spec-fix
description: Fix a broken OpenAPI spec in jentic-public-apis with an OpenAPI Overlay, validate it (spectral lint + idempotency check), and contribute it back via a PR to the community catalog. Falls back to applying the same overlay to the local Jentic registry if the user can't wait for maintainer approval. Use when an API spec is wrong/incomplete (bad server URL, missing enum, wrong content-type, missing param, etc.) and the fix should reach the community project, not just the local box.
argument-hint: [vendor/api or path to the broken spec]
---

# Contribute a Spec Fix (Overlay → PR → optional local apply)

Fix a spec in `jentic-public-apis` the right way: capture the fix as an **OpenAPI Overlay**,
validate it, and **open a PR so the whole community gets the fix**. This is the improvement
flywheel — fixes made by AI agents are contributed back, not siloed on one machine.

Target: `$ARGUMENTS` (a `vendor/api` id like `posthog.com/posthog-api`, or a path to a spec).

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
  and a registered agent (`jentic profile list` shows a valid token).

## Steps

### 1. Locate the spec and understand the fix

Resolve `$ARGUMENTS` to a spec at
`apis/openapi/<vendor>/<api>/<version>/openapi.json` in the jentic-public-apis checkout.
Read the relevant section and confirm with the user exactly what's wrong and what the fixed
value should be (e.g. "servers only has the US host; add an EU host"). Note the `<vendor>`,
`<api>`, and `<version>` path segments — you need them for both the PR and the local apply.

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
etc.). This script is tested; use it as-is.

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

Offer this **only if the user says they can't wait for maintainer approval**. It applies the
*same overlay document* to the locally-registered API via the Jentic registry's native overlay
API, so the fix is usable through the local broker immediately. The PR stays open — do not close
it; this is not a fork.

The API must already exist in the local registry (import it from the catalog first if needed:
`jentic catalog import <api_id>`). Then submit and confirm the overlay against the local control
plane (default `http://127.0.0.1:8000`). Use the agent's token from the active profile.

```
BASE=http://127.0.0.1:8000
TOKEN=$(jentic profile list --json 2>/dev/null | python3 -c "import json,sys;print(json.load(sys.stdin)['active']['token'])")  # or however the active token is exposed
V=<vendor>; N=<api>; VER=<version>

# Submit the overlay (document is the SAME overlay.json used for the PR)
curl -sS -X POST "$BASE/apis/$V/$N/$VER/overlays" \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d "$(python3 -c "import json;print(json.dumps({'document':json.load(open('$OVL')),'contributed_by':'contribute-spec-fix skill'}))")"
# → note the returned overlay "id" and the "_links.confirm" URL

# Confirm it (pending → confirmed) so it takes effect
curl -sS -X POST "$BASE/apis/$V/$N/$VER/overlays/<overlay_id>:confirm" \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' -d '{}'
```

Then verify locally, e.g. `jentic apis spec $V/$N/$VER` reflects the fix, or run the operation
through `jentic execute`. Tell the user: the local registry now has the fix, and PR
`<url>` is still the path for the community — nothing here replaces it.

## Guardrails

- **Never skip the PR.** The default and the whole reason this skill exists is contributing back.
  The local apply is opt-in and additive.
- **Never edit `openapi.json` by hand without the overlay.** The overlay is the auditable record;
  a hand-edit with no overlay breaks the flywheel and can't be re-derived or re-applied locally.
- **Both validations are gates, not formalities.** Do not open the PR if idempotency prints any
  `False` or lint shows any error / new finding code.
- **Reserve the local apply for genuine impatience.** If the user is fine waiting, the PR alone is
  the outcome.
```
