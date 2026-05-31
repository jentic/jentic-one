import { http, HttpResponse } from 'msw';
import { within } from '@testing-library/react';
import { screen, waitFor, renderWithProviders, userEvent } from '../../test-utils';
import { worker } from '../../mocks/browser';
import { DiscoveryView } from '@/components/discovery';

function renderDirectoryDiscover(route = '/discover') {
	// `/discover` hard-codes `forcedSource="directory"` — the only
	// production single-mode mounting site for `DiscoveryView` after
	// the May 2026 source-toggle deletion. Tests that previously used
	// the toggle-bearing helper now drive this surface instead.
	return renderWithProviders(<DiscoveryView forcedSource="directory" />, {
		route,
		path: '/discover',
	});
}

describe('DiscoveryView', () => {
	// ── Heading + base chrome ────────────────────────────────────────────────

	it('renders the sticky toolbar (no source / type filter bar after the May 2026 simplification)', async () => {
		renderDirectoryDiscover();
		expect(await screen.findByTestId('discover-toolbar')).toBeInTheDocument();
		expect(screen.getByRole('searchbox', { name: /search/i })).toBeInTheDocument();
	});

	// ── Browse mode (grid, APIs default) ─────────────────────────────────────

	// ── Search mode ──────────────────────────────────────────────────────────
	// After the May 2026 IA simplification, "search" is just a server-side
	// query param on `/apis` — no separate `/search` endpoint or blended
	// results grid. Typing filters the same browse grid in place.

	it('typing in the search box filters the browse grid via /apis?q=', async () => {
		const user = userEvent.setup();
		let lastQ: string | null = null;
		worker.use(
			http.get('/apis', ({ request }) => {
				const url = new URL(request.url);
				lastQ = url.searchParams.get('q');
				const data = lastQ
					? [{ id: 'stripe.com', name: 'Stripe', source: 'catalog' }]
					: [
							{ id: 'stripe.com', name: 'Stripe', source: 'catalog' },
							{ id: 'github.com', name: 'github.com', source: 'catalog' },
						];
				return HttpResponse.json({ data, total: data.length, page: 1 });
			}),
		);

		renderDirectoryDiscover();
		await waitFor(() => expect(screen.getByText('Stripe')).toBeInTheDocument());
		expect(screen.getByText('github.com')).toBeInTheDocument();

		const input = screen.getByRole('searchbox', { name: /search/i });
		await user.type(input, 'stripe');

		await waitFor(() => {
			expect(lastQ).toBe('stripe');
		});
		await waitFor(() => {
			expect(screen.queryByText('github.com')).not.toBeInTheDocument();
		});
		expect(screen.getByText('Stripe')).toBeInTheDocument();
	});

	it('catalog_api rows from /apis render as ApiCards with source=directory', async () => {
		worker.use(
			http.get('/apis', () =>
				HttpResponse.json({
					data: [
						{
							id: 'plaid.com',
							name: 'plaid.com',
							source: 'catalog',
						},
					],
					total: 1,
					page: 1,
				}),
			),
		);

		renderDirectoryDiscover();

		const card = await screen.findByTestId('discovery-card-api');
		expect(card).toBeInTheDocument();
		expect(within(card).getByText('Available')).toBeInTheDocument();
		expect(within(card).queryByText(/available to import/i)).toBeNull();
	});

	it('has_workflows flag on catalog rows surfaces the "+ workflows" chip on browse cards', async () => {
		worker.use(
			http.get('/apis', () =>
				HttpResponse.json({
					data: [
						{
							id: 'openai.com',
							name: 'openai.com',
							source: 'catalog',
							has_workflows: true,
						},
					],
					total: 1,
					page: 1,
				}),
			),
		);

		renderDirectoryDiscover();

		const card = await screen.findByTestId('discovery-card-api');
		expect(card).toBeInTheDocument();
		expect(within(card).getByText('workflows')).toBeInTheDocument();
		expect(screen.queryByTestId('discovery-card-workflow')).toBeNull();
	});

	// ── Directory-forced (`/discover`) mode ─────────────────────────────────

	it('directory mode search filters via /apis query param (no workflow rows)', async () => {
		const user = userEvent.setup();
		let lastQ: string | null = null;
		worker.use(
			http.get('/apis', ({ request }) => {
				const url = new URL(request.url);
				lastQ = url.searchParams.get('q');
				const data = lastQ
					? [
							{
								id: 'plaid.com',
								name: 'plaid.com',
								source: 'catalog',
								has_workflows: true,
							},
						]
					: [];
				return HttpResponse.json({ data, total: data.length, page: 1 });
			}),
		);

		renderDirectoryDiscover();
		await user.type(screen.getByRole('searchbox', { name: /search/i }), 'plaid');

		await waitFor(() => expect(screen.getByText('plaid.com')).toBeInTheDocument());
		// API card present with the workflow chip.
		expect(screen.getByText('workflows')).toBeInTheDocument();
		// No workflow cards rendered — directory mode only shows APIs.
		expect(screen.queryByTestId('discovery-card-workflow')).toBeNull();
	});

	it('directory mode rewrites stale ?type=workflow URLs to drop the param', async () => {
		// Deep links to `/discover?type=workflow` from before the
		// collapse should not render with a hidden filter active. The
		// `useEffect` URL-fixup in `DiscoveryView` strips the param so
		// the page renders identically to `/discover`.
		worker.use(http.get('/apis', () => HttpResponse.json({ data: [], total: 0, page: 1 })));

		renderDirectoryDiscover('/discover?type=workflow');
		await screen.findByTestId('discover-toolbar');

		// `?type=workflow` should be rewritten away — the URL test
		// utility lets us read the current location through a
		// `data-testid` mirror, but the simplest assertion is that the
		// browse view falls back to APIs (no workflow query fires).
		await waitFor(() => {
			expect(window.location.search).not.toContain('type=workflow');
		});
	});

	it('directory browse renders the "+ workflows" chip when /apis sets has_workflows', async () => {
		// Regression: previously the chip only surfaced on search-result
		// cards because only the `/search` payload carried
		// `has_workflows`. `/apis` now folds the workflow manifest into
		// its catalog rows the same way `/search`'s blender does, so the
		// directory browse grid can advertise workflow availability
		// before the user opens the API detail sheet. Without this,
		// the section inside the sheet was a hidden treasure — there
		// was nothing on the card to hint it existed.
		worker.use(
			http.get('/apis', () =>
				HttpResponse.json({
					data: [
						{
							id: 'plaid.com',
							name: 'plaid.com',
							source: 'catalog',
							has_credentials: false,
							has_workflows: true,
						},
						{
							id: 'github.com',
							name: 'github.com',
							source: 'catalog',
							has_credentials: false,
							has_workflows: false,
						},
					],
					total: 2,
					page: 1,
				}),
			),
		);

		renderDirectoryDiscover();
		await screen.findByTestId('discover-toolbar');

		// Plaid carries the chip; GitHub does not. Scope the assertions
		// to the cards themselves so a stray "+ workflows" string in
		// chrome elsewhere wouldn't pass the check.
		const cards = await screen.findAllByTestId('discovery-card-api');
		const plaidCard = cards.find((c) => within(c).queryByText('plaid.com'));
		const githubCard = cards.find((c) => within(c).queryByText('github.com'));
		expect(plaidCard).toBeDefined();
		expect(githubCard).toBeDefined();
		expect(within(plaidCard!).getByText('workflows')).toBeInTheDocument();
		expect(within(githubCard!).queryByText('workflows')).toBeNull();
	});

	// ── P2: search relevance feedback ────────────────────────────────────────
	// Match snippets and the "matched on" badge were removed in the May 2026
	// IA simplification. Search is now a server-side filter on /apis, not a
	// separate /search endpoint with scoring/highlighting. The filter-based
	// search is tested above in "typing in the search box filters the browse
	// grid via /apis?q=".

	it('search results show a count summary when query is non-empty', async () => {
		const user = userEvent.setup();
		worker.use(
			http.get('/apis', ({ request }) => {
				const url = new URL(request.url);
				const q = url.searchParams.get('q');
				if (q) {
					return HttpResponse.json({
						data: [{ id: 'stripe.com', name: 'Stripe', source: 'local' }],
						total: 1,
						page: 1,
					});
				}
				return HttpResponse.json({
					data: [
						{ id: 'stripe.com', name: 'Stripe', source: 'local' },
						{ id: 'github.com', name: 'github.com', source: 'catalog' },
					],
					total: 2,
					page: 1,
				});
			}),
		);

		renderDirectoryDiscover();
		await waitFor(() => expect(screen.getByText('Stripe')).toBeInTheDocument());

		await user.type(screen.getByRole('searchbox', { name: /search/i }), 'stripe');

		await waitFor(() => {
			expect(screen.getByText(/1 result/)).toBeInTheDocument();
		});
	});

	// ── Directory card inline actions ────────────────────────────────────────

	it('Directory API card exposes Import to workspace + View on GitHub inline (independent of sheet)', async () => {
		worker.use(
			http.get('/apis', () =>
				HttpResponse.json({
					data: [
						{
							id: 'plaid.com',
							name: 'plaid.com',
							source: 'catalog',
							_links: {
								github: 'https://github.com/jentic/jentic-public-apis/tree/main/apis/openapi/plaid.com',
							},
						},
					],
					total: 1,
					page: 1,
				}),
			),
		);

		renderDirectoryDiscover();

		const card = await screen.findByTestId('discovery-card-api');

		// Inline primary action: import to workspace. Now a <button> that
		// fires `POST /import` directly (no credential-form indirection)
		// — see useImportCatalogApi. The card-level click handler is
		// guarded with stopPropagation so the button doesn't ALSO open
		// the sheet (verified by the next test).
		const importBtn = within(card).getByRole('button', { name: /^import$/i });
		expect(importBtn).toHaveAttribute('data-testid', 'discovery-card-import');

		const gh = within(card).getByRole('link', { name: /view plaid\.com on github/i });
		expect(gh).toHaveAttribute(
			'href',
			'https://github.com/jentic/jentic-public-apis/tree/main/apis/openapi/plaid.com',
		);
	});

	it('Directory API card omits a synthetic description (catalog manifest has none)', async () => {
		// May 2026: the adapter used to fabricate "Available in the Jentic
		// public catalog. Add a credential…" so card heights matched
		// workspace cards. Every directory card got the *same* string,
		// which read like real metadata when it wasn't. Now the
		// description column is empty for catalog rows; the differentiation
		// lives in the chip row + action buttons. See
		// `apiToEntity` in DiscoveryView.tsx.
		worker.use(
			http.get('/apis', () =>
				HttpResponse.json({
					data: [
						{
							id: 'plaid.com',
							name: 'plaid.com',
							source: 'catalog',
						},
					],
					total: 1,
					page: 1,
				}),
			),
		);

		renderDirectoryDiscover();

		const card = await screen.findByTestId('discovery-card-api');
		expect(within(card).queryByText(/add a credential to import/i)).toBeNull();
		expect(within(card).queryByText(/available in the jentic public catalog/i)).toBeNull();
	});

	// ── API Detail Sheet (Phase 1) ───────────────────────────────────────────

	it('clicking a workspace API card opens the detail sheet with operations', async () => {
		const user = userEvent.setup();
		worker.use(
			http.get('/apis', () =>
				HttpResponse.json({
					data: [
						{
							id: 'stripe.com',
							name: 'Stripe',
							source: 'local',
							has_credentials: true,
						},
					],
					total: 1,
					page: 1,
				}),
			),
			http.get('/catalog/stripe.com/operations', () =>
				HttpResponse.json({
					data: [
						{
							method: 'GET',
							path: '/v1/customers',
							summary: 'List customers',
							description: '',
							operation_id: 'listCustomers',
							parameters: [],
							security: [],
							tags: [],
						},
						{
							method: 'POST',
							path: '/v1/charges',
							summary: 'Create charge',
							description: '',
							operation_id: 'createCharge',
							parameters: [],
							security: [],
							tags: [],
						},
					],
					total: 2,
					truncated: false,
					offset: 0,
					limit: 25,
					spec_url: '',
					info: { title: 'Stripe', version: '1.0', description: '' },
					security_schemes: {},
				}),
			),
		);

		renderDirectoryDiscover();

		const card = await screen.findByTestId('discovery-card-api');
		await user.click(within(card).getByRole('button'));

		// Sheet opens — header repeats the title in the dialog.
		const sheet = await screen.findByTestId('sheet-primitive');
		expect(within(sheet).getByRole('heading', { name: 'Stripe' })).toBeInTheDocument();

		// Operations list rendered from the catalog operations endpoint.
		await waitFor(() => {
			expect(within(sheet).getByTestId('sheet-ops-list')).toBeInTheDocument();
		});
		expect(within(sheet).getByText('List customers')).toBeInTheDocument();
		expect(within(sheet).getByText('Create charge')).toBeInTheDocument();
		// Method badges + paths rendered.
		expect(within(sheet).getByText('GET')).toBeInTheDocument();
		expect(within(sheet).getByText('POST')).toBeInTheDocument();
		expect(within(sheet).getByText('/v1/customers')).toBeInTheDocument();
		expect(within(sheet).getByText('/v1/charges')).toBeInTheDocument();
	});

	it('sheet shows Workflows-for-this-API section when the API has matching workflows', async () => {
		const user = userEvent.setup();
		worker.use(
			http.get('/apis', () =>
				HttpResponse.json({
					data: [{ id: 'stripe.com', name: 'Stripe', source: 'local' }],
					total: 1,
					page: 1,
				}),
			),
			http.get('/catalog/stripe.com/operations', () =>
				HttpResponse.json({
					data: [],
					total: 0,
					truncated: false,
					offset: 0,
					limit: 25,
					spec_url: '',
					info: { title: 'Stripe', version: '1.0', description: '' },
					security_schemes: {},
				}),
			),
			http.get('/workflows', () =>
				HttpResponse.json([
					{
						slug: 'charge-and-receipt',
						name: 'Charge customer and send receipt',
						involved_apis: ['stripe.com'],
						steps_count: 4,
					},
					{
						slug: 'unrelated',
						name: 'Unrelated workflow that mentions stripe in copy',
						involved_apis: ['github.com'],
						steps_count: 2,
					},
				]),
			),
		);

		renderDirectoryDiscover();
		const card = await screen.findByTestId('discovery-card-api');
		await user.click(within(card).getByRole('button'));

		const sheet = await screen.findByTestId('sheet-primitive');
		await waitFor(() => {
			expect(within(sheet).getByTestId('sheet-workflows-section')).toBeInTheDocument();
		});

		// Only the workflow that actually involves stripe.com renders.
		expect(within(sheet).getByText(/Charge customer and send receipt/)).toBeInTheDocument();
		expect(within(sheet).queryByText(/Unrelated workflow/)).toBeNull();
		// Step count chip rendered.
		expect(within(sheet).getByText(/4 steps/)).toBeInTheDocument();
	});

	it('sheet hides Workflows section when there are no matching workflows', async () => {
		worker.use(
			http.get('/apis', () =>
				HttpResponse.json({
					data: [{ id: 'lonely.com', name: 'Lonely', source: 'local' }],
					total: 1,
					page: 1,
				}),
			),
			http.get('/catalog/lonely.com/operations', () =>
				HttpResponse.json({
					data: [],
					total: 0,
					truncated: false,
					offset: 0,
					limit: 25,
					spec_url: '',
					info: { title: 'Lonely', version: '1.0', description: '' },
					security_schemes: {},
				}),
			),
			http.get('/workflows', () => HttpResponse.json([])),
		);

		const user = userEvent.setup();
		renderDirectoryDiscover();
		const card = await screen.findByTestId('discovery-card-api');
		await user.click(within(card).getByRole('button'));

		const sheet = await screen.findByTestId('sheet-primitive');
		// Wait for the operations section to settle as a proxy for "sheet body loaded".
		await waitFor(() => {
			expect(within(sheet).getByText(/no operations found/i)).toBeInTheDocument();
		});
		// Empty workflow section is suppressed entirely.
		expect(within(sheet).queryByTestId('sheet-workflows-section')).toBeNull();
	});

	it('sheet shows Open in Workspace link for workspace APIs', async () => {
		worker.use(
			http.get('/apis', () =>
				HttpResponse.json({
					data: [{ id: 'fresh.com', name: 'Fresh', source: 'local' }],
					total: 1,
					page: 1,
				}),
			),
			http.get('/catalog/fresh.com/operations', () =>
				HttpResponse.json({
					data: [],
					total: 0,
					truncated: false,
					offset: 0,
					limit: 25,
					spec_url: '',
					info: { title: 'Fresh', version: '1.0', description: '' },
					security_schemes: {},
				}),
			),
		);

		const user = userEvent.setup();
		renderDirectoryDiscover();
		const card = await screen.findByTestId('discovery-card-api');
		await user.click(within(card).getByRole('button'));

		const sheet = await screen.findByTestId('sheet-primitive');
		await waitFor(() => {
			expect(within(sheet).getByText(/Open in Workspace/)).toBeInTheDocument();
		});
		const link = within(sheet)
			.getByText(/Open in Workspace/)
			.closest('a');
		expect(link).toHaveAttribute('href', '/workspace/apis/fresh.com');
	});

	it('sheet shows Import to workspace button for directory APIs', async () => {
		worker.use(
			http.get('/apis', ({ request }) => {
				const url = new URL(request.url);
				const source = url.searchParams.get('source');
				// Resolve query: source=local&q=openai.com → not found in workspace
				if (source === 'local') {
					return HttpResponse.json({ data: [], total: 0, page: 1 });
				}
				return HttpResponse.json({
					data: [{ id: 'openai.com', name: 'OpenAI', source: 'catalog' }],
					total: 1,
					page: 1,
				});
			}),
			http.get('/catalog/openai.com/operations', () =>
				HttpResponse.json({
					data: [],
					total: 0,
					truncated: false,
					offset: 0,
					limit: 25,
					spec_url: 'https://example.com/openai.json',
					info: { title: 'OpenAI', version: '1.0', description: '' },
					security_schemes: {},
				}),
			),
		);

		const user = userEvent.setup();
		renderDirectoryDiscover();
		const card = await screen.findByTestId('discovery-card-api');
		await user.click(within(card).getByRole('button', { name: /openai/i }));

		const sheet = await screen.findByTestId('sheet-primitive');
		await waitFor(() => {
			expect(
				within(sheet).getByRole('button', { name: /import to workspace/i }),
			).toBeInTheDocument();
		});
		expect(within(sheet).getByTestId('sheet-directory-import')).toBeInTheDocument();
	});

	// Regression for the May 2027 sheet "imported" false-positive: the
	// resolver used to ask `/apis?source=local&q=<api_id>` and treat
	// `total > 0` as "imported", but `q=` is a substring filter on the
	// server. Opening the sheet for the catalog leaf `slack.com` would
	// match the imported sub-api `slack.com/openai` and flip the header
	// to the green "Imported" pill, hiding the import CTA. The fix
	// requires an exact-id match against the returned rows.
	it('sheet keeps the Available pill for a catalog leaf when only a path-style sibling is imported', async () => {
		worker.use(
			http.get('/apis', ({ request }) => {
				const url = new URL(request.url);
				const source = url.searchParams.get('source');
				const q = url.searchParams.get('q') ?? '';
				if (source === 'local') {
					// Server-side `q=slack.com` substring-matches the
					// imported sub-api. Pre-fix this fooled the sheet.
					if (q === 'slack.com') {
						return HttpResponse.json({
							data: [
								{
									id: 'slack.com/openai',
									name: 'Slack AI Plugin',
									source: 'local',
								},
							],
							total: 1,
							page: 1,
						});
					}
					return HttpResponse.json({ data: [], total: 0, page: 1 });
				}
				return HttpResponse.json({
					data: [{ id: 'slack.com', name: 'Slack', source: 'catalog' }],
					total: 1,
					page: 1,
				});
			}),
			http.get('/catalog/slack.com/operations', () =>
				HttpResponse.json({
					data: [],
					total: 0,
					truncated: false,
					offset: 0,
					limit: 25,
					spec_url: 'https://example.com/slack.json',
					info: { title: 'Slack', version: '1.0', description: '' },
					security_schemes: {},
				}),
			),
		);

		const user = userEvent.setup();
		renderDirectoryDiscover();
		const card = await screen.findByTestId('discovery-card-api');
		await user.click(within(card).getByRole('button', { name: /slack/i }));

		const sheet = await screen.findByTestId('sheet-primitive');
		// The header pill must remain "Available" — the import CTA is
		// the user-visible signal that the row is genuinely catalog-only.
		await waitFor(() => {
			expect(
				within(sheet).getByRole('button', { name: /import to workspace/i }),
			).toBeInTheDocument();
		});
		expect(within(sheet).getByText(/available/i)).toBeInTheDocument();
		expect(within(sheet).queryByText(/^imported$/i)).toBeNull();
	});

	it('clicking a directory API card opens the sheet and lazy-fetches the spec preview', async () => {
		const user = userEvent.setup();
		let previewCalled = 0;
		worker.use(
			http.get('/apis', () =>
				HttpResponse.json({
					data: [
						{
							id: 'plaid.com',
							name: 'plaid.com',
							source: 'catalog',
							_links: {
								github: 'https://github.com/jentic/jentic-public-apis/tree/main/apis/openapi/plaid.com',
							},
						},
					],
					total: 1,
					page: 1,
				}),
			),
			http.get('/catalog/plaid.com/operations', () => {
				previewCalled++;
				return HttpResponse.json({
					data: [
						{
							method: 'GET',
							path: '/accounts',
							summary: 'List accounts',
							description: 'Returns all accounts.',
							operation_id: 'listAccounts',
						},
					],
					total: 1,
					truncated: false,
					spec_url: 'https://example.com/plaid.json',
					info: { title: 'Plaid API', version: '1.0', description: 'Plaid OpenAPI spec' },
				});
			}),
		);

		renderDirectoryDiscover();

		const card = await screen.findByTestId('discovery-card-api');
		await user.click(within(card).getByRole('button', { name: /plaid/i }));

		const sheet = await screen.findByTestId('sheet-primitive');
		await waitFor(() => {
			expect(within(sheet).getByTestId('sheet-ops-list')).toBeInTheDocument();
		});
		expect(within(sheet).getByText('List accounts')).toBeInTheDocument();
		// Spec description should override the synthetic list-view one.
		expect(within(sheet).getByText('Plaid OpenAPI spec')).toBeInTheDocument();
		expect(previewCalled).toBe(1);
	});

	it('clicking a directory op row opens the directory inspect panel (no extra fetch)', async () => {
		// F8: directory operations should be inspectable too — parameters and
		// auth come from the same `previewCatalogOperations` payload, so the
		// detail view should NOT trigger a second round-trip.
		const user = userEvent.setup();
		let previewCalled = 0;
		worker.use(
			http.get('/apis', ({ request }) => {
				const url = new URL(request.url);
				if (url.searchParams.get('source') === 'local') {
					return HttpResponse.json({ data: [], total: 0, page: 1 });
				}
				return HttpResponse.json({
					data: [{ id: 'plaid.com', name: 'plaid.com', source: 'catalog' }],
					total: 1,
					page: 1,
				});
			}),
			http.get('/catalog/plaid.com/operations', () => {
				previewCalled++;
				return HttpResponse.json({
					data: [
						{
							method: 'GET',
							path: '/accounts/{account_id}',
							summary: 'Get account',
							description: 'Returns an account by id.',
							operation_id: 'getAccount',
							parameters: [
								{
									name: 'account_id',
									in: 'path',
									required: true,
									description: 'The account identifier',
								},
								{
									name: 'fields',
									in: 'query',
									required: false,
									description: 'Comma-separated field list',
								},
							],
							security: ['plaidClientAuth'],
						},
					],
					total: 1,
					truncated: false,
					spec_url: 'https://example.com/plaid.json',
					info: { title: 'Plaid API', version: '1.0', description: '' },
					security_schemes: {
						plaidClientAuth: {
							type: 'apiKey',
							in: 'header',
							name: 'PLAID-CLIENT-ID',
							description: 'Plaid client id header',
						},
					},
				});
			}),
			http.get('/catalog/plaid.com/workflows', () =>
				HttpResponse.json({
					data: [],
					total: 0,
					api_id: 'plaid.com',
					arazzo_url: null,
					github_url: null,
				}),
			),
		);

		renderDirectoryDiscover();
		const card = await screen.findByTestId('discovery-card-api');
		await user.click(within(card).getByRole('button', { name: /plaid/i }));

		const sheet = await screen.findByTestId('sheet-primitive');
		const row = await within(sheet).findByTestId('sheet-ops-row');
		await user.click(row);

		// Inspect panel renders from the SAME cached preview — no second fetch.
		const inspect = await within(sheet).findByTestId('sheet-directory-inspect');
		expect(within(inspect).getByText('Get account')).toBeInTheDocument();
		expect(within(inspect).getByText('account_id')).toBeInTheDocument();
		expect(within(inspect).getByText('fields')).toBeInTheDocument();
		expect(within(inspect).getByText('Required')).toBeInTheDocument();
		expect(within(inspect).getByText('plaidClientAuth')).toBeInTheDocument();
		// `Import to workspace` CTA replaces the upstream link as the forward
		// action (renamed from "Add credential" — see DiscoveryCard May
		// 2026). Now a <button> wired directly to `POST /import` rather
		// than a deep-link to the credential form, since "import" and
		// "set up credentials" are distinct intents.
		const importBtn = await within(inspect).findByRole('button', {
			name: /import to workspace/i,
		});
		expect(importBtn).toHaveAttribute('data-testid', 'sheet-directory-inspect-import');
		// The preview endpoint is called from SheetBody (ops list) and
		// DirectoryInspectPanel (drill-in detail) — both use it, but with
		// different React Query keys, so the handler fires twice.
		expect(previewCalled).toBe(2);
	});

	it('directory inspect back-button returns to the operations list', async () => {
		const user = userEvent.setup();
		worker.use(
			http.get('/apis', () =>
				HttpResponse.json({
					data: [{ id: 'plaid.com', name: 'plaid.com', source: 'catalog' }],
					total: 1,
					page: 1,
				}),
			),
			http.get('/catalog/plaid.com/operations', () =>
				HttpResponse.json({
					data: [
						{
							method: 'POST',
							path: '/items',
							summary: 'Create item',
							description: '',
							operation_id: 'createItem',
							parameters: [],
							security: [],
						},
					],
					total: 1,
					truncated: false,
					spec_url: '',
					info: { title: '', version: '', description: '' },
					security_schemes: {},
				}),
			),
		);

		renderDirectoryDiscover();
		const card = await screen.findByTestId('discovery-card-api');
		await user.click(within(card).getByRole('button', { name: /plaid/i }));
		const sheet = await screen.findByTestId('sheet-primitive');
		await user.click(await within(sheet).findByTestId('sheet-ops-row'));
		expect(await within(sheet).findByTestId('sheet-directory-inspect')).toBeInTheDocument();

		// Back arrow exits the inspect view back to the ops list.
		await user.click(within(sheet).getByRole('button', { name: /back to operations/i }));
		expect(await within(sheet).findByTestId('sheet-ops-list')).toBeInTheDocument();
		expect(within(sheet).queryByTestId('sheet-directory-inspect')).toBeNull();
	});

	it('?inspect=<api_id> on initial load opens the sheet (deep link)', async () => {
		worker.use(
			http.get('/apis', ({ request }) => {
				const url = new URL(request.url);
				// The resolve query sends q=stripe.com&source=local&limit=1
				const q = url.searchParams.get('q');
				if (q === 'stripe.com') {
					return HttpResponse.json({
						data: [{ id: 'stripe.com', name: 'Stripe', source: 'local' }],
						total: 1,
						page: 1,
					});
				}
				return HttpResponse.json({ data: [], total: 0, page: 1 });
			}),
			http.get('/catalog/stripe.com/operations', () =>
				HttpResponse.json({
					data: [
						{
							method: 'GET',
							path: '/v1/customers',
							summary: 'List customers',
							description: '',
							operation_id: 'listCustomers',
							parameters: [],
							security: [],
							tags: [],
						},
					],
					total: 1,
					truncated: false,
					offset: 0,
					limit: 25,
					spec_url: '',
					info: { title: 'Stripe', version: '1.0', description: '' },
					security_schemes: {},
				}),
			),
		);

		renderDirectoryDiscover('/discover?inspect=stripe.com');

		const sheet = await screen.findByTestId('sheet-primitive');
		await waitFor(() => {
			expect(within(sheet).getByText('List customers')).toBeInTheDocument();
		});
	});

	it('clicking the close button collapses the sheet', async () => {
		const user = userEvent.setup();
		worker.use(
			http.get('/apis', () =>
				HttpResponse.json({
					data: [{ id: 'stripe.com', name: 'Stripe', source: 'local' }],
					total: 1,
					page: 1,
				}),
			),
			http.get('/catalog/stripe.com/operations', () =>
				HttpResponse.json({
					data: [],
					total: 0,
					truncated: false,
					offset: 0,
					limit: 25,
					spec_url: '',
					info: { title: 'Stripe', version: '1.0', description: '' },
					security_schemes: {},
				}),
			),
		);

		renderDirectoryDiscover();

		const card = await screen.findByTestId('discovery-card-api');
		await user.click(within(card).getByRole('button'));

		const sheet = await screen.findByTestId('sheet-primitive');
		await waitFor(() => {
			expect(within(sheet).getByText(/no operations found/i)).toBeInTheDocument();
		});

		const closeButton = within(sheet).getByRole('button', { name: /close detail panel/i });
		await user.click(closeButton);

		await waitFor(
			() => {
				expect(screen.queryByTestId('sheet-primitive')).toBeNull();
			},
			{ timeout: 3000 },
		);
	});

	it('clicking inline "Import" on a directory card does NOT open the sheet', async () => {
		const user = userEvent.setup();
		// Stub the import flow so the mutation resolves cleanly. The
		// shape mirrors `POST /import`'s contract (`{results: [...]}`).
		// Without `getCatalogEntry` stubbed we'd hit the fallback path
		// because the test's catalog row has no `spec_url` field.
		worker.use(
			http.get('/apis', () =>
				HttpResponse.json({
					data: [
						{
							id: 'plaid.com',
							name: 'plaid.com',
							source: 'catalog',
							spec_url: 'https://example.com/plaid.json',
						},
					],
					total: 1,
					page: 1,
				}),
			),
			http.post('/import', () =>
				HttpResponse.json({ results: [{ status: 'success', api_id: 'plaid.com' }] }),
			),
		);

		renderDirectoryDiscover();

		const card = await screen.findByTestId('discovery-card-api');
		const importBtn = within(card).getByRole('button', { name: /^import$/i });

		// The CTA must `stopPropagation` so the card's outer onClick
		// (which opens the sheet) does NOT also fire.
		await user.click(importBtn);

		// Sheet must not have opened. (No URL assertion — MemoryRouter
		// state isn't reflected in window.location.)
		expect(screen.queryByTestId('sheet-primitive')).toBeNull();
	});

	it('clicking an operation row in the workspace sheet drills into the inspect view', async () => {
		const user = userEvent.setup();
		worker.use(
			http.get('/apis', () =>
				HttpResponse.json({
					data: [{ id: 'stripe.com', name: 'Stripe', source: 'local' }],
					total: 1,
					page: 1,
				}),
			),
			http.get('/catalog/stripe.com/operations', () =>
				HttpResponse.json({
					data: [
						{
							method: 'GET',
							path: '/v1/customers',
							summary: 'List customers',
							description: 'Returns a list of customers from your Stripe account.',
							operation_id: 'listCustomers',
							parameters: [
								{
									name: 'limit',
									in: 'query',
									required: false,
									description: 'Page size cap',
								},
								{
									name: 'starting_after',
									in: 'query',
									required: false,
									description: 'Cursor for pagination',
								},
							],
							security: ['BasicAuth'],
							tags: [],
						},
					],
					total: 1,
					truncated: false,
					offset: 0,
					limit: 25,
					spec_url: '',
					info: { title: 'Stripe', version: '1.0', description: '' },
					security_schemes: {
						BasicAuth: {
							type: 'http',
							scheme: 'basic',
							description: 'Set header Authorization: Basic <credentials>',
						},
					},
				}),
			),
		);

		renderDirectoryDiscover();
		await user.click(
			within(await screen.findByTestId('discovery-card-api')).getByRole('button'),
		);

		const sheet = await screen.findByTestId('sheet-primitive');
		const row = await within(sheet).findByText('List customers');
		await user.click(row);

		await waitFor(() => {
			expect(within(sheet).getByTestId('sheet-directory-inspect')).toBeInTheDocument();
		});
		expect(
			within(sheet).getByRole('button', { name: /back to operations/i }),
		).toBeInTheDocument();

		const inspect = within(sheet).getByTestId('sheet-directory-inspect');
		expect(within(inspect).getByText('limit')).toBeInTheDocument();
		expect(within(inspect).getByText('starting_after')).toBeInTheDocument();
		expect(within(inspect).getByText('BasicAuth')).toBeInTheDocument();
		expect(within(inspect).getByText('/v1/customers')).toBeInTheDocument();
	});

	it('strips the legacy ?type=importable URL param and still renders results', async () => {
		worker.use(
			http.get('/apis', () =>
				HttpResponse.json({
					data: [{ id: 'stripe.com', name: 'Stripe', source: 'local' }],
					total: 1,
					page: 1,
				}),
			),
		);

		renderDirectoryDiscover('/discover?type=importable');

		// `?type=` was removed entirely in the May 2026 simplification —
		// the URL fixup `useEffect` strips it. Results still render.
		await waitFor(() => expect(screen.getByText('Stripe')).toBeInTheDocument());
		await waitFor(() => {
			expect(window.location.search).not.toContain('type=');
		});
	});

	// ── P9-fe: virtualise + load-more in sheet ────────────────────────────────

	it('directory sheet renders Load more for paginated specs and appends ops', async () => {
		const user = userEvent.setup();

		// Mock a 60-op spec — server pages 25 at a time. The sheet should
		// render the first 25 + a "Load more" footer; clicking it should
		// fetch the next page and append.
		const allOps = Array.from({ length: 60 }).map((_, i) => ({
			method: 'GET',
			path: `/items/${i}`,
			summary: `Get item ${i}`,
			operation_id: `getItem${i}`,
			parameters: [],
			security: [],
			tags: i < 30 ? ['accounts'] : ['transactions'],
		}));

		worker.use(
			http.get('/apis', () =>
				HttpResponse.json({
					data: [{ id: 'big.com', name: 'Big API', source: 'catalog' }],
					total: 1,
					page: 1,
				}),
			),
			http.get('/catalog/big.com/operations', ({ request }) => {
				const url = new URL(request.url);
				const offset = parseInt(url.searchParams.get('offset') ?? '0', 10);
				const limit = parseInt(url.searchParams.get('limit') ?? '25', 10);
				const slice = allOps.slice(offset, offset + limit);
				return HttpResponse.json({
					data: slice,
					total: allOps.length,
					truncated: offset + limit < allOps.length,
					offset,
					limit,
					spec_url: 'https://example.com/big.json',
					info: { title: 'Big API', version: '1.0', description: '' },
					security_schemes: {},
				});
			}),
		);

		renderDirectoryDiscover();
		const card = await screen.findByTestId('discovery-card-api');
		await user.click(within(card).getByRole('button', { name: /big api/i }));

		const sheet = await screen.findByTestId('sheet-primitive');

		// First page renders 25 rows + footer says "Showing 25 of 60".
		await waitFor(() => {
			const rows = within(sheet).getAllByTestId('sheet-ops-row');
			expect(rows).toHaveLength(25);
		});
		expect(within(sheet).getByText(/Showing 25 of 60/)).toBeInTheDocument();

		// Click "Load more" → next 25 rows append.
		await user.click(within(sheet).getByTestId('ops-load-more'));
		await waitFor(() => {
			const rows = within(sheet).getAllByTestId('sheet-ops-row');
			expect(rows).toHaveLength(50);
		});
		expect(within(sheet).getByText(/Showing 50 of 60/)).toBeInTheDocument();
	});

	it('directory sheet renders tag chips and filters in-place when clicked', async () => {
		const user = userEvent.setup();

		const ops = [
			{
				method: 'GET',
				path: '/customers',
				summary: 'List customers',
				operation_id: 'listCustomers',
				parameters: [],
				security: [],
				tags: ['customers'],
			},
			{
				method: 'POST',
				path: '/charges',
				summary: 'Create charge',
				operation_id: 'createCharge',
				parameters: [],
				security: [],
				tags: ['charges'],
			},
			{
				method: 'GET',
				path: '/charges/{id}',
				summary: 'Get charge',
				operation_id: 'getCharge',
				parameters: [],
				security: [],
				tags: ['charges'],
			},
			{
				method: 'GET',
				path: '/refunds',
				summary: 'List refunds',
				operation_id: 'listRefunds',
				parameters: [],
				security: [],
				tags: ['refunds'],
			},
			{
				method: 'POST',
				path: '/refunds',
				summary: 'Create refund',
				operation_id: 'createRefund',
				parameters: [],
				security: [],
				tags: ['refunds'],
			},
			{
				method: 'GET',
				path: '/disputes',
				summary: 'List disputes',
				operation_id: 'listDisputes',
				parameters: [],
				security: [],
				tags: ['disputes'],
			},
		];

		worker.use(
			http.get('/apis', () =>
				HttpResponse.json({
					data: [{ id: 'tagged.com', name: 'Tagged API', source: 'catalog' }],
					total: 1,
					page: 1,
				}),
			),
			http.get('/catalog/tagged.com/operations', () =>
				HttpResponse.json({
					data: ops,
					total: ops.length,
					truncated: false,
					offset: 0,
					limit: 25,
					spec_url: 'https://example.com/tagged.json',
					info: { title: 'Tagged API', version: '1.0', description: '' },
					security_schemes: {},
				}),
			),
		);

		renderDirectoryDiscover();
		const card = await screen.findByTestId('discovery-card-api');
		await user.click(within(card).getByRole('button', { name: /tagged api/i }));

		const sheet = await screen.findByTestId('sheet-primitive');
		await within(sheet).findByText('List customers');

		// Tag chips render — at least 4 unique tags + the "All" chip.
		const tagBar = await within(sheet).findByTestId('ops-tag-bar');
		const chips = within(tagBar).getAllByTestId('ops-tag-chip');
		// "charges" and "refunds" are most-frequent so render first.
		const chipLabels = chips.map((c) => c.textContent);
		expect(chipLabels).toContain('charges');
		expect(chipLabels).toContain('refunds');

		// Click "charges" → only the 2 charges rows visible.
		const chargesChip = chips.find((c) => c.textContent === 'charges');
		await user.click(chargesChip!);
		await waitFor(() => {
			const visibleRows = within(sheet).getAllByTestId('sheet-ops-row');
			expect(visibleRows).toHaveLength(2);
		});
		expect(within(sheet).getByText('Create charge')).toBeInTheDocument();
		expect(within(sheet).getByText('Get charge')).toBeInTheDocument();
		expect(within(sheet).queryByText('List customers')).toBeNull();

		// "All" resets the filter.
		await user.click(within(tagBar).getByText('All'));
		await waitFor(() => {
			const visibleRows = within(sheet).getAllByTestId('sheet-ops-row');
			expect(visibleRows).toHaveLength(6);
		});
	});

	it('directory sheet inline filter narrows ops by summary / path', async () => {
		const user = userEvent.setup();

		// Need >5 ops for the filter input to render.
		const ops = Array.from({ length: 6 }).map((_, i) => ({
			method: 'GET',
			path: `/items/${i}`,
			summary: i === 3 ? 'Find a needle' : `Boring item ${i}`,
			operation_id: `op${i}`,
			parameters: [],
			security: [],
			tags: [],
		}));

		worker.use(
			http.get('/apis', () =>
				HttpResponse.json({
					data: [{ id: 'haystack.com', name: 'Haystack API', source: 'catalog' }],
					total: 1,
					page: 1,
				}),
			),
			http.get('/catalog/haystack.com/operations', () =>
				HttpResponse.json({
					data: ops,
					total: ops.length,
					truncated: false,
					offset: 0,
					limit: 25,
					spec_url: 'https://example.com/haystack.json',
					info: { title: 'Haystack API', version: '1.0', description: '' },
					security_schemes: {},
				}),
			),
		);

		renderDirectoryDiscover();
		const card = await screen.findByTestId('discovery-card-api');
		await user.click(within(card).getByRole('button', { name: /haystack api/i }));

		const sheet = await screen.findByTestId('sheet-primitive');
		await within(sheet).findByText('Find a needle');

		const input = within(sheet).getByTestId('ops-filter-input');
		await user.type(input, 'needle');
		await waitFor(() => {
			const visibleRows = within(sheet).getAllByTestId('sheet-ops-row');
			expect(visibleRows).toHaveLength(1);
		});
		expect(within(sheet).getByText('Find a needle')).toBeInTheDocument();
	});

	// ── P4: API summary in sheet ──────────────────────────────────────────────

	it('directory sheet renders the spec info.description as an API summary', async () => {
		const user = userEvent.setup();
		worker.use(
			http.get('/apis', () =>
				HttpResponse.json({
					data: [{ id: 'doc.com', name: 'Doc API', source: 'catalog' }],
					total: 1,
					page: 1,
				}),
			),
			http.get('/catalog/doc.com/operations', () =>
				HttpResponse.json({
					data: [],
					total: 0,
					truncated: false,
					offset: 0,
					limit: 25,
					spec_url: 'https://example.com/doc.json',
					info: {
						title: 'Doc API',
						version: '1.0',
						description: 'A **bot-free** API for fetching `documents` and metadata.',
					},
					security_schemes: {},
				}),
			),
		);

		renderDirectoryDiscover();
		const card = await screen.findByTestId('discovery-card-api');
		await user.click(within(card).getByRole('button', { name: /doc api/i }));

		const sheet = await screen.findByTestId('sheet-primitive');
		await waitFor(() => {
			const summary = within(sheet).getByTestId('api-summary');
			expect(summary).toHaveTextContent(/bot-free.*API for fetching.*documents.*metadata/i);
		});
		const summary = within(sheet).getByTestId('api-summary');
		// Markdown actually rendered the bold span as <strong>.
		expect(within(summary).getByText('bot-free').tagName.toLowerCase()).toBe('strong');
	});

	it('workspace sheet falls back to host + op count when no description', async () => {
		const user = userEvent.setup();
		worker.use(
			http.get('/apis', () =>
				HttpResponse.json({
					data: [{ id: 'bare.com', name: 'Bare API', source: 'local' }],
					total: 1,
					page: 1,
				}),
			),
			http.get('/catalog/bare.com/operations', () =>
				HttpResponse.json({
					data: [
						{
							method: 'GET',
							path: '/x',
							summary: 'X',
							operation_id: 'x',
							parameters: [],
							security: [],
							tags: ['core'],
						},
						{
							method: 'GET',
							path: '/y',
							summary: 'Y',
							operation_id: 'y',
							parameters: [],
							security: [],
							tags: ['core'],
						},
						{
							method: 'GET',
							path: '/z',
							summary: 'Z',
							operation_id: 'z',
							parameters: [],
							security: [],
							tags: ['admin'],
						},
					],
					total: 3,
					truncated: false,
					offset: 0,
					limit: 25,
					spec_url: '',
					info: { title: 'Bare API', version: '1.0', description: null },
					security_schemes: {},
				}),
			),
		);

		renderDirectoryDiscover();
		const card = await screen.findByTestId('discovery-card-api');
		await user.click(within(card).getByRole('button'));

		const sheet = await screen.findByTestId('sheet-primitive');
		// Fallback shape: "<host> — N operations across M tags"
		// Wait for the operation count to load — `previewCatalogOperations`
		// returns asynchronously so the summary first renders with just the host.
		await waitFor(() => {
			const summary = within(sheet).getByTestId('api-summary');
			expect(summary).toHaveTextContent(/bare\.com.*3 operations across 2 tags/);
		});
	});

	it('long descriptions get a Show more / Show less toggle', async () => {
		const user = userEvent.setup();
		const longDesc = 'Lorem ipsum dolor sit amet. '.repeat(20); // ~540 chars
		worker.use(
			http.get('/apis', () =>
				HttpResponse.json({
					data: [{ id: 'long.com', name: 'Long API', source: 'catalog' }],
					total: 1,
					page: 1,
				}),
			),
			http.get('/catalog/long.com/operations', () =>
				HttpResponse.json({
					data: [],
					total: 0,
					truncated: false,
					offset: 0,
					limit: 25,
					spec_url: 'https://example.com/long.json',
					info: { title: 'Long API', version: '1.0', description: longDesc },
					security_schemes: {},
				}),
			),
		);

		renderDirectoryDiscover();
		const card = await screen.findByTestId('discovery-card-api');
		await user.click(within(card).getByRole('button', { name: /long api/i }));

		const sheet = await screen.findByTestId('sheet-primitive');
		// Wait for the description to be loaded (fallback renders first).
		await waitFor(() => {
			const summary = within(sheet).getByTestId('api-summary');
			expect((summary.textContent ?? '').length).toBeGreaterThan(100);
		});
		const summary = within(sheet).getByTestId('api-summary');
		const toggle = within(sheet).getByTestId('api-summary-toggle');
		expect(toggle).toHaveTextContent('Show more');
		// Truncated form contains the ellipsis sentinel; the rendered
		// description ends in `…` (the toggle button is a sibling).
		expect(summary.textContent ?? '').toMatch(/…/);

		await user.click(toggle);
		expect(toggle).toHaveTextContent('Show less');
		// Expanded — full description no longer ends in the ellipsis.
		// (The full text contains 20 sentence repeats, so check length.)
		expect((summary.textContent ?? '').length).toBeGreaterThan(500);
	});

	// ── P3 — Sheet cross-API navigation (recents + history) ────────────────

	it('shows the recents strip after inspecting two distinct APIs', async () => {
		const user = userEvent.setup();
		// Make sure the store is clean for this test.
		window.sessionStorage.clear();
		worker.use(
			http.get('/apis', () =>
				HttpResponse.json({
					data: [
						{ id: 'stripe.com', name: 'Stripe', source: 'local' },
						{ id: 'github.com', name: 'GitHub', source: 'local' },
					],
					total: 2,
					page: 1,
				}),
			),
			http.get('/catalog/:id/operations', () =>
				HttpResponse.json({
					data: [
						{
							method: 'GET',
							path: '/v1/y',
							summary: 'Some op',
							description: '',
							operation_id: 'someOp',
							parameters: [],
							security: [],
							tags: [],
						},
					],
					total: 1,
					truncated: false,
					offset: 0,
					limit: 25,
					spec_url: '',
					info: { title: '', version: '1.0', description: '' },
					security_schemes: {},
				}),
			),
		);

		renderDirectoryDiscover();
		// Open Stripe.
		const stripeCard = await screen.findByText('Stripe');
		await user.click(stripeCard.closest('button')!);
		const sheet = await screen.findByTestId('sheet-primitive');
		await waitFor(() => expect(within(sheet).getByText(/some op/i)).toBeInTheDocument());

		// Recents strip should be hidden with only one entry.
		expect(within(sheet).queryByTestId('sheet-recents-strip')).toBeNull();

		// Close, open GitHub.
		await user.click(within(sheet).getByRole('button', { name: /close/i }));
		await waitFor(() => expect(screen.queryByTestId('sheet-primitive')).toBeNull(), {
			timeout: 3000,
		});

		const githubCard = await screen.findByText('GitHub');
		await user.click(githubCard.closest('button')!);
		const sheet2 = await screen.findByTestId('sheet-primitive');
		await waitFor(() => expect(within(sheet2).getByText(/some op/i)).toBeInTheDocument());

		// Strip now has both — Stripe is selectable, GitHub is current.
		const strip = await within(sheet2).findByTestId('sheet-recents-strip');
		expect(within(strip).getByRole('button', { name: /stripe/i })).toBeInTheDocument();

		// Click Stripe chip → sheet swaps title to Stripe.
		await user.click(within(strip).getByRole('button', { name: /stripe/i }));
		await waitFor(() => {
			const heading = within(sheet2).getByRole('heading', { level: 2 });
			expect(heading.textContent).toMatch(/stripe/i);
		});
	});

	// ── P6 — Density toggle (removed in May 2026) ─────────────────────────
	// The list/grid density toggle was removed entirely; only the grid
	// remains. Tests that exercised the toggle and the `?view=list` URL
	// param have been deleted.

	// ── P7 — Keyboard ergonomics ──────────────────────────────────────────

	it('typing "/" focuses the search input', async () => {
		const user = userEvent.setup();
		worker.use(
			http.get('/apis', () =>
				HttpResponse.json({
					data: [{ id: 'stripe.com', name: 'Stripe', source: 'local' }],
					total: 1,
					page: 1,
				}),
			),
		);
		renderDirectoryDiscover();
		await screen.findByText('Stripe');
		// Focus on body, then press `/`.
		document.body.focus();
		(document.activeElement as HTMLElement | null)?.blur?.();
		await user.keyboard('/');
		const searchInput = screen.getByRole('searchbox', { name: /search/i });
		expect(document.activeElement).toBe(searchInput);
	});

	// `?` (open keyboard-shortcuts help) is owned by `<PageHelp>` mounted on
	// the page shells (`/workspace`, `/discover`), not by `DiscoveryView`.
	// That binding is exercised in the per-page shell tests instead.

	// ── P8 — Credential close-the-loop ────────────────────────────────────

	it('emits a success toast when a credentialImported event arrives for the open sheet', async () => {
		const { emitCredentialImported } = await import('@/lib/events/credentialImported');
		const toastModule = await import('@/components/ui/toastStore');
		toastModule.clearAllToasts();

		worker.use(
			http.get('/apis', ({ request }) => {
				const url = new URL(request.url);
				const q = url.searchParams.get('q');
				if (q === 'stripe.com') {
					return HttpResponse.json({
						data: [{ id: 'stripe.com', name: 'Stripe', source: 'local' }],
						total: 1,
						page: 1,
					});
				}
				return HttpResponse.json({
					data: [{ id: 'stripe.com', name: 'Stripe', source: 'local' }],
					total: 1,
					page: 1,
				});
			}),
			http.get('/catalog/stripe.com/operations', () =>
				HttpResponse.json({
					data: [],
					total: 0,
					truncated: false,
					offset: 0,
					limit: 25,
					spec_url: '',
					info: { title: 'Stripe', version: '1.0', description: '' },
					security_schemes: {},
				}),
			),
		);
		renderDirectoryDiscover('/discover?inspect=stripe.com');
		await screen.findByTestId('sheet-primitive');

		// Probe component to read the store via the hook.
		let liveToasts: ReturnType<typeof toastModule.useToasts> = [];
		function Probe() {
			liveToasts = toastModule.useToasts();
			return null;
		}
		const { render } = await import('@testing-library/react');
		render(<Probe />);

		emitCredentialImported({ api_id: 'stripe.com' });

		await waitFor(() => {
			expect(liveToasts.length).toBeGreaterThan(0);
		});
		expect(liveToasts[0].title).toMatch(/imported to workspace/i);
		expect(liveToasts[0].variant).toBe('success');
	});
});

