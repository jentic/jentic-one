---
name: add-to-toolkit
description: Request access to a toolkit containing API credentials so you can make proxied requests through the broker
version: 6
---

# Request Toolkit Access

## When to Use

Use this skill when you need to execute operations against an API that requires credentials. Toolkits bundle credentials with API definitions, and requesting access grants you the ability to make authenticated, proxied requests through the Jentic broker.

## Prerequisites

- You must have a registered agent identity with a valid authentication token
- The target API must be registered in the Jentic platform
- A toolkit must exist that contains the credential for the target API
- You must know the toolkit reference in the format `vendor/toolkit-name`

**Important:** If you have just registered an agent, ensure the registration completed successfully and you have a valid token before attempting to request toolkit access. Agent registration is a two-step process: the CLI registers the agent and returns a pending status, then you must approve the agent in the Jentic console before a token is minted. If `jentic whoami` shows no token or authentication fails, check that:
1. You approved the agent in the console (you'll see a URL like `http://<platform>/app/agents/<agent_id>`)
2. You waited for background approval to complete (typically 10-15 seconds after approval)
3. The token exchange succeeded after approval
4. The token was stored in your local `tokens.json` file (typically in your Jentic profile directory)

See the agent registration skill documentation for troubleshooting registration issues.

## Procedure

### 1. Verify Current Toolkit Access

Before requesting access, check what toolkits you currently have bound to your agent.

**CLI:**
```bash
jentic access list
```

**HTTP:**
```
GET {{ platform.control_plane_url }}/agent/access
Authorization: Bearer <your_agent_token>
```

**Expected Response:**
- A list of toolkit bindings (may be empty if you have no access yet)
- Each binding shows a toolkit ID (e.g., `tk_...`) and associated metadata

**If this fails with authentication errors:**
- Verify you have a valid token with `jentic whoami`
- If no token is present, check that your agent registration was approved in the console and the token exchange completed
- Confirm the token was persisted to your local `tokens.json` file (the CLI stores tokens here after successful approval)
- The CLI does not have a `jentic auth` command; authentication is handled through the registration process

### 2. Request Toolkit Access

Submit an access request for the toolkit containing the credential you need.

**CLI:**
```bash
jentic access request --toolkit <vendor>/<toolkit-name> --wait
```

The `--wait` flag causes the command to poll until the request is approved or denied. This is recommended for auto-approved toolkits or when you expect quick manual approval.

**Optional flags:**
- `--reason "Your reason text"` - Provide a reason for the access request (helpful for manual approval workflows)

**HTTP:**
```
POST {{ platform.control_plane_url }}/agent/access/requests
Authorization: Bearer <your_agent_token>
Content-Type: application/json

{
  "toolkit": "<vendor>/<toolkit-name>",
  "reason": "Your reason text (optional)"
}
```

**Expected Response:**
- CLI: Success message indicating the request was approved, with the toolkit binding ID and access request ID (format: `areq_...`)
- HTTP: 201 Created with a request object containing `status` field
- If `--wait` is used (CLI) or you poll the request (HTTP), wait for `status: "approved"`
- Auto-approved toolkits typically complete within seconds

**If Request is Pending:**
- Some toolkits require manual approval
- Poll the request status or wait for approval notification
- HTTP: `GET {{ platform.control_plane_url }}/agent/access/requests/<request_id>`

### 3. Refresh Your Agent Token

After approval, refresh your authentication token to receive updated claims that include the new toolkit binding.

**CLI:**
```bash
jentic access refresh
```

This command updates your local `tokens.json` file with the new token containing the toolkit binding claims.

**HTTP:**
```
POST {{ platform.control_plane_url }}/agent/token/refresh
Authorization: Bearer <your_current_token>
```

**Expected Response:**
- A new JWT token with updated toolkit bindings in its claims
- The CLI automatically updates your stored token in `tokens.json`
- For HTTP, store the new token and use it for subsequent requests

### 4. Verify Toolkit Binding

Confirm the toolkit is now bound to your agent.

**CLI:**
```bash
jentic access list
```

You can also verify your current identity and active toolkit bindings with:
```bash
jentic whoami
```

**HTTP:**
```
GET {{ platform.control_plane_url }}/agent/access
Authorization: Bearer <your_refreshed_token>
```

**Expected Response:**
- The toolkit binding should now appear in the list
- Note the toolkit ID (format: `tk_...`) for reference

## Quick Reference

### CLI Commands
```bash
# Verify identity and token status
jentic whoami

# List current access
jentic access list

# Request toolkit access (wait for approval)
jentic access request --toolkit <vendor>/<toolkit-name> --wait

# Request with reason
jentic access request --toolkit <vendor>/<toolkit-name> --reason "Testing integration" --wait

# Request without waiting
jentic access request --toolkit <vendor>/<toolkit-name>

# Refresh token after approval
jentic access refresh
```

### HTTP Endpoints
```
GET    {{ platform.control_plane_url }}/agent/access
POST   {{ platform.control_plane_url }}/agent/access/requests
GET    {{ platform.control_plane_url }}/agent/access/requests/<request_id>
POST   {{ platform.control_plane_url }}/agent/token/refresh
```

## Pitfalls

- **No valid authentication token**: If you attempt to request toolkit access without a valid token, commands will fail with authentication errors. Always verify you have a token with `jentic whoami` before proceeding. If registration shows pending status but no token was minted, ensure you approved the agent in the Jentic console and waited for background approval to complete (typically 10-15 seconds). Check that the token was persisted to your local `tokens.json` file.
- **Agent registration pending but not approved**: After running `jentic register`, the agent enters a pending state. You must approve it in the Jentic console (using the URL provided in the registration output) before a token is minted. Background approval typically takes 10-15 seconds after you approve it in the console. The token is then stored in your local `tokens.json` file.
- **Token not persisted locally**: Even after successful approval, if the token was not saved to your local `tokens.json` file, the CLI will not have access to it. Verify the token file exists in your Jentic profile directory and contains a valid token entry.
- **Forgetting to refresh the token**: After a request is approved, you must refresh your agent token. The new toolkit binding is encoded in the JWT claims, not stored server-side per request.
- **Wrong toolkit reference format**: Use `vendor/toolkit-name`, not `vendor/api-name` or `vendor/api-name/version`. Toolkits and APIs are separate entities.
- **Not using `--wait` flag**: Without `--wait`, the CLI returns immediately and you must manually poll for approval status. Use `--wait` for auto-approved toolkits or when you expect quick manual approval.
- **Using stale token after refresh**: In HTTP mode, ensure you replace your stored token with the refreshed one. The old token won't include the new toolkit binding. In CLI mode, verify the new token was written to `tokens.json`.
- **Assuming immediate access**: Some toolkits require manual approval. Check the request status if `--wait` times out or the HTTP request returns a pending status.
- **No direct toolkit listing command**: The CLI does not have a `jentic toolkits list` command. To discover available toolkits, you typically need to know the toolkit reference from documentation or use `jentic apis list` to find APIs and then request access using the API's vendor/name format.
- **Looking for `jentic auth` command**: The CLI does not have a separate `jentic auth` command. Authentication is handled through the `jentic register` process and token management is done via `jentic access refresh`.

## Verification

### CLI
```bash
jentic access list
```
Look for the toolkit binding in the output. You should see an entry with a toolkit ID (e.g., `tk_...`) matching your requested toolkit.

You can also use:
```bash
jentic whoami
```
This shows your current identity and active toolkit bindings.

### HTTP
```
GET {{ platform.control_plane_url }}/agent/access
Authorization: Bearer <your_refreshed_token>
```
Parse the JSON response and confirm an object exists with the toolkit reference you requested.

### End-to-End Test
After obtaining toolkit access, you can verify it works by attempting to execute an operation from the API:
```bash
jentic execute <operation_id>
```
If the toolkit binding is correct, the broker will inject the credential and proxy your request. A successful response (even if it's an application error from the upstream API) confirms the toolkit is properly bound.

**Note:** The Jentic broker blocks requests to localhost and other restricted addresses (e.g., 127.0.0.1) as a security measure. If you receive an error about restricted addresses, this is expected behavior and not a configuration issue with your toolkit binding.
