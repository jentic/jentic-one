# Plan: `jentic execute` support for `multipart/form-data` bodies

Tracking issue: [#1316](https://github.com/jentic/jentic-one/issues/1316) —
*"[cli] jentic execute cannot invoke operations with multipart/form-data bodies
(no file-part flag) — image/file-upload ops are uninvokable"*.

## Problem

`jentic execute` can only send a JSON body (`-d/--data`, `--data-file`). Any
catalogued operation whose request body is `multipart/form-data` (image/file
upload, OCR, media ingestion) is discoverable and credential-bindable but
**cannot be executed** — the request goes out with no usable body and fails
validation upstream, silently. This dead-ends whole workflows: e.g. FaceCheck
reverse-image-search requires a multipart `POST /api/upload_pic` first, so the
JSON `POST /api/search` step (which works) can never get its `id_search`.

## Root cause (from code trace)

The blocker is **entirely in the CLI**. The broker is already fine:

- Broker buffers the request body as raw `bytes` and forwards it to the upstream
  via httpx `content=<bytes>` — byte-transparent, no JSON assumption
  (`src/jentic_one/broker/adapters/runners/http.py` `HttpRunner._run`).
- Broker forwards the inbound `Content-Type` **verbatim** — it is not in any
  strip list (`src/jentic_one/broker/core/proxy_headers.py` `forward_headers`),
  so a multipart boundary header passes straight through.
- Credential injection is header-based and independent of the body
  (`src/jentic_one/broker/web/routers/execute.py` `_apply_injection`).
- A `multipart/form-data` body-size cap (50 MiB) already exists
  (`src/jentic_one/shared/config.py` `max_request_bytes_by_type`).

The CLI, however (`cli/internal/cmd/execute.go`):

1. Offers **no** way to build a multipart body — only `-d`/`--data-file` (JSON).
2. When a body is present and no explicit content-type header was given, it
   **unconditionally stamps `Content-Type: application/json`** (lines ~219–221),
   which strips any multipart boundary and guarantees upstream rejection.

## Fix (CLI-only — no broker or transport-contract change)

Add `curl -F`-style flags to `cli/internal/cmd/execute.go`:

- `--form key=value` (repeatable) — text field parts.
- `--form-file key=@path` (repeatable) — file parts read from disk.

When either flag is present:

1. Build the body with Go stdlib `mime/multipart.Writer`: write text fields with
   `CreateFormField`, file parts with `CreateFormFile` (streaming the file
   contents), then `Close()`.
2. Set `Content-Type` from `writer.FormDataContentType()` — this carries the
   generated `boundary=…`. Do **not** overwrite it with the JSON default.
3. Send those bytes as the request body exactly as today.

The broker forwards the bytes + content-type untouched and injects credentials
as it already does for JSON. **Nothing on the CLI→broker contract changes** — no
base64 envelope, no broker code.

### Guardrails

- `--form`/`--form-file` are mutually exclusive with `-d`/`--data-file`/stdin
  body; error clearly if both are supplied.
- `--form-file` value must be `key=@path`; validate the `@` prefix and that the
  file exists/opens, with a clear error otherwise.
- Narrow the existing auto-`application/json` default so it only applies to a
  JSON-style body path and can never clobber a multipart (or other explicit)
  content-type.
- Preserve existing behaviour for all non-multipart invocations byte-for-byte.

## Tests

- **Update** `cli/internal/cmd/execute_test.go` `TestExecuteCmdSendsBody` — it
  currently hardcodes the `application/json` content-type expectation.
- **Add** a CLI test: `--form` + `--form-file` builds a valid multipart body,
  sets a `multipart/form-data; boundary=…` content-type, and forwards it; plus a
  mutual-exclusion error case.
- **Add** a broker smoke test alongside the octet-stream round-trip in
  `tests/smoke/test_broker_execute_domains.py`, asserting a real multipart body
  reaches the upstream intact. The upstream harness already exposes
  `/edge/multipart` (`tests/harness/smoke_upstream/routers/edge.py`).

### End-to-end verification: FaceCheck ID

The issue was discovered wiring FaceCheck reverse-image-search, whose flow is a
multipart upload feeding a JSON search — so it exercises the exact gap and is the
canonical manual/E2E check. Registered API `facecheck-id/facecheck-id/v1.02`
(host `facecheck.id`), credential bound (`Authorization` header), broker up.

1. **Multipart upload (the previously-broken step).** `POST /api/upload_pic` has
   a `multipart/form-data` body with field `images` (`type: string, format:
   binary`). With the new flags it becomes invokable:

   ```bash
   jentic execute op_<upload_pic> \
     --broker-scheme http \
     --form-file images=@face.jpg \
     --raw
   ```

   Expected: a real `200` from FaceCheck returning an `id_search` (not the
   previous empty-body validation failure). Confirm with `--dry-run` that the
   request now carries a `multipart/form-data; boundary=…` content-type and a
   non-empty body.

2. **JSON search (already worked — confirms the round-trip).** Feed the returned
   `id_search` into the JSON step, which was never blocked:

   ```bash
   jentic execute op_a5a5a5fd9a2de907f3edb9fa8a89ceeb620c4be7 \
     --broker-scheme http \
     -d '{"id_search":"<id_from_step_1>"}' \
     --raw
   ```

Passing step 1 (and thus completing step 2 with a real `id_search`) is the
end-to-end proof the multipart path works through the broker with credential
injection intact. Note the raw-broker bypass (`curl -F` straight at
`http://127.0.0.1:8100/https://facecheck.id/api/upload_pic`) is **not** a valid
check — it returns `401` because it lacks the internally-minted caller token; the
capability must be exercised through `jentic execute`.

## Out of scope

- Multi-step workflow orchestration (e.g. chaining upload → search
  automatically) — this is single-call proxying only.
- Spec-import upload issues (#645, #689, #876) — those concern importing an
  OpenAPI document, not executing a catalogued multipart operation.

## Feasibility note

The triage label is `feasibility:med`, but because the broker is already
byte-transparent the change is a self-contained Go CLI addition plus test
updates — closer to low/med effort with no cross-service contract change.
