---
name: inspect-api-operations
description: List the operations available on the test API.
version: 1
---

# Inspect Api Operations

## When to Use

Use this skill when you need to perform the inspect api operations step during onboarding to the Jentic platform.

## Prerequisites

- Access to the Jentic platform
- Completed: Find Credential (`find-credential`)

## Procedure

### 1. Inspect Api Operations

List the operations available on the test API.

**CLI:**

```bash
jentic apis
```

**HTTP:**

```
GET /apis/jentic-test/test-api/1.0.0/operations
```

**Notes:**
- Lists operations (method + path) available on a specific API version
- Endpoint: GET /apis/{vendor}/{name}/{version}/operations

## Quick Reference

### CLI Commands

- `jentic apis`

### HTTP Endpoints

- `GET /apis/jentic-test/test-api/1.0.0/operations`

## Pitfalls

- Must use the exact vendor/name/version from the registry — partial paths return 404

## Verification

- Operations endpoint was hit
