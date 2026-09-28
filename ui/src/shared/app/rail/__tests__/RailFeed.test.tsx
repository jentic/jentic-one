import { describe, it, expect } from 'vitest';
import { render, screen, userEvent, within } from '@/__tests__/test-utils';
import { RailFeed, type RailFeedFilters } from '@/shared/app/rail/RailFeed';
import {
	formatStreamAgo,
	formatStreamDateTimeParts,
	freezeFeed,
	isAfterFreeze,
	railTitle,
	type StreamEvent,
} from '@/shared/lib/agentStream';

/** Minimal StreamEvent factory for feed-rendering tests. */
function ev(partial: Partial<StreamEvent> & Pick<StreamEvent, 'id' | 'tsMs'>): StreamEvent {
	return {
		type: 'execution.completed',
		kind: 'execution',
		severity: 'info',
		title: 'test event',
		tokens: {},
		links: {},
		requiresAction: false,
		acknowledged: false,
		groupKey: `execution:execution.completed:${partial.id}`,
		...partial,
	};
}

const NO_FILTERS: RailFeedFilters = { failuresOnly: false };

describe('RailFeed — day separators (#705)', () => {
	it('inserts day separators when the feed spans more than one day', () => {
		const today = new Date(2026, 6, 17, 10, 20, 3).getTime();
		const older = new Date(2026, 6, 13, 14, 9, 23).getTime();
		// Newest-first order, as the provider supplies events.
		const events = [
			ev({ id: 'a', tsMs: today, title: 'today event' }),
			ev({ id: 'b', tsMs: older, title: 'older event' }),
		];
		const { container } = render(<RailFeed events={events} filters={NO_FILTERS} />);

		// Two distinct days → two separators. "Today" leads; the older day shows
		// its weekday+date label (not "Today"/"Yesterday"). Separators are
		// role="presentation" (#7 — quiet in the live log, still in the DOM), so
		// query by the presentation role attribute rather than the a11y tree.
		const separators = container.querySelectorAll('[role="presentation"]');
		expect(separators.length).toBeGreaterThanOrEqual(2);
	});

	it('renders no day separators for a single-day feed', () => {
		const morning = new Date(2026, 6, 17, 9, 0, 0).getTime();
		const evening = new Date(2026, 6, 17, 21, 0, 0).getTime();
		const events = [
			ev({ id: 'a', tsMs: evening, title: 'evening event' }),
			ev({ id: 'b', tsMs: morning, title: 'morning event' }),
		];
		const { container } = render(<RailFeed events={events} filters={NO_FILTERS} />);
		expect(container.querySelectorAll('[role="presentation"]')).toHaveLength(0);
	});

	it('does not emit a blank separator for a malformed (NaN) timestamp', () => {
		// A NaN timestamp yields an empty day key. It must NOT become its own
		// label-less separator row; the event still renders normally.
		const today = new Date(2026, 6, 17, 10, 0, 0).getTime();
		const older = new Date(2026, 6, 13, 10, 0, 0).getTime();
		const events = [
			ev({ id: 'good1', tsMs: today, title: 'today event' }),
			ev({ id: 'bad', tsMs: NaN, title: 'malformed event' }),
			ev({ id: 'good2', tsMs: older, title: 'older event' }),
		];
		const { container } = render(<RailFeed events={events} filters={NO_FILTERS} />);

		// The malformed event still renders as an event row…
		expect(screen.getByText('malformed event')).toBeInTheDocument();
		// …and every rendered separator carries non-empty visible label text
		// (no blank ones).
		for (const sep of container.querySelectorAll('[role="presentation"]')) {
			expect(sep.textContent).toBeTruthy();
		}
	});

	it('marks day separators role="presentation" so the role="log" feed does not announce them', () => {
		const today = new Date(2026, 6, 17, 10, 0, 0).getTime();
		const older = new Date(2026, 6, 13, 10, 0, 0).getTime();
		const events = [
			ev({ id: 'a', tsMs: today, title: 'today event' }),
			ev({ id: 'b', tsMs: older, title: 'older event' }),
		];
		const { container } = render(<RailFeed events={events} filters={NO_FILTERS} />);
		// `role="presentation"` strips the row from the accessibility tree — so the
		// role="log" + aria-relevant="additions" container never announces a
		// spliced-in separator as a fake "event" — while the label text stays in
		// the DOM and readable on explicit SR navigation.
		const separators = container.querySelectorAll('[role="presentation"]');
		expect(separators.length).toBeGreaterThanOrEqual(2);
		// Day rows must not carry the `separator` role (they're `presentation`).
		expect(screen.queryByRole('separator')).toBeNull();
		for (const sep of separators) {
			expect(sep).toHaveAttribute('role', 'presentation');
			// No aria-label: it's prohibited ARIA on a presentational node
			// (axe: aria-prohibited-attr). The visible text carries the day.
			expect(sep).not.toHaveAttribute('aria-label');
			expect(sep.textContent).toBeTruthy();
		}
	});

	it('shows the full date and time as an instant tooltip on hover of a timestamp', async () => {
		const ts = new Date(2026, 6, 16, 14, 4, 31).getTime();
		const events = [ev({ id: 'a', tsMs: ts, title: 'an event' })];
		render(<RailFeed events={events} filters={NO_FILTERS} />);
		const user = userEvent.setup();

		const { date, time } = formatStreamDateTimeParts(ts);
		// The bubble only exists once the timestamp trigger is hovered.
		expect(screen.queryByRole('tooltip')).toBeNull();
		const stamp = document.querySelector('time');
		if (!stamp) throw new Error('no timestamp');
		// Relative in the row ("16 Jul" for anything over a week old)…
		expect(stamp).toHaveTextContent(formatStreamAgo(ts));
		await user.hover(stamp);
		const tip = await screen.findByRole('tooltip');
		// …exact in the tooltip, as two rows: date on top, time below.
		expect(tip).toHaveTextContent(date);
		expect(tip).toHaveTextContent(time);
	});
});

