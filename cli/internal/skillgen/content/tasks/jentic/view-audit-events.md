---
name: view-audit-events
description: Access and browse the platform audit log to review agent activity and system events
version: 4
---

# View Audit Events

## When to Use

Use this skill when you need to review your agent's activity history, investigate system events, or audit actions taken on the platform. This is useful for debugging, compliance tracking, or understanding the sequence of operations performed.

## Prerequisites

- Active agent registration with the platform
- Agent must be **approved** in the console before token minting will succeed
- Valid authentication token with `events:read` scope (granted by default to agents)
- Access to the platform control plane API
- **Important**: You must have a valid token minted and stored. If registration completed but token minting failed, approval may be pending or you may need to re-register after approval.

## Procedure

### 1. Ensure Agent Registration and Approval

Before querying the events API, your agent must be registered **and approved** in the console.

**CLI:**

```bash
jentic register
```

The output will show:
```
Registered: agent_id=<agent_id> status=pending
Approve this agent in the Jentic console:
    http://<platform_url>/app/agents/<agent_id>
```

⚠️ **Critical**: Token minting happens **after** approval. If you see `status=pending`, you must approve the agent in the console before the token will be minted. The approval process may take 10-15 seconds to complete in the background.

After approval completes, the token will be automatically stored in `~/.config/jentic/tokens.json`.

**Troubleshooting registration:**
- If you see `error: agent for profile "default" is not active yet`, the agent is still pending approval. Check the console approval page and wait 10-15 seconds.
- If registration fails with EOF or timeout errors, the approval process may still be in progress. Wait a few seconds and retry `jentic register`.

### 2. Locate Your Authentication Token

Once your agent is approved and registered, retrieve your bearer token for authentication.

**CLI:**

The `jentic` CLI stores tokens automatically after successful registration and approval. The token is typically stored in `~/.config/jentic/tokens.json`.

```bash
# Verify token exists and is not empty
cat ~/.config/jentic/tokens.json | jq -r '.token'
```

⚠️ **Token Availability**: If the token file is empty, missing the `token` field, or returns `null`:
- Verify the agent was approved in the console (check the agent status page)
- Wait 10-15 seconds after approval for background token minting to complete
- Re-run `jentic register` to retry the registration and token minting process
- Check that the registration process completed successfully without EOF or timeout errors
- If the file exists but contains no valid token, the approval may not have completed. Return to Step 1 and verify approval status in the console.

**HTTP:**

Your token was provided during the authentication flow. It should be stored securely by your agent. The token is used in the `Authorization` header as a Bearer token.

### 3. Query the Events Endpoint

The platform exposes audit events through the `/events` endpoint on the control plane.

**CLI:**

⚠️ **Important**: The `jentic` CLI does not currently provide a built-in command for viewing events (e.g., `jentic events list` or `jentic auth`). You must make a direct HTTP request to the control plane API.

```bash
# Extract token and make direct API call
TOKEN=$(cat ~/.config/jentic/tokens.json | jq -r '.token')
curl -H "Authorization: Bearer $TOKEN" \
     {{ platform.control_plane_url }}/events
```

If the token extraction fails or returns `null`, verify the token file exists and contains a valid token before proceeding. See Step 2 troubleshooting.

**HTTP:**

```
GET /events
Host: {{ platform.control_plane_url }}
Authorization: Bearer <your_token>
```

Expected response (200 OK):
```json
{
  "events": [
    {
      "id": "<event_id>",
      "type": "<event_type>",
      "severity": "<info|warning|error>",
      "summary": "<human_readable_description>",
      "actor": {
        "type": "agent",
        "id": "<agent_id>",
        "name": "<agent_name>"
      },
      "timestamp": "<ISO8601_timestamp>",
      "metadata": { ... }
    }
  ]
}
```

### 4. Filter or Paginate Results (Optional)

If the events list is large, you may need to filter by time range, event type, or paginate through results.

**CLI:**

```bash
# Add query parameters to the curl request
TOKEN=$(cat ~/.config/jentic/tokens.json | jq -r '.token')
curl -H "Authorization: Bearer $TOKEN" \
     "{{ platform.control_plane_url }}/events?limit=50&offset=0"
```

**HTTP:**

```
GET /events?limit=50&offset=0
Host: {{ platform.control_plane_url }}
Authorization: Bearer <your_token>
```

