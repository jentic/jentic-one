import { describe, it, expect, beforeEach } from 'vitest';
import { http, HttpResponse } from 'msw';
import {
	renderWithProviders,
	screen,
	waitFor,
	checkA11y,
	createErrorHandler,
} from '@/__tests__/test-utils';
import { worker } from '@/mocks/browser';
import { setToken } from '@/shared/api';
import DashboardPage from '@/modules/dashboard/pages/DashboardPage';
import { dashboardHandlers } from '@/modules/dashboard/mocks/handlers';

/** An empty `{data:[]}` page for any list endpoint. */
function emptyList(path: string) {
	return http.get(path, () =>
		HttpResponse.json({ data: [], has_more: false, next_cursor: null }),
	);
}

/**
 * Install Dashboard's own fixtures for the four composed endpoints. Sibling
 * modules (e.g. ui-agents) also register `GET /agents` etc. in the shared root
 * handler table, and MSW resolves first-match-wins — so these component tests
 * MUST drive their own data via a runtime override instead of relying on which
 * module's default handler happens to win. `worker.use(...)` prepends, so this
 * always wins for the duration of a test.
 */
function seedDashboard() {
	worker.use(...dashboardHandlers);
}

describe('DashboardPage', () => {
	beforeEach(() => {
		// The api client attaches a Bearer token; seed one so requests look
		// authenticated (MSW handlers don't gate on it, but this mirrors runtime).
		setToken('test-token');
	});

	it('renders a card composed from each of the four sources (populated)', async () => {
		seedDashboard();
		renderWithProviders(<DashboardPage />);

		// Pending agents card + a representative row. `invoice-bot` is unique to
		// Dashboard's fixture (the sibling agents handler seeds different names),
		// so asserting it proves seedDashboard()'s prepend beat the first-match
		// sibling handler — not just that *some* /agents handler answered.
		expect(await screen.findByText('Agents awaiting approval')).toBeInTheDocument();
		expect(await screen.findByText('invoice-bot')).toBeInTheDocument();

		// Alerts card + a representative actionable event.
		expect(await screen.findByText('Credential failing')).toBeInTheDocument();

		// Pending access-requests card + a representative row (summarised items).
		expect(await screen.findByText('Access requests awaiting review')).toBeInTheDocument();
		expect(await screen.findByText('toolkit · use +2 more')).toBeInTheDocument();

		// Recent activity (executions) — an operation id from the sample.
		expect(await screen.findByText('charges/create')).toBeInTheDocument();

		// Success rate tile: 2 of 3 sampled succeeded → 67%.
		expect(await screen.findByText('Success rate')).toBeInTheDocument();
		expect(await screen.findByText('67%')).toBeInTheDocument();

		// APIs registered tile.
		expect(await screen.findByText('APIs registered')).toBeInTheDocument();
	});

	it('renders empty states across the overview on a fresh install', async () => {
		worker.use(
			emptyList('/agents'),
			emptyList('/access-requests'),
			emptyList('/events'),
			emptyList('/executions'),
			emptyList('/apis'),
		);

		renderWithProviders(<DashboardPage />);

		expect(await screen.findByText('No agents waiting')).toBeInTheDocument();
		expect(await screen.findByText('No requests waiting')).toBeInTheDocument();
		expect(await screen.findByText('Nothing needs attention')).toBeInTheDocument();
		// No-activity success rate renders as "—".
		await waitFor(() => {
			expect(screen.getByText('no recent activity')).toBeInTheDocument();
		});
	});

	it('degrades gracefully when ONE source fails (partial error)', async () => {
		// Only the alerts feed (/events) fails; every other widget must still render.
		seedDashboard();
		worker.use(createErrorHandler('get', '/events', { status: 500 }));

		renderWithProviders(<DashboardPage />);

		// The /events failure degrades BOTH consumers of the alerts query: the
		// "Needs attention" overview tile and the AlertsCard, so exactly two
		// inline alerts render (and no more — the other three sources succeed).
		await waitFor(() => {
			expect(screen.getAllByRole('alert')).toHaveLength(2);
		});

		// ...while the other three sources still render their data.
		expect(await screen.findByText('invoice-bot')).toBeInTheDocument();
		expect(await screen.findByText('charges/create')).toBeInTheDocument();
	});

	it('has no critical a11y violations', async () => {
		// Exercise the FULL composition incl. the populated Recent-activity
		// DataTable: its scroll wrapper is keyboard-focusable + labelled (a
		// region), so axe's scrollable-region-focusable no longer fires even
		// when the table overflows. (See shared/ui/DataTable.tsx.)
		seedDashboard();
		const { container } = renderWithProviders(<DashboardPage />);
		await screen.findByText('invoice-bot');
		await screen.findByText('charges/create');
		await checkA11y(container);
	});
});
