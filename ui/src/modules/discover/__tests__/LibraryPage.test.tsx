import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { delay, http, HttpResponse } from 'msw';
import {
	renderWithProviders,
	screen,
	waitFor,
	within,
	userEvent,
	fireEvent,
	checkA11y,
} from '@/__tests__/test-utils';
import { worker } from '@/mocks/browser';
import { setToken, sharedQueryKeys } from '@/shared/api';
import { Toaster } from '@/shared/ui';
import { setImportPollIntervalForTests } from '@/modules/discover/api';
import LibraryPage from '@/modules/discover/pages/LibraryPage';

describe('LibraryPage', () => {
	let restorePollInterval: (() => void) | null = null;

	beforeEach(() => {
		// The API client attaches a Bearer token; seed one so requests look
		// authenticated (MSW handlers don't gate on it, but this mirrors runtime).
		setToken('test-token');
	});

	afterEach(() => {
		restorePollInterval?.();
		restorePollInterval = null;
		vi.restoreAllMocks();
	});

	it('renders the public catalog as a ledger with in-workspace markers', async () => {
		renderWithProviders(<LibraryPage />);

		expect(await screen.findByText('stripe.com')).toBeInTheDocument();
		expect(await screen.findByText('github.com')).toBeInTheDocument();
		expect(await screen.findByText('slack.com')).toBeInTheDocument();

		// stripe.com is registered (In your workspace, grouped first); the rest
		// are available — a blank status, no marker.
		expect(screen.getByRole('table', { name: 'API catalog' })).toBeInTheDocument();
		expect(screen.getByTestId('catalog-status-imported')).toBeInTheDocument();
		expect(
			screen.getByText('In your workspace', { selector: '[role="cell"]' }),
		).toBeInTheDocument();
		const available = screen
			.getAllByTestId('catalog-row')
			.filter((row) => row.dataset.registered === 'false');
		expect(available.length).toBeGreaterThanOrEqual(2);
		for (const row of available) {
			expect(within(row).queryByTestId(/^catalog-status-/)).not.toBeInTheDocument();
		}
	});

	it('offers "Open" on imported rows and not on available ones', async () => {
		renderWithProviders(<LibraryPage />);
		await screen.findByText('stripe.com');

		// The single registered card (stripe.com) links to the Library (whose
		// docked panel lists the workspace): the default registry has no row
		// whose `catalog_api_id` is `stripe.com`, so there's no unambiguous hub
		// to deep-link to — and no versions to filter the panel to.
		const link = screen.getByTestId('catalog-row-open');
		expect(link).toHaveAttribute('href', '/library');

		// Available cards expose Import, never the workspace link — there's exactly
		// one imported entry in the default catalog, so exactly one such link.
		expect(screen.getAllByTestId('catalog-row-open')).toHaveLength(1);
	});

	it('shows the whole-manifest status row', async () => {
		renderWithProviders(<LibraryPage />);
		const status = await screen.findByTestId('discover-status');
		expect(within(status).getByText(/APIs in the catalog/)).toBeInTheDocument();
		expect(within(status).getByText(/in your workspace/)).toBeInTheDocument();
		// Counted from the loaded rows — the response carries no vendor total.
		expect(within(status).getByTestId('discover-status-vendors')).toHaveTextContent('vendors');
		// It heads the catalog column, directly above the toolbar — not a
		// full-width strip of its own under the page header. (The toolbar's
		// zero-height scroll sentinel sits between them.)
		expect(status.nextElementSibling).toBe(screen.getByTestId('discover-toolbar-sentinel'));
		expect(status.nextElementSibling?.nextElementSibling).toBe(
			screen.getByTestId('discover-toolbar'),
		);
	});

	it('disambiguates umbrella sub-APIs by title (nytimes.com)', async () => {
		const user = userEvent.setup();
		renderWithProviders(<LibraryPage />);
		await screen.findByText('stripe.com');

		// Three nytimes.com sub-APIs share one vendor; searching "nyt" must show
		// rows tellable apart by title, with the shared vendor as a subtitle.
		await user.type(screen.getByLabelText('Search APIs'), 'nyt');

		expect(await screen.findByText('Article Search')).toBeInTheDocument();
		expect(screen.getByText('Top Stories')).toBeInTheDocument();
		expect(screen.getByText('Books')).toBeInTheDocument();
		// Search is a flat list: no vendor header, the vendor inline on each row.
		await waitFor(() => expect(screen.getAllByTestId('catalog-row')).toHaveLength(3));
		const rows = screen.getAllByTestId('catalog-row');
		for (const row of rows) expect(row).toHaveTextContent('nytimes.com');
		expect(screen.queryByTestId('catalog-vendor-row')).not.toBeInTheDocument();
		expect(screen.queryByTestId('alpha-rail')).not.toBeInTheDocument();
		// The query is highlighted.
		expect(rows[0].querySelector('mark')).toHaveTextContent('nyt');
	});

	it('shows the spec version parsed from a jentic-public-apis spec_url on catalog rows', async () => {
		renderWithProviders(<LibraryPage />);
		await screen.findByText('stripe.com');
		const versions = screen.getAllByTestId('catalog-row-version').map((el) => el.textContent);
		// Every mock entry uses the real layout: `…/{domain}/{sub}/{version}/openapi.json`.
		expect(versions).toEqual(
			expect.arrayContaining(['v2024-01-01', 'v1.1.4', 'v1.0.0', 'v2.0.0', 'v3.0.0']),
		);
		const books = screen
			.getByRole('button', { name: 'View Books' })
			.closest<HTMLElement>('[role="row"]')!;
		expect(within(books).getByTestId('catalog-row-vendor')).toHaveTextContent('nytimes.com');
		expect(within(books).getByTestId('catalog-row-version')).toHaveTextContent('v3.0.0');
		// Browsing nests a vendor's 2–5 APIs under one vendor header.
		const header = screen
			.getAllByTestId('catalog-vendor-row')
			.find((row) => row.dataset.vendor === 'nytimes.com');
		expect(header).toHaveTextContent('3 APIs');
	});

	it('marks an un-imported entry "Credential ready" from a vendor-wide credential alone', async () => {
		// github.com is Available and has no workspace row; a vendor-wide
		// credential for `github` (the slug its import registers) covers it,
		// a credential for another vendor covers nothing.
		worker.use(
			http.get('/credentials', () =>
				HttpResponse.json({
					data: [
						{
							credential_id: 'cred_gh_all',
							name: 'GitHub org token',
							type: 'bearer_token',
							api: { vendor: 'github', name: '', version: '' },
							catalog_api_id: null,
							provider: 'static',
							active: true,
							created_at: '2026-01-01T00:00:00Z',
						},
						{
							credential_id: 'cred_other',
							name: 'Other',
							type: 'api_key',
							api: { vendor: 'acme', name: '', version: '' },
							catalog_api_id: null,
							provider: 'static',
							active: true,
							created_at: '2026-01-01T00:00:00Z',
						},
					],
					has_more: false,
					next_cursor: null,
				}),
			),
		);
		renderWithProviders(<LibraryPage />);
		const chip = await screen.findByTestId('catalog-row-credential-ready');
		expect(chip).toHaveAttribute('title', expect.stringContaining('GitHub org token'));
		expect(chip).not.toHaveAttribute('title', expect.stringContaining('Other'));
		const row = chip.closest('[data-testid="catalog-row"]');
		expect(row).toHaveTextContent('github.com');
		expect(screen.getAllByTestId('catalog-row-credential-ready')).toHaveLength(1);
	});

	it('has no critical a11y violations', async () => {
		const { container } = renderWithProviders(<LibraryPage />);
		await screen.findByText('stripe.com');
		// Let the header's entrance fade finish — mid-fade text reads as low contrast.
		const heading = screen.getByRole('heading', { name: 'Library' });
		await waitFor(() => {
			for (let el: HTMLElement | null = heading; el; el = el.parentElement) {
				expect(getComputedStyle(el).opacity).toBe('1');
			}
		});
		await checkA11y(container);
	});

	it('filters by search query', async () => {
		const user = userEvent.setup();
		renderWithProviders(<LibraryPage />);
		await screen.findByText('stripe.com');

		await user.type(screen.getByLabelText('Search APIs'), 'github');

		await waitFor(() => {
			expect(screen.queryByText('stripe.com')).not.toBeInTheDocument();
		});
		// Search rows highlight the match, so the name is split around a <mark>.
		const row = (
			await screen.findByRole('button', { name: 'View github.com' })
		).closest<HTMLElement>('[role="row"]')!;
		expect(row.querySelector('mark')).toHaveTextContent('github');
	});

	it('resets scroll to the top when the search query changes (#602)', async () => {
		const user = userEvent.setup();
		// Restored by the describe-level afterEach (vi.restoreAllMocks).
		const scrollSpy = vi.spyOn(window, 'scrollTo').mockImplementation(() => {});

		renderWithProviders(<LibraryPage />);
		await screen.findByText('stripe.com');

		// Initial mount must not yank the viewport.
		expect(scrollSpy).not.toHaveBeenCalled();

		await user.type(screen.getByLabelText('Search APIs'), 'github');
		await screen.findByText('github.com');

		// The committed (debounced) query change snaps back to the top so the
		// freshly-ranked results are in view.
		await waitFor(() => {
			expect(scrollSpy).toHaveBeenCalledWith({ top: 0, left: 0 });
		});
	});

	it('resets scroll to the top when the registration filter changes (#602)', async () => {
		const user = userEvent.setup();
		const scrollSpy = vi.spyOn(window, 'scrollTo').mockImplementation(() => {});

		renderWithProviders(<LibraryPage />);
		await screen.findByText('stripe.com');
		expect(scrollSpy).not.toHaveBeenCalled();

		// Toggling the filter re-ranks the visible list (imported rows drop out),
		// so the viewport must snap back to the top too — same UX as a new query.
		await user.click(screen.getByRole('button', { name: 'Available' }));
		await waitFor(() => {
			expect(screen.queryByText('stripe.com')).not.toBeInTheDocument();
		});

		await waitFor(() => {
			expect(scrollSpy).toHaveBeenCalledWith({ top: 0, left: 0 });
		});
	});

	it('offers All / Available / Update available as catalog filters (no "In your workspace")', async () => {
		renderWithProviders(<LibraryPage />);
		await screen.findByText('stripe.com');
		const toolbar = screen.getByTestId('discover-toolbar');
		for (const name of ['All', 'Available', 'Update available']) {
			expect(within(toolbar).getByRole('button', { name })).toBeInTheDocument();
		}
		expect(
			within(toolbar).queryByRole('button', { name: 'In your workspace' }),
		).not.toBeInTheDocument();
	});

	it('filters by registration state (Available hides imported rows)', async () => {
		const user = userEvent.setup();
		renderWithProviders(<LibraryPage />);
		await screen.findByText('stripe.com');

		await user.click(screen.getByRole('button', { name: 'Available' }));

		await waitFor(() => {
			expect(screen.queryByText('stripe.com')).not.toBeInTheDocument();
		});
		expect(screen.getByText('github.com')).toBeInTheDocument();
	});

	it('opens the detail sheet and previews operations', async () => {
		const user = userEvent.setup();
		renderWithProviders(<LibraryPage />);
		await screen.findByText('github.com');

		await user.click(
			screen
				.getByRole('button', { name: 'View github.com' })
				.closest<HTMLElement>('[role="row"]')!,
		);

		const dialog = await screen.findByRole('dialog');
		expect(await within(dialog).findByText('Get a repository')).toBeInTheDocument();
		expect(within(dialog).getByText('Create an issue')).toBeInTheDocument();
	});

	it("closes the detail sheet from its header X and returns focus to the row's button", async () => {
		const user = userEvent.setup();
		renderWithProviders(<LibraryPage />);
		await screen.findByText('github.com');
		const card = screen.getByRole('button', { name: 'View github.com' });
		await user.click(card);

		const dialog = await screen.findByRole('dialog');
		const close = within(dialog).getByRole('button', { name: 'Close' });
		// After the copy-id control in tab order, so it's the header's last stop.
		const copy = within(dialog).getByRole('button', { name: 'Copy to clipboard' });
		expect(copy.compareDocumentPosition(close) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();

		await user.click(close);
		await waitFor(() => {
			expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
		});
		await waitFor(() => expect(card).toHaveFocus());
	});

	it('drills into an operation and back', async () => {
		const user = userEvent.setup();
		renderWithProviders(<LibraryPage />);
		await screen.findByText('github.com');
		await user.click(
			screen
				.getByRole('button', { name: 'View github.com' })
				.closest<HTMLElement>('[role="row"]')!,
		);

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
		renderWithProviders(<LibraryPage />);
		await screen.findByText('github.com');
		await user.click(
			screen
				.getByRole('button', { name: 'View github.com' })
				.closest<HTMLElement>('[role="row"]')!,
		);

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
		renderWithProviders(<LibraryPage />);
		await screen.findByText('github.com');
		await user.click(
			screen
				.getByRole('button', { name: 'View github.com' })
				.closest<HTMLElement>('[role="row"]')!,
		);
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
		renderWithProviders(<LibraryPage />);
		await screen.findByText('github.com');
		await user.click(
			screen
				.getByRole('button', { name: 'View github.com' })
				.closest<HTMLElement>('[role="row"]')!,
		);
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
		renderWithProviders(<LibraryPage />);
		await screen.findByText('github.com');
		await user.click(
			screen
				.getByRole('button', { name: 'View github.com' })
				.closest<HTMLElement>('[role="row"]')!,
		);

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

	it('enqueues an import from an available row', async () => {
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
				<LibraryPage />
				<Toaster />
			</>,
		);
		await screen.findByText('github.com');

		const githubCard = screen
			.getByRole('button', { name: 'View github.com' })
			.closest<HTMLElement>('[role="row"]')!;
		// Row actions reveal on hover/focus; fire the click directly.
		fireEvent.click(within(githubCard).getByTestId('catalog-row-add'));

		await waitFor(() => expect(importHit).toBe(true));
		// One "Adding…" signal — the row's status. No start toast (and no job id).
		expect(await within(githubCard).findByTestId('catalog-status-pending')).toBeInTheDocument();
		expect(within(githubCard).queryByTestId('catalog-row-add')).not.toBeInTheDocument();
		expect(screen.queryByText('Adding to workspace')).not.toBeInTheDocument();
		expect(screen.queryByText(/job_/)).not.toBeInTheDocument();
	});

	it('names the API in the toast when an import cannot be queued', async () => {
		worker.use(
			http.post('/catalog/*', () =>
				HttpResponse.json({ detail: 'Import queue is full.' }, { status: 503 }),
			),
		);
		renderWithProviders(
			<>
				<LibraryPage />
				<Toaster />
			</>,
		);
		await screen.findByText('github.com');
		const githubCard = screen
			.getByRole('button', { name: 'View github.com' })
			.closest<HTMLElement>('[role="row"]')!;
		fireEvent.click(within(githubCard).getByTestId('catalog-row-add'));
		expect(
			await screen.findByText('Couldn’t add github.com to your workspace'),
		).toBeInTheDocument();
		expect(screen.getByText('Import queue is full.')).toBeInTheDocument();
	});

	it('flips a row to In your workspace when the polled catalog reports it registered', async () => {
		let imported = false;
		// Catalog reads that answered `registered: true` — the poll's own signal.
		let pollsAfterImport = 0;

		// Poll fast & deterministically instead of racing the real 3s tick.
		restorePollInterval = setImportPollIntervalForTests(100);

		// Stateful catalog: github.com starts Available, flips to registered once
		// the import has been enqueued (simulating the async job landing). The
		// page polls /catalog while an import is pending, so the card should
		// resolve on its own without a manual refresh.
		worker.use(
			http.get('/catalog', () => {
				if (imported) pollsAfterImport += 1;
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
				<LibraryPage />
				<Toaster />
			</>,
		);
		await screen.findByText('github.com');

		const githubCard = screen
			.getByRole('button', { name: 'View github.com' })
			.closest<HTMLElement>('[role="row"]')!;
		fireEvent.click(within(githubCard).getByTestId('catalog-row-add'));

		// Immediately enters the pending state: the status reads "Adding…" (the
		// Add verb steps aside meanwhile).
		expect(await within(githubCard).findByTestId('catalog-status-pending')).toBeInTheDocument();
		expect(within(githubCard).queryByTestId('catalog-row-add')).not.toBeInTheDocument();

		// The poll picks up registered: true and resolves the card on its own —
		// wait on the poll itself, then on the (default-budget) UI flip.
		await waitFor(() => expect(pollsAfterImport).toBeGreaterThan(0));
		expect(await screen.findByText('Added to workspace')).toBeInTheDocument();
		expect(await screen.findByTestId('catalog-status-imported')).toBeInTheDocument();
	});

	it('invalidates the workspace API list when an import lands (so it is not stale)', async () => {
		let imported = false;

		let pollsAfterImport = 0;
		restorePollInterval = setImportPollIntervalForTests(100);

		worker.use(
			http.get('/catalog', () => {
				if (imported) pollsAfterImport += 1;
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
				<LibraryPage />
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
		const githubCard = screen
			.getByRole('button', { name: 'View github.com' })
			.closest<HTMLElement>('[role="row"]')!;
		fireEvent.click(within(githubCard).getByTestId('catalog-row-add'));

		// Once the poll observes registered: true, the workspace list must be
		// invalidated — otherwise the 30s global staleTime serves a pre-import
		// snapshot when the user navigates over to Workspace.
		await waitFor(() => expect(pollsAfterImport).toBeGreaterThan(0));
		await screen.findByText('Added to workspace');
		await waitFor(() =>
			expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: sharedQueryKeys.workspaceApis }),
		);
	});

	it('resolves an import started from a rail-jump range (not in the head feed), live in the sheet', async () => {
		// The head feed stops at `acme.com` (its next page never answers), so
		// `zoom.us` is only ever reachable through the Z jump range — and, once
		// registered, the "In your workspace" feed. The import must still land
		// off those polled feeds, not time out waiting on the head.
		let imported = false;
		let pollsAfterImport = 0;
		restorePollInterval = setImportPollIntervalForTests(100);
		const row = (apiId: string, registered: boolean) => ({
			api_id: apiId,
			vendor: apiId,
			path: `apis/${apiId}`,
			spec_url: `https://example.com/${apiId}.json`,
			registered,
			_links: {
				self: `/catalog/${apiId}`,
				operations: `/catalog/${apiId}/operations`,
				import: `/catalog/${apiId}:import`,
				github: null,
			},
		});
		const cursorId = (cursor: string | null) =>
			cursor ? (JSON.parse(atob(cursor)) as { id: string }).id : null;
		const page = (data: ReturnType<typeof row>[], more = false) =>
			HttpResponse.json({
				data,
				catalog_total: 3,
				registered_count: imported ? 1 : 0,
				manifest_age_seconds: 5,
				has_more: more,
				next_cursor: more
					? btoa(JSON.stringify({ id: data[data.length - 1].api_id }))
					: null,
			});
		worker.use(
			http.get('/catalog', async ({ request }) => {
				const url = new URL(request.url);
				if (imported) pollsAfterImport += 1;
				if (url.searchParams.get('registered_only') === 'true') {
					return page(imported ? [row('zoom.us', true)] : []);
				}
				const after = cursorId(url.searchParams.get('cursor'));
				// `acme.com` sits at the frontier, so it's held back; `abc.com` shows.
				if (after == null)
					return page([row('abc.com', false), row('acme.com', false)], true);
				if (after === 'z') return page([row('zoom.us', imported)]);
				// The head's own next page: never answers (keeps zoom.us out of it).
				await delay('infinite');
				return page([]);
			}),
			http.post('/catalog/*', () => {
				imported = true;
				return HttpResponse.json(
					{ job_id: 'job_zoom', status: 'queued', _links: { self: '/jobs/job_zoom' } },
					{ status: 202 },
				);
			}),
		);

		renderWithProviders(
			<>
				<LibraryPage />
				<Toaster />
			</>,
		);
		await screen.findByText('abc.com');

		const rail = screen.getByRole('toolbar', { name: 'Jump to letter' });
		fireEvent.click(within(rail).getByRole('button', { name: /^Z — / }));
		fireEvent.click(await screen.findByRole('button', { name: 'View zoom.us' }));

		const sheet = await screen.findByRole('dialog');
		fireEvent.click(within(sheet).getByTestId('sheet-import'));
		await waitFor(() =>
			expect(within(sheet).getByTestId('sheet-import')).toHaveTextContent('Adding…'),
		);

		// The polled jump range / workspace feed report it registered: the
		// import resolves (no "Still adding" timeout) and the open sheet flips.
		await waitFor(() => expect(pollsAfterImport).toBeGreaterThan(0));
		expect(await screen.findByText('Added to workspace')).toBeInTheDocument();
		expect(await within(sheet).findByTestId('sheet-status-imported')).toBeInTheDocument();
		expect(within(sheet).queryByTestId('sheet-import')).not.toBeInTheDocument();
		expect(screen.queryByTestId('catalog-status-pending')).not.toBeInTheDocument();
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
				<LibraryPage />
				<Toaster />
			</>,
		);
		await screen.findByText('stripe.com');

		await user.click(screen.getByTestId('discover-refresh'));

		await waitFor(() => expect(refreshHit).toBe(true));
		expect(await screen.findByText('Catalog refreshed')).toBeInTheDocument();
	});

	it('surfaces a titled error with Try again, and hides the count line meanwhile', async () => {
		let failing = true;
		worker.use(
			http.get('/catalog', () =>
				failing
					? HttpResponse.json({ detail: 'Upstream down' }, { status: 500 })
					: undefined,
			),
		);
		const user = userEvent.setup();
		renderWithProviders(<LibraryPage />);
		const alert = await screen.findByRole('alert');
		expect(alert).toHaveTextContent("Couldn't load the catalog");
		expect(alert).toHaveTextContent('Upstream down');
		// Zeros while in error are not facts: no "0 APIs … never refreshed".
		expect(screen.queryByTestId('discover-status')).not.toBeInTheDocument();

		failing = false;
		await user.click(within(alert).getByRole('button', { name: 'Try again' }));
		expect(await screen.findByText('stripe.com')).toBeInTheDocument();
		expect(await screen.findByTestId('discover-status')).toBeInTheDocument();
	});

	it('recovers from a 409 on the first catalog read (the first-snapshot race)', async () => {
		let conflicts = 1;
		worker.use(
			http.get('/catalog', () => {
				if (conflicts > 0) {
					conflicts -= 1;
					return HttpResponse.json(
						{ detail: 'The request conflicts with the current state of the resource.' },
						{ status: 409 },
					);
				}
				return undefined;
			}),
		);
		renderWithProviders(<LibraryPage />);
		expect(await screen.findByText('stripe.com')).toBeInTheDocument();
		expect(screen.queryByRole('alert')).not.toBeInTheDocument();
	});

	it('reads the in-workspace group only after the main feed has settled', async () => {
		const order: string[] = [];
		let mainDone = false;
		worker.use(
			http.get('/catalog', async ({ request }) => {
				const registeredOnly =
					new URL(request.url).searchParams.get('registered_only') === 'true';
				order.push(registeredOnly ? (mainDone ? 'ws-after' : 'ws-before') : 'main');
				if (!registeredOnly) {
					await delay(50);
					mainDone = true;
				}
				return undefined;
			}),
		);
		renderWithProviders(<LibraryPage />);
		await screen.findByText('stripe.com');
		await waitFor(() => expect(order).toContain('ws-after'));
		expect(order).not.toContain('ws-before');
	});

	it('labels the vendor figure as loaded-so-far while more pages exist', async () => {
		worker.use(
			http.get('/catalog', async ({ request }) => {
				const params = new URL(request.url).searchParams;
				if (params.get('registered_only') === 'true') return undefined;
				// The next page never arrives: the feed stays "more to come".
				if (params.get('cursor')) await delay('infinite');
				return HttpResponse.json({
					data: ['acme.com', 'beta.io/a', 'beta.io/b'].map((id) => ({
						api_id: id,
						summary: id,
						spec_url: `https://example.com/${id}.json`,
						registered: false,
						_links: {},
					})),
					catalog_total: 6345,
					registered_count: 0,
					outdated_count: 0,
					manifest_age_seconds: 60,
					has_more: true,
					next_cursor: 'eyJpZCI6ICJiZXRhLmlvL2IifQ==',
				});
			}),
		);
		renderWithProviders(<LibraryPage />);
		const status = await screen.findByTestId('discover-status');
		// acme.com + beta.io from the page, stripe.com from the in-workspace read.
		await waitFor(() => expect(status).toHaveTextContent(/from 3\+ vendors so far/));
		expect(status).not.toHaveTextContent('4,870');
	});

	it('lands a backward rail jump on the clicked letter after earlier jumps (Y→G→A→A)', async () => {
		// A sequence of rail jumps to letters that aren't in the head feed each
		// start a fresh keyset range AT the letter (`catalogCursorAfter`). The
		// cursor for a letter is absolute — derived only from the letter, never
		// from the currently loaded range. Jumping *back* to an earlier letter
		// inserts that letter's range ABOVE the current scroll position, which
		// shifts the content down a frame after the single landing scroll was
		// aimed — enough to clamp a smooth scroll a letter short, on the one
		// just before the previous jump's range. The landing must re-pin to the
		// clicked letter through that reflow. Guards the Y→G→A overshoot.
		const row = (apiId: string) => ({
			api_id: apiId,
			vendor: apiId,
			path: `apis/${apiId}`,
			spec_url: `https://example.com/${apiId}.json`,
			registered: false,
			_links: {
				self: `/catalog/${apiId}`,
				operations: `/catalog/${apiId}/operations`,
				import: `/catalog/${apiId}:import`,
				github: null,
			},
		});
		const cursorId = (cursor: string | null) =>
			cursor ? (JSON.parse(atob(cursor)) as { id: string }).id : null;
		const page = (data: ReturnType<typeof row>[], more = false) =>
			HttpResponse.json({
				data,
				catalog_total: 999,
				registered_count: 0,
				outdated_count: 0,
				manifest_age_seconds: 5,
				has_more: more,
				next_cursor: more
					? btoa(JSON.stringify({ id: data[data.length - 1]!.api_id }))
					: null,
			});
		// Each letter's jump range is keyed by the keyset start the rail builds
		// for it (`jumpStartKey`: the lowercased letter). Each range is a tall
		// block of single-API vendors, so jumping back to A inserts enough rows
		// above the (scrolled-to-G) viewport to shift it — the condition that
		// clamps a landing a letter short.
		const block = (c: string) =>
			Array.from({ length: 14 }, (_, i) =>
				row(`${c}${String(i).padStart(2, '0')}vendor.com`),
			);
		const RANGES: Record<string, ReturnType<typeof row>[]> = {
			a: block('a'),
			g: block('g'),
			y: block('y'),
		};
		worker.use(
			http.get('/catalog', async ({ request }) => {
				const url = new URL(request.url);
				if (url.searchParams.get('registered_only') === 'true') return page([]);
				const after = cursorId(url.searchParams.get('cursor'));
				// A jump cursor starts exactly at a letter boundary. A short delay
				// keeps the range's rows arriving a frame after the landing scroll,
				// which is when the content above reflows.
				if (after != null && RANGES[after]) {
					await delay(40);
					return page(RANGES[after]!);
				}
				// The head feed: one vendor that establishes no A–Z frontier (a
				// digit-prefixed id stays under #, held until fully loaded), and
				// its own next page never answers — so A/G/Y are only ever
				// reachable through their own jump ranges, and no letter between
				// them is marked "passed".
				if (after == null) return page([row('0mid.com/a'), row('0mid.com/b')], true);
				await delay('infinite');
				return page([]);
			}),
		);

		renderWithProviders(<LibraryPage />);
		await screen.findByTestId('catalog-ledger');
		const rail = await screen.findByRole('toolbar', { name: 'Jump to letter' });
		const jumpTo = async (letter: string) => {
			const btn = await within(rail).findByRole('button', {
				name: new RegExp(`^${letter} — `),
			});
			fireEvent.click(btn);
		};
		// The letter whose heading currently sits at the landing line — i.e.
		// where the last jump actually landed the viewport.
		const landedLetter = () => {
			let best: string | null = null;
			let bestDist = Infinity;
			for (const el of document.querySelectorAll('[id^="catalog-letter-"]')) {
				const letter = el.id.replace('catalog-letter-', '').replace('num', '#');
				const dist = Math.abs(el.getBoundingClientRect().top);
				if (dist < bestDist) {
					bestDist = dist;
					best = letter;
				}
			}
			return best;
		};

		// Forward (Y, G), each landing on its own letter.
		await jumpTo('Y');
		await waitFor(() => expect(landedLetter()).toBe('Y'));
		await jumpTo('G');
		await waitFor(() => expect(landedLetter()).toBe('G'));

		// Back to A. The regression this guards: A lands on the heading just
		// before the previous (G) range instead of on A. Focus follows the
		// landing, so both the viewport and focus must end on A.
		await jumpTo('A');
		await waitFor(() => expect(document.getElementById('catalog-letter-A')).not.toBeNull());
		await waitFor(() => expect(landedLetter()).toBe('A'));
		expect(document.activeElement).toBe(document.getElementById('catalog-letter-A'));
	});
});
