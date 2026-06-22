# Workflows Guide

## What Is an Arazzo Workflow?

[Arazzo](https://spec.openapis.org/arazzo/latest.html) is an open standard (part of the OpenAPI Initiative) for defining multi-step API workflows. Key concepts:

- A workflow references one or more OpenAPI source specs
- Each **step** references a specific operation from those specs
- Steps can reference each other's outputs using runtime expressions: `$steps.{stepId}.outputs.{field}`
- Inputs can have default values
- The workflow defines its own input/output schema

Arazzo is to API workflows what OpenAPI is to individual operations.

---

## How Jentic Mini Executes Workflows

### Invocation

A workflow can be dispatched two equivalent ways:

```bash
# Direct — short path
curl -X POST http://localhost:8900/workflows/summarise-latest-topics \
  -H "X-Jentic-API-Key: $KEY" \
  -H "Content-Type: application/json" \
  -d '{"topic_count": 5}'
```

```bash
# Via the broker using the capability ID
curl -X POST "http://localhost:8900/$JENTIC_PUBLIC_HOSTNAME/workflows/summarise-latest-topics" \
  -H "X-Jentic-API-Key: $KEY" \
  -H "Content-Type: application/json" \
  -d '{"topic_count": 5}'
```

The broker detects self-hostname (`JENTIC_PUBLIC_HOSTNAME`, `localhost`, or the request's own Host header) and dispatches internally rather than forwarding upstream.

Workflow capability ID format: `POST/{JENTIC_PUBLIC_HOSTNAME}/workflows/{slug}`.

### Execution Flow

```
1. Client calls POST /workflows/{slug}
   (or POST /{JENTIC_PUBLIC_HOSTNAME}/workflows/{slug} via the broker)

2. workflows.py: dispatch_workflow(slug, body_bytes, caller_api_key, toolkit_id, ...)

3. Read Arazzo spec from disk (workflows.arazzo_path)

4. Apply input schema defaults
   (fields with defaults are optional in the request body)

5. Preprocess Arazzo doc:
   For each sourceDescriptions entry:
     - Read the referenced OpenAPI spec
     - Rewrite servers[0].url → http://localhost:8900/{host}
     - Write to a temp file
   Update Arazzo sourceDescriptions to point to temp files

6. Spawn arazzo-runner in a subprocess, handing it a pre-configured
   requests.Session whose X-Jentic-API-Key header forwards the caller's key
   on every step call.

7. arazzo-runner executes each step:
     - Resolves operation from its source spec
     - Builds URL: http://localhost:8900/{host}/{path}
     - Calls the local broker
     - Broker injects credentials, logs trace, forwards to upstream
     - Returns response to runner
     - Runner evaluates output expressions for next step

8. Runner returns: {status, outputs, step_outputs}

9. Write trace: executions row + execution_steps rows

10. Return to client:
    {
      "workflow": "Summarise Latest Topics",
      "slug": "summarise-latest-topics",
      "status": "success",
      "outputs": { ... },
      "simulate": false,
      "trace_id": "uuid",
      "_links": { "trace": "/traces/uuid" }
    }
```

### Error Response

On failure, the broker propagates the upstream HTTP status and returns a structured error:

```json
{
  "error": "workflow_execution_failed",
  "workflow": "My Workflow",
  "slug": "my-workflow",
  "workflow_status": "failed",
  "message": "Step 'callOpenAI' failed on api.openai.com (HTTP 400: max_tokens exceeds model limit)",
  "trace_id": "uuid",
  "_links": {"trace": "/traces/uuid"},
  "failed_step": {
    "step_id": "callOpenAI",
    "operation": "POST/api.openai.com/v1/chat/completions",
    "api": "api.openai.com",
    "http_status": 400,
    "detail": { "status": 400, "body": { "error": "..." } }
  },
  "remediation": {
    "message": "This failure may be caused by a workflow logic error or API drift. Read the workflow definition, execute it step-by-step manually, then POST a repaired workflow as a note to help improve the catalog.",
    "_links": {
      "workflow_definition": "/workflows/my-workflow",
      "post_note": "/workflows/my-workflow/notes"
    }
  }
}
```

`remediation` is omitted for auth-related failures (401, 403, 429) since the client already has enough signal to resolve those.

---

## Known Limitation: No Step-to-Step Data Transformation

Arazzo runtime expressions (`$steps.X.outputs.Y`) pass data **verbatim** between steps. There is no built-in filter, map, or transform primitive.

**Problem:** Step 1 might return a 500KB JSON response. Step 2 (e.g. OpenAI) only needs 3 fields from it. Passing the full payload causes:
- 400 errors from context-length limits
- Wasted tokens / latency

**Current workarounds:**
1. Design workflows where each step's output is naturally compact (e.g. APIs that return minimal data by default)
2. Use query parameters to limit response size (e.g. `?per_page=5` on list endpoints)
3. Pre-process data in workflow inputs before invocation

A `POST /localhost/transform` pseudo-operation accepting `{data, filter}` is a planned resolution — see Phase 5 in `specs/roadmap.md`.

---

## Registering a Workflow

`POST /import` accepts a batch of sources. The endpoint auto-detects OpenAPI vs Arazzo by inspecting the document — there is no top-level `type: "workflow"` field on the request.

### Source Types

`POST /import` accepts `sources[].type` with three valid values: `inline`, `url`, `path`. Each type requires different companion fields:

| Type     | Required Field(s)        | Optional Field(s)    | Description                                      |
|----------|--------------------------|----------------------|--------------------------------------------------|
| `inline` | `content` (JSON string)  | `filename`           | Spec content posted directly in the request      |
| `url`    | `url` (fetch spec URL)   | `filename`           | Fetch spec from a remote HTTP/HTTPS URL          |
| `path`   | `path` (local file path) | —                    | Local filesystem path already accessible to container |

### From a URL

```http
POST /import
X-Jentic-API-Key: {admin_key}
Content-Type: application/json

{
  "sources": [
    {
      "type": "url",
      "url": "https://raw.githubusercontent.com/org/repo/main/my-workflow.arazzo.json"
    }
  ]
}
```

### Inline

```http
POST /import

{
  "sources": [
    {
      "type": "inline",
      "content": "{ ... arazzo document as JSON string ... }"
    }
  ]
}
```

### From a local file

Place the file in `data/workflows/` (or anywhere readable by the container) and reference it by path:

```http
POST /import

{
  "sources": [
    {"type": "path", "path": "data/workflows/my-workflow.arazzo.json"}
  ]
}
```

After import, the workflow appears in:
- `GET /workflows` — list
- `GET /search?q=...` — BM25 search
- `GET /inspect/POST/{JENTIC_PUBLIC_HOSTNAME}/workflows/{slug}` — inspect

---

## Inspecting a Workflow

```http
GET /inspect/POST/{JENTIC_PUBLIC_HOSTNAME}/workflows/summarise-latest-topics
X-Jentic-API-Key: {key}
```

Returns:

```json
{
  "id": "POST/jentic-mini.example.com/workflows/summarise-latest-topics",
  "type": "workflow",
  "name": "Summarise Latest Topics",
  "description": "Fetches latest forum topics and returns an AI-generated summary.",
  "inputs": {
    "type": "object",
    "properties": {
      "topic_count": {"type": "integer", "default": 10}
    }
  },
  "steps": [
    {"step_id": "getTopics", "operation": "GET/forum.example.com/latest.json"},
    {"step_id": "summarise", "operation": "POST/api.openai.com/v1/chat/completions"}
  ],
  "_links": {
    "execute": "/workflows/summarise-latest-topics"
  }
}
```

For LLM consumption, request `Accept: text/markdown` — returns the same info as formatted Markdown optimised for inclusion in a prompt.

---

## Listing Workflows

```http
GET /workflows
X-Jentic-API-Key: {key}
```

```http
GET /workflows/{slug}
```

---

## Arazzo File Format Overview

Minimal Arazzo document structure:

```json
{
  "arazzo": "1.0.0",
  "info": {
    "title": "My Workflow",
    "version": "1.0.0"
  },
  "sourceDescriptions": [
    {
      "name": "openai",
      "url": "https://raw.githubusercontent.com/.../openai-openapi.json",
      "type": "openapi"
    }
  ],
  "workflows": [
    {
      "workflowId": "my-workflow",
      "summary": "Does something useful",
      "inputs": {
        "type": "object",
        "properties": {
          "prompt": {"type": "string"}
        }
      },
      "steps": [
        {
          "stepId": "callOpenAI",
          "operationId": "createChatCompletion",
          "requestBody": {
            "contentType": "application/json",
            "payload": {
              "model": "gpt-4o",
              "messages": [{"role": "user", "content": "$inputs.prompt"}]
            }
          },
          "outputs": {
            "reply": "$response.body#/choices/0/message/content"
          }
        }
      ],
      "outputs": {
        "reply": "$steps.callOpenAI.outputs.reply"
      }
    }
  ]
}
```

Key points for Jentic Mini compatibility:

- `sourceDescriptions[].url` should point to a publicly accessible OpenAPI spec (Jentic Mini rewrites this to route through the local broker at execution time)
- `operationId` must match an `operationId` in the referenced source spec
- Output expressions use JSONPath syntax after `#/`
- The workflow `workflowId` becomes the slug (kebab-cased if needed)
- Each step executes through the broker, so credentials for every involved API must be bound to the calling toolkit
