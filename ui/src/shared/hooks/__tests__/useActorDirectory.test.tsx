import type { ReactNode } from 'react';
import { afterEach, beforeEach, describe, it, expect } from 'vitest';
import { http, HttpResponse } from 'msw';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { renderHook, waitFor } from '@testing-library/react';
import { worker } from '@/mocks/browser';
import { createErrorHandler } from '@/__tests__/test-utils';
import { setToken, clearToken } from '@/shared/api';
import { AuthProvider } from '@/shared/auth/AuthContext';
import { useActorDirectory } from '@/shared/hooks/useActorDirectory';
import { ACTOR_LOOKUP_MAX_IDS } from '@/shared/lib/actorDirectory';

function wrapper({ children }: { children: ReactNode }) {
	const queryClient = new QueryClient({
		defaultOptions: { queries: { retry: false, gcTime: 0 } },
	});
	return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>;
}

/** Like {@link wrapper}, with the signed-in user's permissions served by `/users/me`. */
function memberWrapper(permissions: string[]) {
	worker.use(
		http.get('/users/me', () =>
			HttpResponse.json({
				id: 'usr_member',
				email: 'member@example.com',
				first_name: 'Mem',
				last_name: 'Ber',
				permissions,
				must_change_password: false,
			}),
		),
	);
	return function MemberWrapper({ children }: { children: ReactNode }) {
		const queryClient = new QueryClient({
			defaultOptions: { queries: { retry: false, gcTime: 0 } },
		});
		return (
			<QueryClientProvider client={queryClient}>
				<AuthProvider>{children}</AuthProvider>
			</QueryClientProvider>
		);
	};
}

function lookupEntry(id: string, name: string, actor_type = 'agent') {
	return { id, name, actor_type, active: true };
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

	describe('without users:read', () => {
		it('resolves the requested ids through the by-id lookup, never the listing', async () => {
			let listCalls = 0;
			const lookupCalls: string[][] = [];
			worker.use(
				http.get('/actors', () => {
					listCalls += 1;
					return HttpResponse.json({ detail: 'forbidden' }, { status: 403 });
				}),
				http.get('/actors/lookup', ({ request }) => {
					const ids = new URL(request.url).searchParams.getAll('id');
					lookupCalls.push(ids);
					return HttpResponse.json({
						data: [
							lookupEntry('agnt_1', 'Inbox Triage'),
							lookupEntry('usr_1', 'Ada', 'user'),
						].filter((a) => ids.includes(a.id)),
					});
				}),
			);
			const { result } = renderHook(
				() => useActorDirectory(['agnt_1', 'usr_1', 'agnt_1', null, 'usr_missing']),
				{ wrapper: memberWrapper(['agents:read']) },
			);

			await waitFor(() => expect(result.current.resolve('usr_1')).toBe('Ada'));
			expect(result.current.resolve('agnt_1')).toBe('Inbox Triage');
			expect(result.current.resolve('usr_missing')).toBeUndefined();
			expect(result.current.isError).toBe(false);
			expect(listCalls).toBe(0);
			// One batched call for the distinct ids.
			expect(lookupCalls).toHaveLength(1);
			expect([...lookupCalls[0]].sort()).toEqual(['agnt_1', 'usr_1', 'usr_missing']);
		});

		it(`splits more than ${ACTOR_LOOKUP_MAX_IDS} ids into several calls`, async () => {
			const lookupCalls: string[][] = [];
			worker.use(
				http.get('/actors/lookup', ({ request }) => {
					const ids = new URL(request.url).searchParams.getAll('id');
					lookupCalls.push(ids);
					return HttpResponse.json({
						data: ids.map((id) => lookupEntry(id, `name-${id}`)),
					});
				}),
			);
			const ids = Array.from({ length: ACTOR_LOOKUP_MAX_IDS + 5 }, (_, i) => `agnt_${i}`);
			const { result } = renderHook(() => useActorDirectory(ids), {
				wrapper: memberWrapper(['agents:read']),
			});

			await waitFor(() => expect(result.current.byId.size).toBe(ids.length));
			expect(result.current.resolve('agnt_104')).toBe('name-agnt_104');
			expect(lookupCalls.map((c) => c.length).sort((a, b) => a - b)).toEqual([
				5,
				ACTOR_LOOKUP_MAX_IDS,
			]);
		});

		it('keeps the names one chunk resolved when another chunk fails', async () => {
			worker.use(
				http.get('/actors/lookup', ({ request }) => {
					const ids = new URL(request.url).searchParams.getAll('id');
					if (ids.includes('agnt_0')) {
						return HttpResponse.json({ detail: 'boom' }, { status: 500 });
					}
					return HttpResponse.json({
						data: ids.map((id) => lookupEntry(id, `name-${id}`)),
					});
				}),
			);
			const ids = Array.from({ length: ACTOR_LOOKUP_MAX_IDS + 5 }, (_, i) => `agnt_${i}`);
			const { result } = renderHook(() => useActorDirectory(ids), {
				wrapper: memberWrapper(['agents:read']),
			});

			// The failed chunk holds the first ACTOR_LOOKUP_MAX_IDS ids; the rest resolve.
			await waitFor(() => {
				expect(result.current.isError).toBe(true);
				expect(result.current.byId.size).toBe(5);
			});
			expect(result.current.resolve('agnt_0')).toBeUndefined();
			expect(result.current.resolve('agnt_104')).toBe('name-agnt_104');
		});

		it('falls back to raw ids (undefined names) when the lookup fails', async () => {
			worker.use(createErrorHandler('get', '/actors/lookup', { status: 500 }));
			const { result } = renderHook(() => useActorDirectory(['agnt_1']), {
				wrapper: memberWrapper(['agents:read']),
			});

			await waitFor(() => expect(result.current.isError).toBe(true));
			expect(result.current.resolve('agnt_1')).toBeUndefined();
		});
	});

	it('falls back to the by-id lookup when the full listing is refused', async () => {
		worker.use(
			createErrorHandler('get', '/actors', { status: 403 }),
			http.get('/actors/lookup', () =>
				HttpResponse.json({ data: [lookupEntry('agnt_1', 'Inbox Triage')] }),
			),
		);
		const { result } = renderHook(() => useActorDirectory(['agnt_1']), { wrapper });

		await waitFor(() => expect(result.current.resolve('agnt_1')).toBe('Inbox Triage'));
		expect(result.current.isError).toBe(false);
	});

	it('does not call the lookup when the full directory loads', async () => {
		let lookupCalls = 0;
		worker.use(
			http.get('/actors', () =>
				HttpResponse.json({
					data: [actor('agnt_1', 'Inbox Triage')],
					has_more: false,
					next_cursor: null,
				}),
			),
			http.get('/actors/lookup', () => {
				lookupCalls += 1;
				return HttpResponse.json({ data: [] });
			}),
		);
		const { result } = renderHook(() => useActorDirectory(['agnt_1', 'usr_9']), {
			wrapper: memberWrapper(['users:read']),
		});

		await waitFor(() => expect(result.current.resolve('agnt_1')).toBe('Inbox Triage'));
		expect(lookupCalls).toBe(0);
	});
});
