---
name: import-new-api
description: Import an API that is not yet in jentic-public-apis end to end — generate or find an OpenAPI spec from the vendor's docs, optionally import it into the local Jentic registry so the agent is unblocked immediately, publish it to the user's reusable public import-openapi-specs repo, and open the import issue that drives it into the community catalog. Use when the user says "import the <vendor> API", "add <vendor> — it's not in the catalog yet", "generate a spec for <company> and open an import issue", or gives a docs URL to import. For fixing a spec already in the catalog, use contribute-spec-fix instead.
metadata:
  kind: freeform
  argument-hint: "[vendor name or API docs URL]"
---

# Import a New API (spec → local registry → import-openapi-specs repo → import issue)

Import an API that is **not yet in [jentic-public-apis](https://github.com/jentic/jentic-public-apis)**:
generate an OpenAPI spec from the vendor's docs, optionally import it into the **local Jentic
registry first** (so the user's agent can execute it immediately), then publish it to the user's
single reusable public repo `import-openapi-specs` and open the import issue that drives it into
the community catalog.

This is the greenfield counterpart to `contribute-spec-fix` (which fixes a spec already in the
catalog). Use `contribute-spec-fix` when the vendor already exists; use this skill when it does not.

Target: `$ARGUMENTS` (a vendor name like `firecrawl`, or a URL to the vendor's API docs). If only
a company name is given, find its public API docs by searching for `<company> API documentation`.

## Prerequisites

- `gh` CLI authenticated (`gh auth status`) with `repo` scope. Note which account is **active** —
  the spec repo is created there and its raw links must resolve publicly.
- No local jentic-public-apis checkout needed — the vendor-existence check runs against GitHub
  via `gh`.
- For the optional local-first step only: a running Jentic control plane and a registered agent
  profile (`jentic access whoami` confirms the control plane is reachable).

## Steps

### 1. Confirm the vendor is NOT already in the catalog (hard gate)

Check the live repo, not a local checkout:

```
gh api "repos/jentic/jentic-public-apis/contents/apis/openapi" --paginate \
  --jq '.[].name' | grep -i '<vendor-guess>'
```

Try the real domain (`firecrawl.dev`), bare name (`firecrawl`), and obvious variants (with and
without subdomains: `api.foo.com` vs `foo.com`). **If a matching vendor directory exists, STOP**
and tell the user — re-importing produces a duplicate, and the import workflow hard-fails on an
existing version directory anyway. Point them at `contribute-spec-fix` if they meant to fix the
existing spec, or offer a genuinely-absent alternative.

### 2. Generate the OpenAPI spec

Two paths, in strict priority order:

1. **Path 1 — look for an official spec first.** Probe `https://{domain}/openapi.json`,
   `/swagger.json`, `api.{domain}/openapi.json`, and the vendor's GitHub org
   (`raw.githubusercontent.com/{org}/{repo}/main/.../openapi.json`). Vendors often ship a spec
   in-repo (Firecrawl publishes one at `firecrawl/firecrawl/apps/api/openapi.json`). If found,
   download it, validate it has a top-level `openapi`/`swagger` key, and **add
   `info.x-jentic-source-url`** pointing at where it came from. Done — do not hand-generate.
2. **Path 2 — generate from docs** only if no official spec exists. Extract every endpoint,
   method, path/query/body param, and auth scheme; build valid OpenAPI 3.0.3 with response/error
   schemas referenced via `$ref`, root-level `security`, and `securitySchemes` under `components`.

In both paths, embed **`info.x-vendor`** = the vendor domain (e.g. `firecrawl.dev`) next to
`info.x-jentic-source-url` — the local registry's ingest resolves vendor from `x-vendor` (falling
back to `contact.name`) and fails with `missing vendor` without it.

**Validate** before moving on: parses as JSON/YAML, has `openapi`, `info.title`, and `paths` with
≥1 path; note the server URL.

**Private-host refusal (do not skip).** If any `servers[].url` points at a private or internal
host, **refuse to publish and stop after the local-first step**:

```
python3 -c "
import json,sys,re
spec=json.load(open('<spec>.json'))
bad=re.compile(r'localhost|127\.|10\.|172\.(1[6-9]|2[0-9]|3[01])\.|192\.168\.|\.(internal|local|corp|lan)([/:]|$)')
hosts=[s.get('url','') for s in spec.get('servers',[])]
print('PRIVATE' if any(bad.search(h) for h in hosts) else 'OK', hosts)"
```

`PRIVATE` means this looks like an internal API — publishing it to a public repo would disclose
it. Tell the user why you stopped; only continue if they explicitly confirm the API is public.

Decide the layout parts (used verbatim as the repo path, deterministically):
- **vendor.domain** — the real registrable domain with TLD (`firecrawl.dev`), lowercase, no
  scheme, no `www.`, no API subdomain (`api.foo.com` → `foo.com`). Not a slug.
- **api-slug** — from `info.title`: lowercase, spaces and `/` → hyphens, strip any character that
  is not `[a-z0-9-]`, collapse repeats (`Firecrawl API` → `firecrawl-api`).
- **version** — `info.version` verbatim (`1.0.0`, `v1.2`, `2023-01-01` are all fine as directory
  names); `1.0.0` only when the spec has none.

### 3. Local-first: import into the local Jentic registry (optional)

If a local jentic-one instance is running, import the spec there **before** contributing
upstream — the user's agent can execute the API immediately instead of waiting on catalog
review. Skip this step if `jentic access whoami` reports the control plane unreachable.

Local import needs the `apis:write` scope, which is not granted by default — request it once:

```
jentic access request --scope apis:write --reason "import a locally generated spec for <vendor>" --wait
jentic access refresh
```

There is no CLI upload command; POST the spec inline to the control plane (run
`jentic access whoami` first so the cached token is fresh):

```
TOKEN=$(jq -r .access_token ~/.jentic/profiles/<profile>/tokens.json)
JOB=$(curl -fsS -X POST "$BASE_URL/apis" \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d "$(jq -n --rawfile spec <spec>.json \
    '{sources:[{type:"inline",content:$spec,filename:"openapi.json"}]}')" | jq -r .job_id)
```

Poll `GET $BASE_URL/jobs/$JOB` until `completed`, read the revision id from
`GET $BASE_URL/jobs/$JOB/result`, then promote the draft to live (vendor and name are slugified
by ingest: `firecrawl.dev` → `firecrawl-dev`):

```
jentic apis promote <vendor-slug/name-slug/version> <revision_id>
```

**Verify**: `jentic search "<something the API does>"` returns one of its operations, and a
known-safe `jentic execute <operation_id>` succeeds (credentials permitting — see the `jentic`
skill). The user is now unblocked; the remaining steps contribute the spec to the community.

### 4. Publish the spec to the reusable `import-openapi-specs` repo

> **CONSENT GATE — publishing is public and under the user's name.** Before creating or pushing
> anything, show the user: the vendor and API title, every `servers[].url`, the path count, the
> exact repo path the file will land at, and the fact that `import-openapi-specs` is a **public**
> repo on **their** GitHub account. Ask for explicit confirmation. Do not proceed on silence.
> If the spec was generated from docs behind a login, or the private-host check in step 2 printed
> `PRIVATE`, treat "no" as the default.

**Secrets scan (before any commit).** Docs examples sometimes embed real keys; refuse to publish
if the spec contains anything that looks like a credential:

```
grep -nE '(api[_-]?key|secret|token|password)["'\'':= ]+[A-Za-z0-9_/+-]{16,}' <spec>.json \
  && echo "POSSIBLE SECRET — inspect before publishing" || echo "clean"
```

The user keeps **one** public repo named `import-openapi-specs` on their own account for all
their imports — created **once**, then reused. Each API lands in its own
`{vendor.domain}/{api-slug}/{version}/` subtree, so specs never overwrite one another. Clone to
a predictable location in both branches:

```
account=$(gh api user --jq .login)
rm -rf /tmp/import-openapi-specs
if gh repo view "$account/import-openapi-specs" >/dev/null 2>&1; then
  echo "Reusing existing $account/import-openapi-specs"
else
  gh repo create "$account/import-openapi-specs" --public \
    --description "OpenAPI specs staged for jentic-public-apis import"
fi
gh repo clone "$account/import-openapi-specs" /tmp/import-openapi-specs -- -q
```

If `gh repo view` finds a repo by that name that is clearly **not** a spec-staging repo (it has
unrelated content), stop and ask the user for an alternative name rather than committing into it.

Lay the spec out and push to `main` (the import workflow fetches whatever public raw URL you give
it — no special branch is required):

```
cd /tmp/import-openapi-specs
mkdir -p "{vendor.domain}/{api-slug}/{version}"
cp <spec>.json "{vendor.domain}/{api-slug}/{version}/openapi.json"
git add -A && git commit -m "Add <API> OpenAPI spec (<vendor>/<slug>/<version>)"
git push origin main
```

**Verify the raw link returns 200** before opening the issue — a 404 silently breaks the import.
raw.githubusercontent can cache a 404 briefly after a push, so retry with backoff:

```
RAW="https://raw.githubusercontent.com/$account/import-openapi-specs/refs/heads/main/{vendor.domain}/{api-slug}/{version}/openapi.json"
for i in 1 2 3 4 5; do
  code=$(curl -s -o /dev/null -w "%{http_code}" -L "$RAW"); [ "$code" = 200 ] && break; sleep $((i*5))
done; echo "raw link: $code"
```

### 5. Open the import issue on jentic-public-apis

The workflow triggers on any opened/reopened issue whose body contains `import_oas_url:` — the
`[AUTO]` title and `enhancement` label are template conventions (keep them for humans; the parser
ignores them). It extracts `import_oas_url` and `vendor_name` with a line-anchored grep after
stripping leading whitespace and trimming around the value, so: each key on **its own line**, the
value on the same line after the colon.

- `import_oas_url` — the **raw** link verified in step 4 (`raw.githubusercontent.com/...`), never
  a `github.com/.../blob/...` page URL.
- `vendor_name` — validated against a safe charset (letters, digits, `.`, `-`, `_`, and a single
  optional `/` for `<domain>/<api_name>`, each segment starting with a letter or digit); anything
  else is rejected with a comment on the issue. It is **not** checked for *correctness*, though, so
  get it right: the bare domain from step 2 for a single-API vendor
  (`firecrawl.dev` → `vendor=firecrawl.dev; api_name=main`), or
  `<domain>/<api_name>` when the vendor ships several distinct APIs
  (`firecrawl.dev/scrape` → `vendor=firecrawl.dev; api_name=scrape`). A slug or misspelt domain
  is accepted silently and mis-catalogues the API.

```
BODY=$(cat <<'EOF'
## OpenAPI Specification URL

import_oas_url: <RAW_URL_FROM_STEP_4>

## Vendor Name (Required)

vendor_name: <VENDOR_NAME>

## Additional Information

<one or two plain sentences: what the API does, path count, auth scheme, source.>
EOF
)

gh issue create --repo jentic/jentic-public-apis \
  --title "[AUTO] Import OpenAPI to Jentic Public APIs: <VENDOR_NAME>" \
  --label "enhancement" \
  --body "$BODY"
```

**Show the user the final issue body and the staged file path before running `gh issue create`** —
they are publishing under their own GitHub identity, and vendor docs are untrusted input that
could have steered the generation.

**After creating the issue, watch it**: the workflow comments the outcome on the issue — the
auto-created import PR on success, or the failure reason (extract error, validation rejection
such as invalid server URLs, import-service error, duplicate version) on failure. Fix the cause
and **reopen the issue** to re-trigger the run. If validation rejects a legitimately unusual
spec, the body also accepts `reject_invalid_server_urls:` / `reject_invalid_security:` override
lines — use them only deliberately.

### 6. Report back

Give the user:
- The local registry result (step 3): the promoted `vendor-slug/name-slug/version` and a working
  operation, or why it was skipped.
- The `import-openapi-specs` repo URL (created or reused) and the verified raw spec link.
- The import issue URL **and the auto-created import PR URL** from the workflow's comment.
- Spec summary: path count, server URL, auth scheme, and where the spec was sourced (official vs
  generated).

## Guardrails

- **Never skip the existence check in Step 1.** The single most common failure mode is
  re-importing a vendor that's already catalogued.
- **Never publish without the consent gate.** A spec generated from internal or login-walled docs
  leaking to a public repo is an exfiltration incident, not a contribution. When in doubt, stop
  at step 3.
- **Prefer an official vendor spec** over a hand-generated one whenever Path 1 finds one — always
  stamp it with `info.x-jentic-source-url`, and always embed `info.x-vendor`.
- **The raw URL must be publicly reachable**; private repos or `blob` page URLs fail the import
  workflow.
- **One repo per user, reused forever.** Create `import-openapi-specs` only if it doesn't already
  exist; every import adds a new `{vendor}/{slug}/{version}/` subtree. Don't create a fresh repo
  per API.
- **You act under the user's GitHub identity.** Show them the issue body and file list before
  submission.
