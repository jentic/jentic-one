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
		expect(screen.getByTestId('revisions-section')).toBeInTheDocument();
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
		// The draft revision offers a Promote action.
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

		await user.click(screen.getByTestId('remove-api'));

		// Generic-warning mode (no `dependents` from the page yet) → the
		// type-specific warning copy, not a blast-radius list.
		const dialog = await screen.findByRole('dialog', { name: /remove api/i });
		expect(
			within(dialog).getByText(/this api and all of its operations leave your workspace/i),
		).toBeInTheDocument();
		expect(within(dialog).queryByText(/will also remove/i)).not.toBeInTheDocument();

		// Type-to-confirm — the page header displays the API's display name.
		const confirm = within(dialog).getByRole('button', { name: /^remove api$/i });
		expect(confirm).toBeDisabled();
		await user.type(within(dialog).getByLabelText(/type stripe to confirm/i), 'Stripe');
		await waitFor(() => expect(confirm).toBeEnabled());

		await user.click(confirm);
		await waitFor(() => expect(deleted).toBe('stripe/stripe-api/2024-01-01'));
	});
});
