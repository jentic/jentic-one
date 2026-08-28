---
name: check-agent-scopes
description: Inspect your agent's current permissions and scopes.
version: 1
---

# Check Agent Scopes

## When to Use

Use this skill when you need to perform the check agent scopes step during onboarding to the Jentic platform.

## Prerequisites

- Access to the Jentic platform
- Completed: View Audit Events (`view-audit-events`)

## Procedure

### 1. Check Agent Scopes

Inspect your agent's current permissions and scopes.

**CLI:**

```bash
jentic profile
```

**HTTP:**

```
GET /me
```

**Notes:**
- Use GET /agents/{agent_id} or the `me` endpoint to inspect your current profile
- Shows: status, scopes, toolkit bindings, approval state

## Quick Reference

### CLI Commands

- `jentic profile`

### HTTP Endpoints

- `GET /me`

## Pitfalls

- The `/me` endpoint requires a valid Bearer token — will 401 if token expired

## Verification

- Agent profile is accessible and active
