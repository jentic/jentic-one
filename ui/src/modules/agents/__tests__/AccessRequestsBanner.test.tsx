import { describe, it, expect, afterEach, vi } from 'vitest';
import { renderWithProviders, screen, act, waitFor, userEvent } from '@/__tests__/test-utils';
import type { AccessRequest } from '@/shared/lib';
import {
	AccessRequestsBanner,
	accessRequestWaitingLabel,
} from '@/modules/agents/components/flat/AccessRequestsBanner';

/** Minimal pending request; `minutesAgo` sets how long it has been waiting. */
function pendingRequest(id: string, actorId: string, minutesAgo: number): AccessRequest {
	return {
		id,
		actor_id: actorId,
		status: 'pending',
		reason: null,
		requested_by: 'usr_admin',
		created_by: 'usr_admin',
		filer_owner_id: null,
		approve_url: `https://app.example.test/access-requests/${id}`,
		filed_at: new Date(Date.now() - minutesAgo * 60_000).toISOString(),
		expires_at: new Date(Date.now() + 86_400_000).toISOString(),
		evaluation: { can_fulfill: true, checks: [] },
		items: [{ id: `${id}_item`, resource_type: 'toolkit', action: 'use', status: 'pending' }],
	};
}

/** Requests in backend order — `created_at DESC`, newest FIRST. */
function descRequests(...rows: AccessRequest[]): AccessRequest[] {
	return [...rows].sort((a, b) => b.filed_at.localeCompare(a.filed_at));
}

const noop = () => {};

function renderBanner(
	requests: AccessRequest[],
	over: Partial<React.ComponentProps<typeof AccessRequestsBanner>> = {},
) {
	return renderWithProviders(
		<AccessRequestsBanner requests={requests} onReview={noop} {...over} />,
	);
}

const REGION = 'Access requests awaiting a decision';

