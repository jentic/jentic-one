import type { ReactNode } from 'react';
import { describe, it, expect } from 'vitest';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { renderHook, waitFor } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { worker } from '@/mocks/browser';
import { createErrorHandler } from '@/__tests__/test-utils';
import { useVersionInfo } from '@/shared/hooks/useVersionInfo';

function wrapper({ children }: { children: ReactNode }) {
	const queryClient = new QueryClient({
		defaultOptions: { queries: { retry: false, gcTime: 0 } },
	});
	return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>;
}

describe('useVersionInfo', () => {
	it('reports the current version from the endpoint', async () => {
		const { result } = renderHook(() => useVersionInfo(), { wrapper });
		await waitFor(() => expect(result.current.current).toBe('0.26.0'));
		expect(result.current.update_available).toBe(false);
		expect(result.current.latest).toBeNull();
	});

	it('surfaces an available update', async () => {
		worker.use(
			http.get('/system/version', () =>
				HttpResponse.json({ current: '0.26.0', latest: '0.27.0', update_available: true }),
			),
		);
		const { result } = renderHook(() => useVersionInfo(), { wrapper });
		await waitFor(() => expect(result.current.update_available).toBe(true));
		expect(result.current.latest).toBe('0.27.0');
	});

	it('degrades to no-update when the request fails', async () => {
		worker.use(createErrorHandler('get', '/system/version', { status: 500 }));
		const { result } = renderHook(() => useVersionInfo(), { wrapper });
		// Never paints a banner on failure: update_available stays false.
		await waitFor(() => expect(result.current.current).toBe(''));
		expect(result.current.update_available).toBe(false);
		expect(result.current.latest).toBeNull();
	});
});
