import { describe, it, expect, beforeEach } from 'vitest';
import { http, HttpResponse } from 'msw';
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
import DashboardPage from '@/modules/dashboard/pages/DashboardPage';
import { dashboardHandlers, buildDashboardUsageFixture } from '@/modules/dashboard/mocks/handlers';

/** An empty `{data:[]}` page for any list endpoint. */
function emptyList(path: string) {
	return http.get(path, () =>
		HttpResponse.json({ data: [], has_more: false, next_cursor: null }),
	);
}

/**
 * Install Dashboard's own fixtures for every composed endpoint. Sibling
 * modules (e.g. ui-agents, monitor) also register `GET /agents`,
 * `GET /monitoring/usage` etc. in the shared root handler table, and MSW
 * resolves first-match-wins — so these component tests MUST drive their own
 * data via a runtime override instead of relying on which module's default
 * handler happens to win. `worker.use(...)` prepends, so this always wins for
 * the duration of a test.
 */
function seedDashboard() {
	worker.use(...dashboardHandlers, buildDashboardUsageFixture());
}

/**
 * Rendered under AuthProvider so the org:admin-gated Gateway-health layer
 * resolves against the mocked `/users/me` admin user (same pattern as
 * MonitorPage tests). The seeded token makes the profile query fire.
 */
function renderDashboard() {
	return renderWithProviders(
		<AuthProvider>
			<DashboardPage />
		</AuthProvider>,
	);
}

