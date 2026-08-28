---
name: authenticate
description: Exchange credentials for an access token (JWT bearer or CLI login) to access the Jentic platform
version: 6
---

# Authenticate with Jentic Platform

## When to Use

Use this skill when you need to obtain an access token to interact with the Jentic platform, either through the CLI or HTTP API. Authentication is required before you can access APIs, request toolkit bindings, or execute operations through the broker.

## Prerequisites

- Agent identity must be registered with the platform (see `register` skill)
- For CLI: `jentic` binary installed and configured with control plane URL
- For HTTP: Control plane URL and agent credentials available

## Procedure

### 1. Understand Token Acquisition

Authentication happens automatically during agent registration. When you register an agent identity, the platform issues both an access token (short-lived JWT) and a refresh token (longer-lived).

**CLI:**
The `jentic register` command stores tokens automatically in the CLI's configuration (typically in `tokens.json`). No separate authentication command is needed.

**Important**: Token minting only occurs after the agent registration is approved. Registration creates the agent identity with `status=pending`, but tokens are only minted after approval is completed. If registration completes but approval fails, times out, or is not completed, the profile will be created without a token. In this case, approve the agent in the Jentic console (link provided in registration output), wait 10-15 seconds for background approval processing, then re-run `jentic register` to complete the token minting.

**HTTP:**
The registration endpoint (`POST /agents`) returns tokens in the response body:
```json
{
  "access_token": "<jwt>",
  "refresh_token": "<refresh_jwt>",
  "expires_in": 3600
}
```

Store both tokens securely for subsequent requests.

### 2. Verify Token Works

Test that your access token is valid by making an authenticated request.

**CLI:**
```bash
jentic apis list
```

If this returns a list of APIs (even if empty), your token is valid.

You can also check your profile and token status:
```bash
jentic profile list
```

This shows your active profile with token expiry information (typically 1 hour) and validation status. If the profile shows no token or an invalid token, you may need to re-run registration to complete the approval and token minting process.

**Note**: If you see the error `agent for profile "default" is not active yet`, this means the agent is still pending approval. Proceed to the approval step below before attempting to use the token.

**HTTP:**
```
GET {{ platform.control_plane_url }}/apis
Authorization: Bearer <access_token>
```

A `200 OK` response confirms the token is valid.

### 3. Complete Agent Approval (if needed)

If registration returns `status=pending`, the agent must be approved before tokens are minted.

**CLI:**
After running `jentic register`, if you see `status=pending` in the output:
1. Open the Jentic console URL provided in the registration output (typically `http://<control_plane_url>/app/agents/<agent_id>`)
2. Approve the agent in the console
3. Wait 10-15 seconds for background approval processing
4. Re-run `jentic register` to complete token minting:
```bash
jentic register
```

After re-running, verify the profile now has a valid token:
```bash
jentic profile list
```

**HTTP:**
If the registration response shows `status=pending`, you must approve the agent via the console or API before attempting authenticated requests. Once approved, re-run the registration request or use the refresh endpoint to obtain tokens.

### 4. Refresh Token When Expired

Access tokens expire (typically after 1 hour). Use the refresh token to obtain a new access token without re-registering.

**CLI:**
```bash
jentic refresh
```

This updates the stored access token automatically. You'll need to refresh after requesting new toolkit bindings to get updated claims.

**HTTP:**
```
POST {{ platform.control_plane_url }}/auth/refresh
Content-Type: application/json

{
  "refresh_token": "<refresh_token>"
}
```

Response contains new `access_token` and `expires_in`. Store the new access token and use it for subsequent requests.

## Quick Reference

### CLI Commands
```bash
# Tokens obtained automatically during registration
jentic register --name <agent-name>

# If registration returns status=pending:
# 1. Approve the agent in the Jentic console (link in registration output)
# 2. Wait 10-15 seconds for background approval processing
# 3. Re-run registration to complete token minting:
jentic register

# Verify authentication
jentic apis list

# Check profile and token status
jentic profile list

# Refresh access token
jentic refresh
```

### HTTP Endpoints
```
# Tokens obtained during registration
POST {{ platform.control_plane_url }}/agents

# Verify authentication
GET {{ platform.control_plane_url }}/apis
Authorization: Bearer <access_token>

# Refresh token
POST {{ platform.control_plane_url }}/auth/refresh
Body: { "refresh_token": "<token>" }
```

## Pitfalls

- **No separate auth command**: Don't look for a `jentic login` or `jentic auth` command. Authentication happens during `jentic register`. The CLI will return "unknown command" errors for `jentic auth` or `jentic version`.
- **Approval required for token minting**: Registration creates the agent identity with `status=pending`, but tokens are only minted after approval is completed. If registration returns `status=pending`, you must approve the agent in the Jentic console (URL provided in registration output), wait 10-15 seconds for background approval processing, then re-run `jentic register` to complete token minting. If you don't approve or re-run registration, the profile will exist but have no token.
- **"Agent not active yet" error**: If you see `agent for profile "default" is not active yet`, this means the agent is pending approval. Complete the approval step (approve in console, wait 10-15 seconds, re-run `jentic register`) before attempting authenticated operations.
- **Profile exists without token**: If `jentic profile list` shows a profile but no token, this indicates registration completed but token minting failed. Approve the agent in the console and re-run `jentic register` to complete the process.
- **Token storage**: CLI stores tokens automatically in `tokens.json`. For HTTP mode, you must implement secure token storage yourself.
- **Refresh after toolkit changes**: After requesting toolkit access, run `jentic refresh` to update your token with new credential claims. The old token won't include newly granted permissions.
- **Token expiration**: Access tokens expire. If you get 401 errors, refresh your token before retrying.
- **Refresh token lifetime**: Refresh tokens also expire (longer lifetime than access tokens). If refresh fails, you'll need to re-register.
- **Command naming**: Use `jentic apis` (plural) not `jentic api`. The CLI will suggest corrections for typos. Use `jentic profile list` not `jentic profile show`.

## Verification

### CLI
Run any authenticated command successfully:
```bash
jentic apis list
# Should return API list or empty array, not authentication error
```

Or check your profile status:
```bash
jentic profile list
# Should show active profile with valid token and expiry time (typically 1h)
# If no token is shown, approve the agent in the console and re-run jentic register
# If you see "agent not active yet", complete the approval step first
```

### HTTP
Make an authenticated request:
```
GET {{ platform.control_plane_url }}/apis
Authorization: Bearer <access_token>
```

Success indicators:
- HTTP 200 status code
- Valid JSON response body
- No authentication or authorization errors

Failure indicators:
- HTTP 401: Token invalid or expired (refresh needed)
- HTTP 403: Token valid but lacks permissions (check registration status)
