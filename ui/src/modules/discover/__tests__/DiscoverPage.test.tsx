import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { http, HttpResponse } from 'msw';
import {
	renderWithProviders,
	screen,
	waitFor,
	within,
	userEvent,
	fireEvent,
	checkA11y,
	createErrorHandler,
} from '@/__tests__/test-utils';
import { worker } from '@/mocks/browser';
import { setToken, sharedQueryKeys } from '@/shared/api';
import { Toaster } from '@/shared/ui';
import { setImportPollIntervalForTests } from '@/modules/discover/api';
import DiscoverPage from '@/modules/discover/pages/DiscoverPage';

describe('DiscoverPage', () => {
	let restorePollInterval: (() => void) | null = null;

	beforeEach(() => {
		// The API client attaches a Bearer token; seed one so requests look
		// authenticated (MSW handlers don't gate on it, but this mirrors runtime).
		setToken('test-token');
	});

	afterEach(() => {
		restorePollInterval?.();
		restorePollInterval = null;
	});

	it('renders the public catalog with imported/available badges', async () => {
		renderWithProviders(<DiscoverPage />);

		expect(await screen.findByText('stripe.com')).toBeInTheDocument();
		expect(await screen.findByText('github.com')).toBeInTheDocument();
		expect(await screen.findByText('slack.com')).toBeInTheDocument();

		// stripe.com is registered (Imported); the rest are Available.
		expect(screen.getByTestId('card-status-imported')).toBeInTheDocument();
		expect(screen.getAllByTestId('card-status-available').length).toBeGreaterThanOrEqual(2);
	});

	it('offers "Open Workspace" on imported cards and not on available ones', async () => {
		renderWithProviders(<DiscoverPage />);
		await screen.findByText('stripe.com');

		// The single registered card (stripe.com) links to the Workspace list.
		const link = screen.getByTestId('discovery-card-open-workspace');
		expect(link).toHaveAttribute('href', '/workspace');

		// Available cards expose Import, never the workspace link — there's exactly
		// one imported entry in the default catalog, so exactly one such link.
		expect(screen.getAllByTestId('discovery-card-open-workspace')).toHaveLength(1);
	});

	it('shows the whole-manifest status row', async () => {
		renderWithProviders(<DiscoverPage />);
		const status = await screen.findByTestId('discover-status');
		expect(within(status).getByText(/APIs in the catalog/)).toBeInTheDocument();
		expect(within(status).getByText(/imported/)).toBeInTheDocument();
	});

	it('disambiguates umbrella sub-APIs by title (nytimes.com)', async () => {
		const user = userEvent.setup();
		renderWithProviders(<DiscoverPage />);
		await screen.findByText('stripe.com');

		// Three nytimes.com sub-APIs share one vendor; searching "nyt" must show
		// rows tellable apart by title, with the shared vendor as a subtitle.
		await user.type(screen.getByLabelText('Search APIs'), 'nyt');

		expect(await screen.findByText('Article Search')).toBeInTheDocument();
		expect(screen.getByText('Top Stories')).toBeInTheDocument();
		expect(screen.getByText('Books')).toBeInTheDocument();
		// The vendor still appears, but only as the shared subtitle line.
		expect(screen.getAllByText('nytimes.com').length).toBe(3);
	});

	it('has no critical a11y violations', async () => {
		const { container } = renderWithProviders(<DiscoverPage />);
		await screen.findByText('stripe.com');
		await checkA11y(container);
	});

	it('filters by search query', async () => {
		const user = userEvent.setup();
		renderWithProviders(<DiscoverPage />);
		await screen.findByText('stripe.com');

		await user.type(screen.getByLabelText('Search APIs'), 'github');

		await waitFor(() => {
			expect(screen.queryByText('stripe.com')).not.toBeInTheDocument();
		});
		expect(await screen.findByText('github.com')).toBeInTheDocument();
	});

	it('filters by registration state (Available hides imported rows)', async () => {
		const user = userEvent.setup();
		renderWithProviders(<DiscoverPage />);
		await screen.findByText('stripe.com');

		await user.click(screen.getByRole('button', { name: 'Available' }));

		await waitFor(() => {
			expect(screen.queryByText('stripe.com')).not.toBeInTheDocument();
		});
		expect(screen.getByText('github.com')).toBeInTheDocument();
	});

	it('opens the detail sheet and previews operations', async () => {
		const user = userEvent.setup();
		renderWithProviders(<DiscoverPage />);
		await screen.findByText('github.com');

		await user.click(screen.getByRole('button', { name: 'View github.com' }));

		const dialog = await screen.findByRole('dialog');
		expect(await within(dialog).findByText('Get a repository')).toBeInTheDocument();
		expect(within(dialog).getByText('Create an issue')).toBeInTheDocument();
	});

	it('drills into an operation and back', async () => {
		const user = userEvent.setup();
		renderWithProviders(<DiscoverPage />);
		await screen.findByText('github.com');
		await user.click(screen.getByRole('button', { name: 'View github.com' }));

		const dialog = await screen.findByRole('dialog');
		// Click the operation row to open its detail.
		await user.click(await within(dialog).findByText('Get a repository'));

		const detail = await within(dialog).findByTestId('operation-detail');
		// Parameters + Authentication tables render with the op's data.
		expect(within(detail).getByText('Parameters')).toBeInTheDocument();
		expect(within(detail).getByText('owner')).toBeInTheDocument();
		expect(within(detail).getByText('Authentication')).toBeInTheDocument();
		expect(within(detail).getByText('bearer')).toBeInTheDocument();

		// Back returns to the full operations list.
		await user.click(within(dialog).getByTestId('operation-back'));
		expect(await within(dialog).findByText('Create an issue')).toBeInTheDocument();
		expect(within(dialog).queryByTestId('operation-detail')).not.toBeInTheDocument();
	});

	it('filters operations by search and tag', async () => {
		const user = userEvent.setup();
		renderWithProviders(<DiscoverPage />);
		await screen.findByText('github.com');
		await user.click(screen.getByRole('button', { name: 'View github.com' }));

		const dialog = await screen.findByRole('dialog');
		await within(dialog).findByText('Get a repository');

		// Search trims the visible rows.
		await user.type(within(dialog).getByTestId('ops-filter-input'), 'issue');
		await waitFor(() => {
			expect(within(dialog).queryByText('Get a repository')).not.toBeInTheDocument();
		});
		expect(await within(dialog).findByText('Create an issue')).toBeInTheDocument();

		// Clear, then filter by the `users` tag chip.
		await user.clear(within(dialog).getByTestId('ops-filter-input'));
		await user.click(await within(dialog).findByRole('button', { name: 'users' }));
		await waitFor(() => {
			expect(within(dialog).queryByText('Create an issue')).not.toBeInTheDocument();
		});
		expect(await within(dialog).findByText('Get the authenticated user')).toBeInTheDocument();
	});

	it('pages operations 25 at a time behind a Load more button', async () => {
		// Override the operations endpoint with a 60-op spec so pagination kicks in.
		const makeOps = (count: number) =>
			Array.from({ length: count }, (_, i) => ({
				method: 'get',
				path: `/things/${i}`,
				summary: `Operation number ${i}`,
				description: '',
				operation_id: `op-${i}`,
				parameters: [],
				security: ['bearer'],
				tags: ['things'],
			}));
		const ALL = makeOps(60);
		worker.use(
			http.get('/catalog/:apiId/operations', ({ request }) => {
				const url = new URL(request.url);
				const offset = Number(url.searchParams.get('offset') ?? 0);
				const limit = Number(url.searchParams.get('limit') ?? 200);
				const q = (url.searchParams.get('q') ?? '').trim().toLowerCase();
				let ops = ALL;
				if (q) ops = ops.filter((o) => o.operation_id.toLowerCase().includes(q));
				const window = ops.slice(offset, offset + limit);
				return HttpResponse.json({
					data: window,
					total: ops.length,
					offset,
					truncated: offset + window.length < ops.length,
					info: { title: 'big.com', version: null, description: null },
					security_schemes: {},
				});
			}),
		);

		const user = userEvent.setup();
		renderWithProviders(<DiscoverPage />);
		await screen.findByText('github.com');
		await user.click(screen.getByRole('button', { name: 'View github.com' }));
		const dialog = await screen.findByRole('dialog');

		// First page: 25 rows loaded, footer says "Showing 25 of 60".
		await within(dialog).findByText('Showing 25 of 60');
		expect(within(dialog).getAllByTestId('operations-row')).toHaveLength(25);

		// Load more pages in the next 25 (50 total), then the last 10 (60 total).
		await user.click(within(dialog).getByTestId('ops-load-more'));
		await within(dialog).findByText('Showing 50 of 60');
		await user.click(within(dialog).getByTestId('ops-load-more'));
		await within(dialog).findByText('Showing 60 of 60');
		// No more pages → the Load more button is gone.
		expect(within(dialog).queryByTestId('ops-load-more')).not.toBeInTheDocument();
	});

	it('searches across the whole spec server-side (not just the loaded page)', async () => {
		const ALL = Array.from({ length: 60 }, (_, i) => ({
			method: 'get',
			path: `/things/${i}`,
			summary: `Operation number ${i}`,
			description: '',
			operation_id: `op-${i}`,
			parameters: [],
			security: ['bearer'],
			tags: ['things'],
		}));
		worker.use(
			http.get('/catalog/:apiId/operations', ({ request }) => {
				const url = new URL(request.url);
				const offset = Number(url.searchParams.get('offset') ?? 0);
				const limit = Number(url.searchParams.get('limit') ?? 200);
				const q = (url.searchParams.get('q') ?? '').trim().toLowerCase();
				let ops = ALL;
				if (q) ops = ops.filter((o) => o.operation_id.toLowerCase().includes(q));
				const window = ops.slice(offset, offset + limit);
				return HttpResponse.json({
					data: window,
					total: ops.length,
					offset,
					truncated: offset + window.length < ops.length,
					info: { title: 'big.com', version: null, description: null },
					security_schemes: {},
				});
			}),
		);

		const user = userEvent.setup();
		renderWithProviders(<DiscoverPage />);
		await screen.findByText('github.com');
		await user.click(screen.getByRole('button', { name: 'View github.com' }));
		const dialog = await screen.findByRole('dialog');
		await within(dialog).findByText('Showing 25 of 60');

		// "op-57" is beyond the first loaded page; a server-side search still finds it.
		// Use fireEvent to set the full value atomically — user.type() races the
		// 250ms debounce in browser-mode CI because each keystroke goes through real
		// Playwright keyboard events with non-trivial latency.
		const searchInput = within(dialog).getByTestId('ops-filter-input');
		fireEvent.change(searchInput, { target: { value: 'op-57' } });
		await within(dialog).findByText('Showing 1 of 1', {}, { timeout: 3000 });
		expect(within(dialog).getByText('/things/57')).toBeInTheDocument();
	});

	it('renders the API description as markdown with show more/less', async () => {
		const user = userEvent.setup();
		renderWithProviders(<DiscoverPage />);
		await screen.findByText('github.com');
		await user.click(screen.getByRole('button', { name: 'View github.com' }));

		const dialog = await screen.findByRole('dialog');
		const summary = await within(dialog).findByTestId('api-summary');
		// Markdown emphasis renders as a <strong>, not literal asterisks.
		expect(within(summary).getByText('GitHub REST API').tagName).toBe('STRONG');

		// The long description is truncated with a toggle; expanding reveals the tail.
		const toggle = within(summary).getByTestId('api-summary-toggle');
		expect(toggle).toHaveTextContent('Show more');
		await user.click(toggle);
		expect(toggle).toHaveTextContent('Show less');
		expect(within(summary).getByText(/Show more toggle to expand/)).toBeInTheDocument();
	});

	it('enqueues an import from an available card', async () => {
		const user = userEvent.setup();
		let importHit = false;
		worker.use(
			http.post('/catalog/*', ({ request }) => {
				importHit = true;
				const url = new URL(request.url);
				const apiId = decodeURIComponent(url.pathname.replace(/^\/catalog\//, '')).replace(
					/:import$/,
					'',
				);
				return HttpResponse.json(
					{ job_id: 'job_x', status: 'queued', _links: { self: `/jobs/${apiId}` } },
					{ status: 202 },
				);
			}),
		);

		renderWithProviders(
			<>
				<DiscoverPage />
				<Toaster />
			</>,
		);
		await screen.findByText('github.com');

		const githubCard = screen.getByRole('button', { name: 'View github.com' }).closest('div');
		const importBtn = within(githubCard as HTMLElement).getByTestId('discovery-card-import');
		await user.click(importBtn);

		await waitFor(() => expect(importHit).toBe(true));
		expect(await screen.findByText('Import started')).toBeInTheDocument();
	});

	it('flips a card to Imported when the polled catalog reports it registered', async () => {
		const user = userEvent.setup();
		let imported = false;

		// Poll fast & deterministically instead of racing the real 3s tick against
		// the assertion budget (the prior source of browser-mode flakiness).
		restorePollInterval = setImportPollIntervalForTests(100);

		// Stateful catalog: github.com starts Available, flips to registered once
		// the import has been enqueued (simulating the async job landing). The
		// page polls /catalog while an import is pending, so the card should
		// resolve on its own without a manual refresh.
		worker.use(
			http.get('/catalog', () => {
				const data = [
					{
						api_id: 'github.com',
						vendor: 'github',
						path: 'apis/github.com/openapi.json',
						spec_url: 'https://example.com/github.json',
						registered: imported,
						_links: {
							self: '/catalog/github.com',
							operations: '/catalog/github.com/operations',
							import: '/catalog/github.com:import',
							github: null,
						},
					},
				];
				return HttpResponse.json({
					data,
					catalog_total: 1,
					registered_count: imported ? 1 : 0,
					manifest_age_seconds: 5,
					has_more: false,
					next_cursor: null,
				});
			}),
			http.post('/catalog/*', () => {
				imported = true;
				return HttpResponse.json(
					{
						job_id: 'job_github',
						status: 'queued',
						_links: { self: '/jobs/job_github' },
					},
					{ status: 202 },
				);
			}),
		);

		renderWithProviders(
			<>
				<DiscoverPage />
				<Toaster />
			</>,
		);
		await screen.findByText('github.com');

		const githubCard = screen.getByRole('button', { name: 'View github.com' }).closest('div');
		await user.click(within(githubCard as HTMLElement).getByTestId('discovery-card-import'));

		// Immediately enters the pending state.
		expect(await screen.findByText('Import started')).toBeInTheDocument();
		expect(await screen.findByTestId('card-status-pending')).toBeInTheDocument();

		// The poll picks up registered: true and resolves the card on its own.
		expect(
			await screen.findByText('Import complete', {}, { timeout: 2000 }),
		).toBeInTheDocument();
		expect(
			await screen.findByTestId('card-status-imported', {}, { timeout: 2000 }),
		).toBeInTheDocument();
		expect(screen.queryByTestId('card-status-pending')).not.toBeInTheDocument();
	});

	it('invalidates the workspace API list when an import lands (so it is not stale)', async () => {
		const user = userEvent.setup();
		let imported = false;

		restorePollInterval = setImportPollIntervalForTests(100);

		worker.use(
			http.get('/catalog', () => {
				const data = [
					{
						api_id: 'github.com',
						vendor: 'github',
						path: 'apis/github.com/openapi.json',
						spec_url: 'https://example.com/github.json',
						registered: imported,
						_links: {
							self: '/catalog/github.com',
							operations: '/catalog/github.com/operations',
							import: '/catalog/github.com:import',
							github: null,
						},
					},
				];
				return HttpResponse.json({
					data,
					catalog_total: 1,
					registered_count: imported ? 1 : 0,
					manifest_age_seconds: 5,
					has_more: false,
					next_cursor: null,
				});
			}),
			http.post('/catalog/*', () => {
				imported = true;
				return HttpResponse.json(
					{
						job_id: 'job_github',
						status: 'queued',
						_links: { self: '/jobs/job_github' },
					},
					{ status: 202 },
				);
			}),
		);

		const { queryClient } = renderWithProviders(
			<>
				<DiscoverPage />
				<Toaster />
			</>,
		);
		// Seed a cached workspace list so we can prove the import busts it. Without
		// this the query never existed, so "invalidate" would be a no-op we can't
		// distinguish from the bug.
		queryClient.setQueryData(sharedQueryKeys.workspaceApis, {
			items: [],
			hasMore: false,
			nextCursor: null,
		});
		const invalidateSpy = vi.spyOn(queryClient, 'invalidateQueries');

		await screen.findByText('github.com');
		const githubCard = screen.getByRole('button', { name: 'View github.com' }).closest('div');
		await user.click(within(githubCard as HTMLElement).getByTestId('discovery-card-import'));

		// Once the poll observes registered: true, the workspace list must be
		// invalidated — otherwise the 30s global staleTime serves a pre-import
		// snapshot when the user navigates over to Workspace.
		await screen.findByText('Import complete', {}, { timeout: 2000 });
		await waitFor(() =>
			expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: sharedQueryKeys.workspaceApis }),
		);
	});

	it('refreshes the catalog via POST /catalog:refresh', async () => {
		const user = userEvent.setup();
		let refreshHit = false;
		worker.use(
			http.post('/catalog:refresh', () => {
				refreshHit = true;
				return HttpResponse.json({ count: 3, status: 'refreshed' });
			}),
		);

		renderWithProviders(
			<>
				<DiscoverPage />
				<Toaster />
			</>,
		);
		await screen.findByText('stripe.com');

		await user.click(screen.getByTestId('discover-refresh'));

		await waitFor(() => expect(refreshHit).toBe(true));
		expect(await screen.findByText('Catalog refreshed')).toBeInTheDocument();
	});

	it('surfaces an error when the catalog fails', async () => {
		worker.use(createErrorHandler('get', '/catalog', { status: 500 }));
		renderWithProviders(<DiscoverPage />);
		expect(await screen.findByRole('alert')).toBeInTheDocument();
	});
});
