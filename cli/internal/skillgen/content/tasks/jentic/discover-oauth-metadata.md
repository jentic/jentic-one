---
name: discover-oauth-metadata
description: Fetch the platform's OAuth authorization server metadata.
version: 1
---

# Discover Oauth Metadata

## When to Use

Use this skill when you need to perform the discover oauth metadata step during onboarding to the Jentic platform.

## Prerequisites

- Access to the Jentic platform
- Completed: Read Api Spec (`read-api-spec`)

## Procedure

### 1. Discover Oauth Metadata

Fetch the platform's OAuth authorization server metadata.

**CLI:**

```bash
# (CLI command to be documented)
```

**HTTP:**

```
GET /.well-known/oauth-authorization-server
```

**Notes:**
- Standard RFC 8414 endpoint returning authorization server metadata
- Key fields in response: `token_endpoint`, `grant_types_supported`, `jwks_uri`
- Use the `token_endpoint` value as the `aud` claim in JWT assertions

## Quick Reference

### CLI Commands

- *(To be documented)*

### HTTP Endpoints

- `GET /.well-known/oauth-authorization-server`

## Pitfalls

- This is a public endpoint (no auth required) but agents often skip it and hardcode URLs

## Verification

- OAuth metadata endpoint was hit
