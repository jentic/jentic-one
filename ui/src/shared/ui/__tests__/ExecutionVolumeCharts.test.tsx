import { renderWithProviders, screen, checkA11y } from '@/__tests__/test-utils';
import { ExecutionVolumeCharts, type UsageChartBucket } from '@/shared/ui/ExecutionVolumeCharts';

const DAY = 86_400;
const BASE_TS = 1_700_000_000;

function bucket(i: number, success: number, failed: number): UsageChartBucket {
	return { ts: BASE_TS + i * DAY, total: success + failed, success, failed };
}

const BUCKETS = [bucket(0, 60, 4), bucket(1, 80, 8), bucket(2, 70, 1)];

describe('ExecutionVolumeCharts', () => {
	it('renders the stacked volume chart and the call trend side by side', () => {
		renderWithProviders(<ExecutionVolumeCharts buckets={BUCKETS} bucketSeconds={DAY} />);

		expect(screen.getByText('Execution volume · 7d')).toBeInTheDocument();
		expect(screen.getByText('Success rate · 7d')).toBeInTheDocument();

		// Volume chart summarises totals from the buckets (223 total, 13 failed).
		expect(
			screen.getByRole('img', {
				name: 'Execution volume over the last 7d: 223 total, 13 failed.',
			}),
		).toBeInTheDocument();
		expect(
			screen.getByRole('img', { name: /success rate per bucket over the last 7d/i }),
		).toBeInTheDocument();

		// Legend for the stacked segments.
		expect(screen.getByText('Succeeded')).toBeInTheDocument();
		expect(screen.getByText('Failed')).toBeInTheDocument();
	});

	it('respects a custom window label', () => {
		renderWithProviders(
			<ExecutionVolumeCharts buckets={BUCKETS} bucketSeconds={DAY} windowLabel="24h" />,
		);
		expect(screen.getByText('Execution volume · 24h')).toBeInTheDocument();
		expect(screen.getByText('Success rate · 24h')).toBeInTheDocument();
	});

	it('collapses to a single empty panel when there are no buckets', () => {
		renderWithProviders(
			<ExecutionVolumeCharts
				buckets={[]}
				bucketSeconds={DAY}
				emptyMessage="No executions yet."
			/>,
		);
		expect(screen.getByText('No executions yet.')).toBeInTheDocument();
		expect(screen.queryByText('Success rate · 7d')).not.toBeInTheDocument();
		expect(screen.queryByRole('img')).not.toBeInTheDocument();
	});

	it('shows a quiet note instead of a trend line with fewer than two buckets', () => {
		renderWithProviders(
			<ExecutionVolumeCharts buckets={[bucket(0, 10, 2)]} bucketSeconds={DAY} />,
		);
		// The stacked chart still renders a single column…
		expect(
			screen.getByRole('img', { name: /execution volume over the last 7d/i }),
		).toBeInTheDocument();
		// …but a one-point trend line would be meaningless.
		expect(screen.getByText('Not enough data for a trend yet.')).toBeInTheDocument();
	});

	it('renders skeletons while loading', () => {
		renderWithProviders(<ExecutionVolumeCharts buckets={[]} bucketSeconds={DAY} isLoading />);
		expect(screen.getByText('Execution volume · 7d')).toBeInTheDocument();
		expect(screen.queryByRole('img')).not.toBeInTheDocument();
	});

	it('is accessible', async () => {
		const { container } = renderWithProviders(
			<ExecutionVolumeCharts buckets={BUCKETS} bucketSeconds={DAY} />,
		);
		await checkA11y(container);
	});
});