describe('AccessRequestsBanner', () => {
	afterEach(() => {
		vi.useRealTimers();
	});

	// --- Zero-pending: no reserved space -------------------------------------

	it('renders nothing at all when no request is pending', () => {
		const { container } = renderBanner([]);
		expect(screen.queryByRole('region', { name: REGION })).not.toBeInTheDocument();
		expect(container).toBeEmptyDOMElement();
	});

	it('ignores non-pending rows served by a stale response', () => {
		const decided: AccessRequest = {
			...pendingRequest('ar_a', 'now-decided-bot', 5),
			status: 'approved',
		};
		const { container } = renderBanner([decided]);
		expect(container).toBeEmptyDOMElement();
	});

	// --- Longest-waiting pick (backend order is created_at DESC) -------------

	it('names the longest-waiting request — the LAST row of the DESC page', () => {
		const rows = descRequests(
			pendingRequest('ar_new', 'newest-bot', 2),
			pendingRequest('ar_mid', 'middling-bot', 30),
			pendingRequest('ar_old', 'oldest-bot', 90),
		);
		// Sanity: DESC really does put the oldest LAST.
		expect(rows[rows.length - 1]!.id).toBe('ar_old');

		renderBanner(rows);
		const banner = screen.getByRole('region', { name: REGION });
		expect(banner).toHaveTextContent('oldest-bot');
		expect(banner).not.toHaveTextContent('newest-bot');
		expect(banner).not.toHaveTextContent('middling-bot');
		// The item summary is the shared queue-row copy.
		expect(banner).toHaveTextContent('toolkit · use');
		// The rest fold into a count, not extra banners.
		expect(banner).toHaveTextContent('and 2 more waiting');
	});

	it('omits the more-waiting count when exactly one request is pending', () => {
		renderBanner([pendingRequest('ar_only', 'only-bot', 10)]);
		const banner = screen.getByRole('region', { name: REGION });
		expect(banner).toHaveTextContent('only-bot');
		expect(banner).not.toHaveTextContent('more waiting');
	});

	it('advances to the next longest-waiting when the named request leaves the queue', async () => {
		const oldest = pendingRequest('ar_old', 'oldest-bot', 90);
		const next = pendingRequest('ar_mid', 'middling-bot', 30);
		const { rerender } = renderBanner(descRequests(oldest, next));
		expect(screen.getByRole('region', { name: REGION })).toHaveTextContent('oldest-bot');

		rerender(<AccessRequestsBanner requests={[next]} onReview={noop} />);
		const banner = screen.getByRole('region', { name: REGION });
		expect(banner).toHaveTextContent('middling-bot');
		expect(banner).not.toHaveTextContent('oldest-bot');
		expect(banner).not.toHaveTextContent('more waiting');

		rerender(<AccessRequestsBanner requests={[]} onReview={noop} />);
		// The exit animation finishes and the banner leaves the DOM entirely.
		await waitFor(() => {
			expect(screen.queryByRole('region', { name: REGION })).not.toBeInTheDocument();
		});
	});

	// --- Live elapsed wait ---------------------------------------------------

	it('formats the wait honestly across magnitudes', () => {
		vi.useFakeTimers();
		vi.setSystemTime(new Date('2026-09-17T12:00:00Z'));
		const at = (iso: string) => accessRequestWaitingLabel(iso);
		// A ~30s display tick cannot honestly animate seconds.
		expect(at('2026-09-17T11:59:40Z')).toBe('waiting under a minute');
		expect(at('2026-09-17T11:56:00Z')).toBe('waiting 4m');
		expect(at('2026-09-17T09:00:00Z')).toBe('waiting 3h');
		expect(at('2026-09-15T12:00:00Z')).toBe('waiting 2d');
		expect(at('not-a-date')).toBe('waiting');
	});

	it('ticks the elapsed wait while mounted, recomputing from filed_at', () => {
		vi.useFakeTimers();
		const row = {
			...pendingRequest('ar_w', 'waiting-bot', 0),
			filed_at: new Date(Date.now() - 45_000).toISOString(), // 45s ago
		};
		// Capture the banner's own interval id so the unmount assertion below is
		// exact — the QueryClient ActorLabel needs also schedules timers.
		const setSpy = vi.spyOn(window, 'setInterval');
		const { unmount } = renderBanner([row]);
		const intervalId = setSpy.mock.results[0]!.value as number;
		const banner = () => screen.getByRole('region', { name: REGION });
		expect(banner()).toHaveTextContent('waiting under a minute');

		// One tick later the wait has crossed into minutes.
		act(() => vi.advanceTimersByTime(31_000));
		expect(banner()).toHaveTextContent('waiting 1m');

		// Recomputed from filed_at each tick — a long gap (as after a tab
		// sleep) snaps to the truth instead of accumulating drift.
		act(() => vi.advanceTimersByTime(10 * 60_000));
		expect(banner()).toHaveTextContent('waiting 11m');

		// Unmount clears the banner's interval — no timer leak. Assert the clear
		// directly rather than via getTimerCount: the QueryClient that ActorLabel
		// needs schedules its own timers, so the global count is not the banner's.
		const clearSpy = vi.spyOn(window, 'clearInterval');
		unmount();
		expect(clearSpy).toHaveBeenCalledWith(intervalId);
	});

	// --- Review routes the named request to the shared dialog ----------------

	it('fires Review for the longest-waiting request', async () => {
		const user = userEvent.setup();
		const onReview = vi.fn();
		const rows = descRequests(
			pendingRequest('ar_new', 'newest-bot', 2),
			pendingRequest('ar_old', 'oldest-bot', 60),
		);
		renderBanner(rows, { onReview });

		await user.click(
			screen.getByRole('button', { name: 'Review access request from oldest-bot' }),
		);
		expect(onReview).toHaveBeenCalledTimes(1);
		expect(onReview.mock.calls[0]![0]!.id).toBe('ar_old');
	});
});
