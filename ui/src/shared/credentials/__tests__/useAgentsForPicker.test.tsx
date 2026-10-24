import type { ReactNode } from 'react';
import { describe, expect, it } from 'vitest';
import { http, HttpResponse } from 'msw';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { renderHook, waitFor } from '@testing-library/react';
import { worker } from '@/mocks/browser';
import { sharedQueryKeys } from '@/shared/api/queryKeys';
import { useAgentsForPicker } from '@/shared/credentials/api/vendors-hooks';

describe('useAgentsForPicker', () => {
	it('refetches when the shared agents root is invalidated', async () => {
		let names = ['agent-a'];
		worker.use(
			http.get('/agents', () =>
				HttpResponse.json({
					data: names.map((name, i) => ({ id: `agnt_${i}`, name, status: 'active' })),
					has_more: false,
					next_cursor: null,
				}),
			),
		);
		const queryClient = new QueryClient({
			defaultOptions: { queries: { retry: false, gcTime: 0 } },
		});
		const wrapper = ({ children }: { children: ReactNode }) => (
			<QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
		);

		const { result } = renderHook(() => useAgentsForPicker(), { wrapper });
		await waitFor(() =>
			expect(result.current.data?.data.map((a) => a.name)).toEqual(['agent-a']),
		);

		names = ['agent-a', 'agent-b'];
		await queryClient.invalidateQueries({ queryKey: sharedQueryKeys.agentsRoot });

		await waitFor(() =>
			expect(result.current.data?.data.map((a) => a.name)).toEqual(['agent-a', 'agent-b']),
		);
	});
});
