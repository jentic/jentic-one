# Skill: import-new-api

Import an API that is **not yet in [jentic-public-apis](https://github.com/jentic/jentic-public-apis)** end to end: generate an OpenAPI spec from the vendor's docs, publish it to the user's **single reusable public repo `import-openapi-specs`** on their personal GitHub account, and open the import issue that drives it into the community catalog — using the **raw content link** from that repo.

This is the greenfield counterpart to `contribute-spec-fix` (which fixes a spec already in the catalog). Use `contribute-spec-fix` when the vendor already exists; use this skill when it does not.

---

## Trigger

Invoked when the user wants to add a brand-new API to the catalog. Examples:
- "import the Firecrawl API into jentic-public-apis"
- "add <vendor> — it's not in the catalog yet"
- "generate a spec for <company> and open an import issue"
- "run import-new-api on <docs URL>"

If the user gives only a company name, find its public API docs by searching for `<company> API documentation`.

---

## Prerequisites

- `gh` CLI authenticated (`gh auth status`) with `repo` scope. Note which account is **active** — the spec repo is created there and its raw links must resolve publicly.
- Read access to a local jentic-public-apis checkout (default `~/oak`) to check for existing vendors.

---

## Steps

### 1. Confirm the vendor is NOT already in the catalog (hard gate)

Check the local catalog before doing any work:

```
ls -d <jentic-public-apis>/apis/openapi/<vendor-guess>* 2>/dev/null
```

Try the real domain (`firecrawl.dev`), bare name (`firecrawl`), and obvious variants. **If a matching vendor directory exists, STOP** and tell the user — re-importing produces a duplicate. Point them at `contribute-spec-fix` if they meant to fix the existing spec, or offer a genuinely-absent alternative.

### 2. Generate the OpenAPI spec (`api-to-openapi`)

Follow the `api-to-openapi` skill. In short:

1. **Path 1 — look for an official spec first.** Probe `https://{domain}/openapi.json`, `/swagger.json`, `api.{domain}/openapi.json`, and the vendor's GitHub org (`raw.githubusercontent.com/{org}/{repo}/main/.../openapi.json`). Vendors often ship a spec in-repo (Firecrawl publishes one at `firecrawl/firecrawl/apps/api/openapi.json`). If found, download it, validate it has a top-level `openapi`/`swagger` key, and **add `info.x-jentic-source-url`** pointing at where it came from. Done — do not hand-generate.
2. **Path 2 — generate from docs** only if no official spec exists. Extract every endpoint, method, path/query/body param, and auth scheme; build valid OpenAPI 3.0.3 with response/error schemas referenced via `$ref`, root-level `security`, and `securitySchemes` under `components`.

**Validate** before moving on: parses as JSON/YAML, has `openapi`, `info.title`, and `paths` with ≥1 path; note the server URL (flag localhost / `/` placeholders).

Decide the layout parts (used verbatim as the repo path):
- **vendor.domain** — real domain with TLD (`firecrawl.dev`), not a slug.
- **api-slug** — from `info.title`, lowercased, spaces→hyphens (`firecrawl-api`).
- **version** — from `info.version`, as-is; `1.0.0` if none.

### 3. Publish the spec to the reusable `import-openapi-specs` repo

The user keeps **one** public repo named `import-openapi-specs` on their own account for all their imports — created **once**, then reused for every subsequent API. Each API lands in its own path inside that repo, so specs never overwrite one another. This keeps every contribution in a single, predictable place rather than scattering one repo per API.

First determine the active account, then create the repo **only if it doesn't already exist** (idempotent — reuse the existing one otherwise):

```
account=$(gh api user --jq .login)
if gh repo view "$account/import-openapi-specs" >/dev/null 2>&1; then
  echo "Reusing existing $account/import-openapi-specs"
  gh repo clone "$account/import-openapi-specs" /tmp/import-openapi-specs -- -q
else
  gh repo create "$account/import-openapi-specs" --public \
    --description "OpenAPI specs staged for jentic-public-apis import" --clone
  # ...moved to /tmp/import-openapi-specs
fi
```

Lay the spec out under the standard path **inside that repo** (this is what keeps multiple APIs from colliding):

```
<repo-root>/{vendor.domain}/{api-slug}/{version}/openapi.json
```

Commit on `main`, and ensure the `import-jentic-pr-specs` branch (the one the raw-URL convention points at) exists and is fast-forwarded to `main`:

```
cd /tmp/import-openapi-specs
mkdir -p "{vendor.domain}/{api-slug}/{version}"
cp <spec>.json "{vendor.domain}/{api-slug}/{version}/openapi.json"
git add -A && git commit -m "Add <API> OpenAPI spec (<vendor>/<slug>/<version>)"
git push origin main
# create-or-update the shared import branch to include this spec
git branch -f import-jentic-pr-specs main
git push -f origin import-jentic-pr-specs
```

**Verify the raw link returns 200** before opening the issue — a 404 here silently breaks the import:

```
curl -s -o /dev/null -w "%{http_code}\n" -L \
  "https://raw.githubusercontent.com/$account/import-openapi-specs/refs/heads/import-jentic-pr-specs/{vendor.domain}/{api-slug}/{version}/openapi.json"
```

### 4. Open the import issue on jentic-public-apis

> ⚠️ **Injection hazard — keep the body free of shell metacharacters.** The import workflow interpolates the raw issue body directly into a shell script (`BODY="${{ github.event.issue.body }}"`). Any backtick or `$(...)` in the body is **command-substituted by the runner** and fails the job (e.g. a stray `` `x-jentic-source-url` `` in prose produces `x-jentic-source-url: command not found`). So the body you submit must contain **no backticks, no `$(...)`, no `${...}`** — not even inside comments or the "Additional Information" prose. Write the body plainly and, before calling `gh issue create`, assert it with `grep -qE '[\`$]' && { echo "unsafe body"; exit 1; }`.

Create the issue with the template below. The workflow parses `import_oas_url` and `vendor_name` by key (via `grep -oP '(?<=^import_oas_url:).*'`), so keep those two lines verbatim and at column 0.

- `import_oas_url` — the **raw** link (`raw.githubusercontent.com/...`), never a `github.com/.../blob/...` page URL.
- `vendor_name` — the vendor, which the workflow splits into `vendor` + `api_name`:
  - `firecrawl.dev` → `vendor=firecrawl.dev; api_name=main`
  - `firecrawl.dev/scrape` → `vendor=firecrawl.dev; api_name=scrape`

  Use a bare domain for a single-API vendor; append `/<api_name>` only when the vendor ships several distinct APIs.

Write the body to a variable, assert it is shell-safe, then create the issue:

```
BODY=$(cat <<'EOF'
## OpenAPI Specification URL

import_oas_url: https://raw.githubusercontent.com/<account>/import-openapi-specs/refs/heads/import-jentic-pr-specs/<PATH_TO_SPEC>

## Vendor Name (Required)

vendor_name: <VENDOR_NAME>

## Additional Information

<one or two plain sentences: what the API does, path count, auth scheme, source. No backticks, no $(...), no code fences.>
EOF
)

# Guard: the import workflow shell-evals the body — refuse anything with ` or $
printf '%s' "$BODY" | grep -qE '[`$]' && { echo "Unsafe issue body (contains backtick or \$)"; exit 1; }

gh issue create --repo jentic/jentic-public-apis \
  --title "[AUTO] Import OpenAPI to Jentic Public APIs: <VENDOR_NAME>" \
  --label "enhancement" \
  --body "$BODY"
```

Rules for the body:
- `import_oas_url:` and `vendor_name:` must each be a **line of their own, at column 0**, with a single space after the colon and the value on the same line. These are the only two lines the workflow reads.
- `<PATH_TO_SPEC>` is the repo-relative path (for example `firecrawl.dev/firecrawl-api/1.0.0/openapi.json`); `<VENDOR_NAME>` is the bare domain (for example `firecrawl.dev`), or `<domain>/<api_name>` for a multi-API vendor.
- Keep "Additional Information" to plain prose. **Do not** paste field names in backticks, JSON snippets, or shell expressions — those are exactly what break the runner. The HTML-comment help text from the GitHub template is optional and best omitted, since a stray metacharacter inside a comment breaks the parse just the same.

### 5. Report back

Give the user:
- The `import-openapi-specs` repo URL (noting whether it was created or reused) and the verified raw spec link.
- The import issue URL.
- Spec summary: path count, server URL, auth scheme, and where the spec was sourced (official vs generated).

---

## Notes

- **Never skip the existence check in Step 1.** The single most common failure mode is re-importing a vendor that's already catalogued.
- Prefer an official vendor spec over a hand-generated one whenever Path 1 finds one — always stamp it with `info.x-jentic-source-url`.
- The raw URL must be publicly reachable; private repos or `blob` page URLs will fail the import workflow.
- **One repo per user, reused forever.** Create `import-openapi-specs` only if it doesn't already exist; every subsequent import adds a new `{vendor}/{slug}/{version}/` subtree to the same repo. Don't create a fresh repo per API.
