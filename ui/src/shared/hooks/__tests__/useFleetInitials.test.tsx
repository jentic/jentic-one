/**
 * useFleetInitials — one reading of every actor's avatar letters: the
 * directory's agents joined with the caller's roster, then the directory's
 * users, so every surface reads the same letters.
 */
import type { ReactNode } from 'react';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { http, HttpResponse } from 'msw';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { renderHook, waitFor } from '@testing-library/react';
import { worker } from '@/mocks/browser';
import { createErrorHandler } from '@/__tests__/test-utils';
import { setToken, clearToken } from '@/shared/api';
import { useFleetInitials } from '@/shared/hooks/useFleetInitials';
import { actorDirectoryKey } from '@/shared/hooks/useActorDirectory';
import { smartInitials } from '@/shared/lib/smartInitials';

let queryClient: QueryClient;
function wrapper({ children }: { children: ReactNode }) {
	return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>;
}

const actor = (id: string, name: string, actor_type: 'agent' | 'user', day: number) => ({
	id,
	name,
	actor_type,
	active: true,
	created_at: `2026-01-0${day}T00:00:00Z`,
});

const DIRECTORY = [
	actor('agnt_1', 'support-triage', 'agent', 1),
	actor('agnt_2', 'support-triage-eu', 'agent', 2),
	actor('usr_1', 'Sam Tan', 'user', 1),
];

const ROSTER = [
	{ id: 'agnt_2', name: 'support-triage-eu', createdAt: '2026-01-02T00:00:00Z' },
	{ id: 'agnt_3', name: 'mailer', createdAt: '2026-01-03T00:00:00Z' },
];

describe('useFleetInitials', () => {
	beforeEach(() => {
		queryClient = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
		setToken('mock-access-token');
	});
	afterEach(() => clearToken());

	it('reads the letters the whole fleet gives, from the directory and the roster together', async () => {
		worker.use(
			http.get('/actors', () =>
				HttpResponse.json({ data: DIRECTORY, has_more: false, next_cursor: null }),
			),
		);
		const { result } = renderHook(() => useFleetInitials(ROSTER), { wrapper });
		await waitFor(() => expect(result.current.get('agnt_1')).toBe('ST'));
		// The agents read exactly as `smartInitials` reads the joined fleet …
		const fleet = [
			{ id: 'agnt_1', name: 'support-triage', createdAt: '2026-01-01T00:00:00Z' },
			...ROSTER,
		];
		for (const [id, letters] of smartInitials(fleet))
			expect(result.current.get(id)).toBe(letters);
		expect(result.current.get('agnt_2')).toBe('STE');
		expect(result.current.get('agnt_3')).toBe('MA');
		// … and the user, older than every agent, still picks around them.
		expect(result.current.get('usr_1')).toBe('SAT');
	});

	it('reads the roster alone when the directory cannot be listed', async () => {
		worker.use(createErrorHandler('get', '/actors', { status: 403 }));
		const { result } = renderHook(() => useFleetInitials(ROSTER), { wrapper });
		expect(result.current.get('agnt_2')).toBe('ST');
		expect(result.current.get('agnt_3')).toBe('MA');
		// The listing's failure arrives, and changes nothing.
		await waitFor(() =>
			expect(queryClient.getQueryState(actorDirectoryKey)?.status).toBe('error'),
		);
		expect(result.current.get('agnt_2')).toBe('ST');
		expect(result.current.size).toBe(2);
	});

	it('reads nothing for an empty fleet', () => {
		worker.use(createErrorHandler('get', '/actors', { status: 403 }));
		const { result } = renderHook(() => useFleetInitials(), { wrapper });
		expect(result.current.size).toBe(0);
	});
});
