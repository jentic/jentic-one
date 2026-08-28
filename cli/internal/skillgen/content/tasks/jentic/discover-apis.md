---
name: discover-apis
description: Browse the API catalog and list all available APIs.
version: 1
---

# Discover Apis

## When to Use

Use this skill when you need to perform the discover apis step during onboarding to the Jentic platform.

## Prerequisites

- Access to the Jentic platform
- Completed: Authenticate (`authenticate`)

## Procedure

### 1. Discover Apis

Browse the API catalog and list all available APIs.

**CLI:**

```bash
jentic catalog
```

```bash
jentic apis
```

**HTTP:**

```
GET /apis
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
- `vendor`
- `cursor`
- `limit`

```
GET /catalog
```

**Response:**
```json
{
  "catalog_total": "<integer>"
  "data": "<array>"
  "has_more": "<boolean>"
  "manifest_age_seconds": "<integer>"
  "next_cursor": "<string>"
  "outdated_count": "<integer>"
  "registered_count": "<integer>"
}
```

**Query parameters:**
- `q`
- `registered_only`
- `unregistered_only`
- `outdated_only`
- `include_snoozed`
- `cursor`
- `limit`

**Notes:**
- Returns a paginated list of all published APIs in the registry
- Use query params `vendor`, `name` to filter; `cursor` for pagination

## Quick Reference

### CLI Commands

- `jentic catalog`
- `jentic apis`

### HTTP Endpoints

- `GET /apis`
- `GET /catalog`

## Pitfalls

- Requires a valid Bearer token — authenticate first

## Verification

- Catalog endpoint was hit
