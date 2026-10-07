import { describe, it, expect, beforeEach } from 'vitest';
import { http, HttpResponse } from 'msw';
import { renderWithProviders, screen, waitFor } from '@/__tests__/test-utils';
import { worker } from '@/mocks/browser';
import { setToken } from '@/shared/api';
import { usePendingOverlayCounts } from '@/shared/hooks';

/**
 * Pending-overlay counts are a fan-out (one read per API), so the hook must say
 * how much of the answer it has: a page-capped count is a floor (`atLeast`),
 * APIs past the fan-out cap are `truncated`, a failed read never `complete`s
 * but does `settle`.
 */

type Ref = { vendor: string; name: string; version: string };
const ref = (vendor: string): Ref => ({ vendor, name: 'api', version: '1' });

function Probe({ refs }: { refs: Ref[] }) {
	const counts = usePendingOverlayCounts(refs);
	return (
		<pre data-testid="counts">
			{JSON.stringify({
				byApi: Object.fromEntries(counts.byApi),
				complete: counts.complete,
				settled: counts.settled,
				truncated: counts.truncated,
			})}
		</pre>
	);
}

function read() {
	return JSON.parse(screen.getByTestId('counts').textContent ?? '{}') as {
		byApi: Record<string, { count: number; atLeast: boolean }>;
		complete: boolean;
		settled: boolean;
		truncated: boolean;
	};
}

let reads = 0;

describe('usePendingOverlayCounts', () => {
	beforeEach(() => {
		setToken('test-token');
		reads = 0;
		worker.use(
			http.get('/apis/:vendor/:name/:version/overlays', ({ params, request }) => {
				reads += 1;
				expect(new URL(request.url).searchParams.get('status')).toBe('pending');
				const vendor = String(params.vendor);
				if (vendor === 'broken') return HttpResponse.json({ detail: 'x' }, { status: 403 });
				const n = vendor === 'busy' ? 50 : vendor === 'one' ? 1 : 0;
				return HttpResponse.json({
					data: Array.from({ length: n }, (_, i) => ({
						id: `ov_${i}`,
						status: 'pending',
					})),
					has_more: vendor === 'busy',
					next_cursor: vendor === 'busy' ? 'next' : null,
				});
			}),
		);
	});

	it('counts the first page and flags a page-capped count as a floor', async () => {
		renderWithProviders(<Probe refs={[ref('one'), ref('busy'), ref('none')]} />);
		await waitFor(() => expect(read().complete).toBe(true));
		const { byApi, truncated } = read();
		expect(byApi['one/api/1']).toEqual({ count: 1, atLeast: false });
		expect(byApi['busy/api/1']).toEqual({ count: 50, atLeast: true });
		expect(byApi['none/api/1']).toEqual({ count: 0, atLeast: false });
		expect(truncated).toBe(false);
	});

	it('settles, but never completes, when a read fails', async () => {
		renderWithProviders(<Probe refs={[ref('one'), ref('broken')]} />);
		await waitFor(() => expect(read().settled).toBe(true));
		expect(read().complete).toBe(false);
		expect(read().byApi['broken/api/1']).toBeUndefined();
	});

	it('checks at most 50 APIs and reports the rest as truncated', async () => {
		const refs = Array.from({ length: 51 }, (_, i) => ref(`v${i}`));
		renderWithProviders(<Probe refs={refs} />);
		await waitFor(() => expect(read().complete).toBe(true), { timeout: 5000 });
		expect(read().truncated).toBe(true);
		expect(reads).toBe(50);
	});
});
