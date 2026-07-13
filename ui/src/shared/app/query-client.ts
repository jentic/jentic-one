import { QueryClient } from '@tanstack/react-query';
import { isClientError } from '@/shared/api';

/**
 * Shared QueryClient.
 *
 * Client errors (HTTP 4xx — expired/invalid token, missing permission, bad
 * input, not-found) are deterministic: retrying can't fix them, it only delays
 * the error state (and, for looping queries, hammers the server). So they are
 * non-retryable; 5xx / network errors get the default bounded retry.
 */
export function createQueryClient(): QueryClient {
	return new QueryClient({
		defaultOptions: {
			queries: {
				retry: (failureCount, error) => {
					if (isClientError(error)) return false;
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
