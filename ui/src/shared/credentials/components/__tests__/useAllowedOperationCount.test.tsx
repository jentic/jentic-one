/**
 * useAllowedOperationCount — how many of an API's operations a binding's rules
 * let through, and where the read stands: `undefined` while it is in flight,
 * `null` once there is nothing to judge.
 */
import type { ReactNode } from 'react';
import { describe, it, expect, beforeEach } from 'vitest';
import { http, HttpResponse } from 'msw';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { renderHook, waitFor } from '@/__tests__/test-utils';
import { worker } from '@/mocks/browser';
import { setToken } from '@/shared/api';
import { useAllowedOperationCount } from '@/shared/credentials/components/OperationImpactPreview';

const API = { vendor: 'acme.com', name: 'main', version: '1.0.0' };
const OPS_PATH = '/apis/acme.com/main/1.0.0/operations';

function wrapper({ children }: { children: ReactNode }) {
	const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
	return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

function op(method: string, path: string) {
	return { operation_id: `${method} ${path}`, method, path, summary: null };
}

describe('useAllowedOperationCount', () => {
	beforeEach(() => setToken('test-token'));

	it('counts the operations the rules let through', async () => {
		worker.use(
			http.get(OPS_PATH, () =>
				HttpResponse.json({
					data: [op('GET', '/a'), op('POST', '/a'), op('GET', '/b')],
					has_more: false,
					next_cursor: null,
				}),
			),
		);
		const { result } = renderHook(
			() => useAllowedOperationCount(API, [{ effect: 'allow', methods: ['GET'] }]),
			{ wrapper },
		);
		expect(result.current).toBeUndefined();
		await waitFor(() => expect(result.current).toEqual({ allowed: 2, total: 3 }));
	});

	it('is null once a read finishes with no operation list (the import is not there)', async () => {
		worker.use(
			http.get(OPS_PATH, () => HttpResponse.json({ detail: 'nope' }, { status: 404 })),
		);
		const { result } = renderHook(() => useAllowedOperationCount(API, []), { wrapper });
		expect(result.current).toBeUndefined();
		await waitFor(() => expect(result.current).toBeNull());
	});

	it('is null when the reference names no single version', () => {
		const { result } = renderHook(
			() => useAllowedOperationCount({ ...API, version: null }, []),
			{ wrapper },
		);
		expect(result.current).toBeNull();
	});

	it('stays undefined while the rules are unknown', async () => {
		worker.use(
			http.get(OPS_PATH, () =>
				HttpResponse.json({ data: [op('GET', '/a')], has_more: false, next_cursor: null }),
			),
		);
		const { result } = renderHook(() => useAllowedOperationCount(API, undefined), {
			wrapper,
		});
		await new Promise((r) => setTimeout(r, 100));
		expect(result.current).toBeUndefined();
	});
});
