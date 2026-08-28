---
name: read-api-spec
description: Download and examine the test API's OpenAPI specification.
version: 1
---

# Read Api Spec

## When to Use

Use this skill when you need to perform the read api spec step during onboarding to the Jentic platform.

## Prerequisites

- Access to the Jentic platform
- Completed: Inspect Api Operations (`inspect-api-operations`)

## Procedure

### 1. Read Api Spec

Download and examine the test API's OpenAPI specification.

**CLI:**

```bash
jentic apis
```

**HTTP:**

```
GET /apis/jentic-test/test-api/1.0.0/openapi
```

**Notes:**
- Downloads the raw OpenAPI JSON/YAML for a specific API version
- Endpoint: GET /apis/{vendor}/{name}/{version}/openapi

## Quick Reference

### CLI Commands

- `jentic apis`

### HTTP Endpoints

- `GET /apis/jentic-test/test-api/1.0.0/openapi`

## Pitfalls

- The spec content is returned inline — large specs may be truncated in some clients

## Verification

- OpenAPI spec endpoint was hit
