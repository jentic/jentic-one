import { describe, it, expect, beforeEach } from 'vitest';
import { http, HttpResponse } from 'msw';
import { useLocation } from 'react-router-dom';
import {
	renderWithProviders,
	screen,
	waitFor,
	within,
	userEvent,
	checkA11y,
	createErrorHandler,
} from '@/__tests__/test-utils';
import { worker } from '@/mocks/browser';
import { setToken } from '@/shared/api';
import { AuthProvider } from '@/shared/auth';
import { Toaster } from '@/shared/ui';
import MonitorPage from '@/modules/monitor/pages/MonitorPage';
import { monitorHandlers } from '@/modules/monitor/mocks/handlers';

/** Mirrors the router location search string into the DOM for assertions. */
function LocationProbe() {
	const location = useLocation();
	return <div data-testid="location-search">{location.search}</div>;
}

/**
 * MonitorPage is rendered under AuthProvider so the org:admin-gated actions
 * (cancel job, audit lens) resolve against the mocked `/users/me` admin user.
 * A seeded token makes the profile query fire.
 *
 * Defaults to the Executions lens (`?tab=executions`) because most of these
 * specs exercise the trace log; the Overview tab is now the implicit landing
 * lens (asserted separately) and tests that need it click into it.
 */
function renderMonitor(route = '/app/monitor?tab=executions') {
	return renderWithProviders(
		<AuthProvider>
			<MonitorPage />
			<LocationProbe />
			<Toaster />
		</AuthProvider>,
		{ route },
	);
}

