---
name: make-proxied-request
description: Make authenticated API requests through the Jentic broker using toolkit credentials
version: 6
---

# Making Proxied API Requests Through Jentic

## When to Use

Use this skill when you need to call an external API through Jentic's broker, which handles authentication, credential injection, and request proxying on your behalf.

## Prerequisites

- Agent identity registered and authenticated with Jentic platform
- Target API registered in the Jentic registry
- Toolkit access granted containing credentials for the target API
- Valid authentication token with toolkit binding
- Target API must be accessible from the broker (not localhost/private IP ranges in production environments)

## Procedure

### 0. Agent Registration and Authentication (If Not Already Completed)

Before you can make proxied requests, you must have a registered agent with a valid authentication token.

**CLI:**

Register a new agent:
```bash
jentic register --yes
```

The `--yes` flag auto-approves the registration in development environments. Without it, you'll need to manually approve the agent via the console URL provided in the output.

**Important:** The CLI does not have a `jentic auth` command for initial authentication. Authentication is handled automatically during registration. If registration completes but token minting fails (indicated by an exit code 1 error or "agent for profile is not active yet" message), the agent has been created but approval is still pending. 

**Recovery steps:**
1. Wait 10-15 seconds for background approval to complete
2. Re-run `jentic register` to attempt token minting again
3. The CLI will detect the existing agent and attempt to complete the authentication process
4. If the error persists, manually approve the agent via the console URL shown in the registration output, then re-run `jentic register`

Check your profile status:
```bash
jentic profile show
```

This displays your agent_id and token status. If you see a profile but no token, re-run `jentic register` to complete the authentication.

**Expected outcome:** You should have a profile with an active authentication token. The `jentic profile show` command should display your agent_id and confirm token presence.

**HTTP:**

Register a new agent:
```
POST {{ platform.control_plane_url }}/agents
Content-Type: application/json

{
  "name": "<agent-name>"
}
```

Response includes `agent_id` and approval status. You must then approve the agent (via console or API) before proceeding to token minting.

After approval, mint a token:
```
POST {{ platform.control_plane_url }}/agents/<agent_id>/tokens
```

Response includes the authentication token to use for subsequent requests.

### 1. Locate the Target API in the Registry

First, verify the API exists and get its identifier.

**CLI:**
```bash
jentic apis list
```

Look for your target API in the output. Note the vendor/name/version format (e.g., `vendor-name/api-name/1.0.0`).

**Important:** The command is `jentic apis` (plural), not `jentic api` (singular).

**HTTP:**
```
GET {{ platform.control_plane_url }}/apis
Authorization: Bearer <your_token>
```

Response will include an array of API objects with `vendor`, `name`, and `version` fields.

**Expected outcome:** You should see your target API listed with its full identifier.

### 2. Request Toolkit Access

If you don't already have access to a toolkit containing the API's credentials, request it.

**CLI:**
```bash
jentic access request --toolkit <api-name> --wait
```

The `--wait` flag makes the command block until approval is received. Auto-approval is common for test environments. You can also use the API name directly as the toolkit name.

Alternative without waiting:
```bash
jentic access request <toolkit-name>
```

**HTTP:**
```
POST {{ platform.control_plane_url }}/access-requests
Authorization: Bearer <your_token>
Content-Type: application/json

{
  "toolkit": "<toolkit-name>"
}
```

**Expected outcome:** Response indicates `approved: true` or similar approval status.

**Note:** There is no `jentic toolkits` command to list available toolkits. Use the access request command with the API name or toolkit name you need.

### 3. Refresh Your Authentication Token

After gaining new toolkit access, refresh your token to include the new binding.

**CLI:**
```bash
jentic auth refresh
```

This updates your local token with the new permissions.

**HTTP:**
```
POST {{ platform.control_plane_url }}/auth/refresh
Authorization: Bearer <your_current_token>
```

Response includes a new token with updated `toolkit_bindings`.

**Expected outcome:** Your token now includes the toolkit in its bindings. Verify with `jentic auth status` (CLI) or by decoding the JWT.

### 4. Find the Operation ID

To execute a request, you need the operation's unique identifier from the registry.

**CLI:**
```bash
jentic apis operations <vendor>/<api-name>/<version> --json
```

The `--json` flag is **required** because the formatted output doesn't display operation IDs. Look for the `operation_id` field (format: `op_<hash>`).

**Important:** The command expects the full three-part identifier. Using a partial identifier (e.g., `vendor/api-name` without version) will fail with "invalid API reference" error.

You can also inspect a specific operation:
```bash
jentic apis inspect <operation_id>
```

This shows the full operation details including the upstream URL.

**HTTP:**
```
GET {{ platform.control_plane_url }}/apis/<vendor>/<api-name>/<version>/operations
Authorization: Bearer <your_token>
```

Parse the JSON response to find the operation matching your desired HTTP method and path.

**Expected outcome:** You have an operation ID like `op_6a58da5c629c0e3b921f48c9`.

### 5. Execute the Proxied Request

Use the operation ID to make the request through the broker.

**CLI:**
```bash
jentic execute <operation_id>
```

For requests with parameters, query strings, or body:
```bash
jentic execute <operation_id> --param key=value --header "X-Custom: value"
```

Alternative formats supported:
```bash
jentic execute METHOD:url              # Full URL
jentic execute METHOD:/path            # Path only (requires API context)
```

**HTTP:**
```
POST {{ platform.broker_url }}/execute/<operation_id>
Authorization: Bearer <your_token>
Content-Type: application/json

{
  "parameters": {},
  "headers": {},
  "body": {}
}
```

**Expected outcome:** The broker proxies your request to the upstream API, injects credentials from your toolkit, and returns the API's response.