Common query parameters (check platform documentation for full list):
- `limit`: Maximum number of events to return
- `offset`: Number of events to skip (for pagination)
- `type`: Filter by event type
- `since`: ISO8601 timestamp for events after a certain time

### 5. Parse and Analyze Events

Review the returned events to find relevant activity. Key fields to examine:

- **type**: The category of event (e.g., `agent.registered`, `access.granted`, `api.called`)
- **severity**: Importance level of the event
- **summary**: Human-readable description of what happened
- **actor**: Who or what triggered the event
- **timestamp**: When the event occurred
- **metadata**: Additional context specific to the event type

## Quick Reference

### CLI Commands

```bash
# Step 1: Register agent (must be approved in console before token is minted)
jentic register

# Step 2: Verify token exists
cat ~/.config/jentic/tokens.json | jq -r '.token'

# Step 3: Query events via direct API access
TOKEN=$(cat ~/.config/jentic/tokens.json | jq -r '.token')
curl -H "Authorization: Bearer $TOKEN" {{ platform.control_plane_url }}/events
```

### HTTP Endpoints

```
GET /events                    # List all events
GET /events?limit=N&offset=M   # Paginated results
GET /events?type=<event_type>  # Filter by type
```

## Pitfalls

- **Agent not approved**: Registration creates an agent with `status=pending`. Token minting only occurs **after** the agent is approved in the console. If you attempt to access events before approval, the token will not exist. Always check the console approval page and wait 10-15 seconds for background processing. If you see `error: agent for profile "default" is not active yet`, approval is still pending.

- **No CLI command**: The `jentic` CLI does not provide a native command for viewing events. Commands like `jentic events list` or `jentic auth` do not exist. You must make direct HTTP requests to the control plane API, even when using CLI mode for other operations.

- **Wrong endpoint**: Do not confuse `/events` with `/audit`. The `/audit` endpoint requires `audit:read` scope which agents typically do not have by default. Use `/events` for agent activity logs.

- **Token extraction**: When using CLI mode, you need to manually extract the token from the CLI's storage location (typically `~/.config/jentic/tokens.json`) to make direct API calls. Always verify the extracted token is not `null` before using it in requests.

- **Missing or empty token**: If registration completed but token minting failed (e.g., due to EOF errors, timeouts, or approval issues), the tokens.json file may exist but contain no valid token. Check that the file contains a `token` field with a non-empty value before attempting to use it. Verify approval status in the console first. If the file exists but is empty, the approval process may not have completed—return to Step 1.

- **Registration state issues**: If registration shows `status=pending` and token minting fails, the agent may be created but not approved. You must manually approve the agent in the console before the token will be minted. After approval, wait 10-15 seconds for background token minting to complete. If you see `error: agent for profile "default" is not active yet`, the approval is still in progress.

- **Missing Authorization header**: The events endpoint requires authentication. Always include `Authorization: Bearer <token>` in your HTTP requests.

- **Scope requirements**: Ensure your token has the `events:read` scope. This is granted by default to agents during registration, but verify if you encounter 403 Forbidden errors.

- **Token extraction in scripts**: When extracting the token using `jq`, ensure the command completes successfully. If `jq` is not installed or the JSON is malformed, the token variable will be empty. Test token extraction before using it in curl commands.

## Verification

**Successful retrieval indicators:**

- HTTP 200 OK response status
- JSON response containing an `events` array
- Events include your recent activity (e.g., registration, authentication, API calls)
- Each event has required fields: `id`, `type`, `severity`, `summary`, `actor`, `timestamp`

**To verify you're seeing your own activity:**

1. Check the `actor.id` field matches your agent ID
2. Look for events with timestamps corresponding to your recent actions
3. Verify event types match operations you performed (e.g., `agent.registered`, `toolkit.access_requested`)

**Common error responses:**

- **401 Unauthorized**: Token is missing, invalid, or expired
- **403 Forbidden**: Token lacks `events:read` scope
- **404 Not Found**: Wrong endpoint URL (check you're using `/events` not `/audit`)

**Before attempting to view events:**

1. Verify agent is approved in the console: Check the agent status page at `http://<platform_url>/app/agents/<agent_id>`
2. Wait 10-15 seconds after approval for token minting to complete
3. Verify your token exists: `cat ~/.config/jentic/tokens.json | jq -r '.token'`
4. Ensure the output is not empty or `null`
5. If token is still missing, re-run `jentic register` to retry the registration and token minting process
6. If you see `error: agent for profile "default" is not active yet`, approval is still pending—wait and retry