describe('MonitorPage', () => {
	beforeEach(() => {
		setToken('mock-access-token');
		// `/executions`, `/events`(+stream), and `/audit` are also mocked by the
		// dashboard + Agent Rail handlers, which register earlier in the global
		// table. Install Monitor's handlers at runtime so they take precedence
		// for this page's requests; MSW resets runtime handlers after each test.
		worker.use(...monitorHandlers);
	});

	it('defaults an unparametered visit to the Overview lens (#628)', async () => {
		renderMonitor('/app/monitor');
		// With no `?tab=`, Overview is the landing lens: its health/volume
		// content renders without any tab click. The global filter bar (list-tab
		// only) is hidden on Overview.
		expect(await screen.findByText('Execution Volume')).toBeInTheDocument();
		expect(screen.getByRole('tab', { name: 'Overview' })).toHaveAttribute(
			'aria-selected',
			'true',
		);
	});

	it('renders the Executions tab with trace rows', async () => {
		renderMonitor();
		expect(await screen.findByText('POST /v1/charges')).toBeInTheDocument();
		expect(screen.getByText('GET /repos/{owner}/{repo}')).toBeInTheDocument();
		// status pills off the union (status text also appears in the filter
		// toggle, so assert at least one occurrence).
		expect(screen.getAllByText('Completed').length).toBeGreaterThanOrEqual(1);
		expect(screen.getAllByText('Failed').length).toBeGreaterThanOrEqual(1);
	});

	it('filters Executions by terminal status (backend accepts only completed/failed)', async () => {
		const user = userEvent.setup();
		renderMonitor();
		await screen.findByText('GET /repos/{owner}/{repo}');

		// Failed-only: the github 503 row stays, the completed charge row drops.
		await user.click(screen.getByRole('button', { name: 'Failed' }));
		await waitFor(() => {
			expect(screen.queryByText('POST /v1/charges')).not.toBeInTheDocument();
		});
		expect(screen.getByText('GET /repos/{owner}/{repo}')).toBeInTheDocument();

		// Completed-only: the inverse.
		await user.click(screen.getByRole('button', { name: 'Completed' }));
		await waitFor(() => {
			expect(screen.queryByText('GET /repos/{owner}/{repo}')).not.toBeInTheDocument();
		});
		expect(screen.getByText('POST /v1/charges')).toBeInTheDocument();
	});

	it('goes live on the Events tab without crashing on heartbeat frames', async () => {
		const user = userEvent.setup();
		renderMonitor();
		await screen.findByText('POST /v1/charges');

		await user.click(screen.getByRole('tab', { name: 'Events' }));
		await screen.findByText('Execution failed: github-api');

		// The SSE mock interleaves a heartbeat frame (no severity) with real
		// events; clicking Go live must not throw `charAt of undefined`.
		await user.click(screen.getByRole('button', { name: 'Go live' }));

		// Stream connects and real events still render (heartbeat is dropped).
		expect(await screen.findByText('Stop live')).toBeInTheDocument();
		expect(screen.getByText('Execution failed: github-api')).toBeInTheDocument();
	});

	it('renders the Overview health strip + volume chart + breakdown from the stats endpoint (#386)', async () => {
		const user = userEvent.setup();
		renderMonitor();
		await screen.findByText('POST /v1/charges');

		await user.click(screen.getByRole('tab', { name: 'Overview' }));

		// HealthStrip pill + the Execution Volume chart render from the
		// aggregation endpoint — no more "coming soon" gate. The fixture's
		// ~91% success rate maps to the "Degraded" health pill.
		expect(await screen.findByText('Execution Volume')).toBeInTheDocument();
		expect(screen.getByText('Degraded')).toBeInTheDocument();

		// Breakdown panel lists the busiest operations.
		expect(screen.getByText('Breakdown')).toBeInTheDocument();
		expect(screen.getByText('POST /v1/refunds')).toBeInTheDocument();
	});

	it('reloads Overview stats when the window selector changes', async () => {
		const user = userEvent.setup();
		renderMonitor();
		await screen.findByText('POST /v1/charges');
		await user.click(screen.getByRole('tab', { name: 'Overview' }));
		await screen.findByText('Execution Volume');

		// Switching the window re-queries (days=30) and keeps the chart mounted.
		await user.click(screen.getByRole('button', { name: '30d' }));
		expect(await screen.findByText('Execution Volume')).toBeInTheDocument();
	});

	it('switches to the Jobs tab', async () => {
		const user = userEvent.setup();
		renderMonitor();
		await screen.findByText('POST /v1/charges');

		await user.click(screen.getByRole('tab', { name: 'Jobs' }));

		// Two jobs share the import kind; one is an execution job.
		expect((await screen.findAllByText('import')).length).toBeGreaterThanOrEqual(1);
		expect(screen.getByText('execution')).toBeInTheDocument();
	});

	it('opens a job and surfaces the org:admin Cancel action for an active job', async () => {
		const user = userEvent.setup();
		renderMonitor();
		await screen.findByText('POST /v1/charges');
		await user.click(screen.getByRole('tab', { name: 'Jobs' }));

		// Wait for admin profile to resolve so the gate opens.
		const runningRow = await screen.findByText('job_import_1');
		await user.click(runningRow);

		const dialog = await screen.findByRole('dialog');
		expect(
			await within(dialog).findByRole('button', { name: 'Cancel job' }),
		).toBeInTheDocument();
	});

	it('switches to the Events tab and acknowledges an action event', async () => {
		const user = userEvent.setup();
		renderMonitor();
		await screen.findByText('POST /v1/charges');

		await user.click(screen.getByRole('tab', { name: 'Events' }));

		expect(await screen.findByText('Execution failed: github-api')).toBeInTheDocument();
		const ackButton = screen.getByRole('button', { name: 'Acknowledge' });
		await user.click(ackButton);

		expect(await screen.findByText('Event acknowledged')).toBeInTheDocument();
	});

	it('switches to the Audit tab (actor lens) and shows actors', async () => {
		const user = userEvent.setup();
		renderMonitor();
		await screen.findByText('POST /v1/charges');

		await user.click(screen.getByRole('tab', { name: 'Audit' }));

		expect(await screen.findByText('execution.start')).toBeInTheDocument();
		expect(screen.getByText('job.cancel')).toBeInTheDocument();
		// Actor ids resolve to friendly names via the actor directory
		// (user_admin → "Admin User") rather than rendering the raw id/type.
		expect(screen.getAllByText('Admin User').length).toBeGreaterThanOrEqual(1);
	});

	it('filters the Audit tab by trace_id deep-link param', async () => {
		renderMonitor('/app/monitor?tab=audit&trace_id=trace_aaaaaaaa');
		// Only the execution.start entry carries trace_aaaaaaaa.
		expect(await screen.findByText('execution.start')).toBeInTheDocument();
		await waitFor(() => {
			expect(screen.queryByText('job.cancel')).not.toBeInTheDocument();
		});
	});

	it('shows the trace actor read from the execution record (#375)', async () => {
		const user = userEvent.setup();
		renderMonitor();
		// exec_1/exec_3 (trace_aaaaaaaa) carry actor_type "agent" on the record.
		await user.click(await screen.findByText('POST /v1/charges'));

		const sheet = await screen.findByRole('dialog');
		// Actor renders off ExecutionResponse.actor_id resolved through the actor
		// directory (agent_billing → "Billing Agent"), with a subtle type prefix —
		// not the raw id or bare actor_type.
		expect(await within(sheet).findByText(/Billing Agent/)).toBeInTheDocument();
	});

	it('surfaces an error when the executions feed fails', async () => {
		worker.use(createErrorHandler('get', '/executions', { status: 500 }));
		renderMonitor();
		expect(await screen.findByRole('alert')).toBeInTheDocument();
	});

	it('renders the global filter bar with a window toggle and actor picker on list tabs', async () => {
		renderMonitor();
		await screen.findByText('POST /v1/charges');

		// Window toggle (global bar) + actor Select hydrated from /actors.
		expect(screen.getByRole('button', { name: '7d' })).toBeInTheDocument();
		const actorSelect = screen.getByRole('combobox', { name: 'Filter by actor' });
		expect(actorSelect).toBeEnabled();
		expect(await within(actorSelect).findByText(/Billing Agent/)).toBeInTheDocument();
	});

	it('hides the global filter bar on the Overview tab', async () => {
		const user = userEvent.setup();
		renderMonitor();
		await screen.findByText('POST /v1/charges');
		await user.click(screen.getByRole('tab', { name: 'Overview' }));
		await screen.findByText('Execution Volume');
		expect(screen.queryByRole('combobox', { name: 'Filter by actor' })).not.toBeInTheDocument();
	});

	it('disables the actor picker on the Jobs tab (no backend actor filter)', async () => {
		const user = userEvent.setup();
		renderMonitor();
		await screen.findByText('POST /v1/charges');
		await user.click(screen.getByRole('tab', { name: 'Jobs' }));
		await screen.findByText('job_import_1');
		expect(screen.getByRole('combobox', { name: 'Filter by actor' })).toBeDisabled();
	});

	it('filters executions by the selected actor', async () => {
		const user = userEvent.setup();
		renderMonitor();
		await screen.findByText('GET /repos/{owner}/{repo}');

		// user_admin only owns exec_2 (the github 503) — selecting it drops the
		// agent_billing charge row.
		await user.selectOptions(
			screen.getByRole('combobox', { name: 'Filter by actor' }),
			screen.getByRole('option', { name: /Admin User/ }),
		);
		await waitFor(() => {
			expect(screen.queryByText('POST /v1/charges')).not.toBeInTheDocument();
		});
		expect(screen.getByText('GET /repos/{owner}/{repo}')).toBeInTheDocument();
	});

	it('pages executions with the cursor pager (Older / Newer)', async () => {
		const user = userEvent.setup();
		renderMonitor();
		// Page 1 holds exec_1 + exec_2 (limit 2); exec_3 is on page 2.
		await screen.findByText('POST /v1/charges');
		expect(screen.queryByText('POST /v1/refunds')).not.toBeInTheDocument();

		await user.click(screen.getByRole('button', { name: 'Older results' }));
		expect(await screen.findByText('POST /v1/refunds')).toBeInTheDocument();
		expect(screen.queryByText('POST /v1/charges')).not.toBeInTheDocument();

		await user.click(screen.getByRole('button', { name: 'Newer results' }));
		expect(await screen.findByText('POST /v1/charges')).toBeInTheDocument();
	});

	it('preserves global filter params but clears per-tab status when switching tabs', async () => {
		const user = userEvent.setup();
		renderMonitor('/app/monitor?tab=executions&status=failed&days=30&actor_id=user_admin');
		await screen.findByText('GET /repos/{owner}/{repo}');

		await user.click(screen.getByRole('tab', { name: 'Events' }));
		await waitFor(() => {
			expect(screen.getByRole('tab', { name: 'Events' })).toHaveAttribute(
				'aria-selected',
				'true',
			);
		});

		// Global params survive the tab switch; per-tab status is purged.
		const params = new URLSearchParams(screen.getByTestId('location-search').textContent ?? '');
		expect(params.get('days')).toBe('30');
		expect(params.get('actor_id')).toBe('user_admin');
		expect(params.get('status')).toBeNull();
	});

	it('has no critical a11y violations', async () => {
		const { container } = renderMonitor();
		await screen.findByText('POST /v1/charges');
		await checkA11y(container);
	});
});

