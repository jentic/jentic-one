import { describe, it, expect, afterEach, vi } from 'vitest';
import { render, screen, act, waitFor, userEvent } from '@/__tests__/test-utils';
import type { AgentEntity } from '@/modules/agents/api';
import {
	PendingApprovalBanner,
	waitingLabel,
} from '@/modules/agents/components/flat/PendingApprovalBanner';

/** Minimal pending row; `minutesAgo` sets how long it has been waiting. */
function pendingRow(id: string, name: string, minutesAgo: number): AgentEntity {
	return {
		id,
		name,
		description: null,
		status: 'pending',
		ownerId: null,
		parentAgentId: null,
		denialReason: null,
		createdAt: new Date(Date.now() - minutesAgo * 60_000).toISOString(),
		approvedAt: null,
		attribution: { registeredBy: 'self', approvedBy: null, deniedBy: null },
		hasApiKey: false,
	};
}

/** Rows in backend order — `created_at DESC`, newest FIRST. */
function descRows(...rows: AgentEntity[]): AgentEntity[] {
	return [...rows].sort((a, b) => b.createdAt.localeCompare(a.createdAt));
}

const noop = () => {};

function renderBanner(
	pending: AgentEntity[],
	over: Partial<React.ComponentProps<typeof PendingApprovalBanner>> = {},
) {
	return render(
		<PendingApprovalBanner
			pending={pending}
			atLeast={false}
			onReview={noop}
			onApprove={noop}
			onDeny={noop}
			approvePendingId={null}
			{...over}
		/>,
	);
}

