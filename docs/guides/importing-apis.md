# Import APIs

An agent can only search and call APIs that are in this instance's registry.
You get them there two ways: import an entry from the public
[Jentic API Directory](https://github.com/jentic/jentic-public-apis) catalog,
or import your own OpenAPI description. Either way the import runs as a
background job and produces an API **revision**; the broker routes calls by
the revision's server hosts.

Routes and permissions: [endpoint reference](../reference/endpoints.md). CLI
flags: the in-app CLI reference (`/app/docs`, Reference → CLI).

## From the public catalog

| Surface | How |
| ------- | --- |
| Web UI | **Library** (`/app/library`): open an entry, then **Add to workspace** |
| CLI | `jentic catalog search <query>`, then `jentic catalog import <api_id>` (`jentic catalog` bare opens an interactive browser) |
| MCP | `search_catalog`, then `import_api` with the hit's `api_id` |
| HTTP | `POST /catalog/{api_id}:import` (`catalog:import`) → 202 with a `job_id`; poll `GET /jobs/{job_id}` (`jobs:read`) |

The `api_id` is `domain[/sub-api]`, for example `httpbin.org/httpbin`. Over
HTTP, send its `/` unencoded: `POST /catalog/httpbin.org/httpbin:import`.

A catalog import is served as soon as the job completes, unless the
[server-host guard](#re-import-revisions-and-promote) holds it as a draft.
`jentic catalog import` waits for the job (`--timeout`, default 2m) and then
promotes any draft revision it produced; pass `--no-promote` to leave drafts
alone, or `--no-wait` to return the job id immediately. The MCP `import_api`
tool does the same wait-and-promote; if it returns a non-terminal status,
call it again with the same `api_id`.

## Your own OpenAPI description

Use this for private or internal services, or for an API the catalog lacks.

| Surface | How |
| ------- | --- |
| Web UI | **Library** → **Import your own API** → *From URL*, *Paste content*, or *Upload a file* (`.json`, `.yaml`, `.yml`) |
| CLI | `jentic apis import <file\|url>` — a local path is sent inline, an `http(s)` URL is fetched by the server. `--vendor`, `--name`, `--version` override the identity; `--dry-run` shows the request without sending it |
| HTTP | `POST /apis` (`apis:write`) → 202 with a `job_id` |

`POST /apis` takes 1–100 `sources`, each `{"type": "url", "url": …}` or
`{"type": "inline", "filename": …, "content": …}`, optionally with `vendor`,
`api_name`, and `version` (by default read from the document).

What ingest accepts:

- **OpenAPI 3.x**, as JSON or YAML. A document without an `openapi` key
  (for example Swagger 2.0) fails with `Missing 'openapi' key`; Arazzo
  documents are refused.
- **Size:** each inline document up to `ingest.max_spec_bytes` (25 MiB by
  default); the whole request body up to `ingest.max_import_request_bytes`.
  Over either limit the request is refused with 413 before a job is queued.
- **URL fetches** follow up to `ingest.max_redirects` and pass the ingest
  egress policy: a host on a private network needs
  `ingest.egress.allowed_private_subnets` or
  `ingest.egress.allowed_internal_domains`.

A manual import always lands as a **draft**. Nothing serves it until you
promote it (below).

## Server URLs

The broker routes every call by its upstream host, so each operation needs an
absolute server URL. Ingest handles the cases a description declares:

- **Absolute servers** (`https://api.example.com/v1`) are used as written.
- **Relative servers** (`url: /api/v3`, as in the stock Petstore) resolve
  against the URL the description was fetched from, after redirects:
  importing `https://petstore3.swagger.io/api/v3/openapi.json` gives
  `https://petstore3.swagger.io/api/v3`. Document, path-item, and operation
  `servers` are all resolved.
- **No `servers` at all**: the API imports inspect-only; `execute` refuses its
  operations with `RESOLVE_FAILED`. Ingest does not apply the OpenAPI default
  (`/`), since that would point the API at whatever host serves the file.

The import job's result lists what ingest did in `server_url_changes`
(`resolved`, `dropped`, `unresolved`). Check the resolved host before you
promote the revision or bind a credential to the API.

`execute` checks `broker.egress` separately from the ingest egress policy: an
internal host must be allowed in both.

## Re-import, revisions, and promote

Each import with new content creates a new revision; an API serves one
revision at a time. Importing content byte-identical to an active revision
fails the job with `A revision with identical content already exists for this
API version`.

| Task | CLI | HTTP |
| ---- | --- | ------------------- |
| List revisions | `jentic apis revisions <vendor/name/version>` | `GET /apis/{vendor}/{name}/{version}/revisions` (`apis:read`) |
| Promote a draft to live | `jentic apis promote <vendor/name/version> <revision_id>` | `POST …/revisions/{revision_id}:promote` (`apis:write`) |
| Archive a draft | `jentic apis archive <vendor/name/version> <revision_id>` | `POST …/revisions/{revision_id}:archive` (`apis:write`) |
| Delete an archived revision, or the API | `jentic apis rm <vendor/name/version> [revision_id]` | `DELETE …/revisions/{revision_id}`, `DELETE /apis/{vendor}/{name}/{version}` (`apis:write`) |
| Download the spec | `jentic apis spec <vendor/name/version>` | `GET …/revisions/{revision_id}/openapi` (`apis:read`) |

In the UI, the API's page (`/app/library/workspace/<vendor>/<name>/<version>`)
has a **Revisions** section with **Promote** per draft. Promoting archives
the revision it replaces.

To try a draft before promoting it, pin calls to it with the header
`Jentic-Revision: <vendor>:<name>:<version>=<revision_id>`.

**Server-host guard.** When the new revision declares different server hosts
(or moves a host to plaintext `http`) and the API has credentials bound to
agents, making it current needs `org:admin`, since the bound credentials
would be sent somewhere new:

- a catalog re-import by anyone else lands as a draft and the current
  revision stays live;
- a promote by anyone else is refused with 403 `host_change_requires_operator`;
- a `Jentic-Revision` pin to such a draft resolves only for an `org:admin`.

**Catalog updates.** `jentic catalog outdated` lists registered entries whose
upstream spec changed; re-run `jentic catalog import <api_id>` to adopt the
change. Snooze a known change with `POST /catalog/{api_id}:snooze`
(`events:write`). Re-importing over a live confirmed overlay also needs
`overlays:confirm` — see [overlays](overlays.md#the-update-loop).

## Troubleshooting

### Import fails: server URL can't be made absolute

A manual import (URL, paste, or upload) is refused when a `servers` list is
left with no absolute URL. The job error names the reason:

| Error starts with | Cause | Fix |
| --- | --- | --- |
| `relative server URL has no base` | Pasted or uploaded inline: there is no document URL. | Import from the description's URL, or make the server URL absolute. |
| `spec host serves files` | The description is served from a file host (see below), not from the API. Resolving would send the API's credentials to the file host. | Make the server URL absolute (`https://api.example.com/v3`), or import the catalog entry instead. |
| `server URL looks like a host; add https://` | `api.example.com/v1`, `localhost:8080`, or `[::1]:8080/v1` is a host without a scheme, not a path. | Add the scheme. |
| `server URL starts with a file name` | A path like `index.php/api` can't be told from a mistyped host, so it is refused too. | Write it as a path from the root (`/index.php/api`), or make it absolute. |
| `spec URL redirected to http` | An https URL redirected to plaintext http. | Import from the final https URL, or make the server URL absolute. |
| `server URL must be http(s)`, `… only a query or fragment`, `… starts with a relative variable`, `… has an empty host` | The URL can't name an http(s) API host (`file:`, `?v=3`, `{basePath}/v1` with a relative default, `//`). | Make the server URL absolute. |

File hosts include Git forges (`github.com`, `*.githubusercontent.com`,
`gitlab.com`, `bitbucket.org`), repository CDNs (`*.jsdelivr.net`,
`unpkg.com`, SwaggerHub), cloud object storage (S3, Google Cloud Storage,
Azure Blob), Dropbox, static sites (`*.github.io`, `*.gitlab.io`), and the
catalog mirror. No list covers every file host, so check the resolved host in
`server_url_changes` before you promote a manual import or bind a credential
to it.

Unresolvable entries next to an absolute server in the same list are dropped
instead, and listed in `server_url_changes`. Catalog imports, catalog updates,
and overlay materializations never fail on this: an unresolvable server stays
as declared and the operations it covers are inspect-only. Fix such an API with
an [overlay](overlays.md) that sets an absolute server, or re-import it from a
URL as below.

### `execute` says an operation "has no upstream host"

An API stored with host-relative servers has operations that `execute`
refuses with `RESOLVE_FAILED` ("has no upstream host"). Import the description
from its URL again (`jentic apis import <url>`) and promote the new draft
(`jentic apis promote <vendor/name/version> <revision_id>`); the new revision
stores the resolved servers, so it doesn't collide with the live one. If
credentials are bound to the API, promoting needs `org:admin`, since the
API's host changes.
