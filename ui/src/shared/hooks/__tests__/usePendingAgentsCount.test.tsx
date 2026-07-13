import type { ReactNode } from 'react';
import { describe, it, expect } from 'vitest';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { renderHook, waitFor } from '@testing-library/react';
import { worker } from '@/mocks/browser';
import { createErrorHandler } from '@/__tests__/test-utils';
import { usePendingAgentsCount } from '@/shared/hooks/usePendingAgentsCount';

function wrapper({ children }: { children: ReactNode }) {
	const queryClient = new QueryClient({
		defaultOptions: { queries: { retry: false, gcTime: 0 } },
	});
	return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>;
}

describe('usePendingAgentsCount', () => {
	it('reports the count of pending agents from the real endpoint', async () => {
		const { result } = renderHook(() => usePendingAgentsCount(), { wrapper });
		await waitFor(() => expect(result.current.count).toBeGreaterThan(0));
		// The seed's pending set fits in one page, so it's an exact count, not "N+".
		expect(result.current.atLeast).toBe(false);
	});

	it('resolves to 0 (never a misleading badge) when the request fails', async () => {
		worker.use(createErrorHandler('get', '/agents', { status: 500 }));
		const { result } = renderHook(() => usePendingAgentsCount(), { wrapper });
		await waitFor(() => expect(result.current.count).toBe(0));
		expect(result.current.atLeast).toBe(false);
	});
});
