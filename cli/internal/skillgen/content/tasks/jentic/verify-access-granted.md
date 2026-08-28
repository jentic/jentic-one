---
name: verify-access-granted
description: Confirm the access request was approved and check granted permissions.
version: 1
---

# Verify Access Granted

## When to Use

Use this skill when you need to perform the verify access granted step during onboarding to the Jentic platform.

## Prerequisites

- Access to the Jentic platform
- Completed: Add To Toolkit (`add-to-toolkit`)

## Procedure

### 1. Verify Access Granted

Confirm the access request was approved and check granted permissions.

**CLI:**

```bash
jentic access
```

**HTTP:**

```
GET /access-requests
```

**Response:**
```json
{
  "data": "<array>"
  "has_more": "<boolean>"
  "next_cursor": "<string>"
}
```

**Query parameters:**
- `actor_id`
- `status`
- `cursor`
- `limit`

**Notes:**
- Poll GET /access-requests with your actor_id to check status
- Status transitions: pending → approved (or denied)

## Quick Reference

### CLI Commands

- `jentic access`

### HTTP Endpoints

- `GET /access-requests`

## Pitfalls

- Approval is not instant — poll every few seconds for up to 15s before concluding it's stuck

## Verification

- Agent confirmed approved access request exists
