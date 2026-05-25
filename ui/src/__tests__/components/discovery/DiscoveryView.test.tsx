import { http, HttpResponse } from 'msw';
import { within } from '@testing-library/react';
import { screen, waitFor, renderWithProviders, userEvent } from '../../test-utils';
import { worker } from '../../mocks/browser';
import { DiscoveryView } from '@/components/discovery';

function renderDiscover(route = '/catalog') {
	return renderWithProviders(<DiscoveryView />, { route, path: '/catalog' });
}

describe('DiscoveryView', () => {
	// ── Heading + base chrome ────────────────────────────────────────────────

	it('renders the sticky toolbar and filter bar with default Type segments', async () => {
		renderDiscover();
		expect(await screen.findByTestId('discover-toolbar')).toBeInTheDocument();
		expect(screen.getByTestId('discovery-filter-bar')).toBeInTheDocument();
		expect(screen.getByRole('textbox', { name: /search/i })).toBeInTheDocument();
		// Browse mode segments are just APIs + Workflows (no All, no Endpoints,
		// no Importable — importable was removed entirely from the UI vocab).
		const filterBar = screen.getByTestId('discovery-filter-bar');
		expect(within(filterBar).getByRole('button', { name: 'APIs' })).toBeInTheDocument();
		expect(within(filterBar).getByRole('button', { name: 'Workflows' })).toBeInTheDocument();
		expect(within(filterBar).queryByRole('button', { name: /^endpoints$/i })).toBeNull();
		expect(within(filterBar).queryByRole('button', { name: /^importable$/i })).toBeNull();
	});

	// ── Browse mode (grid, APIs default) ─────────────────────────────────────

	it('defaults to APIs in browse mode and lists workspace + directory together', async () => {
		// `GET /apis` (no source param) returns the server-side blended list.
		worker.use(
			http.get('/apis', () =>
				HttpResponse.json({
					data: [
						{
							id: 'stripe-api',
							name: 'Stripe',
							source: 'local',
							has_credentials: true,
						},
						{ id: 'github.com', name: 'github.com', source: 'catalog' },
					],
					total: 2,
					page: 1,
				}),
			),
		);

		renderDiscover();

		await waitFor(() => expect(screen.getByText('Stripe')).toBeInTheDocument());
		expect(screen.getByText('github.com')).toBeInTheDocument();
		// Workflow query should NOT have fired in default browse mode.
		expect(screen.queryByText(/no workflows to show/i)).toBeNull();
	});

	it('switches to workflows when the Workflows Type segment is picked', async () => {
		const user = userEvent.setup();
		worker.use(
			http.get('/apis', () => HttpResponse.json({ data: [], total: 0, page: 1 })),
			http.get('/workflows', () =>
				HttpResponse.json([{ id: 'wf-1', name: 'My Workflow', description: 'does stuff' }]),
			),
		);

		renderDiscover();
		await screen.findByTestId('discover-toolbar');

		await user.click(screen.getByRole('button', { name: 'Workflows' }));

		await waitFor(() => expect(screen.getByText('My Workflow')).toBeInTheDocument());
	});

	it('Workspace source narrows to the workspace slice (uses /apis?source=local)', async () => {
		const user = userEvent.setup();
		let lastApisCall: URL | null = null;
		worker.use(
			http.get('/apis', ({ request }) => {
				lastApisCall = new URL(request.url);
				const source = lastApisCall.searchParams.get('source');
				const data =
					source === 'local'
						? [{ id: 'stripe-api', name: 'Stripe', source: 'local' }]
						: [
								{ id: 'stripe-api', name: 'Stripe', source: 'local' },
								{ id: 'github.com', name: 'github.com', source: 'catalog' },
							];
				return HttpResponse.json({ data, total: data.length, page: 1 });
			}),
		);

		renderDiscover();
		await waitFor(() => expect(screen.getByText('Stripe')).toBeInTheDocument());
		expect(screen.getByText('github.com')).toBeInTheDocument();

		await user.click(screen.getByRole('button', { name: /my workspace/i }));

		await waitFor(() => {
			expect(screen.queryByText('github.com')).not.toBeInTheDocument();
		});
		expect(lastApisCall).not.toBeNull();
		expect((lastApisCall as unknown as URL).searchParams.get('source')).toBe('local');
	});

	// ── Search mode ──────────────────────────────────────────────────────────

	it('groups search results into Endpoints / Workflows / APIs sections', async () => {
		const user = userEvent.setup();
		worker.use(
			http.get('/search', () =>
				HttpResponse.json([
					{
						id: 'GET/api.stripe.com/v1/customers',
						type: 'operation',
						source: 'local',
						summary: 'List customers',
						score: 0.92,
					},
					{
						id: 'wf-stripe-checkout',
						type: 'workflow',
						source: 'local',
						summary: 'Stripe Checkout Flow',
						score: 0.7,
					},
					{
						id: 'plaid.com',
						type: 'catalog_api',
						source: 'catalog',
						api_id: 'plaid.com',
						summary: 'plaid.com — available in Jentic public catalog',
						score: 0,
					},
				]),
			),
		);

		renderDiscover();
		const input = screen.getByRole('textbox', { name: /search/i });
		await user.type(input, 'stripe');

		await waitFor(() => {
			expect(screen.getByTestId('search-section-endpoint')).toBeInTheDocument();
		});
		expect(screen.getByTestId('search-section-workflow')).toBeInTheDocument();
		expect(screen.getByTestId('search-section-api')).toBeInTheDocument();
		expect(screen.getByText('List customers')).toBeInTheDocument();
		expect(screen.getByText('Stripe Checkout Flow')).toBeInTheDocument();
		expect(screen.getByText('plaid.com')).toBeInTheDocument();
	});

	it('catalog_api search results render as ApiCards with source=directory', async () => {
		const user = userEvent.setup();
		worker.use(
			http.get('/search', () =>
				HttpResponse.json([
					{
						id: 'plaid.com',
						type: 'catalog_api',
						source: 'catalog',
						api_id: 'plaid.com',
						summary: 'plaid.com — available in Jentic public catalog',
						score: 0,
					},
				]),
			),
		);

		renderDiscover();
		await user.type(screen.getByRole('textbox', { name: /search/i }), 'plaid');

		const card = await screen.findByTestId('discovery-card-api');
		expect(card).toBeInTheDocument();
		// Directory source pill is present (exact-match avoids colliding with
		// the longer description copy that also contains "directory").
		expect(within(card).getByText('Directory')).toBeInTheDocument();
		// The standalone "Available to import" pill no longer exists anywhere
		// — it's been folded into the regular API surface.
		expect(within(card).queryByText(/available to import/i)).toBeNull();
	});

	it('catalog_workflow_source search results render as WorkflowCards (not ApiCards)', async () => {
		// Regression for May 2026 screenshot review: searching "openai" showed
		// a `catalog_workflow_source` row at the bottom rendered with the API
		// card chrome ("API" + "Directory" pills) even though the summary text
		// clearly described workflows. Map it to type=workflow instead so the
		// chrome matches the content.
		const user = userEvent.setup();
		worker.use(
			http.get('/search', () =>
				HttpResponse.json([
					{
						id: 'catalog:workflows:openai.com',
						type: 'catalog_workflow_source',
						source: 'catalog',
						api_id: 'openai.com',
						summary: 'openai.com workflows — available in Jentic public catalog',
						description:
							'Multi-step Arazzo workflows for openai.com. Add credentials to import.',
						score: 0,
					},
				]),
			),
		);

		renderDiscover();
		await user.type(screen.getByRole('textbox', { name: /search/i }), 'openai');

		const card = await screen.findByTestId('discovery-card-workflow');
		expect(card).toBeInTheDocument();
		// Workflow chrome shows the "Workflow" pill — NOT the "API" pill.
		expect(within(card).getByText('Workflow')).toBeInTheDocument();
		expect(within(card).queryByText('API')).toBeNull();
		// No API card should be created for this row.
		expect(screen.queryByTestId('discovery-card-api')).toBeNull();
	});

	it('Type=Endpoints narrows search to only the endpoint section', async () => {
		const user = userEvent.setup();
		// Server-side narrowing post-P5: when ?type=endpoint is passed the
		// backend filters out workflow rows itself. We simulate that here so
		// the test reflects production wire behaviour rather than the now-
		// removed client-side filter.
		worker.use(
			http.get('/search', ({ request }) => {
				const typeParam = new URL(request.url).searchParams.get('type');
				const all = [
					{
						id: 'GET/api.stripe.com/v1/charges',
						type: 'operation',
						source: 'local',
						summary: 'List charges',
						score: 0.88,
					},
					{
						id: 'wf-stripe-checkout',
						type: 'workflow',
						source: 'local',
						summary: 'Stripe Checkout Flow',
						score: 0.7,
					},
				];
				const filtered =
					typeParam === 'endpoint'
						? all.filter((r) => r.type === 'operation')
						: typeParam === 'workflow'
							? all.filter((r) => r.type === 'workflow')
							: all;
				return HttpResponse.json(filtered);
			}),
		);

		renderDiscover();
		const input = screen.getByRole('textbox', { name: /search/i });
		await user.type(input, 'stripe');

		await waitFor(() => expect(screen.getByText('List charges')).toBeInTheDocument());
		expect(screen.getByText('Stripe Checkout Flow')).toBeInTheDocument();

		const filterBar = screen.getByTestId('discovery-filter-bar');
		await user.click(within(filterBar).getByRole('button', { name: 'Endpoints' }));

		await waitFor(() => {
			expect(screen.queryByText('Stripe Checkout Flow')).not.toBeInTheDocument();
		});
		expect(screen.getByText('List charges')).toBeInTheDocument();
	});

	// ── Backward-compat ──────────────────────────────────────────────────────

	it('treats legacy ?type=operation as Endpoints in search mode', async () => {
		const user = userEvent.setup();
		worker.use(
			http.get('/search', () =>
				HttpResponse.json([
					{
						id: 'GET/api.stripe.com/v1/charges',
						type: 'operation',
						source: 'local',
						summary: 'List charges',
						score: 0.88,
					},
				]),
			),
		);

		renderDiscover('/catalog?type=operation');
		const input = screen.getByRole('textbox', { name: /search/i });
		await user.type(input, 'stripe');

		await waitFor(() => expect(screen.getByText('List charges')).toBeInTheDocument());
		// Endpoints segment should be the active one — proven by the fact that
		// the result is shown under the endpoint section despite the legacy URL.
		expect(screen.getByTestId('search-section-endpoint')).toBeInTheDocument();
	});

	// ── P2: search relevance feedback ────────────────────────────────────────

	it('renders match_snippet with highlighted spans + matched_on badge for hits', async () => {
		const user = userEvent.setup();
		worker.use(
			http.get('/search', () =>
				HttpResponse.json([
					{
						id: 'GET/api.stripe.com/v1/customers',
						type: 'operation',
						source: 'local',
						summary: 'Create customer',
						description: 'Create a new customer record',
						score: 0.95,
						matched_on: ['operation_summary'],
						match_snippet: 'Create \u0001customer\u0001',
					},
				]),
			),
		);

		renderDiscover();
		await user.type(screen.getByRole('textbox', { name: /search/i }), 'customer');

		// Snippet renders inside the card.
		const snippet = await screen.findByTestId('discovery-card-match-snippet');
		// `<mark>` should wrap exactly the highlighted span.
		const marks = snippet.querySelectorAll('mark');
		expect(marks).toHaveLength(1);
		expect(marks[0].textContent).toBe('customer');

		// Provenance badge surfaces the field that hit (summary in this case).
		expect(screen.getByTestId('discovery-card-matched-on')).toHaveTextContent(
			/matched on summary/i,
		);
	});

	it('omits the matched_on badge when only the description fallback hits', async () => {
		const user = userEvent.setup();
		worker.use(
			http.get('/search', () =>
				HttpResponse.json([
					{
						id: 'GET/api.stripe.com/v1/charges',
						type: 'operation',
						source: 'local',
						summary: 'List charges',
						description: 'List all charges for a customer',
						score: 0.45,
						// Description-only fallback case: no substring hit, snippet null.
						matched_on: ['description'],
						match_snippet: null,
					},
				]),
			),
		);

		renderDiscover();
		await user.type(screen.getByRole('textbox', { name: /search/i }), 'charges');

		await screen.findByText('List charges');
		// Both helpers are intentionally suppressed in this case.
		expect(screen.queryByTestId('discovery-card-match-snippet')).toBeNull();
		expect(screen.queryByTestId('discovery-card-matched-on')).toBeNull();
	});

	it('treats legacy ?source=local,catalog as "All"', async () => {
		worker.use(
			http.get('/apis', () =>
				HttpResponse.json({
					data: [
						{ id: 'stripe-api', name: 'Stripe', source: 'local' },
						{ id: 'github.com', name: 'github.com', source: 'catalog' },
					],
					total: 2,
					page: 1,
				}),
			),
		);

		renderDiscover('/catalog?source=local,catalog');

		await waitFor(() => expect(screen.getByText('Stripe')).toBeInTheDocument());
		expect(screen.getByText('github.com')).toBeInTheDocument();
	});

	// ── Directory card inline actions ────────────────────────────────────────

	it('Directory API card exposes Add credential + View on GitHub inline (independent of sheet)', async () => {
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

		renderDiscover();

		const card = await screen.findByTestId('discovery-card-api');

		// Inline primary action: add credential. Links are <a> not <button>, so
		// they don't trip the card's click handler (verified by the next test).
		const addCred = within(card).getByRole('link', { name: /add credential/i });
		expect(addCred).toHaveAttribute(
			'href',
			expect.stringContaining('/credentials/new?api_id=plaid.com'),
		);

		const gh = within(card).getByRole('link', { name: /view plaid\.com on github/i });
		expect(gh).toHaveAttribute(
			'href',
			'https://github.com/jentic/jentic-public-apis/tree/main/apis/openapi/plaid.com',
		);
	});

	it('Directory API card gets a synthetic description so heights match workspace cards', async () => {
		worker.use(
			http.get('/apis', () =>
				HttpResponse.json({
					data: [
						{
							id: 'plaid.com',
							name: 'plaid.com',
							source: 'catalog',
							// No `description` field — the adapter must synthesize one.
						},
					],
					total: 1,
					page: 1,
				}),
			),
		);

		renderDiscover();

		const card = await screen.findByTestId('discovery-card-api');
		expect(within(card).getByText(/add a credential to import/i)).toBeInTheDocument();
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
			// Real server shape: token-efficient `{id, summary, description}`
			// where `id` is the jentic_id (METHOD/host/path). Method + path are
			// NOT separate fields — the sheet must derive them from `id`. This
			// test guards against a regression where ops rendered as `?` badges.
			http.get('/apis/stripe.com/operations', () =>
				HttpResponse.json({
					data: [
						{
							id: 'GET/api.stripe.com/v1/customers',
							summary: 'List customers',
							description: '',
						},
						{
							id: 'POST/api.stripe.com/v1/charges',
							summary: 'Create charge',
							description: '',
						},
					],
					total: 2,
					page: 1,
				}),
			),
		);

		renderDiscover();

		const card = await screen.findByTestId('discovery-card-api');
		await user.click(within(card).getByRole('button'));

		// Sheet opens — header repeats the title in the dialog.
		const sheet = await screen.findByTestId('sheet-primitive');
		expect(within(sheet).getByRole('heading', { name: 'Stripe' })).toBeInTheDocument();

		// Operations list rendered from the workspace endpoint.
		await waitFor(() => {
			expect(within(sheet).getByTestId('sheet-ops-list')).toBeInTheDocument();
		});
		expect(within(sheet).getByText('List customers')).toBeInTheDocument();
		expect(within(sheet).getByText('Create charge')).toBeInTheDocument();
		// Method badges + paths are derived from `id` even when the server
		// only sends `{id, summary, description}`.
		expect(within(sheet).getByText('GET')).toBeInTheDocument();
		expect(within(sheet).getByText('POST')).toBeInTheDocument();
		expect(within(sheet).getByText('/v1/customers')).toBeInTheDocument();
		expect(within(sheet).getByText('/v1/charges')).toBeInTheDocument();
	});

	it('sheet shows Workflows-for-this-API section when the API has matching workflows', async () => {
		// Regression: the sheet should deep-link to the dedicated workflow page
		// rather than expanding inline, AND filter strictly on
		// `involved_apis` membership (server `q=` matches description too —
		// we don't want a workflow that just *mentions* the api in copy).
		const user = userEvent.setup();
		worker.use(
			http.get('/apis', () =>
				HttpResponse.json({
					data: [{ id: 'stripe.com', name: 'Stripe', source: 'local' }],
					total: 1,
					page: 1,
				}),
			),
			http.get('/apis/stripe.com/operations', () =>
				HttpResponse.json({ data: [], total: 0, page: 1 }),
			),
			http.get('/workflows', ({ request }) => {
				const url = new URL(request.url);
				// Sanity-check the call shape — sheet must filter server-side.
				expect(url.searchParams.get('q')).toBe('stripe.com');
				expect(url.searchParams.get('source')).toBe('local');
				return HttpResponse.json([
					{
						slug: 'charge-and-receipt',
						name: 'Charge customer and send receipt',
						involved_apis: ['stripe.com'],
						steps_count: 4,
					},
					{
						// Mentions stripe in description but doesn't actually
						// involve it — must be filtered out client-side.
						slug: 'unrelated',
						name: 'Unrelated workflow that mentions stripe in copy',
						involved_apis: ['github.com'],
						steps_count: 2,
					},
				]);
			}),
		);

		renderDiscover();
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
			http.get('/apis/lonely.com/operations', () =>
				HttpResponse.json({ data: [], total: 0, page: 1 }),
			),
			http.get('/workflows', () => HttpResponse.json([])),
		);

		const user = userEvent.setup();
		renderDiscover();
		const card = await screen.findByTestId('discovery-card-api');
		await user.click(within(card).getByRole('button'));

		const sheet = await screen.findByTestId('sheet-primitive');
		// Wait for the credentials section as a proxy for "sheet body settled".
		await waitFor(() => {
			expect(within(sheet).getByTestId('sheet-credentials-section')).toBeInTheDocument();
		});
		// Empty workflow section is suppressed entirely — empty sections are noisy.
		expect(within(sheet).queryByTestId('sheet-workflows-section')).toBeNull();
	});

	it('sheet shows Credentials-for-this-API section with Add CTA when zero creds', async () => {
		worker.use(
			http.get('/apis', () =>
				HttpResponse.json({
					data: [{ id: 'fresh.com', name: 'Fresh', source: 'local' }],
					total: 1,
					page: 1,
				}),
			),
			http.get('/apis/fresh.com/operations', () =>
				HttpResponse.json({ data: [], total: 0, page: 1 }),
			),
			// Default handler returns `{data: [], total: 0}` — the sheet
			// adapter must tolerate both raw-list and envelope shapes.
		);

		const user = userEvent.setup();
		renderDiscover();
		const card = await screen.findByTestId('discovery-card-api');
		await user.click(within(card).getByRole('button'));

		const sheet = await screen.findByTestId('sheet-primitive');
		const section = await within(sheet).findByTestId('sheet-credentials-section');
		expect(within(section).getByText(/no credentials configured/i)).toBeInTheDocument();
		// Add CTA deep-links to the credentials creation flow filtered by api_id.
		const addCta = within(section).getByText(/add credential/i);
		expect(addCta.closest('a')).toHaveAttribute(
			'href',
			expect.stringContaining('/credentials/new?api_id=fresh.com'),
		);
	});

	it('sheet lists existing credentials with a "Manage credentials" deep-link', async () => {
		worker.use(
			http.get('/apis', () =>
				HttpResponse.json({
					data: [{ id: 'openai.com', name: 'OpenAI', source: 'local' }],
					total: 1,
					page: 1,
				}),
			),
			http.get('/apis/openai.com/operations', () =>
				HttpResponse.json({ data: [], total: 0, page: 1 }),
			),
			http.get('/credentials', ({ request }) => {
				const url = new URL(request.url);
				expect(url.searchParams.get('api_id')).toBe('openai.com');
				return HttpResponse.json([
					{ id: 'cred_prod_abc', label: 'OPENAI_PROD', api_id: 'openai.com' },
					{ id: 'cred_staging_xyz', label: 'OPENAI_STAGING', api_id: 'openai.com' },
				]);
			}),
		);

		const user = userEvent.setup();
		renderDiscover();
		const card = await screen.findByTestId('discovery-card-api');
		await user.click(within(card).getByRole('button'));

		const sheet = await screen.findByTestId('sheet-primitive');
		const section = await within(sheet).findByTestId('sheet-credentials-section');
		expect(within(section).getByText('OPENAI_PROD')).toBeInTheDocument();
		expect(within(section).getByText('OPENAI_STAGING')).toBeInTheDocument();
		// Footer link lets the user jump to the canonical Credentials surface.
		const manageLink = within(section).getByText(/manage credentials/i);
		expect(manageLink.closest('a')).toHaveAttribute(
			'href',
			expect.stringContaining('/credentials?api_id=openai.com'),
		);
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

		renderDiscover();

		const card = await screen.findByTestId('discovery-card-api');
		await user.click(within(card).getByRole('button'));

		const sheet = await screen.findByTestId('sheet-primitive');
		await waitFor(() => {
			expect(within(sheet).getByTestId('sheet-ops-list-directory')).toBeInTheDocument();
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
			http.get('/apis', () =>
				HttpResponse.json({
					data: [{ id: 'plaid.com', name: 'plaid.com', source: 'catalog' }],
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
		);

		renderDiscover();
		const card = await screen.findByTestId('discovery-card-api');
		await user.click(within(card).getByRole('button'));

		const sheet = await screen.findByTestId('sheet-primitive');
		const row = await within(sheet).findByTestId('sheet-ops-row-directory');
		await user.click(row);

		// Inspect panel renders from the SAME cached preview — no second fetch.
		const inspect = await within(sheet).findByTestId('sheet-directory-inspect');
		expect(within(inspect).getByText('Get account')).toBeInTheDocument();
		expect(within(inspect).getByText('account_id')).toBeInTheDocument();
		expect(within(inspect).getByText('fields')).toBeInTheDocument();
		expect(within(inspect).getByText('required')).toBeInTheDocument();
		expect(within(inspect).getByText('plaidClientAuth')).toBeInTheDocument();
		// `Add credential` CTA replaces the upstream link as the forward action.
		const addCred = within(inspect).getByText(/add credential/i);
		expect(addCred.closest('a')).toHaveAttribute(
			'href',
			expect.stringContaining('/credentials/new?api_id=plaid.com'),
		);
		// Critical: a single preview fetch served both the row list AND the
		// inspect view. Two would mean the React-Query cache key drifted.
		expect(previewCalled).toBe(1);
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

		renderDiscover();
		const card = await screen.findByTestId('discovery-card-api');
		await user.click(within(card).getByRole('button'));
		const sheet = await screen.findByTestId('sheet-primitive');
		await user.click(await within(sheet).findByTestId('sheet-ops-row-directory'));
		expect(await within(sheet).findByTestId('sheet-directory-inspect')).toBeInTheDocument();

		// Back arrow exits the inspect view back to the ops list.
		await user.click(within(sheet).getByRole('button', { name: /back to operations/i }));
		expect(await within(sheet).findByTestId('sheet-ops-list-directory')).toBeInTheDocument();
		expect(within(sheet).queryByTestId('sheet-directory-inspect')).toBeNull();
	});

	it('?inspect=<api_id> on initial load opens the sheet (deep link)', async () => {
		worker.use(
			http.get('/apis', () => HttpResponse.json({ data: [], total: 0, page: 1 })),
			// No cached entity → sheet must resolve source itself. `getApi`
			// succeeds → workspace path → operations endpoint called.
			http.get('/apis/stripe.com', () =>
				HttpResponse.json({ id: 'stripe.com', name: 'Stripe', source: 'local' }),
			),
			http.get('/apis/stripe.com/operations', () =>
				HttpResponse.json({
					data: [
						{
							id: 'op-1',
							jentic_id: 'GET/api.stripe.com/v1/customers',
							method: 'GET',
							path: '/v1/customers',
							summary: 'List customers',
						},
					],
					total: 1,
					page: 1,
				}),
			),
		);

		renderDiscover('/catalog?inspect=stripe.com');

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
			http.get('/apis/stripe.com/operations', () =>
				HttpResponse.json({ data: [], total: 0, page: 1 }),
			),
		);

		renderDiscover();

		const card = await screen.findByTestId('discovery-card-api');
		await user.click(within(card).getByRole('button'));

		const sheet = await screen.findByTestId('sheet-primitive');
		// Wait until the sheet has fully entered (data loaded) so we're
		// closing a stable surface, not racing the entrance animation.
		// Browser-mode + React 19 concurrent rendering occasionally leaves
		// the sheet in a half-mounted state where the close button exists
		// but its onClick handler hasn't been attached yet — waiting on a
		// rendered child that proves the inner tree is committed avoids
		// the race.
		await waitFor(() => {
			expect(within(sheet).getByText(/no operations indexed yet/i)).toBeInTheDocument();
		});

		// Use userEvent for the close click — it sets up pointer events the
		// way React 19 expects, which the synthetic `fireEvent.click` does
		// not always emulate cleanly under the parallel test pool.
		const closeButton = within(sheet).getByRole('button', { name: /close detail panel/i });
		await user.click(closeButton);

		// The closing animation takes ~300ms then the sheet unmounts entirely.
		// Behaviour-level assertion — URL state lives in MemoryRouter and isn't
		// reflected in window.location, so we can't assert on it directly.
		// Generous timeout: occasionally races with React 19 paint/layout
		// scheduling under the parallel test pool, which can stretch the
		// unmount tick beyond the animation duration.
		await waitFor(
			() => {
				expect(screen.queryByTestId('sheet-primitive')).toBeNull();
			},
			{ timeout: 3000 },
		);
	});

	it('clicking inline "Add credential" on a directory card does NOT open the sheet', async () => {
		const user = userEvent.setup();
		worker.use(
			http.get('/apis', () =>
				HttpResponse.json({
					data: [{ id: 'plaid.com', name: 'plaid.com', source: 'catalog' }],
					total: 1,
					page: 1,
				}),
			),
		);

		renderDiscover();

		const card = await screen.findByTestId('discovery-card-api');
		const addCred = within(card).getByRole('link', { name: /add credential/i });

		// Use a non-bubbling click so React Router doesn't actually navigate
		// the test harness — we only care about whether the parent button's
		// click handler runs (it must NOT, thanks to stopPropagation).
		await user.click(addCred);

		// Sheet must not have opened. (No URL assertion — MemoryRouter
		// state isn't reflected in window.location.)
		expect(screen.queryByTestId('sheet-primitive')).toBeNull();
	});

	it('clicking an operation row in the workspace sheet drills into InspectPanel', async () => {
		const user = userEvent.setup();
		worker.use(
			http.get('/apis', () =>
				HttpResponse.json({
					data: [{ id: 'stripe.com', name: 'Stripe', source: 'local' }],
					total: 1,
					page: 1,
				}),
			),
			http.get('/apis/stripe.com/operations', () =>
				HttpResponse.json({
					data: [
						{
							id: 'op-1',
							jentic_id: 'GET/api.stripe.com/v1/customers',
							method: 'GET',
							path: '/v1/customers',
							summary: 'List customers',
						},
					],
					total: 1,
					page: 1,
				}),
			),
			// The generated InspectService does NOT URL-encode the slashes in
			// capability ids, so the request lands as a multi-segment path
			// (e.g. `/inspect/GET/api.stripe.com/v1/customers`). Match with a
			// regex so we catch the whole prefix regardless of depth.
			// Real backend shape: `parameters` is a dict keyed by location,
			// `auth` (not `auth_instructions`) is a pre-shaped scheme list.
			// Mirror this precisely — drift between mock and server is what
			// hid the original InspectPanel bug.
			http.get(/\/inspect\/.+/, () =>
				HttpResponse.json({
					id: 'GET/api.stripe.com/v1/customers',
					method: 'GET',
					url: 'https://api.stripe.com/v1/customers',
					summary: 'List customers (detailed)',
					description: 'Returns a list of customers from your Stripe account.',
					parameters: {
						query: [
							{
								name: 'limit',
								required: false,
								description: 'Page size cap',
							},
							{
								name: 'starting_after',
								required: false,
								description: 'Cursor for pagination',
							},
						],
					},
					auth: [
						{
							scheme: 'BasicAuth',
							type: 'http_basic',
							instruction: 'Set header `Authorization: Basic <credentials>`',
						},
					],
					api: { id: 'stripe.com', name: 'Stripe' },
					_links: { upstream: 'https://stripe.com/docs/api/customers/list' },
				}),
			),
		);

		renderDiscover();
		await user.click(
			within(await screen.findByTestId('discovery-card-api')).getByRole('button'),
		);

		const sheet = await screen.findByTestId('sheet-primitive');
		const row = await within(sheet).findByText('List customers');
		await user.click(row);

		// Drill-down view rendered: InspectPanel description appears AND the
		// "Back to operations" arrow replaces the vendor icon (only present
		// in drill-down mode).
		await waitFor(() => {
			expect(
				within(sheet).getByText('Returns a list of customers from your Stripe account.'),
			).toBeInTheDocument();
		});
		expect(
			within(sheet).getByRole('button', { name: /back to operations/i }),
		).toBeInTheDocument();

		// PARITY REGRESSION GUARD: workspace inspect must surface parameters
		// (from the dict-shaped `parameters` field) AND auth (from `auth`,
		// not `auth_instructions`). Both were silently broken before — the
		// only thing keeping the test green was a mock that lied about the
		// server's shape. Each of the four assertions below would have
		// failed against either of the original bugs.
		const inspect = within(sheet).getByTestId('inspect-panel');
		expect(within(inspect).getByText('limit')).toBeInTheDocument();
		expect(within(inspect).getByText('starting_after')).toBeInTheDocument();
		expect(within(inspect).getByText('BasicAuth')).toBeInTheDocument();
		expect(within(inspect).getByText(/Authorization: Basic <credentials>/)).toBeInTheDocument();
		// Method + path header is the visual continuity from the op row.
		expect(within(inspect).getByText('/v1/customers')).toBeInTheDocument();
	});

	it('normalizes the now-removed ?type=importable to "all"', async () => {
		const user = userEvent.setup();
		worker.use(
			http.get('/search', () =>
				HttpResponse.json([
					{
						id: 'GET/api.stripe.com/v1/charges',
						type: 'operation',
						source: 'local',
						summary: 'List charges',
						score: 0.88,
					},
				]),
			),
		);

		renderDiscover('/catalog?type=importable');
		await user.type(screen.getByRole('textbox', { name: /search/i }), 'stripe');

		// "All" is the active segment (the legacy value collapses to All), so
		// the endpoint result is visible.
		await waitFor(() => expect(screen.getByText('List charges')).toBeInTheDocument());
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

		renderDiscover();
		const card = await screen.findByTestId('discovery-card-api');
		await user.click(within(card).getByRole('button'));

		const sheet = await screen.findByTestId('sheet-primitive');

		// First page renders 25 rows + footer says "Showing 25 of 60".
		await waitFor(() => {
			const rows = within(sheet).getAllByTestId('sheet-ops-row-directory');
			expect(rows).toHaveLength(25);
		});
		expect(within(sheet).getByText(/Showing 25 of 60/)).toBeInTheDocument();

		// Click "Load more" → next 25 rows append.
		await user.click(within(sheet).getByTestId('ops-load-more'));
		await waitFor(() => {
			const rows = within(sheet).getAllByTestId('sheet-ops-row-directory');
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

		renderDiscover();
		const card = await screen.findByTestId('discovery-card-api');
		await user.click(within(card).getByRole('button'));

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
			const visibleRows = within(sheet).getAllByTestId('sheet-ops-row-directory');
			expect(visibleRows).toHaveLength(2);
		});
		expect(within(sheet).getByText('Create charge')).toBeInTheDocument();
		expect(within(sheet).getByText('Get charge')).toBeInTheDocument();
		expect(within(sheet).queryByText('List customers')).toBeNull();

		// "All" resets the filter.
		await user.click(within(tagBar).getByText('All'));
		await waitFor(() => {
			const visibleRows = within(sheet).getAllByTestId('sheet-ops-row-directory');
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

		renderDiscover();
		const card = await screen.findByTestId('discovery-card-api');
		await user.click(within(card).getByRole('button'));

		const sheet = await screen.findByTestId('sheet-primitive');
		await within(sheet).findByText('Find a needle');

		const input = within(sheet).getByTestId('ops-filter-input');
		await user.type(input, 'needle');
		await waitFor(() => {
			const visibleRows = within(sheet).getAllByTestId('sheet-ops-row-directory');
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

		renderDiscover();
		const card = await screen.findByTestId('discovery-card-api');
		await user.click(within(card).getByRole('button'));

		const sheet = await screen.findByTestId('sheet-primitive');
		const summary = await within(sheet).findByTestId('api-summary');
		expect(summary).toHaveTextContent(/bot-free.*API for fetching.*documents.*metadata/i);
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
			http.get('/apis/bare.com', () =>
				HttpResponse.json({ id: 'bare.com', info: { description: null } }),
			),
			http.get('/apis/bare.com/operations', () =>
				HttpResponse.json({
					data: [
						{ id: 'GET/bare.com/x', summary: 'X', tags: ['core'] },
						{ id: 'GET/bare.com/y', summary: 'Y', tags: ['core'] },
						{ id: 'GET/bare.com/z', summary: 'Z', tags: ['admin'] },
					],
					total: 3,
					page: 1,
					offset: 0,
					limit: 25,
					total_pages: 1,
					has_more: false,
					truncated: false,
				}),
			),
		);

		renderDiscover();
		const card = await screen.findByTestId('discovery-card-api');
		await user.click(within(card).getByRole('button'));

		const sheet = await screen.findByTestId('sheet-primitive');
		const summary = await within(sheet).findByTestId('api-summary');
		// Fallback shape: "<host> — N operations across M tags"
		expect(summary).toHaveTextContent(/bare\.com.*3 operations across 2 tags/);
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

		renderDiscover();
		const card = await screen.findByTestId('discovery-card-api');
		await user.click(within(card).getByRole('button'));

		const sheet = await screen.findByTestId('sheet-primitive');
		const summary = await within(sheet).findByTestId('api-summary');
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
			http.get('/apis/:id', ({ params }) =>
				HttpResponse.json({
					id: params.id as string,
					name: params.id as string,
					source: 'local',
				}),
			),
			http.get('/apis/:id/operations', () =>
				HttpResponse.json({
					data: [
						{
							id: 'op-1',
							jentic_id: 'GET/x/v1/y',
							method: 'GET',
							path: '/v1/y',
							summary: 'Some op',
						},
					],
					total: 1,
					page: 1,
				}),
			),
		);

		renderDiscover();
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

	// ── P6 — Density toggle (grid vs list) ────────────────────────────────

	it('clicking the list-density button switches BrowseResults to list rows', async () => {
		const user = userEvent.setup();
		// Make sure no leftover URL/localStorage view from previous tests.
		window.localStorage.removeItem('discover.view.v1');
		worker.use(
			http.get('/apis', () =>
				HttpResponse.json({
					data: [{ id: 'stripe.com', name: 'Stripe', source: 'local' }],
					total: 1,
					page: 1,
				}),
			),
		);
		renderDiscover();
		const grid = await screen.findByTestId('browse-grid');
		expect(grid.getAttribute('data-density')).not.toBe('list');

		await user.click(screen.getByTestId('density-list'));
		const grid2 = screen.getByTestId('browse-grid');
		expect(grid2.getAttribute('data-density')).toBe('list');
		const card = screen.getByTestId('discovery-card-api');
		expect(card.getAttribute('data-density')).toBe('list');
	});

	it('?view=list on initial load renders list rows', async () => {
		worker.use(
			http.get('/apis', () =>
				HttpResponse.json({
					data: [{ id: 'github.com', name: 'GitHub', source: 'local' }],
					total: 1,
					page: 1,
				}),
			),
		);
		window.localStorage.removeItem('discover.view.v1');
		renderDiscover('/catalog?view=list');
		const grid = await screen.findByTestId('browse-grid');
		expect(grid.getAttribute('data-density')).toBe('list');
	});

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
		renderDiscover();
		await screen.findByText('Stripe');
		// Focus on body, then press `/`.
		document.body.focus();
		(document.activeElement as HTMLElement | null)?.blur?.();
		await user.keyboard('/');
		const searchInput = screen.getByRole('textbox', { name: /search/i });
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
			http.get('/apis', () =>
				HttpResponse.json({
					data: [{ id: 'stripe.com', name: 'Stripe', source: 'local' }],
					total: 1,
					page: 1,
				}),
			),
			http.get('/apis/stripe.com', () =>
				HttpResponse.json({ id: 'stripe.com', name: 'Stripe', source: 'local' }),
			),
			http.get('/apis/stripe.com/operations', () =>
				HttpResponse.json({ data: [], total: 0, page: 1 }),
			),
		);
		renderDiscover('/catalog?inspect=stripe.com');
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
		expect(liveToasts[0].title).toMatch(/credential added/i);
		expect(liveToasts[0].variant).toBe('success');
	});
});
