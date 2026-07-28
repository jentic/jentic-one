import type { ReactNode } from 'react';
import { describe, it, expect, vi } from 'vitest';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { renderHook, waitFor } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { worker } from '@/mocks/browser';
import { sharedQueryKeys } from '@/shared/api';
import { toolkitKeys, useUpdateToolkit } from '@/modules/toolkits/api';

/**
 * `useUpdateToolkit` renames/re-describes a toolkit. Its `onSuccess` must keep
 * every surface that shows a toolkit name in sync — the module's own list/detail
 * cache AND the shared Dashboard root — symmetrically with the other
 * name-changing mutations (`useUpdateAgent`), so a rename doesn't leave the
 * dashboard tiles stale until their own poll (#12).
 */

function makeWrapper() {
	const queryClient = new QueryClient({
		defaultOptions: { queries: { retry: false, gcTime: 0 } },
	});
	const wrapper = ({ children }: { children: ReactNode }) => (
		<QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
	);
	return { queryClient, wrapper };
}

describe('useUpdateToolkit', () => {
	it('seeds the detail cache and invalidates the toolkits + dashboard roots on success (#12)', async () => {
		const updated = {
			toolkit_id: 'tk_demo_github',
			name: 'GitHub Suite',
			description: 'Repo automation.',
			active: true,
			key_count: 2,
			credential_count: 1,
			permissions: [],
			created_at: '2026-05-01T10:00:00Z',
			updated_at: '2026-05-03T10:00:00Z',
		};
		worker.use(http.patch('/toolkits/:toolkitId', () => HttpResponse.json(updated)));

		const { queryClient, wrapper } = makeWrapper();
		const invalidateSpy = vi.spyOn(queryClient, 'invalidateQueries');
		const setDataSpy = vi.spyOn(queryClient, 'setQueryData');

		const { result } = renderHook(() => useUpdateToolkit('tk_demo_github'), { wrapper });
		result.current.mutate({ name: 'GitHub Suite' });

		await waitFor(() => expect(result.current.isSuccess).toBe(true));

		// Detail cache is seeded with the server response (instant header update).
		expect(setDataSpy).toHaveBeenCalledWith(
			toolkitKeys.detail('tk_demo_github'),
			expect.objectContaining({ name: 'GitHub Suite' }),
		);
		// Both the module root and the shared dashboard root are invalidated.
		const invalidatedKeys = invalidateSpy.mock.calls.map((c) => c[0]?.queryKey);
		expect(invalidatedKeys).toContainEqual(toolkitKeys.all);
		expect(invalidatedKeys).toContainEqual(sharedQueryKeys.dashboardRoot);
	});
});
