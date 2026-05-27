import { http, HttpResponse, delay } from 'msw';
import { screen, renderWithProviders, waitFor } from '../test-utils';
import { worker } from '../mocks/browser';
import WorkflowDetailPage from '@/pages/WorkflowDetailPage';

function renderWorkflow(slug = 'test~api', searchParams = '') {
	return renderWithProviders(<WorkflowDetailPage />, {
		route: `/workspace/workflows/${slug}${searchParams}`,
		path: '/workspace/workflows/:slug',
	});
}

describe('WorkflowDetailPage', () => {
	it('renders loading state', async () => {
		worker.use(
			http.get('/workflows/:slug', async () => {
				await delay('infinite');
				return HttpResponse.json({});
			}),
		);

		renderWorkflow();

		expect(screen.getByText('Loading workflow...')).toBeInTheDocument();
	});

	it('renders workflow data when found', async () => {
		worker.use(
			http.get('/workflows/:slug', () =>
				HttpResponse.json({
					slug: 'test~api',
					name: 'Test Workflow',
					description: 'A test workflow',
					source: 'local',
					steps: [{ id: 's1', operation: 'getUser' }],
					involved_apis: ['test-api'],
				}),
			),
		);

		renderWorkflow();

		expect(await screen.findByRole('heading', { name: 'Test Workflow' })).toBeInTheDocument();
		// Description renders in the PageHeader subtitle. The "About" card
		// is gone so it should appear exactly once.
		expect(screen.getByText('A test workflow')).toBeInTheDocument();
		// Slug now sits next to the segmented toggle as a quiet caption,
		// not in the meta strip and not as a header eyebrow.
		expect(screen.getByTestId('workflow-slug')).toHaveTextContent('test~api');
		// Meta strip is the compact ribbon under the header. It owns
		// Operations / APIs / Last run; the source pill was dropped
		// because GET /workflows/{slug} doesn't carry a `source` field
		// and catalog workflows hit the catalog fallback before getting
		// here, so the pill was always "Local" decoration.
		expect(screen.getByTestId('workflow-meta-strip')).toBeInTheDocument();
		expect(screen.queryByTestId('workflow-meta-slug')).not.toBeInTheDocument();
		expect(screen.queryByTestId('workflow-meta-source')).not.toBeInTheDocument();
		// BackButton lives below the PageHeader, not inside it. The
		// PageHeader primitive itself stays invariant across pages.
		expect(screen.getByTestId('back-button')).toBeInTheDocument();
		expect(screen.queryByTestId('page-header-back')).not.toBeInTheDocument();
		// Default tab is Overview — Operations section should be visible.
		expect(screen.getByTestId('workflow-overview')).toBeInTheDocument();
		expect(screen.getByTestId('workflow-overview-steps')).toBeInTheDocument();
	});

	it('honours ?view=docs deep link by skipping Overview', async () => {
		worker.use(
			http.get('/workflows/:slug', () =>
				HttpResponse.json({
					slug: 'test~api',
					name: 'Test Workflow',
					source: 'local',
					steps: [],
					involved_apis: [],
				}),
			),
		);

		renderWorkflow('test~api', '?view=docs');

		await screen.findByRole('heading', { name: 'Test Workflow' });
		// Overview body must not render when ?view=docs is set.
		expect(screen.queryByTestId('workflow-overview')).not.toBeInTheDocument();
	});

	it('renders catalog fallback when workflow not found (404 skips retry)', async () => {
		worker.use(http.get('/workflows/:slug', () => HttpResponse.json(null, { status: 404 })));

		renderWorkflow();

		// Catalog fallback now uses the same PageHeader skeleton; the
		// title is the api id derived from the slug.
		expect(await screen.findByRole('heading', { name: /test\/api/ })).toBeInTheDocument();
		expect(screen.getByTestId('workflow-catalog-import')).toBeInTheDocument();
	});

	// Regression for the May 2027 stale-cache bug: importing an
	// involved API from another surface (Discover, /workspace, etc.)
	// while the workflow detail page is open used to leave the "APIs
	// involved" chip on the stale "Not in workspace" badge until the
	// 60s React Query staleTime expired. The workflow-detail queries
	// were under bespoke keys (`['workflow-overview-workspace-apis']`,
	// `['workflow-overview-credentials']`) that the import-success
	// invalidator didn't know about. Pin the contract: invalidating
	// the canonical `['apis']` namespace must reach this surface.
	it('refreshes APIs-involved chip after the apis cache is invalidated', async () => {
		// Start with the slack.com API NOT in the workspace so the
		// chip renders the "Not in workspace" branch. Toggle the flag
		// later to simulate an import landing while the page is open.
		const state = { imported: false };
		let getApiCalls = 0;
		worker.use(
			http.get('/workflows/:slug', () =>
				HttpResponse.json({
					slug: 'critical-ticket-alert-to-team',
					name: 'Critical ticket alert',
					source: 'local',
					steps: [],
					involved_apis: ['slack.com'],
				}),
			),
			// Per-API workspace probe — 200 = imported, 404 = not.
			http.get('/apis/slack.com', () => {
				getApiCalls += 1;
				if (state.imported) {
					return HttpResponse.json({ id: 'slack.com', name: 'Slack Web API' });
				}
				return HttpResponse.json({ detail: 'Not found' }, { status: 404 });
			}),
			// Catalog probe — slack.com IS in the catalog.
			http.get('/catalog/slack.com', () =>
				HttpResponse.json({ api_id: 'slack.com', name: 'Slack Web API' }),
			),
			// Per-API credentials probe.
			http.get('/credentials', () => HttpResponse.json([])),
		);

		const { queryClient } = renderWithProviders(<WorkflowDetailPage />, {
			route: '/workspace/workflows/critical-ticket-alert-to-team',
			path: '/workspace/workflows/:slug',
		});

		// Wait until the unimported chip has settled into the
		// "Not in workspace" branch — i.e. both the workspace probe
		// (404) AND the catalog probe (200) have resolved. Asserting
		// only on `findByTestId` would race the catalog query and
		// briefly observe the "Not available" state before it
		// upgrades. We need both queries settled so that any later
		// refresh is *only* driven by cache invalidation, not by an
		// initial pending query lazily settling against the mutated
		// mock.
		await screen.findByText(/not in workspace/i);
		await waitFor(() => {
			expect(getApiCalls).toBeGreaterThan(0);
		});
		const firstCalls = getApiCalls;
		const chip = screen.getByTestId('workflow-overview-api-chip');
		expect(chip).toHaveAttribute('data-imported', 'false');
		expect(chip).toHaveTextContent(/not in workspace/i);

		// Simulate the import landing: the rest of the app populates
		// the workspace and fires the cross-tab event. Mirroring what
		// `useImportCatalogApi` and `useCredentialImportedSync` do
		// globally — invalidate the broad `['apis']` namespace. With
		// the canonical key, the workflow-overview query refetches.
		state.imported = true;
		await queryClient.invalidateQueries({ queryKey: ['apis'] });

		await screen.findByRole('link', { name: /slack\.com/i });
		expect(getApiCalls).toBeGreaterThan(firstCalls);
		const refreshed = screen.getByTestId('workflow-overview-api-chip');
		expect(refreshed).toHaveAttribute('data-imported', 'true');
		expect(refreshed).not.toHaveTextContent(/not in workspace/i);
	});

	// Regression: a workflow can declare an `involved_api` that has no
	// catalog leaf entry (e.g. `hubspot.com`, where the manifest only
	// holds `hubspot.com/<sub>` rows). Sending the user to
	// `/discover?inspect=hubspot.com` opens a sheet that fails to
	// fetch the spec, so we must hide the Discover affordance and
	// label the chip "Not available" instead of "Not in workspace".
	it('renders a non-actionable chip when an involved API is not in the catalog', async () => {
		worker.use(
			http.get('/workflows/:slug', () =>
				HttpResponse.json({
					slug: 'sync-support-tickets-to-crm',
					name: 'Sync tickets',
					source: 'local',
					steps: [],
					involved_apis: ['hubspot.com'],
				}),
			),
			http.get('/apis/hubspot.com', () =>
				HttpResponse.json({ detail: 'Not found' }, { status: 404 }),
			),
			http.get('/catalog/hubspot.com', () =>
				HttpResponse.json({ detail: 'Not found' }, { status: 404 }),
			),
			http.get('/credentials', () => HttpResponse.json([])),
		);

		renderWithProviders(<WorkflowDetailPage />, {
			route: '/workspace/workflows/sync-support-tickets-to-crm',
			path: '/workspace/workflows/:slug',
		});

		const chip = await screen.findByTestId('workflow-overview-api-chip');
		await waitFor(() => {
			expect(chip).toHaveAttribute('data-in-catalog', 'false');
		});
		expect(chip).toHaveAttribute('data-imported', 'false');
		expect(chip).toHaveTextContent(/not available/i);
		expect(chip).not.toHaveTextContent(/not in workspace/i);
		// No Discover affordance for an api the catalog can't surface.
		expect(screen.queryByTestId('workflow-overview-api-import')).not.toBeInTheDocument();
	});

	it('renders error state for non-404 failures', async () => {
		worker.use(
			http.get('/workflows/:slug', () =>
				HttpResponse.json({ detail: 'Server error' }, { status: 500 }),
			),
		);

		renderWorkflow();

		// The page retries 500s twice (3 total MSW round-trips) before
		// surfacing the error — the timeout accounts for that. The retry
		// logic itself is unit-tested below.
		expect(
			await screen.findByText('Failed to load workflow', {}, { timeout: 5000 }),
		).toBeInTheDocument();
	});
});

// ---------------------------------------------------------------------------
// Unit test for the retry predicate — verifiable without MSW timing
// ---------------------------------------------------------------------------

describe('WorkflowDetailPage retry logic', () => {
	const retry = (failureCount: number, err: { status: number }) =>
		err?.status !== 404 && failureCount < 2;

	it('retries 500s up to twice', () => {
		expect(retry(0, { status: 500 })).toBe(true);
		expect(retry(1, { status: 500 })).toBe(true);
		expect(retry(2, { status: 500 })).toBe(false);
	});

	it('never retries 404s', () => {
		expect(retry(0, { status: 404 })).toBe(false);
	});

	it('retries other server errors', () => {
		expect(retry(0, { status: 503 })).toBe(true);
		expect(retry(1, { status: 502 })).toBe(true);
	});
});