describe('RailFeed — folding routine runs', () => {
	const now = Date.now();
	const call = (id: string, minsAgo: number, actorId = 'support-triage') =>
		ev({
			id,
			tsMs: now - minsAgo * 60_000,
			title: `Execution completed: op.${id}`,
			actorId,
			actorType: 'agent',
		});
	const names = (id?: string) => (id === 'support-triage' ? 'Support Triage' : id);

	it('folds a same-actor run of successes into one row that expands', async () => {
		const user = userEvent.setup();
		const events = [call('c', 1), call('b', 3), call('a', 9)];
		render(
			<RailFeed
				events={events}
				filters={NO_FILTERS}
				resolveActor={(e) => names(e.actorId)}
			/>,
		);
		const group = screen.getByRole('button', { name: /Support Triage: 3 calls succeeded/ });
		expect(screen.queryByText('op.a')).not.toBeInTheDocument();
		await user.click(group);
		expect(group).toHaveAttribute('aria-expanded', 'true');
		// Every member shows, without repeating the actor already on the group row.
		for (const op of ['op.a', 'op.b', 'op.c']) expect(screen.getByText(op)).toBeInTheDocument();
		expect(screen.getAllByText('Support Triage')).toHaveLength(1);
	});

	it('stays open when Load older extends the run at its oldest end', async () => {
		const user = userEvent.setup();
		const events = [call('c', 1), call('b', 3)];
		const { rerender } = render(<RailFeed events={events} filters={NO_FILTERS} />);
		await user.click(screen.getByRole('button', { name: /2 calls succeeded/ }));
		rerender(<RailFeed events={[...events, call('a', 9)]} filters={NO_FILTERS} />);
		expect(screen.getByRole('button', { name: /3 calls succeeded/ })).toHaveAttribute(
			'aria-expanded',
			'true',
		);
	});

	it('never folds failures, and a different actor breaks the run', () => {
		const failed = ev({
			id: 'f',
			tsMs: now - 2 * 60_000,
			type: 'execution.failed',
			kind: 'execution',
			severity: 'error',
			title: 'Execution failed: boom',
			actorId: 'support-triage',
			actorType: 'agent',
		});
		const events = [call('c', 1), failed, call('b', 3), call('x', 4, 'invoice-bot')];
		const { container } = render(<RailFeed events={events} filters={NO_FILTERS} />);
		expect(container.querySelectorAll('[data-rail-row]')).toHaveLength(4);
		expect(screen.queryByRole('button', { name: /Expand group/ })).not.toBeInTheDocument();
	});

	it('keeps the folded row mounted when a live arrival joins it', () => {
		const events = [call('b', 3), call('a', 9)];
		const { rerender, container } = render(<RailFeed events={events} filters={NO_FILTERS} />);
		const before = container.querySelector('[data-rail-row]');
		rerender(<RailFeed events={[call('c', 0), ...events]} filters={NO_FILTERS} />);
		expect(container.querySelector('[data-rail-row]')).toBe(before);
		expect(
			within(container).getByRole('button', { name: /3 calls succeeded/ }),
		).toBeInTheDocument();
	});
});

describe("railTitle — the feed's short wording", () => {
	it('drops what the icon already says and keeps the operation, error or API', () => {
		expect(railTitle({ type: 'execution.completed', title: 'Execution completed: a.b' })).toBe(
			'a.b',
		);
		expect(
			railTitle({ type: 'execution.failed', title: 'Execution failed: 401 upstream' }),
		).toBe('Failed: 401 upstream');
		expect(railTitle({ type: 'import.completed', title: 'Import completed: petstore' })).toBe(
			'Imported petstore',
		);
	});

	it("turns the backend's id-only summary into words, and leaves the unknown alone", () => {
		expect(
			railTitle({ type: 'execution.completed', title: 'Execution exec_2Kx9 completed' }),
		).toBe('Call succeeded');
		expect(railTitle({ type: 'agent.registered', title: 'Agent registered: x' })).toBe(
			'Agent registered: x',
		);
	});
});

describe('formatStreamAgo — compact relative time', () => {
	const now = new Date(2026, 6, 20, 12, 0, 0).getTime();
	it('reads now / minutes / hours / days, then a date', () => {
		expect(formatStreamAgo(now - 10_000, now)).toBe('now');
		expect(formatStreamAgo(now - 4 * 60_000, now)).toBe('4m');
		expect(formatStreamAgo(now - 2 * 3_600_000, now)).toBe('2h');
		expect(formatStreamAgo(now - 3 * 86_400_000, now)).toBe('3d');
		expect(formatStreamAgo(now - 30 * 86_400_000, now)).toMatch(/20/);
	});
	it('never throws or prints "NaN" on a malformed timestamp', () => {
		expect(formatStreamAgo(Number.NaN, now)).toBe('—');
	});
});

describe('freezeFeed — what pause and "scrolled away" hold back', () => {
	const at = (id: string, tsMs: number) => ev({ id, tsMs });
	it('holds back only arrivals after the freeze, never older history loaded later', () => {
		const freeze = freezeFeed([at('b', 200), at('a', 100)]);
		expect(isAfterFreeze(at('b', 200), freeze)).toBe(false);
		// A live arrival is newer than everything that was loaded…
		expect(isAfterFreeze(at('c', 300), freeze)).toBe(true);
		// …while "Load older" or a lens's backlog brings in older rows.
		expect(isAfterFreeze(at('z', 50), freeze)).toBe(false);
	});
});
