import { describe, it, expect, beforeEach, afterEach } from 'vitest';
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
import { clearAllToasts, Toaster } from '@/shared/ui';
import { Link, Route, Routes, useNavigate } from 'react-router';
import ApiDetailPage from '@/modules/workspace/pages/ApiDetailPage';
import { makeMockCredential, resetCredentialsStore } from '@/shared/credentials/mocks/handlers';

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

const PATH = '/library/workspace/:vendor/:name/:version';

function renderAt(route: string) {
	return renderWithProviders(<ApiDetailPage />, { route, path: PATH });
}

describe('ApiDetailPage', () => {
	beforeEach(() => {
		setToken('test-token');
	});

	it('renders overview + operations for a published API', async () => {
		renderAt('/library/workspace/stripe/stripe-api/2024-01-01?tab=operations');

		expect(await screen.findByRole('heading', { name: 'Stripe' })).toBeInTheDocument();
		// Operations of the live revision show up (GET + POST /v1/charges).
		expect((await screen.findAllByText('/v1/charges')).length).toBeGreaterThanOrEqual(1);
		expect(screen.getByTestId('operations-section')).toBeInTheDocument();
	});

	it('groups the API into Overview / Operations / Versions / Spec tabs', async () => {
		const user = userEvent.setup();
		renderAt('/library/workspace/stripe/stripe-api/2024-01-01');

		// Overview is the default tab and carries the overview strip.
		expect(await screen.findByTestId('workspace-overview-strip')).toBeInTheDocument();
		expect(screen.queryByTestId('revisions-section')).not.toBeInTheDocument();

		await user.click(screen.getByRole('tab', { name: /versions/i }));
		expect(await screen.findByTestId('revisions-section')).toBeInTheDocument();
		expect(screen.getByTestId('overlays-section')).toBeInTheDocument();
	});

	describe('back navigation', () => {
		const HUB = '/library/workspace/stripe/stripe-api/2024-01-01';

		function HistoryBack() {
			const navigate = useNavigate();
			return (
				<button type="button" onClick={() => navigate(-1)}>
					browser back
				</button>
			);
		}

		/** Workspace list → (push) hub, so there's real history behind the hub. */
		function renderFromWorkspace() {
			return renderWithProviders(
				<Routes>
					<Route
						path="/library/workspace"
						element={
							<div data-testid="workspace-page">
								<Link to={HUB}>open hub</Link>
							</div>
						}
					/>
					<Route
						path={PATH}
						element={
							<>
								<ApiDetailPage />
								<HistoryBack />
							</>
						}
					/>
				</Routes>,
				{ route: '/library/workspace' },
			);
		}

		async function visitTabs(user: ReturnType<typeof userEvent.setup>) {
			await user.click(screen.getByRole('link', { name: 'open hub' }));
			await screen.findByTestId('workspace-overview-strip');
			for (const name of [/operations/i, /versions/i, /spec/i, /overview/i, /versions/i]) {
				await user.click(screen.getByRole('tab', { name }));
			}
		}

		it('"Back to your workspace" lands on the workspace in one click after several tabs', async () => {
			const user = userEvent.setup();
			renderFromWorkspace();
			await visitTabs(user);
			await user.click(screen.getByRole('link', { name: /back to your workspace/i }));
			expect(await screen.findByTestId('workspace-page')).toBeInTheDocument();
		});

		it('tab switches replace the history entry, so browser Back skips them', async () => {
			const user = userEvent.setup();
			renderFromWorkspace();
			await visitTabs(user);
			await user.click(screen.getByRole('button', { name: 'browser back' }));
			expect(await screen.findByTestId('workspace-page')).toBeInTheDocument();
		});
	});

	it('has no critical a11y violations', async () => {
		const { container } = renderAt(
			'/library/workspace/stripe/stripe-api/2024-01-01?tab=operations',
		);
		await screen.findAllByText('/v1/charges');
		await settleAnimations(container);
		await checkA11y(container);
	});

	it('shows the "no live revision" state for a draft-only API', async () => {
		const user = userEvent.setup();
		renderAt('/library/workspace/adyen/pos-terminal-management-api/1?tab=operations');

		// Operations 404 with no_current_revision → promote-a-revision empty state.
		expect(await screen.findByText('No live revision yet')).toBeInTheDocument();
		// The draft revision offers a Promote action (in the Versions tab).
		await user.click(screen.getByRole('tab', { name: /versions/i }));
		expect(await screen.findByTestId('revision-promote')).toBeInTheDocument();
	});

	it('"Go to Versions" in the no-live-revision state switches tabs', async () => {
		const user = userEvent.setup();
		renderAt('/library/workspace/adyen/pos-terminal-management-api/1?tab=operations');
		await user.click(await screen.findByTestId('operations-go-to-versions'));
		expect(screen.getByRole('tab', { name: /versions/i })).toHaveAttribute(
			'aria-selected',
			'true',
		);
		expect(await screen.findByTestId('revision-promote')).toBeInTheDocument();
	});

	it('keeps each tab mounted (hidden) once visited, so its state survives a switch', async () => {
		const user = userEvent.setup();
		renderAt('/library/workspace/bigco/big-api/1?tab=operations');

		await screen.findByTestId('operations-next-page');
		await waitFor(() => {
			expect(screen.getByTestId('operations-count')).not.toHaveTextContent(
				/loading the rest/,
			);
		});
		await user.click(screen.getByTestId('operations-next-page'));
		await waitFor(() =>
			expect(screen.getByTestId('operations-page-indicator')).toHaveTextContent('2 / 3'),
		);

		// Unvisited tabs aren't mounted at all.
		expect(screen.queryByTestId('api-hub-panel-versions')).not.toBeInTheDocument();

		await user.click(screen.getByRole('tab', { name: /versions/i }));
		const ops = screen.getByTestId('api-hub-panel-operations');
		expect(ops).not.toBeVisible();
		expect(ops).toHaveAttribute('hidden');
		expect(screen.getByTestId('api-hub-panel-versions')).toBeVisible();
		// Exactly one visible tabpanel, labelled by the active tab.
		expect(screen.getAllByRole('tabpanel')).toHaveLength(1);
		expect(screen.getByRole('tabpanel')).toHaveAttribute(
			'aria-labelledby',
			'api-hub-tab-versions',
		);

		await user.click(screen.getByRole('tab', { name: /operations/i }));
		expect(screen.getByTestId('api-hub-panel-operations')).toBeVisible();
		// Still on page 2 — the Operations paging wasn't reset by the switch.
		expect(screen.getByTestId('operations-page-indicator')).toHaveTextContent('2 / 3');
	});

	it('the header "View spec" opens the Spec tab (one copy of the document)', async () => {
		const user = userEvent.setup();
		renderAt('/library/workspace/stripe/stripe-api/2024-01-01');
		const viewSpec = await screen.findByTestId('view-spec');
		await waitFor(() => expect(viewSpec).toBeEnabled());
		await user.click(viewSpec);
		expect(await screen.findByTestId('spec-panel-content')).toBeInTheDocument();
		expect(screen.getByRole('tab', { name: /^spec$/i })).toHaveAttribute(
			'aria-selected',
			'true',
		);
		expect(screen.queryByTestId('spec-viewer-content')).not.toBeInTheDocument();
	});

	it('?tab=versions deep-links straight onto Versions, badged by the pending-overlay read', async () => {
		worker.use(
			http.get('/apis/:vendor/:name/:version/overlays', ({ request }) => {
				const pending = new URL(request.url).searchParams.get('status') === 'pending';
				return HttpResponse.json({
					data: pending
						? [
								{ id: 'ov_1', status: 'pending' },
								{ id: 'ov_2', status: 'pending' },
							]
						: [],
					has_more: false,
					next_cursor: null,
				});
			}),
		);
		renderAt('/library/workspace/stripe/stripe-api/2024-01-01?tab=versions');
		expect(await screen.findByTestId('revisions-section')).toBeInTheDocument();
		const tab = screen.getByRole('tab', { name: /versions/i });
		expect(tab).toHaveAttribute('aria-selected', 'true');
		await waitFor(() => expect(tab).toHaveTextContent('2'));
		// Unvisited tabs control no panel yet (it isn't in the DOM).
		expect(screen.getByRole('tab', { name: /operations/i })).not.toHaveAttribute(
			'aria-controls',
		);
		expect(tab).toHaveAttribute('aria-controls', 'api-hub-panel-versions');
	});

	it('renders a not-found error for an unknown API', async () => {
		renderAt('/library/workspace/ghost/nope/9');
		await waitFor(() => {
			expect(screen.getByText(/could not be loaded|not found/i)).toBeInTheDocument();
		});
	});

	describe('paginated operations (BigCo, 60 ops over multiple pages)', () => {
		it('shows the authoritative total and only one 25-row page', async () => {
			renderAt('/library/workspace/bigco/big-api/1?tab=operations');

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
			renderAt('/library/workspace/bigco/big-api/1?tab=operations');

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
			renderAt('/library/workspace/bigco/big-api/1?tab=operations');

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
		renderAt('/library/workspace/bigco/big-api/1?tab=operations');

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
		renderAt('/library/workspace/adyen/pos-terminal-management-api/1');

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
		renderAt('/library/workspace/stripe/stripe-api/2024-01-01');
		await screen.findByRole('heading', { name: 'Stripe' });

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

	describe('Who can use it', () => {
		const STRIPE_CRED = makeMockCredential({
			credential_id: 'cred_stripe_live',
			name: 'Stripe live',
			api: { vendor: 'stripe', name: 'stripe-api', version: '2024-01-01' },
		});
		beforeEach(() => resetCredentialsStore([STRIPE_CRED]));
		afterEach(() => resetCredentialsStore());

		it("opens a credential's details in place from its row", async () => {
			const user = userEvent.setup();
			renderAt('/library/workspace/stripe/stripe-api/2024-01-01');

			const row = await screen.findByRole('button', { name: 'View Stripe live' });
			expect(row).toHaveTextContent('Bearer token');
			await user.click(row);

			// The shared edit sheet, on this credential — the hub stays underneath.
			const sheet = await screen.findByRole('dialog', { name: 'Edit credential' });
			expect(await within(sheet).findByDisplayValue('Stripe live')).toBeVisible();
			expect(screen.getByTestId('hub-access')).toBeInTheDocument();
		});

		it("opens the Add credential flow on this API's form", async () => {
			const user = userEvent.setup();
			renderAt('/library/workspace/stripe/stripe-api/2024-01-01');

			await user.click(await screen.findByTestId('hub-access-add-credential'));
			expect(
				await screen.findByRole('dialog', { name: 'Add credential — Stripe' }),
			).toBeVisible();
			expect(screen.getByText(/Step 2 of 2/)).toBeVisible();
		});

		it('lists an unpinned (any-version) credential — how the create flow saves them', async () => {
			// The backend stores an unpinned scope as `version: ""`; it covers
			// every version of the API, so the hub must count it.
			resetCredentialsStore([
				makeMockCredential({
					credential_id: 'cred_stripe_any',
					name: 'Stripe any version',
					api: { vendor: 'stripe', name: 'stripe-api', version: '' },
				}),
				makeMockCredential({
					credential_id: 'cred_other_vendor',
					name: 'Not Stripe',
					api: { vendor: 'acme', name: 'stripe-api', version: '' },
				}),
			]);
			renderAt('/library/workspace/stripe/stripe-api/2024-01-01');

			expect(
				await screen.findByRole('button', { name: 'View Stripe any version' }),
			).toBeVisible();
			expect(
				screen.queryByRole('button', { name: 'View Not Stripe' }),
			).not.toBeInTheDocument();
			expect(screen.queryByTestId('hub-access-none')).not.toBeInTheDocument();
		});

		it('binds an existing agent in place and the Agents list refreshes', async () => {
			const user = userEvent.setup();
			clearAllToasts();
			renderWithProviders(
				<>
					<ApiDetailPage />
					<Toaster />
				</>,
				{ route: '/library/workspace/stripe/stripe-api/2024-01-01', path: PATH },
			);

			// No agent bound yet → the card offers the bind action.
			expect(
				await screen.findByText('No agent is bound to these credentials yet.'),
			).toBeVisible();
			await user.click(await screen.findByTestId('hub-access-bind-agent'));

			const dialog = await screen.findByRole('dialog', { name: 'Bind to an agent' });
			// Only one credential covers this API → it's used without a picker.
			expect(within(dialog).getByText('Stripe live')).toBeVisible();
			// Real agents list, with lifecycle status; archived/rejected are left out.
			const option = await within(dialog).findByRole('checkbox', {
				name: 'support-agent',
			});
			expect(within(dialog).queryByText('spammy-bot')).not.toBeInTheDocument();
			expect(within(dialog).getByTestId('bind-agent-confirm')).toBeDisabled();

			await user.click(option);
			await user.click(within(dialog).getByRole('button', { name: 'Bind to agent' }));

			// Same pattern as credential create/edit: close + a success toast,
			// no extra step and no permissions prompt.
			await waitFor(() => expect(dialog).not.toBeVisible());
			const toast = await screen.findByTestId('toast');
			expect(toast).toHaveTextContent('Credential bound');
			expect(toast).toHaveTextContent('Bound Stripe to support-agent.');
			expect(within(toast).queryByRole('link')).toBeNull();

			// The credential's agent roster refetched, so the card now lists it.
			const access = screen.getByTestId('hub-access');
			expect(
				await within(access).findByRole('link', { name: /support-agent/ }),
			).toBeVisible();
			expect(
				screen.queryByText('No agent is bound to these credentials yet.'),
			).not.toBeInTheDocument();
		});

		it('offers one card-level Bind action; the dialog picks among several credentials', async () => {
			const user = userEvent.setup();
			resetCredentialsStore([
				STRIPE_CRED,
				makeMockCredential({
					credential_id: 'cred_stripe_test',
					name: 'Stripe test',
					api: { vendor: 'stripe', name: 'stripe-api', version: '2024-01-01' },
				}),
			]);
			renderAt('/library/workspace/stripe/stripe-api/2024-01-01');

			// Rows carry no per-credential Bind — just the one action on the card.
			expect(await screen.findByRole('button', { name: 'View Stripe test' })).toBeVisible();
			expect(screen.queryByRole('button', { name: /^Bind .* to an agent$/ })).toBeNull();
			const actions = await screen.findAllByTestId('hub-access-bind-agent');
			expect(actions).toHaveLength(1);

			await user.click(actions[0]);
			const dialog = await screen.findByRole('dialog', { name: 'Bind to an agent' });
			const picker = within(dialog).getByLabelText('Credential');
			expect(picker).toHaveValue('cred_stripe_live');
			await user.selectOptions(picker, 'cred_stripe_test');
			expect(picker).toHaveValue('cred_stripe_test');
		});

		it('shows a create-an-agent empty state when there are no agents', async () => {
			const user = userEvent.setup();
			worker.use(
				http.get('/agents', () =>
					HttpResponse.json({ data: [], has_more: false, next_cursor: null }),
				),
			);
			renderAt('/library/workspace/stripe/stripe-api/2024-01-01');

			await user.click(await screen.findByTestId('hub-access-bind-agent'));
			const dialog = await screen.findByRole('dialog', { name: 'Bind to an agent' });
			expect(await within(dialog).findByText('No agents yet')).toBeVisible();
			expect(within(dialog).getByRole('link', { name: 'Go to Agents' })).toHaveAttribute(
				'href',
				'/agents',
			);
		});
	});
});