// ── Sectioned mode (used by /workspace) ──────────────────────────────────────
//
// The same DiscoveryView component, mounted with `mode="sectioned"`, must
// render two parallel sections (workspace + catalog) in browse mode and
// collapse to a single search feed in search mode.

describe('DiscoveryView (sectioned)', () => {
	function renderSectioned(route = '/workspace') {
		return renderWithProviders(<DiscoveryView mode="sectioned" />, {
			route,
			path: '/workspace',
		});
	}

	it('renders both section headers in browse mode', async () => {
		worker.use(http.get('/apis', () => HttpResponse.json({ data: [], total: 0, page: 1 })));
		renderSectioned();

		expect(await screen.findByTestId('discovery-section-your-workspace')).toBeInTheDocument();
		expect(screen.getByTestId('discovery-section-from-the-catalog')).toBeInTheDocument();
	});

	it('issues two parallel /apis requests, one per section, with distinct source params', async () => {
		const seenSources: (string | null)[] = [];
		worker.use(
			http.get('/apis', ({ request }) => {
				const url = new URL(request.url);
				seenSources.push(url.searchParams.get('source'));
				return HttpResponse.json({ data: [], total: 0, page: 1 });
			}),
		);
		renderSectioned();
		await screen.findByTestId('discovery-section-your-workspace');
		await waitFor(() => {
			expect(seenSources).toContain('local');
			expect(seenSources).toContain('catalog');
		});
	});

	it('routes "Browse all in Discover" to /discover', async () => {
		worker.use(http.get('/apis', () => HttpResponse.json({ data: [], total: 0, page: 1 })));
		renderSectioned();
		const link = await screen.findByTestId('browse-all-discover');
		expect(link).toHaveAttribute('href', '/discover');
	});

	it('shows an inline cold-start notice in the workspace section when empty (and keeps the catalog section visible)', async () => {
		worker.use(
			http.get('/apis', ({ request }) => {
				const url = new URL(request.url);
				const source = url.searchParams.get('source');
				if (source === 'local') {
					return HttpResponse.json({ data: [], total: 0, page: 1 });
				}
				return HttpResponse.json({
					data: [{ id: 'github.com', name: 'GitHub', source: 'catalog' }],
					total: 1,
					page: 1,
				});
			}),
		);
		renderSectioned();
		// Inline cold-start in the workspace section.
		expect(
			await screen.findByTestId('discover-empty-cold-start-sectioned'),
		).toBeInTheDocument();
		// Catalog section still rendering its row.
		const catalogSection = screen.getByTestId('discovery-section-from-the-catalog');
		expect(await within(catalogSection).findByText('GitHub')).toBeInTheDocument();
	});

	it('collapses to a single search feed when ?q is non-empty', async () => {
		const user = userEvent.setup();
		worker.use(
			http.get('/apis', ({ request }) => {
				const url = new URL(request.url);
				const q = url.searchParams.get('q');
				if (q) {
					return HttpResponse.json({
						data: [{ id: 'stripe.com', name: 'Stripe', source: 'catalog' }],
						total: 1,
						page: 1,
					});
				}
				return HttpResponse.json({ data: [], total: 0, page: 1 });
			}),
		);
		renderSectioned();
		await screen.findByTestId('discovery-section-your-workspace');

		await user.type(screen.getByLabelText(/search apis/i), 'stripe');
		await waitFor(() => {
			expect(screen.queryByTestId('discovery-section-your-workspace')).toBeNull();
		});
		expect(screen.queryByTestId('discovery-section-from-the-catalog')).toBeNull();
		// The browse grid is rendered with search results.
		await waitFor(() => {
			expect(screen.getByText('Stripe')).toBeInTheDocument();
		});
	});
});