describe('DashboardPage', () => {
	beforeEach(() => {
		// Must match the root mock's MOCK_TOKEN so `/users/me` authenticates and
		// the org:admin permission gate resolves.
		setToken('mock-access-token');
	});

	it('surfaces the unified action queue behind the header bell', async () => {
		seedDashboard();
		renderDashboard();
		const user = userEvent.setup();

		// One bell, one count badge — not three body cards.
		const bell = await screen.findByRole('button', { name: /Needs your action \(\d+/ });
		expect(screen.queryByText('Agents awaiting approval')).not.toBeInTheDocument();
		expect(screen.queryByText('Access requests awaiting review')).not.toBeInTheDocument();

		await user.click(bell);
		const inbox = await screen.findByRole('dialog', { name: 'Needs your action' });

		// A pending agent row with its explicit action. `invoice-bot` is unique
		// to Dashboard's fixture (the sibling agents handler seeds different
		// names), so asserting it proves seedDashboard()'s prepend beat the
		// first-match sibling handler.
		expect(await within(inbox).findByText('invoice-bot')).toBeInTheDocument();
		expect(
			within(inbox).getByRole('button', { name: 'Review agent invoice-bot' }),
		).toBeInTheDocument();

		// An actionable alert row with its View action.
		expect(await within(inbox).findByText('Credential failing')).toBeInTheDocument();

		// An access-request row (summarised items) decidable in place.
		expect(await within(inbox).findByText('toolkit · use +2 more')).toBeInTheDocument();
		expect(
			within(inbox).getByRole('button', {
				name: 'Decide access request toolkit · use +2 more',
			}),
		).toBeInTheDocument();

		// Recent activity (executions) — an operation id from the sample.
		expect(await screen.findByText('charges/create')).toBeInTheDocument();
	});

	it('sorts the bell queue by urgency: severe alerts before approvals', async () => {
		seedDashboard();
		renderDashboard();
		const user = userEvent.setup();

		await user.click(await screen.findByRole('button', { name: /Needs your action/ }));
		const inbox = await screen.findByRole('dialog', { name: 'Needs your action' });
		await within(inbox).findByText('invoice-bot');

		const items = within(inbox).getAllByRole('listitem');
		const labels = items.map((li) => li.textContent ?? '');
		const alertIdx = labels.findIndex((t) => t.includes('Credential failing'));
		const agentIdx = labels.findIndex((t) => t.includes('invoice-bot'));
		expect(alertIdx).toBeGreaterThanOrEqual(0);
		expect(agentIdx).toBeGreaterThan(alertIdx);
	});

	it('renders the Gateway-health layer from the real usage aggregate (admin)', async () => {
		seedDashboard();
		renderDashboard();

		// Scope to the section: raw values like "200" also appear elsewhere on
		// the page (e.g. HTTP 200 badges in the recent-activity table).
		const section = await screen.findByRole('region', { name: 'Gateway health' });

		// KPIs come from the stats block — NOT client-side approximations:
		// 200 total, 188/200 = 94%, p95 1240ms → "1.2s", 3 active.
		expect(await within(section).findByText('200')).toBeInTheDocument();
		expect(await within(section).findByText('94%')).toBeInTheDocument();
		expect(await within(section).findByText('1.2s')).toBeInTheDocument();
		expect(within(section).getByText('Active now')).toBeInTheDocument();

		// Both charts render as labelled images.
		expect(
			await screen.findByRole('img', { name: /Execution volume over the last 24 hours/ }),
		).toBeInTheDocument();
		expect(
			await screen.findByRole('img', { name: /Success rate per bucket/ }),
		).toBeInTheDocument();

		// Top-usage table renders the api lens by default (label strips vendor).
		const table = await screen.findByRole('region', { name: 'Top usage' });
		expect(within(table).getByText('stripe-api')).toBeInTheDocument();
	});

	it('re-scopes the top-usage table when the lens flips (tabs)', async () => {
		seedDashboard();
		renderDashboard();
		const user = userEvent.setup();

		expect(
			within(await screen.findByRole('region', { name: 'Top usage' })).getByText(
				'stripe-api',
			),
		).toBeInTheDocument();

		await user.click(screen.getByRole('tab', { name: 'Toolkits' }));

		const table = await screen.findByRole('region', { name: 'Top usage' });
		expect(await within(table).findByText('tk_payments')).toBeInTheDocument();

		await user.click(screen.getByRole('tab', { name: 'Agents' }));
		const agentsTable = await screen.findByRole('region', { name: 'Top usage' });
		expect(await within(agentsTable).findByText('invoice-bot')).toBeInTheDocument();
		// Null agent keys surface as an explicit bucket, not a blank row.
		expect(await within(agentsTable).findByText('Unattributed')).toBeInTheDocument();
	});

	it('hides the Gateway-health layer for non-admin users', async () => {
		seedDashboard();
		// Same shape as the root /users/me mock, minus org:admin.
		worker.use(
			http.get('/users/me', () =>
				HttpResponse.json({
					id: '00000000-0000-0000-0000-000000000002',
					email: 'viewer@local',
					first_name: 'View',
					last_name: 'Only',
					active: true,
					permissions: [],
					must_change_password: false,
					created_at: '2026-01-01T00:00:00Z',
					updated_at: null,
				}),
			),
		);

		renderDashboard();

		// The rest of the page still composes…
		expect(
			await screen.findByRole('button', { name: /Needs your action \(\d+/ }),
		).toBeInTheDocument();
		expect(await screen.findByText('charges/create')).toBeInTheDocument();
		// …but the admin-only layer never mounts (no doomed 403 request either).
		expect(screen.queryByText('Gateway health')).not.toBeInTheDocument();
	});

	it('swaps to the first-run checklist on a fresh install (no agents, no executions)', async () => {
		worker.use(
			emptyList('/agents'),
			emptyList('/access-requests'),
			emptyList('/events'),
			emptyList('/executions'),
			emptyList('/apis'),
			buildDashboardUsageFixture({ empty: true }),
		);

		renderDashboard();

		expect(await screen.findByText('Set up your workspace')).toBeInTheDocument();
		expect(screen.getByText('Discover an API')).toBeInTheDocument();
		expect(screen.getByText('Register an agent')).toBeInTheDocument();

		// The queues and health layers are replaced/quiet, not rendered empty…
		expect(screen.queryByRole('dialog', { name: 'Needs your action' })).not.toBeInTheDocument();
		// …the bell stays mounted but carries no badge…
		expect(
			await screen.findByRole('button', { name: 'Needs your action (all clear)' }),
		).toBeInTheDocument();
		expect(screen.queryByText('Gateway health')).not.toBeInTheDocument();

		// …while the detail layer keeps its own empty state.
		expect(
			await screen.findByText(
				'No executions yet. Activity appears here once agents start calling APIs.',
			),
		).toBeInTheDocument();
	});

	it('keeps the working layout when agents exist but nothing ran yet', async () => {
		seedDashboard();
		worker.use(emptyList('/executions'), buildDashboardUsageFixture({ empty: true }));

		renderDashboard();

		// Agents exist → NOT first-run, even with zero executions: the working
		// layout (health section) renders and the bell carries its badge.
		expect(
			await screen.findByRole('button', { name: /Needs your action \(\d+/ }),
		).toBeInTheDocument();
		expect(screen.queryByText('Set up your workspace')).not.toBeInTheDocument();

		// The empty usage window renders honest placeholders, not fake zero charts.
		expect(await screen.findByText('No data in this window')).toBeInTheDocument();
		expect(await screen.findByText('Not enough data yet')).toBeInTheDocument();
		expect(await screen.findByText('No executions in this window yet.')).toBeInTheDocument();
	});

	it('degrades gracefully when ONE source fails (partial error)', async () => {
		// Only the alerts feed (/events) fails; every other widget must still render.
		seedDashboard();
		worker.use(createErrorHandler('get', '/events', { status: 500 }));

		renderDashboard();
		const user = userEvent.setup();

		// The badge still renders from the healthy sources; open the panel.
		await user.click(await screen.findByRole('button', { name: /Needs your action/ }));
		const inbox = await screen.findByRole('dialog', { name: 'Needs your action' });

		// The /events failure degrades to ONE inline row inside the panel…
		await waitFor(() => {
			expect(within(inbox).getAllByRole('alert')).toHaveLength(1);
		});
		expect(within(inbox).getByText(/Couldn't load alerts/)).toBeInTheDocument();

		// …while the other queue rows in the same panel still render.
		expect(await within(inbox).findByText('invoice-bot')).toBeInTheDocument();
		expect(await within(inbox).findByText('toolkit · use +2 more')).toBeInTheDocument();
		expect(await screen.findByText('charges/create')).toBeInTheDocument();
		expect(await screen.findByText('Gateway health')).toBeInTheDocument();
	});

	it('shows an all-clear panel and no badge when nothing is waiting', async () => {
		seedDashboard();
		worker.use(emptyList('/agents'), emptyList('/access-requests'), emptyList('/events'));

		renderDashboard();
		const user = userEvent.setup();

		// The rest of the page (executions exist) keeps the working layout…
		expect(await screen.findByText('charges/create')).toBeInTheDocument();
		expect(screen.queryByText('Set up your workspace')).not.toBeInTheDocument();

		// …and the bell reads all-clear (no count badge), with the panel
		// confirming it instead of listing an empty queue.
		const bell = await screen.findByRole('button', {
			name: 'Needs your action (all clear)',
		});
		await user.click(bell);
		const inbox = await screen.findByRole('dialog', { name: 'Needs your action' });
		expect(await within(inbox).findByText('All clear.')).toBeInTheDocument();
	});

	it('degrades only the health layer when the usage aggregate fails', async () => {
		seedDashboard();
		worker.use(createErrorHandler('get', '/monitoring/usage', { status: 500 }));

		renderDashboard();

		// The health section shows its own error…
		expect(await screen.findByText('Gateway health')).toBeInTheDocument();
		await waitFor(() => {
			expect(screen.getAllByRole('alert')).toHaveLength(1);
		});
		// …while the queues (bell badge) and detail layers stay healthy.
		expect(
			await screen.findByRole('button', { name: /Needs your action \(\d+/ }),
		).toBeInTheDocument();
		expect(await screen.findByText('charges/create')).toBeInTheDocument();
	});

	it('opens the quick-actions menu from the header', async () => {
		seedDashboard();
		renderDashboard();
		const user = userEvent.setup();

		await user.click(screen.getByRole('button', { name: /Quick actions/ }));
		expect(screen.getByRole('menuitem', { name: 'Discover APIs' })).toBeInTheDocument();
		expect(screen.getByRole('menuitem', { name: 'Add credential' })).toBeInTheDocument();
		expect(screen.getByRole('menuitem', { name: 'Create toolkit' })).toBeInTheDocument();
		expect(screen.getByRole('menuitem', { name: 'Open workspace' })).toBeInTheDocument();
	});

	it('has no critical a11y violations', async () => {
		// Exercise the FULL composition: the open bell panel, charts
		// (role="img"), the tabbed top-usage table, and the populated
		// Recent-activity DataTable.
		seedDashboard();
		const { container } = renderDashboard();
		const user = userEvent.setup();
		await screen.findByText('charges/create');
		await screen.findByRole('region', { name: 'Top usage' });
		await user.click(await screen.findByRole('button', { name: /Needs your action/ }));
		await within(await screen.findByRole('dialog', { name: 'Needs your action' })).findByText(
			'invoice-bot',
		);
		await checkA11y(container);
	});
});
