---
name: register-agent
description: Register a new agent identity on the Jentic platform and obtain authentication credentials
version: 6
---

# Register Agent Identity

## When to Use

Use this skill when you need to create a new agent identity on the Jentic platform for the first time. This is typically the first step before you can access APIs, request toolkit access, or execute operations through the broker.

## Prerequisites

- Access to the Jentic platform control plane URL
- Network connectivity to the platform
- For CLI: `jentic` binary installed and available in PATH
- For HTTP: Ability to make HTTP requests and store credentials

## Procedure

### 1. Initiate Agent Registration

**CLI:**
```bash
jentic register --yes
```

The `--yes` flag auto-confirms the registration. Without it, you'll be prompted to confirm.

**Expected output:**
```
Registering agent "<agent-name>" with <control-plane-url> ...
Registered: agent_id=<your-agent-id> status=pending
```

**HTTP:**
```
POST {{ platform.control_plane_url }}/v1/agents/register
Content-Type: application/json

{
  "name": "<optional-agent-name>",
  "description": "<optional-description>"
}
```

**Expected Response:**
- HTTP: 202 Accepted with registration request details including a request ID

### 2. Wait for Admin Approval

**CLI:**
After registration is initiated, the agent enters `pending` status. The CLI will display an approval URL:
```
Approve this agent in the Jentic console:
    <control-plane-url>/app/agents/<agent-id>
```

In development environments with automatic approval enabled, approval typically completes within 10-15 seconds. The CLI will automatically poll for approval status and proceed to token minting once approved.

**Important:** If the CLI exits with an error after showing the approval URL, this may indicate the token minting step failed (see Pitfalls section). The agent may still be approved — check the approval URL or retry registration. The error message will typically state: `agent for profile "default" is not active yet (Assertion is invalid); wait for approval, or re-run 'jentic register --profile default'`

**HTTP:**
Poll the registration status endpoint:
```
GET {{ platform.control_plane_url }}/v1/agents/register/<request-id>
Authorization: Bearer <temporary-token-from-registration-response>
```

Continue polling (with exponential backoff) until the status changes from `pending` to `approved`.

**Expected Response:**
- Status: `approved`
- Response includes agent ID and authentication tokens

### 3. Store Authentication Credentials

**CLI:**
The CLI automatically saves credentials to `.jentic/profiles/default` in your home directory or working directory. Credentials are stored in a `tokens.json` file within the profile directory. No manual action required. The output will clearly show:
- Your agent_id
- Approval URL (if applicable)
- Token expiry information
- Next steps for using the platform

**Note:** In some cases, the profile may be created but token minting may fail during the registration flow. If this occurs, see the "Token Minting Failure" section in Pitfalls below. You can verify token storage by checking that `.jentic/profiles/default/tokens.json` exists and contains token data.

**HTTP:**
Extract and securely store the following from the approval response:
- `agent_id`: Your unique agent identifier
- `access_token`: Short-lived token for API requests
- `refresh_token`: Long-lived token for obtaining new access tokens

Store these in a secure location (environment variables, secrets manager, or encrypted config file).

### 4. Verify Registration

**CLI:**
```bash
jentic agents list
```

Look for your agent ID in the output. This confirms both registration and authentication are working. If you receive an error stating the agent is "not active yet," the approval process has not completed — wait 10-15 seconds and try again.

**HTTP:**
```
GET {{ platform.control_plane_url }}/v1/agents/me
Authorization: Bearer <access-token>
```

