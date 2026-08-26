/**
 * WebhookEndpointList specs — the list surface's data container, focused on the
 * **bounded** per-endpoint `/stats` fan-out and the graceful-degradation
 * contract the overview strip depends on.
 *
 * Health is not served as an aggregate: the list fans out one polling `/stats`
 * query per endpoint. Left unbounded that is one forever-polling request per
 * endpoint, so the fan-out is capped at the first {@link STATS_FANOUT_CAP} ids.
 * These specs pin the two properties that would be dangerous to regress:
 *
 *  - the fan-out never exceeds the cap, no matter how many endpoints exist —
 *    endpoints past the cap simply degrade to the neutral "Health unknown" pill
 *    (the same missing-data path as a not-yet-loaded stat);
 *  - the overview strip rolls up only the *loaded* endpoints and says so with an
 *    "N of M loaded" partial caption, rather than waiting on the slowest query
 *    or fabricating a full total, and flags a sub-100% success rate with an
 *    icon + text cue (not colour alone).
 *
 * The per-query terminal-stop (poll only while a pending/retrying delivery is in
 * flight) mirrors the delivery log's stop condition; it is exercised there and
 * shares the same `refetchInterval` shape, so it is not re-driven with fake
 * timers here.
 */
import { describe, it, expect, beforeEach } from 'vitest';
import { http, HttpResponse } from 'msw';
import { page } from '@vitest/browser/context';
import { render, screen, waitFor, within } from '@/__tests__/test-utils';
import { MemoryRouter } from 'react-router';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { worker } from '@/mocks/browser';
import { STATS_FANOUT_CAP, type WebhookEndpointEntity } from '@/modules/webhooks/api';
import { WebhookEndpointList } from '@/modules/webhooks/components/WebhookEndpointList';

const ID_PREFIX = 'whe_00000000000000000000';

function makeEndpoint(n: number): WebhookEndpointEntity {
	return {
		id: `${ID_PREFIX}${String(n).padStart(4, '0')}`,
		name: `endpoint-${n}`,
		targetUrl: `https://hooks.example.com/${n}`,
		eventTypes: [],
		allowedCidrs: [],
		active: true,
		createdAt: '2026-01-01T00:00:00Z',
		previousSecretExpiresAt: null,
	};
}

/**
 * Register a `/stats` handler that records which endpoint ids it was asked for
 * and answers with a fixed degraded shape (one dead-lettered delivery → a <100%
 * success rate and a "needs attention" count). Returns the recorded id set so a
 * spec can assert the fan-out was bounded.
 */
function trackStats(): { requested: Set<string> } {
	const requested = new Set<string>();
	worker.use(
		http.get('/webhooks/endpoints/:id/stats', ({ params }) => {
			requested.add(params.id as string);
			return HttpResponse.json({
				total: 2,
				counts_by_status: { succeeded: 1, dead: 1 },
				recent_total: 2,
				recent_failed: 1,
				last_status_code: 500,
				last_attempt_at: '2026-01-01T00:00:00Z',
				last_duration_ms: 100,
				next_attempt_at: null,
				avg_duration_ms: 100,
			});
		}),
	);
	return { requested };
}

function renderList(endpoints: WebhookEndpointEntity[]) {
	const queryClient = new QueryClient({
		defaultOptions: {
			queries: { retry: false, gcTime: 0 },
			mutations: { retry: false },
		},
	});
	return render(
		<QueryClientProvider client={queryClient}>
			<MemoryRouter initialEntries={['/webhooks']}>
				<WebhookEndpointList endpoints={endpoints} canWrite onCreate={() => {}} />
			</MemoryRouter>
		</QueryClientProvider>,
	);
}

describe('WebhookEndpointList — bounded /stats fan-out', () => {
	beforeEach(async () => {
		await page.viewport(1280, 900);
	});

	it('caps the /stats fan-out at the first M endpoints, degrading the rest to "unknown"', async () => {
		const total = STATS_FANOUT_CAP + 5;
		const endpoints = Array.from({ length: total }, (_, i) => makeEndpoint(i + 1));
		const { requested } = trackStats();

		renderList(endpoints);

		// Wait until the first endpoint's stats have loaded (its health pill leaves
		// the neutral "unknown" state), proving the fan-out actually ran.
		await waitFor(() =>
			expect(screen.getByLabelText(/Delivery health for endpoint-1:/i)).not.toHaveTextContent(
				/unknown/i,
			),
		);

		// Give any (incorrect) over-cap requests a chance to fire before asserting.
		await waitFor(() => expect(requested.size).toBe(STATS_FANOUT_CAP));
		// Only the first M distinct ids were ever fetched.
		expect(requested.has(endpoints[0].id)).toBe(true);
		expect(requested.has(endpoints[STATS_FANOUT_CAP - 1].id)).toBe(true);
		// An endpoint past the cap is never fetched and stays "Health unknown".
		const overCap = endpoints[STATS_FANOUT_CAP];
		expect(requested.has(overCap.id)).toBe(false);
		expect(
			screen.getByLabelText(new RegExp(`Delivery health for ${overCap.name}:`, 'i')),
		).toHaveTextContent(/unknown/i);
	});

	it('rolls up only loaded endpoints in the strip, flagging the partial state', async () => {
		const total = STATS_FANOUT_CAP + 5;
		const endpoints = Array.from({ length: total }, (_, i) => makeEndpoint(i + 1));
		trackStats();

		renderList(endpoints);

		const strip = await screen.findByRole('region', { name: /Webhooks health overview/i });

		// The endpoint tile is exact (never stats-gated).
		expect(within(strip).getByText(total.toLocaleString())).toBeInTheDocument();

		// The delivery tiles roll up only the M endpoints that could load, and say
		// so with an "N of M loaded" partial caption rather than faking a full total.
		await waitFor(() =>
			expect(
				within(strip).getAllByText(`${STATS_FANOUT_CAP} of ${total} loaded`).length,
			).toBeGreaterThan(0),
		);

		// Each loaded endpoint reports a 50% success rate (1 succeeded / 1 dead), so
		// the rolled-up rate is still <100% even on the partial total.
		await waitFor(() => expect(within(strip).getByText('50%')).toBeInTheDocument());
	});

	it('flags a sub-100% success rate with a non-colour (icon + text) cue', async () => {
		// A small list (under the cap) so every endpoint loads and the strip is NOT
		// partial — isolating the degraded cue from the partial caption.
		const endpoints = [makeEndpoint(1), makeEndpoint(2)];
		trackStats();

		renderList(endpoints);

		const strip = await screen.findByRole('region', { name: /Webhooks health overview/i });
		// 50% across both loaded endpoints → degraded, surfaced as a text cue (not
		// colour alone), mirroring the per-row HealthPill.
		await waitFor(() => expect(within(strip).getByText('50%')).toBeInTheDocument());
		expect(within(strip).getByText('degraded')).toBeInTheDocument();
	});
});