describe('PendingApprovalBanner', () => {
	afterEach(() => {
		vi.useRealTimers();
	});

	// --- Zero-pending: no reserved space (risk O8) ---------------------------

	it('renders nothing at all when no agent is pending', () => {
		const { container } = renderBanner([]);
		expect(screen.queryByRole('region', { name: 'Awaiting approval' })).not.toBeInTheDocument();
		expect(container).toBeEmptyDOMElement();
	});

	it('ignores non-pending rows served by a stale response', () => {
		const active: AgentEntity = {
			...pendingRow('agnt_a', 'now-active-bot', 5),
			status: 'active',
		};
		const { container } = renderBanner([active]);
		expect(container).toBeEmptyDOMElement();
	});

	// --- Longest-waiting pick (backend order is created_at DESC) -------------

	it('names the longest-waiting agent — the LAST row of the DESC page', () => {
		const rows = descRows(
			pendingRow('agnt_new', 'newest-bot', 2),
			pendingRow('agnt_mid', 'middling-bot', 30),
			pendingRow('agnt_old', 'oldest-bot', 90),
		);
		// Sanity: DESC really does put the oldest LAST.
		expect(rows[rows.length - 1]!.id).toBe('agnt_old');

		renderBanner(rows);
		const banner = screen.getByRole('region', { name: 'Awaiting approval' });
		expect(banner).toHaveTextContent('oldest-bot');
		expect(banner).not.toHaveTextContent('newest-bot');
		expect(banner).not.toHaveTextContent('middling-bot');
		// The rest fold into a count, not extra banners.
		expect(banner).toHaveTextContent('and 2 more waiting');
	});

	it('omits the more-waiting count when exactly one agent is pending', () => {
		renderBanner([pendingRow('agnt_only', 'only-bot', 10)]);
		const banner = screen.getByRole('region', { name: 'Awaiting approval' });
		expect(banner).toHaveTextContent('only-bot');
		expect(banner).not.toHaveTextContent('more waiting');
	});

	// --- Honesty while the pending list is a floor (atLeast) -----------------

	it('hedges the more-waiting count as N+ while the list is a floor', () => {
		const rows = descRows(
			pendingRow('agnt_new', 'newest-bot', 2),
			pendingRow('agnt_mid', 'middling-bot', 30),
			pendingRow('agnt_old', 'oldest-so-far-bot', 90),
		);
		renderBanner(rows, { atLeast: true });
		const banner = screen.getByRole('region', { name: 'Awaiting approval' });
		// The longest-waiting agent LOADED SO FAR stays named and actionable
		// (functionality over polish, D17)…
		expect(banner).toHaveTextContent('oldest-so-far-bot');
		expect(screen.getByRole('button', { name: 'Approve oldest-so-far-bot' })).toBeEnabled();
		// …but the fold-in count never claims an exact tally the still-draining
		// (or failed) list can't prove — it hedges like the nav badge's "N+".
		expect(banner).toHaveTextContent('and 2+ more waiting');
	});

	it('says "and more waiting" when the floor holds a single loaded row', () => {
		// One row loaded, drain incomplete: has_more proved more exist, but no
		// number is defensible yet — so the copy hedges without one.
		renderBanner([pendingRow('agnt_only', 'first-loaded-bot', 10)], { atLeast: true });
		const banner = screen.getByRole('region', { name: 'Awaiting approval' });
		expect(banner).toHaveTextContent('first-loaded-bot');
		expect(banner).toHaveTextContent('and more waiting');
		expect(banner).not.toHaveTextContent(/and \d/);
	});

	it('advances to the next longest-waiting when the named agent leaves the pool', async () => {
		const oldest = pendingRow('agnt_old', 'oldest-bot', 90);
		const next = pendingRow('agnt_mid', 'middling-bot', 30);
		const { rerender } = renderBanner(descRows(oldest, next));
		expect(screen.getByRole('region', { name: 'Awaiting approval' })).toHaveTextContent(
			'oldest-bot',
		);

		rerender(
			<PendingApprovalBanner
				pending={[next]}
				atLeast={false}
				onReview={noop}
				onApprove={noop}
				onDeny={noop}
				approvePendingId={null}
			/>,
		);
		const banner = screen.getByRole('region', { name: 'Awaiting approval' });
		expect(banner).toHaveTextContent('middling-bot');
		expect(banner).not.toHaveTextContent('oldest-bot');
		expect(banner).not.toHaveTextContent('more waiting');

		rerender(
			<PendingApprovalBanner
				pending={[]}
				atLeast={false}
				onReview={noop}
				onApprove={noop}
				onDeny={noop}
				approvePendingId={null}
			/>,
		);
		// The exit animation finishes and the banner leaves the DOM entirely.
		await waitFor(() => {
			expect(
				screen.queryByRole('region', { name: 'Awaiting approval' }),
			).not.toBeInTheDocument();
		});
	});

	// --- D16 live elapsed wait -----------------------------------------------

	it('formats the wait honestly across magnitudes', () => {
		vi.useFakeTimers();
		vi.setSystemTime(new Date('2026-09-17T12:00:00Z'));
		const at = (iso: string) => waitingLabel(iso);
		// A ~30s display tick cannot honestly animate seconds.
		expect(at('2026-09-17T11:59:40Z')).toBe('waiting under a minute');
		expect(at('2026-09-17T11:56:00Z')).toBe('waiting 4m');
		expect(at('2026-09-17T09:00:00Z')).toBe('waiting 3h');
		expect(at('2026-09-15T12:00:00Z')).toBe('waiting 2d');
		expect(at('not-a-date')).toBe('waiting');
	});

	it('ticks the elapsed wait while mounted, recomputing from created_at', () => {
		vi.useFakeTimers();
		const row = {
			...pendingRow('agnt_w', 'waiting-bot', 0),
			createdAt: new Date(Date.now() - 45_000).toISOString(), // 45s ago
		};
		const { unmount } = renderBanner([row]);
		const banner = () => screen.getByRole('region', { name: 'Awaiting approval' });
		expect(banner()).toHaveTextContent('waiting under a minute');

		// One tick later the wait has crossed into minutes.
		act(() => vi.advanceTimersByTime(31_000));
		expect(banner()).toHaveTextContent('waiting 1m');

		// Recomputed from created_at each tick — a long gap (as after a tab
		// sleep) snaps to the truth instead of accumulating drift.
		act(() => vi.advanceTimersByTime(10 * 60_000));
		expect(banner()).toHaveTextContent('waiting 11m');

		// Unmount clears the interval — no timer leak.
		unmount();
		expect(vi.getTimerCount()).toBe(0);
	});

	// --- Actions with per-id scoping -----------------------------------------

	it('fires Review / Approve / Deny for the named agent', async () => {
		const user = userEvent.setup();
		const onReview = vi.fn();
		const onApprove = vi.fn();
		const onDeny = vi.fn();
		renderBanner(
			descRows(
				pendingRow('agnt_new', 'newest-bot', 2),
				pendingRow('agnt_old', 'oldest-bot', 60),
			),
			{ onReview, onApprove, onDeny },
		);

		await user.click(screen.getByRole('button', { name: 'Review oldest-bot' }));
		expect(onReview).toHaveBeenCalledWith('agnt_old');

		await user.click(screen.getByRole('button', { name: 'Approve oldest-bot' }));
		expect(onApprove).toHaveBeenCalledWith('agnt_old');

		await user.click(screen.getByRole('button', { name: 'Deny oldest-bot' }));
		expect(onDeny).toHaveBeenCalledWith({ id: 'agnt_old', name: 'oldest-bot' });
	});

	it('scopes the in-flight state to the named agent id', () => {
		const rows = descRows(
			pendingRow('agnt_new', 'newest-bot', 2),
			pendingRow('agnt_old', 'oldest-bot', 60),
		);
		// Another agent's approve in flight leaves this banner idle…
		const { unmount } = renderBanner(rows, { approvePendingId: 'agnt_new' });
		expect(screen.getByRole('button', { name: 'Approve oldest-bot' })).toBeEnabled();
		unmount();

		// …while the named agent's own approve locks all three actions.
		renderBanner(rows, { approvePendingId: 'agnt_old' });
		expect(screen.getByRole('button', { name: 'Approve oldest-bot' })).toHaveAttribute(
			'aria-busy',
			'true',
		);
		expect(screen.getByRole('button', { name: 'Review oldest-bot' })).toBeDisabled();
		expect(screen.getByRole('button', { name: 'Deny oldest-bot' })).toBeDisabled();
	});
});
