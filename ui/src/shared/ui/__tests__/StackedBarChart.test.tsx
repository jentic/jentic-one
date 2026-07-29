import { renderWithProviders, screen, userEvent, checkA11y } from '@/__tests__/test-utils';
import { StackedBarChart, type StackedBarDatum } from '@/shared/ui/charts/StackedBarChart';

const BARS: StackedBarDatum[] = [
	{
		key: 'mon',
		label: 'Mon',
		segments: [
			{ key: 'ok', label: 'succeeded', value: 20, colorClassName: 'bg-accent-green' },
			{ key: 'fail', label: 'failed', value: 2, colorClassName: 'bg-destructive' },
		],
	},
	{
		key: 'tue',
		label: 'Tue',
		segments: [
			{ key: 'ok', label: 'succeeded', value: 31, colorClassName: 'bg-accent-green' },
			{ key: 'fail', label: 'failed', value: 0, colorClassName: 'bg-destructive' },
		],
	},
];

describe('StackedBarChart', () => {
	it('exposes the chart as a single labelled image', () => {
		renderWithProviders(<StackedBarChart bars={BARS} ariaLabel="Execution volume" />);
		expect(screen.getByRole('img', { name: 'Execution volume' })).toBeInTheDocument();
	});

	it('shows an exact-count tooltip while a column is hovered', async () => {
		const user = userEvent.setup();
		const { container } = renderWithProviders(
			<StackedBarChart bars={BARS} ariaLabel="Execution volume" />,
		);
		expect(screen.queryByRole('tooltip')).not.toBeInTheDocument();

		await user.hover(container.querySelector('[data-bar="mon"]')!);
		const tooltip = screen.getByRole('tooltip');
		expect(tooltip).toHaveTextContent('Mon');
		expect(tooltip).toHaveTextContent('20 succeeded');
		expect(tooltip).toHaveTextContent('2 failed');

		// Zero segments are skipped in the column but still listed in the tooltip.
		await user.hover(container.querySelector('[data-bar="tue"]')!);
		expect(screen.getByRole('tooltip')).toHaveTextContent('0 failed');

		await user.unhover(container.querySelector('[data-bar="tue"]')!);
		expect(screen.queryByRole('tooltip')).not.toBeInTheDocument();
	});

	it('scales the y axis to the busiest column', () => {
		renderWithProviders(<StackedBarChart bars={BARS} ariaLabel="Execution volume" />);
		expect(screen.getByText('31')).toBeInTheDocument(); // max = 31 + 0
		expect(screen.getByText('0')).toBeInTheDocument();
	});

	it('renders a placeholder when there are no bars', () => {
		renderWithProviders(<StackedBarChart bars={[]} ariaLabel="Execution volume" />);
		expect(screen.getByText('No data in this window')).toBeInTheDocument();
		expect(screen.queryByRole('img')).not.toBeInTheDocument();
	});

	it('has no critical a11y violations (tooltip shown)', async () => {
		const user = userEvent.setup();
		const { container } = renderWithProviders(
			<StackedBarChart bars={BARS} ariaLabel="Execution volume" />,
		);
		await user.hover(container.querySelector('[data-bar="mon"]')!);
		await checkA11y(container);
	});
});
