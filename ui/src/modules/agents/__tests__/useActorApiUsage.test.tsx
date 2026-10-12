import type { ReactNode } from 'react';
import { describe, it, expect, beforeEach } from 'vitest';
import { http, HttpResponse } from 'msw';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { renderHook, waitFor } from '@/__tests__/test-utils';
import { worker } from '@/mocks/browser';
import { setToken } from '@/shared/api';
import { AuthProvider, useAuth } from '@/shared/auth';
import { useActorApiUsage } from '@/modules/agents/api/hooks';

/** Serve `GET /users/me` for the test token with these permissions. */
function seedViewer(permissions: string[]) {
	worker.use(
		http.get('/users/me', () =>
			HttpResponse.json({
				id: 'usr_viewer_1',
				email: 'viewer@local',
				first_name: 'View',
				last_name: 'Er',
				active: true,
				permissions,
				must_change_password: false,
				created_at: '2026-01-01T00:00:00Z',
				updated_at: null,
			}),
		),
	);
}

/** Counts the per-API usage reads (`group_by=credential`) the hook makes. */
function countUsageReads(): { count: number } {
	const seen = { count: 0 };
	worker.events.on('request:start', ({ request }) => {
		const url = new URL(request.url);
		if (url.pathname === '/monitoring/usage' && url.searchParams.get('api_id') != null)
			seen.count += 1;
	});
	return seen;
}

function wrapper({ children }: { children: ReactNode }) {
	const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
	return (
		<QueryClientProvider client={client}>
			<AuthProvider>{children}</AuthProvider>
		</QueryClientProvider>
	);
}

/** The hook beside the auth state, so a spec can wait for the viewer first. */
function useUsageWithViewer() {
	const auth = useAuth();
	return { user: auth.user, usage: useActorApiUsage('agnt_active_1', ['api_1', 'api_2']) };
}

describe('useActorApiUsage — gated on org:admin', () => {
	beforeEach(() => {
		worker.events.removeAllListeners();
		setToken('test-token');
	});

	it('never reads per-API usage for a viewer without org:admin', async () => {
		seedViewer(['agents:read', 'credentials:read']);
		const reads = countUsageReads();
		const { result } = renderHook(useUsageWithViewer, { wrapper });

		await waitFor(() => expect(result.current.user).not.toBeNull());
		// Each gated query settles to `null` without a request.
		await waitFor(() => expect(result.current.usage.size).toBe(2));
		expect(result.current.usage.get('api_1')).toBeNull();
		expect(result.current.usage.get('api_2')).toBeNull();
		expect(reads.count).toBe(0);
	});

	it('reads each API once for an org:admin', async () => {
		seedViewer(['org:admin']);
		const reads = countUsageReads();
		const { result } = renderHook(useUsageWithViewer, { wrapper });

		await waitFor(() => expect(result.current.user).not.toBeNull());
		await waitFor(() => expect(reads.count).toBe(2));
		await waitFor(() => expect(result.current.usage.size).toBe(2));
	});
});
