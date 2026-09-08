import { renderWithProviders, screen, checkA11y } from '@/__tests__/test-utils';
import { RecentExecutionsCard, type RecentExecutionItem } from '@/shared/ui/RecentExecutionsCard';

const ITEMS: RecentExecutionItem[] = [
	{
		id: 'exec_1',
		status: 'completed',
		httpStatus: 201,
		label: 'github.create_issue',
		durationMs: 310,
		startedAt: new Date(Date.now() - 4 * 60_000).toISOString(),
	},
	{
		id: 'exec_2',
		status: 'failed',
		httpStatus: 403,
		label: 'github.delete_repo',
		error: 'Denied by permission rule (deny /admin/*).',
		durationMs: 22,
		startedAt: new Date(Date.now() - 18 * 60_000).toISOString(),
	},
	{
		id: 'exec_3',
		status: 'running',
		label: 'slack.post_message',
		durationMs: null,
		startedAt: new Date(Date.now() - 30_000).toISOString(),
	},
];

describe('RecentExecutionsCard', () => {
	it('renders one row per execution with label, HTTP status, error, and duration', () => {
		renderWithProviders(<RecentExecutionsCard items={ITEMS} monitorHref="/monitor" />);

		expect(screen.getAllByTestId('execution-feed-row')).toHaveLength(3);
		expect(screen.getByText('github.create_issue')).toBeInTheDocument();
		expect(screen.getByText('201')).toBeInTheDocument();
		expect(screen.getByText('403')).toBeInTheDocument();
		expect(screen.getByText(/denied by permission rule/i)).toBeInTheDocument();
		expect(screen.getByText('310ms')).toBeInTheDocument();
		// No duration yet → em-dash placeholder.
		expect(screen.getByText('—')).toBeInTheDocument();
		// Lifecycle status is carried as text for AT, not just the dot colour.
		expect(screen.getByText('running')).toBeInTheDocument();
	});

	it('links into Monitor from the header and the has-more footnote', () => {
		renderWithProviders(
			<RecentExecutionsCard items={ITEMS} monitorHref="/monitor?actor_id=a1" hasMore />,
		);
		const header = screen.getByRole('link', { name: /open monitor/i });
		expect(header).toHaveAttribute('href', '/monitor?actor_id=a1');
		expect(screen.getByText(/showing the 3 most recent/i)).toBeInTheDocument();
		expect(
			screen.getByRole('link', { name: /see the full history in monitor/i }),
		).toHaveAttribute('href', '/monitor?actor_id=a1');
	});

	it('renders the optional attribution slot', () => {
		renderWithProviders(
			<RecentExecutionsCard
				items={[{ ...ITEMS[0]!, meta: <span>support-agent</span> }]}
				monitorHref="/monitor"
			/>,
		);
		expect(screen.getByText('support-agent')).toBeInTheDocument();
	});

	it('shows the empty state and loading skeletons', () => {
		const { rerender } = renderWithProviders(
			<RecentExecutionsCard
				items={[]}
				monitorHref="/monitor"
				emptyMessage="No recent executions for this toolkit."
			/>,
		);
		expect(screen.getByText('No recent executions for this toolkit.')).toBeInTheDocument();

		rerender(<RecentExecutionsCard items={[]} monitorHref="/monitor" isLoading />);
		expect(
			screen.queryByText('No recent executions for this toolkit.'),
		).not.toBeInTheDocument();
		expect(screen.queryAllByTestId('execution-feed-row')).toHaveLength(0);
	});

	it('is accessible', async () => {
		const { container } = renderWithProviders(
			<RecentExecutionsCard items={ITEMS} monitorHref="/monitor" hasMore />,
		);
		await checkA11y(container);
	});
});
