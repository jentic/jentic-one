import { describe, it, expect, beforeEach } from 'vitest';
import { http, HttpResponse } from 'msw';
import {
	renderWithProviders,
	screen,
	waitFor,
	userEvent,
	within,
	checkA11y,
} from '@/__tests__/test-utils';
import { worker } from '@/mocks/browser';
import { setToken } from '@/shared/api';
import ApiDetailPage from '@/modules/workspace/pages/ApiDetailPage';

/** See WorkspacePage.test for why we settle the PageHeader entrance animation. */
async function settleAnimations(container: HTMLElement): Promise<void> {
	await waitFor(() => {
		const faded = Array.from(container.querySelectorAll<HTMLElement>('*')).find((el) => {
			if (el.hasAttribute('disabled') || el.getAttribute('aria-disabled') === 'true')
				return false;
			const opacity = Number.parseFloat(getComputedStyle(el).opacity);
			return !Number.isNaN(opacity) && opacity > 0 && opacity < 1;
		});
		expect(faded).toBeUndefined();
	});
}

const PATH = '/workspace/:vendor/:name/:version';

function renderAt(route: string) {
	return renderWithProviders(<ApiDetailPage />, { route, path: PATH });
}

describe('ApiDetailPage', () => {
	beforeEach(() => {
		setToken('test-token');
	});

	it('renders overview + operations for a published API', async () => {
		renderAt('/workspace/stripe/stripe-api/2024-01-01');

		expect(await screen.findByRole('heading', { name: 'Stripe' })).toBeInTheDocument();
		// Operations of the live revision show up (GET + POST /v1/charges).
		expect((await screen.findAllByText('/v1/charges')).length).toBeGreaterThanOrEqual(1);
		expect(screen.getByTestId('operations-section')).toBeInTheDocument();
		// History lives on its own tab.
		expect(screen.queryByTestId('revisions-section')).not.toBeInTheDocument();
	});

	it('switches to the revisions & overlays tab', async () => {
		const user = userEvent.setup();
		renderAt('/workspace/stripe/stripe-api/2024-01-01');
		await screen.findByRole('heading', { name: 'Stripe' });

		await user.click(screen.getByRole('tab', { name: /Revisions & overlays/ }));
		expect(await screen.findByTestId('revisions-section')).toBeInTheDocument();
		expect(screen.getByTestId('overlays-section')).toBeInTheDocument();
		expect(screen.queryByTestId('operations-section')).not.toBeInTheDocument();
	});

	it('opens a tab from the URL', async () => {
		renderAt('/workspace/stripe/stripe-api/2024-01-01?tab=changes');
		expect(await screen.findByTestId('revisions-section')).toBeInTheDocument();
		expect(screen.getByRole('tab', { name: /Revisions & overlays/ })).toHaveAttribute(
			'aria-selected',
			'true',
		);
	});

	it('calls out overlays waiting for review and jumps to them', async () => {
		const user = userEvent.setup();
		renderAt('/workspace/showcase/kitchen-sink/1.0.0');

		const callout = await screen.findByTestId('pending-overlays-callout');
		expect(within(callout).getByText(/1 overlay waiting for review/)).toBeInTheDocument();
		// The tab carries the same count.
		expect(screen.getByRole('tab', { name: /Revisions & overlays/ })).toHaveTextContent('1');

		await user.click(within(callout).getByRole('button', { name: 'Review' }));
		expect(await screen.findByTestId('overlays-section')).toBeInTheDocument();
	});

	it('shows credentials, their agents and recent callers on the access tab', async () => {
		worker.use(
			http.get('/credentials/cred_stripe_1/agents', () =>
				HttpResponse.json({
					data: [
						{
							agent_id: 'agnt_billing',
							agent_name: 'Billing bot',
							bound_at: '2026-02-01T00:00:00Z',
							rule_set_id: null,
							status: 'active',
							suspended: false,
						},
					],
					has_more: false,
					next_cursor: null,
				}),
			),
			http.get('/monitoring/usage', ({ request }) => {
				const url = new URL(request.url);
				if (url.searchParams.get('group_by') !== 'agent') return undefined;
				expect(url.searchParams.get('api_id')).toBe('stripe/stripe-api');
				return HttpResponse.json({
					group_by: 'agent',
					stats: { total: 12, success: 10, failed: 2 },
					buckets: [],
					top: [
						{
							key: 'agent/agnt_billing',
							label: 'agent/agnt_billing',
							total: 12,
							success: 10,
							failed: 2,
							avg_ms: 300,
							trend: [],
						},
					],
				});
			}),
		);
		const user = userEvent.setup();
		renderAt('/workspace/stripe/stripe-api/2024-01-01');
		await screen.findByRole('heading', { name: 'Stripe' });

		await user.click(screen.getByRole('tab', { name: 'Access' }));

		const credentials = await screen.findByTestId('access-credentials');
		expect(await within(credentials).findByText('Stripe live key')).toBeInTheDocument();
		expect(
			await within(credentials).findByRole('link', { name: /Billing bot/ }),
		).toHaveAttribute('href', '/agents?agent=agnt_billing');

		const callers = screen.getByTestId('access-agents');
		const caller = await within(callers).findByRole('link', { name: /12 calls/ });
		expect(caller).toHaveAttribute('href', '/agents?agent=agnt_billing');
		expect(within(caller).getByText(/2 failed/)).toBeInTheDocument();
	});

	it('has no critical a11y violations', async () => {
		const { container } = renderAt('/workspace/stripe/stripe-api/2024-01-01');
		await screen.findAllByText('/v1/charges');
		await settleAnimations(container);
		await checkA11y(container);
	});

	it('shows the "no live revision" state for a draft-only API', async () => {
		renderAt('/workspace/adyen/pos-terminal-management-api/1');

		// Operations 404 with no_current_revision → promote-a-revision empty state.
		expect(await screen.findByText('No live revision yet')).toBeInTheDocument();
		// The draft revision offers a Promote action, on the history tab.
		await userEvent.setup().click(screen.getByRole('tab', { name: /Revisions & overlays/ }));
		expect(await screen.findByTestId('revision-promote')).toBeInTheDocument();
	});

	it('renders a not-found error for an unknown API', async () => {
		renderAt('/workspace/ghost/nope/9');
		await waitFor(() => {
			expect(screen.getByText(/could not be loaded|not found/i)).toBeInTheDocument();
		});
	});

	describe('paginated operations (BigCo, 60 ops over multiple pages)', () => {
		it('shows the authoritative total and only one 25-row page', async () => {
			renderAt('/workspace/bigco/big-api/1');

			// The total comes from the API's operation_count, known before the
			// background walk finishes loading every page.
			await waitFor(() => {
				expect(screen.getByTestId('operations-count')).toHaveTextContent(/of 60/);
			});
			// Once the background walk has loaded all pages, the paginator spans 3
			// pages but the list still paints only the first 25 rows.
			await waitFor(() => {
				expect(screen.getByTestId('operations-count')).not.toHaveTextContent(
					/loading the rest/,
				);
			});
			expect(screen.getAllByTestId('operation-row')).toHaveLength(25);
			expect(screen.getByTestId('operations-page-indicator')).toHaveTextContent('1 / 3');
		});

		it('pages forward through the loaded operations', async () => {
			const user = userEvent.setup();
			renderAt('/workspace/bigco/big-api/1');

			await screen.findByTestId('operations-next-page');
			// First page starts at /v1/resource/0.
			expect(await screen.findByText('/v1/resource/0')).toBeInTheDocument();

			await user.click(screen.getByTestId('operations-next-page'));
			await waitFor(() => {
				expect(screen.getByTestId('operations-page-indicator')).toHaveTextContent('2 / 3');
			});
			// Page 2 shows the 26th operation and no longer the first.
			expect(screen.getByText('/v1/resource/25')).toBeInTheDocument();
			expect(screen.queryByText('/v1/resource/0')).not.toBeInTheDocument();
		});

		it('filters across every loaded operation, not just the first page', async () => {
			const user = userEvent.setup();
			renderAt('/workspace/bigco/big-api/1');

			// Wait for the background walk to load all 60 before filtering.
			await waitFor(() => {
				expect(screen.getByTestId('operations-count')).not.toHaveTextContent(
					/loading the rest/,
				);
			});

			// /v1/resource/55 lives on the third page — a first-page-only filter
			// would miss it.
			await user.type(screen.getByLabelText('Filter operations'), 'resource/55');
			await waitFor(() => {
				expect(screen.getByText('/v1/resource/55')).toBeInTheDocument();
			});
			expect(screen.getByTestId('operations-count')).toHaveTextContent('1 of 60 match');
			expect(screen.getAllByTestId('operation-row')).toHaveLength(1);
		});
	});

	it('surfaces an inline retry when the operations walk fails mid-load', async () => {
		// First page (cursor=null) succeeds; every later page 500s — the partial
		// list is kept and a non-fatal retry banner appears instead of a full
		// error that would discard what we already loaded.
		worker.use(
			http.get('/apis/bigco/big-api/1/operations', ({ request }) => {
				const cursor = new URL(request.url).searchParams.get('cursor');
				if (cursor) return new HttpResponse(null, { status: 500 });
				const items = Array.from({ length: 25 }, (_, i) => ({
					operation_id: `Op${i}`,
					method: 'get',
					path: `/v1/resource/${i}`,
					name: `Operation ${i}`,
					description: null,
					tags: [],
					deprecated: false,
					revision_id: 'rev_big_live',
					_links: {},
				}));
				return HttpResponse.json({ data: items, has_more: true, next_cursor: '25' });
			}),
		);
		renderAt('/workspace/bigco/big-api/1');

		await waitFor(() => {
			expect(screen.getByTestId('operations-partial-error')).toBeInTheDocument();
		});
		expect(screen.getByTestId('operations-retry')).toBeInTheDocument();
		// The first page's rows are still shown — the error didn't wipe them out.
		expect(screen.getByText('/v1/resource/0')).toBeInTheDocument();
	});

	it('titles a draft-only sub-API through the shared friendly-name rule, matching the tile', async () => {
		// The Adyen sub-API has no user-set display_name, so the header must
		// route the raw `vendor`/`name` through `apiRefDisplayName` — the same
		// rule the workspace tile uses — instead of rendering the raw
		// `vendor/name` tuple. `pos-terminal-management-api` → `Pos Terminal
		// Management Api` (the vendor prefix doesn't match, so nothing is stripped).
		renderAt('/workspace/adyen/pos-terminal-management-api/1');

		expect(
			await screen.findByRole('heading', { name: 'Pos Terminal Management Api' }),
		).toBeInTheDocument();
		// The raw tuple must not leak into the heading.
		expect(
			screen.queryByRole('heading', { name: 'adyen/pos-terminal-management-api' }),
		).not.toBeInTheDocument();
	});

	it('removes the API through the cascade dialog (generic-warning mode)', async () => {
		// Per-test DELETE handler so we record the call without mutating the
		// shared APIS fixture (other tests rely on its presence).
		let deleted: string | null = null;
		worker.use(
			http.delete('/apis/:vendor/:name/:version', ({ params }) => {
				deleted = `${params.vendor}/${params.name}/${params.version}`;
				return new HttpResponse(null, { status: 204 });
			}),
		);

		const user = userEvent.setup();
		renderAt('/workspace/stripe/stripe-api/2024-01-01');
		await screen.findByRole('heading', { name: 'Stripe' });

		// Removal sits behind the overflow menu.
		await user.click(screen.getByRole('button', { name: 'More actions for Stripe' }));
		await user.click(screen.getByTestId('remove-api'));

		// Generic-warning mode (no `dependents` from the page yet) → the
		// type-specific warning copy, not a blast-radius list.
		const dialog = await screen.findByRole('dialog', { name: /remove api/i });
		expect(
			within(dialog).getByText(/this api and all of its operations leave your workspace/i),
		).toBeInTheDocument();
		expect(within(dialog).queryByText(/will also remove/i)).not.toBeInTheDocument();

		// Type-to-confirm — the delete gate now requires a fixed word, not the
		// API's (tuple) name. The name still shows in the dialog body for context.
		const confirm = within(dialog).getByRole('button', { name: /^remove api$/i });
		expect(confirm).toBeDisabled();
		await user.type(within(dialog).getByLabelText(/type delete to confirm/i), 'delete');
		await waitFor(() => expect(confirm).toBeEnabled());

		await user.click(confirm);
		await waitFor(() => expect(deleted).toBe('stripe/stripe-api/2024-01-01'));
	});
});
