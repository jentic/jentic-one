import { describe, it, expect } from 'vitest';
import { renderWithProviders, screen, checkA11y } from '@/__tests__/test-utils';
import { ApiUsageSummary, StreamEventRow } from '@/shared/ui';
import type { StreamEvent } from '@/shared/lib/agentStream';

describe('ApiUsageSummary', () => {
	it('row: calls, failures and the all-versions scope', () => {
		renderWithProviders(
			<ApiUsageSummary
				size="row"
				calls={1204}
				failed={3}
				trend={[1, 2, 3]}
				testId="usage"
				failuresTestId="failed"
			/>,
		);
		expect(screen.getByTestId('usage')).toHaveTextContent('1,204 calls');
		expect(screen.getByTestId('failed')).toHaveTextContent('3 failed');
		expect(screen.getByTestId('usage')).toHaveAttribute(
			'title',
			expect.stringMatching(/all versions/),
		);
	});

	it('large: says "no calls in the last 7 days" for a true zero, "no failures" otherwise', () => {
		const { unmount } = renderWithProviders(<ApiUsageSummary size="large" calls={0} />);
		expect(screen.getByText(/no calls in the last 7 days/)).toBeInTheDocument();
		unmount();
		renderWithProviders(<ApiUsageSummary size="large" calls={9} />);
		expect(screen.getByText(/no failures/)).toBeInTheDocument();
	});

	it('compact has no a11y violations', async () => {
		const { container } = renderWithProviders(
			<ApiUsageSummary size="compact" calls={47} trend={[1, 4]} />,
		);
		expect(screen.getByText('47 calls')).toBeInTheDocument();
		await checkA11y(container);
	});
});

function makeEvent(partial: Partial<StreamEvent>): StreamEvent {
	return {
		id: 'ev_1',
		tsMs: Date.now(),
		type: 'catalog.update_available',
		kind: 'catalog',
		severity: 'warning',
		title: 'Update available for stripe.com',
		tokens: { vendor: 'stripe', name: 'stripe-api', version: '1' },
		links: {},
		requiresAction: true,
		resolved: false,
		groupKey: 'catalog:catalog.update_available:',
		...partial,
	};
}

describe('StreamEventRow', () => {
	it('links an event with a destination (the API hub for a catalog event)', () => {
		renderWithProviders(
			<ul>
				<StreamEventRow ev={makeEvent({})} />
			</ul>,
		);
		expect(
			screen.getByRole('link', { name: /Update available for stripe.com/ }),
		).toHaveAttribute('href', '/library/workspace/stripe/stripe-api/1');
	});

	it('renders plain text for an event without one', async () => {
		const { container } = renderWithProviders(
			<ul>
				<StreamEventRow ev={makeEvent({ kind: 'import', tokens: {}, title: 'Imported' })} />
			</ul>,
		);
		expect(screen.getByText('Imported')).toBeInTheDocument();
		expect(screen.queryByRole('link')).not.toBeInTheDocument();
		await checkA11y(container);
	});
});