### 6. Verify the Response

Check that you received a successful response from the upstream API (not an error from the broker).

**CLI:**
The command output will show the HTTP status and response body from the upstream API.

**HTTP:**
The response status and body reflect the upstream API's response. Broker errors (4xx) will have a JSON body with an `error` field describing broker-level issues.

**Expected outcome:** A 2xx status code and response body from the target API indicates success.

## Quick Reference

### CLI Commands
```bash
jentic register --yes                               # Register agent with auto-approval
jentic profile show                                 # Check agent profile and token status
jentic apis list                                    # List available APIs (note: plural "apis")
jentic apis show <vendor>/<name>/<version>          # Show API details
jentic apis inspect <operation_id>                  # Inspect specific operation
jentic access request --toolkit <name> --wait       # Request toolkit access (blocking)
jentic auth refresh                                 # Refresh token with new bindings
jentic auth status                                  # Check current bindings
jentic apis operations <vendor>/<name>/<version> --json  # List operations with IDs
jentic execute <operation_id>                       # Execute proxied request
jentic execute <operation_id> --param key=value     # Execute with parameters
```

### HTTP Endpoints
```
GET  /apis                                          # List APIs
POST /access-requests                               # Request toolkit access
POST /auth/refresh                                  # Refresh authentication token
GET  /apis/<vendor>/<name>/<version>/operations     # List operations
POST /execute/<operation_id>                        # Execute proxied request
GET  /executions                                    # View execution history
```

## Pitfalls

- **No "auth" command in CLI for initial setup**: The CLI does not have a `jentic auth` command for initial authentication. Authentication happens during the `jentic register` process. If you see "unknown command 'auth'" error, you're trying to use a command that doesn't exist. Use `jentic register` for initial setup and `jentic auth refresh` or `jentic auth status` for token management after registration.

- **Registration may fail to mint token with exit code 1**: The `jentic register` command may complete registration (creating the agent) but fail during token minting with an exit code 1 error or "agent for profile is not active yet" message. This typically means the agent is created but approval is still pending. Wait 10-15 seconds for background approval to complete, then re-run `jentic register`. The CLI will detect the existing agent and attempt to complete the authentication process. If `jentic profile show` displays an agent_id but no token, re-run `jentic register` to complete the authentication. If the error persists, manually approve the agent via the console URL provided in the registration output.

- **Exit code 137 during registration**: If registration is interrupted with exit code 137, the agent may be created but not fully authenticated. Check status with `jentic profile show` and re-run `jentic register` if needed.

- **Command is "apis" not "api"**: The CLI command is `jentic apis` (plural). Using `jentic api` will result in "unknown command" error with a suggestion to use `apis`.

- **Operation ID not visible**: The formatted output of `jentic apis operations` doesn't show operation IDs. Always use `--json` flag to see the `operation_id` field needed for execution.

- **Stale token after access grant**: After requesting and receiving toolkit access, you must refresh your authentication token. The new binding won't be active until you do.

- **SSRF protection blocks localhost**: The broker blocks requests to localhost, 127.0.0.1, and private IP ranges for security. Test APIs must be hosted on publicly routable addresses or the broker must be configured to allow the target range. This affects both URL-based and operation-ID-based execution. Error message: "upstream URL resolves to a blocked address range" with `invalid_upstream_url` error code.

- **Wrong API identifier format**: API references must be in `vendor/name/version` format (all three parts required). Partial identifiers like `vendor/name` will fail with "invalid API reference; expected vendor/name/version" error. The `jentic apis operations` and `jentic apis show` commands strictly require the full three-part identifier.

- **Search command limitations**: The `jentic search` command may not return results reliably. The `--api` flag is not supported and will cause "unknown flag" errors. Use `jentic apis list` and `jentic apis operations` for discovery instead.

- **Confusing operation reference formats**: The `execute` command requires the registry operation ID (e.g., `op_<hash>`), not the API path, HTTP method, or spec operationId. Always get this from `jentic apis operations --json`.

- **No CLI command for execution history**: There is no CLI command to view execution history. You must use the HTTP endpoint `GET /executions` with your bearer token to view past executions.

- **No CLI command to list toolkits**: There is no `jentic toolkits` command. To request toolkit access, use the API name or known toolkit name directly with `jentic access request --toolkit <name>`.

- **OpenAPI spec download may fail**: Attempting to download the OpenAPI spec for an API revision may return a 500 error if the spec file is not stored in the system.

- **Authentication requirements not in metadata**: The operation metadata (`auth` field and `security_schemes` field) may show `null` or be empty even when the API requires authentication headers. Authentication requirements are defined in the OpenAPI spec and injected by the broker via toolkit credentials, not visible in the operation listing.

## Verification

**CLI:**
```bash
# Verify agent registration and token
jentic profile show
# Should show agent_id and confirm token is present

# Verify toolkit binding
jentic auth status
# Should show your toolkit in the bindings list

# Inspect operation before executing
jentic apis inspect <operation_id>
# Shows full operation details including upstream URL

# Verify successful execution
jentic execute <operation_id>
# Should return 2xx status with upstream API response body
```

**HTTP:**
```
# Verify toolkit binding
GET {{ platform.control_plane_url }}/auth/status
# Response includes toolkit_bindings array

# Verify successful execution
POST {{ platform.broker_url }}/execute/<operation_id>
# Response status 200-299 with upstream API data (not broker error JSON)

# View execution history
GET {{ platform.control_plane_url }}/executions
# Returns array of execution records
```

Success means: (1) your agent is registered with a valid token, (2) your token includes the toolkit binding, (3) the execute command/endpoint returns a response, (4) the response is from the upstream API (not a broker error), and (5) the status indicates success per the API's contract.
