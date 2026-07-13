import type { ReactNode } from 'react';
import { afterEach, beforeEach, describe, it, expect } from 'vitest';
import { http, HttpResponse } from 'msw';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { renderHook, waitFor } from '@testing-library/react';
import { worker } from '@/mocks/browser';
import { createErrorHandler } from '@/__tests__/test-utils';
import { setToken, clearToken } from '@/shared/api';
import { useActorDirectory } from '@/shared/hooks/useActorDirectory';

function wrapper({ children }: { children: ReactNode }) {
	const queryClient = new QueryClient({
		defaultOptions: { queries: { retry: false, gcTime: 0 } },
	});
	return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>;
}

function actor(id: string, name: string, actor_type = 'agent') {
	return { id, name, actor_type, active: true, created_at: '2026-01-01T00:00:00Z' };
}

describe('useActorDirectory', () => {
	beforeEach(() => setToken('mock-access-token'));
	afterEach(() => clearToken());

	it('builds a lookup map from the directory and resolves ids to names', async () => {
		worker.use(
			http.get('/actors', () =>
				HttpResponse.json({
					data: [actor('agnt_1', 'Inbox Triage'), actor('usr_1', 'Ada', 'user')],
					has_more: false,
					next_cursor: null,
				}),
			),
		);
		const { result } = renderHook(() => useActorDirectory(), { wrapper });

		await waitFor(() => expect(result.current.byId.size).toBe(2));
		expect(result.current.resolve('agnt_1')).toBe('Inbox Triage');
		expect(result.current.resolve('usr_1')).toBe('Ada');
		expect(result.current.byId.get('usr_1')?.actor_type).toBe('user');
	});

	it('paginates through every page via next_cursor', async () => {
		worker.use(
			http.get('/actors', ({ request }) => {
				const cursor = new URL(request.url).searchParams.get('cursor');
				if (cursor == null) {
					return HttpResponse.json({
						data: [actor('agnt_1', 'Page One Bot')],
						has_more: true,
						next_cursor: 'cursor-2',
					});
				}
				return HttpResponse.json({
					data: [actor('agnt_2', 'Page Two Bot')],
					has_more: false,
					next_cursor: null,
				});
			}),
		);
		const { result } = renderHook(() => useActorDirectory(), { wrapper });

		await waitFor(() => expect(result.current.byId.size).toBe(2));
		expect(result.current.resolve('agnt_1')).toBe('Page One Bot');
		expect(result.current.resolve('agnt_2')).toBe('Page Two Bot');
	});

	// Safety: a misbehaving backend that claims `has_more: true` but hands back a
	// null cursor must terminate the pagination loop, not spin forever.
	it('terminates when has_more is true but next_cursor is null', async () => {
		let calls = 0;
		worker.use(
			http.get('/actors', () => {
				calls += 1;
				return HttpResponse.json({
					data: [actor('agnt_1', 'Only Bot')],
					has_more: true,
					next_cursor: null,
				});
			}),
		);
		const { result } = renderHook(() => useActorDirectory(), { wrapper });

		await waitFor(() => expect(result.current.byId.size).toBe(1));
		expect(result.current.resolve('agnt_1')).toBe('Only Bot');
		expect(calls).toBe(1);
	});

	// Safety: a backend stuck returning the SAME non-null cursor must terminate
	// once we've already followed that cursor, rather than looping indefinitely.
	it('terminates when the backend repeats the same next_cursor', async () => {
		let calls = 0;
		worker.use(
			http.get('/actors', ({ request }) => {
				calls += 1;
				const cursor = new URL(request.url).searchParams.get('cursor');
				// Always advertise more with the same cursor token.
				return HttpResponse.json({
					data: [actor(cursor == null ? 'agnt_1' : 'agnt_2', 'Stuck Bot')],
					has_more: true,
					next_cursor: 'stuck-cursor',
				});
			}),
		);
		const { result } = renderHook(() => useActorDirectory(), { wrapper });

		// First page (cursor=null) → follows 'stuck-cursor' once → sees it repeat → stops.
		await waitFor(() => expect(result.current.byId.size).toBe(2));
		expect(calls).toBe(2);
	});

	it('resolves unknown ids to undefined and handles an empty directory', async () => {
		worker.use(
			http.get('/actors', () =>
				HttpResponse.json({ data: [], has_more: false, next_cursor: null }),
			),
		);
		const { result } = renderHook(() => useActorDirectory(), { wrapper });

		await waitFor(() => expect(result.current.isLoading).toBe(false));
		expect(result.current.byId.size).toBe(0);
		expect(result.current.resolve('agnt_missing')).toBeUndefined();
	});

	it('does not fetch (or crash) when unauthenticated', async () => {
		clearToken();
		const { result } = renderHook(() => useActorDirectory(), { wrapper });

		// Gated off: never loading, never errors, empty map — nothing to wait for.
		expect(result.current.isLoading).toBe(false);
		expect(result.current.byId.size).toBe(0);
		expect(result.current.resolve('agnt_1')).toBeUndefined();
	});

	it('surfaces an error without crashing when the endpoint fails', async () => {
		worker.use(createErrorHandler('get', '/actors', { status: 500 }));
		const { result } = renderHook(() => useActorDirectory(), { wrapper });

		await waitFor(() => expect(result.current.isError).toBe(true));
		// The map stays empty and resolve() is a safe no-op, so callers fall back.
		expect(result.current.byId.size).toBe(0);
		expect(result.current.resolve('agnt_1')).toBeUndefined();
	});
});