/**
 * Cross-tab deep-linking. Every Monitor surface can pivot to another via the
 * URL param vocabulary in lib/links.ts; these cover each direction and the
 * "unknown trace" degradation (the backend stores `trace_id="unknown"` for
 * header-less runs, which must never produce a broken trace/audit link).
 */
describe('Monitor inter-linking', () => {
	beforeEach(() => {
		setToken('mock-access-token');
		worker.use(...monitorHandlers);
	});

	function currentParams() {
		return new URLSearchParams(screen.getByTestId('location-search').textContent ?? '');
	}

	it('Executions row → trace sheet → "View in audit" carries trace_id', async () => {
		const user = userEvent.setup();
		renderMonitor();
		await user.click(await screen.findByText('POST /v1/charges'));

		// The sheet opens grouped by the row's real trace.
		await screen.findByRole('link', { name: /View trace .* in the audit log/ });
		expect(currentParams().get('trace_id')).toBe('trace_aaaaaaaa');

		await user.click(screen.getByRole('link', { name: /View trace .* in the audit log/ }));
		await waitFor(() => {
			const params = currentParams();
			expect(params.get('tab')).toBe('audit');
			expect(params.get('trace_id')).toBe('trace_aaaaaaaa');
		});
	});

	it('Executions row with unknown trace opens by execution_id, no audit link', async () => {
		// Deep-link straight to the unknown-trace execution's sheet.
		renderMonitor('/app/monitor?tab=executions&execution_id=exec_4');

		// Header reads "Execution" (not "Trace") and shows the execution id.
		expect(await screen.findByRole('heading', { name: 'exec_4' })).toBeInTheDocument();
		expect(screen.getByText('Execution')).toBeInTheDocument();
		// No trace-scoped audit link is offered for an unusable trace.
		expect(
			screen.queryByRole('link', { name: /View trace .* in the audit log/ }),
		).not.toBeInTheDocument();
		// And the URL never leaks the placeholder trace id.
		expect(currentParams().get('trace_id')).toBeNull();
	});

	it('Jobs row → job sheet → "View in audit" sends target_type + target_id (no 400)', async () => {
		const user = userEvent.setup();
		renderMonitor('/app/monitor?tab=jobs');
		await user.click(await screen.findByText('job_import_1'));

		const auditLink = await screen.findByRole('link', {
			name: /View job .* in the audit log/,
		});
		await user.click(auditLink);
		await waitFor(() => {
			const params = currentParams();
			expect(params.get('tab')).toBe('audit');
			expect(params.get('target_type')).toBe('job');
			expect(params.get('target_id')).toBe('job_import_1');
		});
		// The audit lens resolves the row (200, not the 400 a lone target_id gets).
		expect(await screen.findByText('target:')).toBeInTheDocument();
		expect(screen.getByText('job_import_1')).toBeInTheDocument();
	});

	it('Job sheet → linked execution deep-links by execution_id', async () => {
		const user = userEvent.setup();
		renderMonitor('/app/monitor?tab=jobs');
		// job_import_2 carries execution_id=exec_1.
		await user.click(await screen.findByText('job_import_2'));
		await user.click(await screen.findByRole('link', { name: /Open execution exec_1/ }));
		await waitFor(() => {
			const params = currentParams();
			expect(params.get('tab')).toBe('executions');
			expect(params.get('execution_id')).toBe('exec_1');
		});
	});

	it('Audit row → Executions (trace) and → Jobs (job) links round-trip', async () => {
		const user = userEvent.setup();
		renderMonitor('/app/monitor?tab=audit');

		// audit_1 has a real trace → Trace link into Executions.
		await user.click(await screen.findByRole('link', { name: /Open trace .* in Executions/ }));
		await waitFor(() => {
			const params = currentParams();
			expect(params.get('tab')).toBe('executions');
			expect(params.get('trace_id')).toBe('trace_aaaaaaaa');
		});

		// Back to audit; audit_2 has a job → Job link into Jobs.
		await user.click(screen.getByRole('tab', { name: 'Audit' }));
		await user.click(await screen.findByRole('link', { name: /Open job .* in Jobs/ }));
		await waitFor(() => {
			const params = currentParams();
			expect(params.get('tab')).toBe('jobs');
			expect(params.get('job_id')).toBe('job_import_1');
		});
	});

	it('switching tabs clears every per-tab deep-link param', async () => {
		const user = userEvent.setup();
		renderMonitor('/app/monitor?tab=audit&target_type=job&target_id=job_import_1&days=7');
		await screen.findByText('target:');

		await user.click(screen.getByRole('tab', { name: 'Executions' }));
		await waitFor(() => {
			const params = currentParams();
			// Per-tab params purged…
			expect(params.get('target_type')).toBeNull();
			expect(params.get('target_id')).toBeNull();
			// …global window preserved.
			expect(params.get('days')).toBe('7');
		});
	});

	it('Live event stream forwards the time-window as `since`', async () => {
		// Capture the SSE subscription URL so we can assert the window lower-bound
		// reaches the stream (regression: `from` was dropped on the live path).
		let streamUrl: URL | null = null;
		worker.use(
			http.get('/events/stream', ({ request }) => {
				streamUrl = new URL(request.url);
				// Empty but well-formed SSE body; the client just needs it to open.
				return new HttpResponse(': keep-alive\n\n', {
					headers: { 'Content-Type': 'text/event-stream', 'Cache-Control': 'no-cache' },
				});
			}),
		);

		// Open the Events tab live with an explicit 24h window.
		renderMonitor('/app/monitor?tab=events&live=1&days=1');

		await waitFor(() => expect(streamUrl).not.toBeNull());
		// The 24h window is sent as the SSE `since` lower-bound.
		expect(streamUrl!.searchParams.get('since')).toBeTruthy();
	});
});
