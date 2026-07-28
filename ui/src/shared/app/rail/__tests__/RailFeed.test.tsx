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
		render(<RailFeed events={events} filters={NO_FILTERS} />);

		// Two distinct days → two separators. "Today" leads; the older day shows
		// its weekday+date label (not "Today"/"Yesterday").
		const separators = screen.getAllByRole('separator');
		expect(separators.length).toBeGreaterThanOrEqual(2);
	});

	it('renders no day separators for a single-day feed', () => {
		const morning = new Date(2026, 6, 17, 9, 0, 0).getTime();
		const evening = new Date(2026, 6, 17, 21, 0, 0).getTime();
		const events = [
			ev({ id: 'a', tsMs: evening, title: 'evening event' }),
			ev({ id: 'b', tsMs: morning, title: 'morning event' }),
		];
		render(<RailFeed events={events} filters={NO_FILTERS} />);
		expect(screen.queryByRole('separator')).toBeNull();
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
