/**
 * API client setup for jentic-one.
 *
 * Wraps the codegen'd OpenAPI client (`./generated`, regenerate with
 * `npm run codegen`) with the bits the generator can't know about:
 *
 *   - Bearer-JWT auth: a TOKEN resolver reads the live token from the token
 *     store so every generated service call carries `Authorization: Bearer`.
 *   - No cookies: jentic-one is stateless JWT, so credentials are omitted
 *     (the generator defaults to `credentials: 'include'`).
 *
 * Import generated services/models from `@/shared/api` (the facade), not from
 * `./generated` directly, so this configuration is always applied first.
 */
import { OpenAPI } from '@/shared/api/generated/core/OpenAPI';
import { ApiError } from '@/shared/api/generated/core/ApiError';
import { getToken } from '@/shared/api/token-store';

// Clear any absolute URL hardcoded by codegen (e.g. from openapi.json servers block).
// This ensures relative routing works in dev/test/MSW.
OpenAPI.BASE = '';

// Stateless Bearer-JWT — never send cookies.
OpenAPI.WITH_CREDENTIALS = false;
OpenAPI.CREDENTIALS = 'omit';
// Resolve the bearer token per-request so it always reflects the current
// session (login/logout/refresh). Returning '' for the logged-out state makes
// the generator omit the Authorization header entirely.
OpenAPI.TOKEN = async () => getToken() ?? '';

export { ApiError };

/** True when the error is an auth failure the client should not retry. */
export function isAuthError(error: unknown): error is ApiError {
	return error instanceof ApiError && (error.status === 401 || error.status === 403);
}

/**
 * True for deterministic client errors (HTTP 4xx). These won't succeed on
 * retry — the request itself is the problem — so callers should fail fast
 * instead of burning retries (and, for looping queries, hammering the server).
 */
export function isClientError(error: unknown): error is ApiError {
	return error instanceof ApiError && error.status >= 400 && error.status < 500;
}
