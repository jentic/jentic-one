import type { ReactNode } from 'react';
import { describe, it, expect } from 'vitest';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { renderHook, waitFor } from '@testing-library/react';
import { worker } from '@/mocks/browser';
import { createErrorHandler } from '@/__tests__/test-utils';
import { usePendingAccessRequestCount } from '@/shared/hooks/usePendingAccessRequestCount';

function wrapper({ children }: { children: ReactNode }) {
	const queryClient = new QueryClient({
		defaultOptions: { queries: { retry: false, gcTime: 0 } },
	});
	return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>;
}

describe('usePendingAccessRequestCount', () => {
	it('reports the count of pending access requests from the real endpoint', async () => {
		const { result } = renderHook(() => usePendingAccessRequestCount(), { wrapper });
		await waitFor(() => expect(result.current.count).toBeGreaterThan(0));
		// The seed's pending set fits in one page, so it's an exact count, not "N+".
		expect(result.current.atLeast).toBe(false);
	});

	it('resolves to 0 (never a misleading badge) when the request fails', async () => {
		worker.use(createErrorHandler('get', '/access-requests', { status: 500 }));
		const { result } = renderHook(() => usePendingAccessRequestCount(), { wrapper });
		// Give the failed query a tick to settle; the floor stays at 0.
		await waitFor(() => expect(result.current.count).toBe(0));
		expect(result.current.atLeast).toBe(false);
	});
});
