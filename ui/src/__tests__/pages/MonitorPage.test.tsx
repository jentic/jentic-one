import { http, HttpResponse } from 'msw';
import { screen, renderWithProviders, waitFor } from '../test-utils';
import { worker } from '../mocks/browser';
import MonitorPage from '@/pages/MonitorPage';

function renderMonitor(initialPath = '/monitor') {
	return renderWithProviders(<MonitorPage />, {
		route: initialPath,
		path: '/monitor',
	});
}

describe('MonitorPage', () => {
	it('renders the page header and tabs by default', async () => {
		renderMonitor();

		expect(await screen.findByRole('heading', { name: 'Monitor' })).toBeInTheDocument();
		expect(screen.getByRole('button', { name: 'Overview' })).toBeInTheDocument();
		expect(screen.getByRole('button', { name: 'Execution Log' })).toBeInTheDocument();
	});

	it('renders execution-log filters when ?tab=log', async () => {
		renderMonitor('/monitor?tab=log');

		await waitFor(() => {
			expect(screen.getByLabelText(/all toolkits/i)).toBeInTheDocument();
		});
		expect(screen.getByLabelText(/all apis/i)).toBeInTheDocument();
		expect(screen.getByLabelText(/all agents|no agents/i)).toBeInTheDocument();
	});

	it('shows the active-now pill when usage reports active jobs', async () => {
		worker.use(
			http.get('/traces/usage', () =>
				HttpResponse.json({
					since: 0,
					until: 0,
					bucket_seconds: 60,
					group_by: 'toolkit',
					top_limit: 10,
					stats: {
						total: 5,
						success: 4,
						failed: 1,
						avg_ms: 100,
						p50_ms: 90,
						p95_ms: 250,
						active_now: 3,
					},
					buckets: [],
					top: [],
				}),
			),
		);

		renderMonitor();

		expect(await screen.findByText(/3 active now/i)).toBeInTheDocument();
	});

	it('exposes a Jobs tab and renders its filters when ?tab=jobs', async () => {
		renderMonitor('/monitor?tab=jobs');

		expect(await screen.findByRole('button', { name: 'Jobs' })).toBeInTheDocument();
		// Status segmented toggle includes the job-specific labels.
		await waitFor(() => {
			expect(screen.getByRole('button', { name: 'Pending' })).toBeInTheDocument();
		});
		expect(screen.getByRole('button', { name: 'Workflows' })).toBeInTheDocument();
		expect(screen.getByRole('button', { name: 'Broker calls' })).toBeInTheDocument();
	});

	it('lists jobs returned by /jobs and tags them with their kind', async () => {
		worker.use(
			http.get('/jobs', () =>
				HttpResponse.json({
					data: [
						{
							job_id: 'job_test_1',
							capability: 'slack.chat.post',
							status: 'running',
							kind: 'broker',
							toolkit_id: null,
							agent_id: null,
							trace_id: null,
							upstream_job_url: null,
							http_status: null,
							error: null,
							created_at: 1_700_000_000,
							completed_at: null,
						},
					],
					total: 1,
				}),
			),
		);

		renderMonitor('/monitor?tab=jobs');

		expect(await screen.findByText('slack.chat.post')).toBeInTheDocument();
		// Kind cell renders the lowercased kind label.
		expect(screen.getByText('broker')).toBeInTheDocument();
	});
});
