import { QueryClient } from '@tanstack/react-query';
import { isClientError } from '@/shared/api';

/**
 * True when `error` is an HTTP 4xx. Recognises the generated client's
 * `ApiError` and the module repository errors that wrap it
 * (`WorkspaceApiError`, `DiscoverApiError`, `AgentsApiError`, …), which all
 * carry the HTTP status as a numeric `status` field (`null` when the request
 * never reached the server — a network failure, which stays retryable).
 */
export function isClientErrorStatus(error: unknown): boolean {
	if (isClientError(error)) return true;
	if (!(error instanceof Error) || !('status' in error)) return false;
	const { status } = error as { status: unknown };
	return typeof status === 'number' && status >= 400 && status < 500;
}

/**
 * Shared QueryClient.
 *
 * Client errors (HTTP 4xx — expired/invalid token, missing permission, bad
 * input, not-found) are deterministic: retrying can't fix them, it only delays
 * the error state (and, for looping queries, hammers the server). So they are
 * non-retryable — whether raw `ApiError`s or a module's wrapped error — while
 * 5xx / network errors get the default bounded retry.
 */
export function createQueryClient(): QueryClient {
	return new QueryClient({
		defaultOptions: {
			queries: {
				retry: (failureCount, error) => {
					if (isClientErrorStatus(error)) return false;
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
