import { QueryClient } from '@tanstack/react-query';
import { isClientError } from '@/shared/api';

/** 4xx answers that are about timing, not the request: a timeout (408) and a
 * rate limit (429) can succeed on a later attempt, so they stay retryable. */
const RETRYABLE_CLIENT_STATUSES = new Set([408, 429]);

/**
 * A non-retryable HTTP 4xx, whether it is the generated `ApiError` itself or a
 * module's sentinel error (`MonitorApiError`, `AgentsApiError`, …) that wraps
 * one and carries its numeric `status`. 408 and 429 are excluded.
 */
export function isClientErrorLike(error: unknown): boolean {
	let status: unknown;
	if (isClientError(error)) status = error.status;
	else if (error instanceof Error && 'status' in error)
		status = (error as { status: unknown }).status;
	return (
		typeof status === 'number' &&
		status >= 400 &&
		status < 500 &&
		!RETRYABLE_CLIENT_STATUSES.has(status)
	);
}

/**
 * Shared QueryClient.
 *
 * Client errors (HTTP 4xx — expired/invalid token, missing permission, bad
 * input, not-found) are deterministic: retrying can't fix them, it only delays
 * the error state (and, for looping queries, hammers the server). So they are
 * non-retryable; 408 / 429, 5xx and network errors get the default bounded
 * retry.
 */
export function createQueryClient(): QueryClient {
	return new QueryClient({
		defaultOptions: {
			queries: {
				retry: (failureCount, error) => {
					if (isClientErrorLike(error)) return false;
					return failureCount < 2;
				},
				staleTime: 30_000,
			},
			mutations: {
				retry: false,
			},
		},
	});
}
