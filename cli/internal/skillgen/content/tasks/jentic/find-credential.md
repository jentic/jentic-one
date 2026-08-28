---
name: find-credential
description: Locate an API in the platform registry and identify its authentication requirements and available endpoints
version: 6
---

# Finding API Credentials and Endpoints

## When to Use

Use this skill when you need to discover what authentication method a registered API requires and what operations are available, before requesting access or making calls through the broker.

## Prerequisites

- Valid agent identity registered with the platform
- Active authentication token
- Knowledge of the API's vendor/name/version identifier (format: `vendor/name/version`)

**Important:** You must have a valid authentication token before using any of the commands in this skill. If you encounter authentication errors or have no token, complete agent registration and authentication first (see separate skill documentation).

**Note on agent registration:** After registering a new agent, you will receive a pending status and a link to approve it in the Jentic console. Token exchange may fail immediately after registration — this is normal. Wait 10-15 seconds for background approval to complete, then retry token exchange before proceeding with API discovery.

**Note on token storage:** After successful token exchange, tokens are stored in `tokens.json` in your profile directory. If you copy a profile from another agent or workspace, ensure `tokens.json` is also copied to maintain authentication state.

## Procedure

### 1. List Available APIs

Start by listing all APIs in the registry to confirm the target API exists and get its exact identifier.

**CLI:**
```bash
jentic apis list
```

**HTTP:**
```
GET {{ platform.control_plane_url }}/apis
Authorization: Bearer <token>
```

Expected response includes an array of API objects with `vendor`, `name`, `version`, and `id` fields. Note the exact version string for the target API.

**Common mistake:** The command is `jentic apis` (plural), not `jentic api` (singular).

### 2. View API Details

Once you've identified the target API, view its details to see basic information.

**CLI:**
```bash
jentic apis show <vendor>/<name>/<version>
```

**HTTP:**
```
GET {{ platform.control_plane_url }}/apis/<vendor>/<name>/<version>
Authorization: Bearer <token>
```

This provides overview information about the API including its current revision.

**Important:** The full `vendor/name/version` identifier is required. Omitting the version (e.g., `jentic apis show vendor/name`) will fail with "invalid API reference" error.

### 3. Check Security Schemes

The `jentic apis show` command includes a `security_schemes` field in its output. However, this field is often empty even when the API requires authentication.

**CLI:**
```bash
jentic apis show <vendor>/<name>/<version> --output json
```

Look for the `security_schemes` array in the JSON output. If empty, authentication requirements cannot be determined from this metadata alone.

**Known limitation:** Security scheme information is frequently not populated in API metadata, even for APIs that require authentication (e.g., X-API-Key headers). You will need to consult the OpenAPI spec (step 5) or external documentation to determine actual authentication requirements.

### 4. List Operations for the Target API

List the API's operations to see available endpoints.

**CLI:**
```bash
jentic apis operations <vendor>/<name>/<version>
```

**HTTP:**
```
GET {{ platform.control_plane_url }}/apis/operations?api=<vendor>/<name>/<version>
Authorization: Bearer <token>
```

Expected response includes operation objects with:
- `id`: The operation identifier (format: `op_<hash>`) needed for execution
- `method`: HTTP method (GET, POST, etc.)
- `path`: The endpoint path
- `_links`: Related resource links

**Important:** Save the operation `id` values - these are required for executing operations later. The formatted CLI output may not display IDs prominently; use `--output json` to see full details.

**Note:** If this returns 0 operations or an empty list, the API may be registered but not yet have operations defined in its current revision.

### 5. Inspect Individual Operations

Get detailed information about a specific operation using its operation ID.

**CLI:**
```bash
jentic apis inspect <operation-id>
```

**HTTP:**
```
GET {{ platform.control_plane_url }}/operations/<operation-id>
Authorization: Bearer <token>
```

This shows operation details including method, path, and an `auth` field. 

**Important Limitation:** The `auth` field may show `null` even when the API requires authentication. The operation metadata does not reliably expose authentication requirements. You may need to consult the OpenAPI spec (step 6) or external documentation.

**Note:** The `jentic inspect` command (without `apis` subcommand) may also work but `jentic apis inspect` is the correct form for operation inspection.

### 6. Attempt to Retrieve the OpenAPI Specification

Try to get the full OpenAPI spec to understand authentication requirements and request/response schemas.

**CLI:**
```bash
jentic apis spec <vendor>/<name>/<version>
```

**HTTP:**
```
GET {{ platform.control_plane_url }}/apis/<vendor>/<name>/<version>/spec
Authorization: Bearer <token>
```

**Note:** This may fail with HTTP 500 if the spec file wasn't stored during API registration. The error message will indicate "Revision '<id>' has no stored spec file". If this occurs, authentication requirements cannot be discovered through the platform and must be obtained from external documentation or the API provider.

### 7. Check Catalog for Published Information

If direct API inspection is limited, check if the API is published in the public catalog with documentation.

**CLI:**
```bash
jentic catalog
```

**HTTP:**
```
GET {{ platform.control_plane_url }}/catalog
Authorization: Bearer <token>
```

This shows APIs that have been explicitly published with descriptions and may include authentication guidance.

