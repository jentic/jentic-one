import { describe, it, expect } from 'vitest';
import { render, screen, userEvent } from '@/__tests__/test-utils';
import { RailFeed, type RailFeedFilters } from '@/shared/app/rail/RailFeed';
import { formatStreamDateTimeParts, type StreamEvent } from '@/shared/lib/agentStream';

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

const NO_FILTERS: RailFeedFilters = {
	search: '',
	severities: new Set(),
	kinds: new Set(),
};

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
		// The rows are no longer exposed with the `separator` role.
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
		await user.hover(screen.getByText('14:04:31'));
		const tip = await screen.findByRole('tooltip');
		// Rendered as two rows: date on top, time below.
		expect(tip).toHaveTextContent(date);
		expect(tip).toHaveTextContent(time);
	});
});
