import { renderWithProviders, screen, userEvent, checkA11y } from '@/__tests__/test-utils';
import { TrendLineChart } from '@/shared/ui/charts/TrendLineChart';

const DATA = [
	{ ts: 1_700_000_000, value: 90 },
	{ ts: 1_700_003_600, value: 95 },
	{ ts: 1_700_007_200, value: 92 },
];

const fmtValue = (v: number) => `${Math.round(v)}%`;
const fmtTs = (ts: number) => new Date(ts * 1000).toISOString().slice(11, 16);

function renderChart(data = DATA, yDomain?: [number, number]) {
	return renderWithProviders(
		<TrendLineChart
			data={data}
			formatValue={fmtValue}
			formatTs={fmtTs}
			yDomain={yDomain}
			ariaLabel="Success rate trend"
		/>,
	);
}

describe('TrendLineChart', () => {
	it('exposes the chart as a single labelled image', () => {
		renderChart();
		expect(screen.getByRole('img', { name: 'Success rate trend' })).toBeInTheDocument();
	});

	it('labels the y axis with the pinned domain and the x axis with the window edges', () => {
		renderChart(DATA, [0, 100]);
		expect(screen.getByText('100%')).toBeInTheDocument();
		expect(screen.getByText('0%')).toBeInTheDocument();
		expect(screen.getByText(fmtTs(DATA[0].ts))).toBeInTheDocument();
		expect(screen.getByText(fmtTs(DATA[2].ts))).toBeInTheDocument();
	});

	it('falls back to the data min/max when no domain is pinned', () => {
		renderChart();
		expect(screen.getByText('95%')).toBeInTheDocument();
		expect(screen.getByText('90%')).toBeInTheDocument();
	});

	it('renders a placeholder when there are fewer than 2 points', () => {
		renderChart([{ ts: 1, value: 1 }]);
		expect(screen.getByText('Not enough data yet')).toBeInTheDocument();
		expect(screen.queryByRole('img')).not.toBeInTheDocument();
	});

	it('shows the nearest point value and timestamp while hovering the plot', async () => {
		const user = userEvent.setup();
		const { container } = renderChart(DATA, [0, 100]);
		expect(screen.queryByRole('tooltip')).not.toBeInTheDocument();

		// Point the cursor at the plot centre — nearest point is the middle one (95%).
		const plot = container.querySelector('svg')!.parentElement!;
		const rect = plot.getBoundingClientRect();
		await user.pointer({
			target: plot,
			coords: { clientX: rect.left + rect.width / 2, clientY: rect.top + rect.height / 2 },
		});
		const tooltip = screen.getByRole('tooltip');
		expect(tooltip).toHaveTextContent('95%');
		expect(tooltip).toHaveTextContent(fmtTs(DATA[1].ts));

		await user.unhover(plot);
		expect(screen.queryByRole('tooltip')).not.toBeInTheDocument();
	});

	it('has no critical a11y violations (tooltip shown)', async () => {
		const user = userEvent.setup();
		const { container } = renderChart();
		await user.hover(container.querySelector('svg')!.parentElement!);
		await checkA11y(container);
	});
});