### 8. Verify Current Toolkit Access

Check whether you already have access to a toolkit containing credentials for this API.

**CLI:**
```bash
jentic toolkits
```

**HTTP:**
```
GET {{ platform.control_plane_url }}/toolkits
Authorization: Bearer <token>
```

**Note:** The `jentic toolkits` command may not be available in all CLI versions. If you receive "unknown command" error, skip this step and proceed to request access (covered in separate skill documentation). You can verify access after requesting it through other means.

If the response is empty or doesn't include the target API, you'll need to request access (covered in separate skill documentation).

## Quick Reference

### CLI Commands
```bash
# List all APIs
jentic apis list

# Show API details
jentic apis show <vendor>/<name>/<version>

# Show API details with JSON output (to see security_schemes)
jentic apis show <vendor>/<name>/<version> --output json

# List operations for specific API
jentic apis operations <vendor>/<name>/<version>

# Inspect operation by ID
jentic apis inspect <operation-id>

# Get OpenAPI spec (may fail)
jentic apis spec <vendor>/<name>/<version>

# Check catalog
jentic catalog

# Check toolkit access (may not be available)
jentic toolkits
```

### HTTP Endpoints
```
GET /apis                                    # List APIs
GET /apis/<vendor>/<name>/<version>          # Show API details
GET /apis/operations?api=<vendor/name/ver>   # List operations
GET /operations/<operation-id>                # Inspect operation
GET /apis/<vendor>/<name>/<version>/spec     # Get spec
GET /catalog                                 # Public catalog
GET /toolkits                                # Current access
```

All HTTP requests require `Authorization: Bearer <token>` header.

## Pitfalls

- **Don't use singular "api"**: The command is `jentic apis` (plural), not `jentic api` (singular). Using the singular form will result in "unknown command" error.
- **Don't use partial API identifiers**: Commands like `jentic apis show <vendor>/<name>` without version will fail with "invalid API reference" error. Always use the full `vendor/name/version` format.
- **Don't use unknown flags**: The `jentic apis operations` command does not accept an `--api` flag. The API identifier is passed as a positional argument.
- **Don't use "auth" command**: There is no `jentic auth` command. Authentication is handled through the `jentic register` and profile management system. If you see "unknown command 'auth'" error, you're using an invalid command.
- **Search functionality may be unreliable**: The `jentic search` command may return empty results or HTTP 422 errors even for valid queries. Prefer direct listing commands like `jentic apis list` and `jentic apis show`.
- **Operation IDs are hidden in formatted output**: Use `--output json` with CLI commands to see the full operation object including the `id` field needed for execution.
- **Spec retrieval may fail**: If `jentic apis spec` returns HTTP 500 with message "Revision '<id>' has no stored spec file", the API was registered without storing its OpenAPI specification. Fall back to operation listing and inspection, but be aware that authentication requirements may not be discoverable.
- **Authentication requirements are often not visible**: The `security_schemes` field in API metadata is frequently empty, and the operation `auth` field often shows `null` even when authentication is required. If spec retrieval fails, you cannot reliably discover authentication requirements through the platform. Consult external documentation or the API provider.
- **Path-based operation lookup is fragile**: Commands like `jentic inspect 'GET /path'` may fail to find operations. Always use the operation ID from `jentic apis operations`.
- **Empty operations list**: An API may be registered but show 0 operations if its current revision has no operations defined. This is not an error, but indicates the API is not yet ready for use.
- **"toolkits" command may not exist**: The `jentic toolkits` command may not be available in all CLI versions. If you get "unknown command" error, this is expected and you should skip that verification step.
- **Must be authenticated first**: All commands in this skill require a valid authentication token. If you don't have a token or your agent registration is incomplete, you must complete authentication before attempting to discover APIs. See agent registration and authentication skill documentation.
- **Token exchange may fail immediately after registration**: After registering a new agent, the initial token exchange attempt may fail even though registration succeeded. This is normal — wait 10-15 seconds for background approval to complete, then retry the token exchange before proceeding with API discovery.
- **Token storage is profile-specific**: Tokens are stored in `tokens.json` within your profile directory. If you switch profiles or copy a profile to a new location, ensure `tokens.json` is also copied. If `tokens.json` is missing, you will need to re-authenticate even if your agent is approved.

## Verification

You have successfully completed this task when you can answer:

1. **Does the API exist?** Confirmed by seeing it in `jentic apis list` output
2. **What operations are available?** Listed via `jentic apis operations` with method and path for each
3. **What are the operation IDs?** Retrieved from JSON output of operations list
4. **What authentication does it require?** Determined from spec (if available) or external documentation

**CLI verification:**
```bash
jentic apis operations <vendor>/<name>/<version> --output json
```
Should return JSON array with at least one operation object containing `id`, `method`, and `path`.

**HTTP verification:**
```
GET {{ platform.control_plane_url }}/apis/operations?api=<vendor>/<name>/<version>
```
Should return 200 status with operation array in response body.

**Note:** If authentication requirements are critical and cannot be discovered through the platform (spec retrieval failed, `security_schemes` is empty, and operation metadata shows `auth: null`), you must obtain this information from external sources before proceeding to request access or execute operations.
