---
name: check-execution-history
description: Query execution records to verify that proxied API requests were logged by the platform
version: 4
---

# Check Execution History

## When to Use

Use this skill when you need to verify that your proxied API requests were successfully logged by the platform, or when debugging request flows to understand what was executed, when, and with what result.

## Prerequisites

- You must be authenticated with a valid agent token
- Your agent must be **approved** by an administrator (see [Agent Registration Prerequisites](#agent-registration-prerequisites) below)
- You should have previously made at least one proxied request through the broker (otherwise the execution history will be empty)
- You need access to the control plane API at `{{ platform.control_plane_url }}`

### Agent Registration Prerequisites

Before you can query execution history, your agent must be registered and approved:

1. Run `jentic register` to create a new agent
2. The CLI will display a pending status and a console URL
3. **An administrator must approve your agent** in the Jentic console before you can obtain a token
4. After approval, your token will be automatically stored in `tokens.json` in your agent workspace
5. Approval may take 10–15 seconds; if your token is not immediately available, wait briefly and retry
6. To verify approval is complete, run `jentic profile show` — if it displays a token, approval succeeded

## Procedure

### 1. Retrieve Your Execution History

The platform logs all proxied requests made through the broker. Query the executions endpoint to retrieve your execution records.

**CLI:**

There is no dedicated CLI command for listing execution history. You must use `curl` or similar HTTP client with your bearer token.

To get your token after agent approval:

```bash
# View your agent profile and token (if approved)
jentic profile show
```

If `jentic profile show` does not display a token, your agent has not yet been approved. Wait 10–15 seconds and retry, or check the Jentic console to confirm approval status.

Once you have your token, query executions:

```bash
curl -H "Authorization: Bearer <your_token>" \
  {{ platform.control_plane_url }}/executions
```

**HTTP:**

```
GET /executions
Authorization: Bearer <your_agent_token>
```

**Expected Response:**

```json
[
  {
    "id": "<execution_id>",
    "agent_id": "<your_agent_id>",
    "operation_id": "<operation_id>",
    "timestamp": "<iso8601_timestamp>",
    "status": "success|failure|blocked",
    "request": { ... },
    "response": { ... }
  }
]
```

If you haven't made any successful proxied requests, the response will be an empty array `[]`.

### 2. Interpret the Execution Records

Each execution record contains:

- **id**: Unique identifier for this execution
- **agent_id**: Your agent identifier (should match your authenticated identity)
- **operation_id**: The API operation that was invoked
- **timestamp**: When the request was executed
- **status**: Whether the request succeeded, failed, or was blocked by policy
- **request**: Details about the proxied request (method, path, headers, body)
- **response**: The response received from the upstream API

**CLI:**

Use `jq` or similar tools to filter and format the results:

```bash
curl -s -H "Authorization: Bearer <your_token>" \
  {{ platform.control_plane_url }}/executions | jq '.[] | {id, timestamp, status}'
```

**HTTP:**

The raw JSON response can be parsed programmatically. Filter by timestamp, operation_id, or status as needed.

### 3. Verify Your Specific Request

To confirm a specific proxied request was logged:

1. Note the timestamp when you made your proxied request
2. Look for an execution record with a matching or nearby timestamp
3. Verify the `operation_id` matches the API operation you invoked
4. Check the `status` field to confirm the request was processed (not blocked)

**CLI:**

```bash
# Filter by recent executions (last hour example)
curl -s -H "Authorization: Bearer <your_token>" \
  {{ platform.control_plane_url }}/executions | \
  jq '[.[] | select(.timestamp > (now - 3600 | todate))]'
```

**HTTP:**

Apply client-side filtering to the JSON array returned from `GET /executions`. The platform does not currently support server-side filtering via query parameters.

## Quick Reference

### CLI Commands

```bash
# View all executions (no dedicated CLI command exists)
curl -H "Authorization: Bearer <your_token>" \
  {{ platform.control_plane_url }}/executions

# Get agent profile info (includes agent_id and token after approval)
jentic profile show

# Register a new agent (requires admin approval before token is available)
jentic register
```

### HTTP Endpoints

```
GET /executions
  → Returns array of execution records for authenticated agent
  → Requires: Authorization: Bearer <token>
  → No query parameters currently supported
```

## Pitfalls

- **Agent must be approved before querying executions**: After running `jentic register`, your agent enters a pending state. An administrator must approve it in the Jentic console before you can obtain a token and query execution history. Attempting to query without an approved agent will result in a 401 error.
- **Token availability delay**: After agent approval, the token may not be immediately available. If `jentic profile show` shows no token, wait 10–15 seconds and retry. The token is automatically stored in `tokens.json` once approval completes.
- **No CLI command exists**: Unlike other platform features, there is no `jentic executions list` or similar command. You must construct HTTP requests manually with `curl`.
- **Verify approval completion**: Use `jentic profile show` to confirm your agent has been approved. If the output displays a token, approval is complete. If no token is shown, approval is still pending.
- **Empty results don't mean failure**: An empty array `[]` is the expected response if no proxied requests have been successfully executed. This could be because requests were blocked by policy (e.g., localhost restrictions) or because you haven't made any proxied requests yet.
- **Blocked requests may not appear**: If the broker blocks a request due to policy violations (e.g., restricted destination addresses), check whether these appear with `status: "blocked"` or are omitted entirely from the execution log.
- **No server-side filtering**: The `/executions` endpoint returns all execution records for your agent. You must filter client-side by timestamp, operation, or status.

## Verification

**Success Criteria:**

1. Your agent is registered and approved (visible in the Jentic console)
2. `jentic profile show` displays your agent ID and token
3. The `/executions` endpoint returns HTTP 200 (not 401 or 403)
4. If you made proxied requests, you see corresponding execution records with matching timestamps
5. Each record contains the expected `operation_id` and `status` fields
6. The `agent_id` in each record matches your authenticated agent identity

**CLI Verification:**

```bash
# Verify agent is approved and token is available
jentic profile show

# Should return 200 and valid JSON array
curl -i -H "Authorization: Bearer <your_token>" \
  {{ platform.control_plane_url }}/executions
```

**HTTP Verification:**

Check that `GET /executions` returns status 200 and a JSON array (even if empty). If you receive 401, your token may be expired, invalid, or your agent may not be approved. If you receive 404, verify the control plane URL is correct.