**Expected Response:**
- Your agent details including ID, name, and registration timestamp
- Empty or minimal `toolkit_bindings` array (you haven't requested access to anything yet)

## Quick Reference

### CLI Commands
```bash
# Register new agent (with auto-confirm)
jentic register --yes

# Register with interactive confirmation
jentic register

# View your agent details
jentic agents list

# Check current authentication status
jentic agents list  # Your agent should appear in the list

# Log out (clears profile)
jentic logout
```

### HTTP Endpoints
```
# Register agent
POST {{ platform.control_plane_url }}/v1/agents/register

# Check registration status
GET {{ platform.control_plane_url }}/v1/agents/register/<request-id>

# Get current agent details
GET {{ platform.control_plane_url }}/v1/agents/me

# Refresh access token (when expired)
POST {{ platform.control_plane_url }}/v1/auth/refresh
Authorization: Bearer <refresh-token>
```

## Pitfalls

- **Don't lose your tokens**: CLI stores them automatically in `.jentic/profiles/default/tokens.json`, but if using HTTP directly, ensure you persist the refresh token securely. Access tokens expire, but refresh tokens are long-lived.

- **Registration requires approval**: The registration process is asynchronous. Don't assume immediate access. The CLI handles polling automatically, but HTTP clients must implement polling logic. In development environments, approval may be automatic and take 10-15 seconds.

- **Profile location (CLI)**: The CLI saves credentials to `.jentic/profiles/default`. If running in a containerized or restricted environment, ensure this path is writable and persistent. Verify that `tokens.json` is created in this directory after successful registration.

- **Token refresh**: Access tokens expire. The CLI handles refresh automatically. HTTP clients must detect 401 responses and use the refresh token to obtain a new access token before retrying.

- **No `whoami` command**: There is no `jentic whoami` command. Use `jentic agents list` to see your agent identity.

- **No `auth` command**: There is no `jentic auth` command. Authentication is handled automatically during registration. If you need to re-authenticate, use `jentic logout` followed by `jentic register`.

- **Authentication is automatic**: After successful registration with the CLI, you are immediately authenticated. There is no separate authentication step required - the registration process handles credential storage and you can immediately proceed to use other commands like `jentic apis list` or `jentic access request`.

- **Token minting failure**: The CLI may exit with an error after displaying the approval URL and agent ID. This typically indicates a failure during the token minting step (e.g., connection error to the OAuth token endpoint). The agent may still be created and approved. If this occurs:
  1. Check the approval URL shown in the output to verify the agent was created
  2. Wait 10-15 seconds to ensure approval has completed
  3. Run `jentic register --yes` again — the CLI will detect the existing agent and attempt to complete token minting
  4. If the problem persists, the OAuth token endpoint may be unavailable or misconfigured
  5. Check platform logs or console for OAuth service errors
  6. Verify that `.jentic/profiles/default/tokens.json` was created; if the file exists but is empty or malformed, this indicates token minting failed

- **Re-running register with existing agent**: If you run `jentic register` and an agent profile already exists, the CLI will display "Using existing agent_id=..." and show the approval URL. This can be used to recover from partial registration failures.

- **Agent not active error**: If `jentic agents list` fails with the error `agent for profile "default" is not active yet (Assertion is invalid)`, the approval process has not yet completed. Wait 10-15 seconds and retry. If the error persists after 30 seconds, the approval may have failed — check the approval URL in the Jentic console or retry the full registration flow.

## Verification

### CLI Verification
Run `jentic agents list` and confirm:
- Your agent appears in the output
- The agent ID matches what was shown during registration
- No error messages about authentication or agent not being active

### CLI Credential Verification
Verify that credentials were properly stored:
```bash
# Check that the profile directory exists
ls -la ~/.jentic/profiles/default/

# Verify tokens.json exists and contains data
cat ~/.jentic/profiles/default/tokens.json
```

Expected: `tokens.json` file exists and contains JSON with token data (do not display actual tokens).

### HTTP Verification
Make a request to `GET {{ platform.control_plane_url }}/v1/agents/me` and confirm:
- Response status is 200 OK
- Response body contains your agent ID
- Response includes `toolkit_bindings` field (even if empty)

### Additional Checks
- CLI: Check that `.jentic/profiles/default/tokens.json` exists and contains token data
- HTTP: Verify you can make authenticated requests to other endpoints (e.g., `GET /v1/apis`)
- Both: Confirm that unauthenticated requests to protected endpoints return 401 Unauthorized

### Troubleshooting Failed Registration
If `jentic agents list` fails with authentication errors after registration:
1. Check if `.jentic/profiles/default` exists
2. Inspect the profile directory to verify `tokens.json` exists
3. If the profile exists but `tokens.json` is missing or empty, this indicates a token minting failure
4. Try `jentic logout` followed by `jentic register --yes` to retry the full flow
5. If the CLI exits with an error after showing the approval URL, wait 10-15 seconds and run `jentic register --yes` again to retry token minting
6. If the error persists, check that the approval URL is accessible in the Jentic console and that the agent shows as approved
