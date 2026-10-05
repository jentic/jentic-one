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
import { CredentialType, setToken } from '@/shared/api';
import { clearAllToasts, Toaster } from '@/shared/ui';
import { Link, Route, Routes, useNavigate } from 'react-router';
import ApiDetailPage from '@/modules/workspace/pages/ApiDetailPage';
import { AuthProvider } from '@/shared/auth/AuthContext';
import { makeMockCredential, resetCredentialsStore } from '@/shared/credentials/mocks/handlers';
import { patchMockApi } from '@/modules/workspace/mocks/handlers';
import { resetAgentsStore, seedCredentialBindings } from '@/modules/agents/mocks/handlers';

/**
 * Settle the PageHeader entrance animation before asserting (framer-motion's
 * opacity would otherwise trip the a11y colour-contrast checks).
 */
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

	it('says "Update available" once: the Overview banner, else the badge by Back', async () => {
		const user = userEvent.setup();
		patchMockApi('stripe/stripe-api/2024-01-01', { update_available: true });
		try {
			renderAt('/library/workspace/stripe/stripe-api/2024-01-01');
			expect(await screen.findByTestId('workspace-update-available')).toBeInTheDocument();
			const state = screen.getByTestId('hub-state');
			// The banner says it on Overview; the badges row keeps Live only.
			expect(within(state).queryByTestId('api-state-update')).not.toBeInTheDocument();
			expect(within(state).getByTestId('api-state-live')).toBeInTheDocument();
			// Off Overview (no banner) the badge is back.
			await user.click(screen.getByRole('tab', { name: /versions/i }));
			expect(await within(state).findByTestId('api-state-update')).toBeInTheDocument();
			expect(screen.getByTestId('workspace-update-available')).not.toBeVisible();
		} finally {
			patchMockApi('stripe/stripe-api/2024-01-01', { update_available: false });
		}
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

		/** Library (its workspace panel) → (push) hub, so there's real history behind the hub. */
		function renderFromWorkspace() {
			return renderWithProviders(
				<Routes>
					<Route
						path="/library"
						element={
							<div data-testid="library-page">
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
				{ route: '/library' },
			);
		}

		async function visitTabs(user: ReturnType<typeof userEvent.setup>) {
			await user.click(screen.getByRole('link', { name: 'open hub' }));
			await screen.findByTestId('workspace-overview-strip');
			for (const name of [/operations/i, /versions/i, /spec/i, /overview/i, /versions/i]) {
				await user.click(screen.getByRole('tab', { name }));
			}
		}

		it('Back returns to where the hub was opened from', async () => {
			const user = userEvent.setup();
			renderFromWorkspace();
			await user.click(screen.getByRole('link', { name: 'open hub' }));
			await screen.findByTestId('workspace-overview-strip');
			await user.click(screen.getByTestId('back-button'));
			expect(await screen.findByTestId('library-page')).toBeInTheDocument();
		});

		it('Back leaves the hub in one step after several tab switches', async () => {
			const user = userEvent.setup();
			renderFromWorkspace();
			await visitTabs(user);
			const back = screen.getByTestId('back-button');
			expect(back.tagName).toBe('BUTTON');
			expect(back).toHaveTextContent(/^Back$/);
			await user.click(back);
			expect(await screen.findByTestId('library-page')).toBeInTheDocument();
		});

		it('Back falls back to the Library on a direct visit', async () => {
			const user = userEvent.setup();
			renderWithProviders(
				<Routes>
					<Route path="/library" element={<div data-testid="library-page" />} />
					<Route path={PATH} element={<ApiDetailPage />} />
				</Routes>,
				{ route: HUB },
			);
			await screen.findByTestId('workspace-overview-strip');
			await user.click(screen.getByTestId('back-button'));
			expect(await screen.findByTestId('library-page')).toBeInTheDocument();
		});

		it('tab switches replace the history entry, so browser Back skips them', async () => {
			const user = userEvent.setup();
			renderFromWorkspace();
			await visitTabs(user);
			await user.click(screen.getByRole('button', { name: 'browser back' }));
			expect(await screen.findByTestId('library-page')).toBeInTheDocument();
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

			const row = await screen.findByRole('button', { name: /^View Stripe live(\s|$)/ });
			expect(row).toHaveTextContent('Bearer token');
			// The badges are part of the row's name (no overriding aria-label).
			expect(row).toHaveAccessibleName(expect.stringContaining('Bearer token'));
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
			// Any version by default; this API's version is offered as the pin.
			expect(await screen.findByTestId('selected-api-summary')).toHaveTextContent(
				'stripe/stripe-api · any version',
			);
			const scope = screen.getByRole('group', { name: 'Use this credential for' });
			expect(within(scope).getByRole('button', { name: 'Any version' })).toHaveAttribute(
				'aria-pressed',
				'true',
			);
			expect(
				within(scope).getByRole('button', { name: 'Version 2024-01-01' }),
			).toHaveAttribute('aria-pressed', 'false');
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
				await screen.findByRole('button', { name: /^View Stripe any version(\s|$)/ }),
			).toBeVisible();
			expect(
				screen.queryByRole('button', { name: /^View Not Stripe(\s|$)/ }),
			).not.toBeInTheDocument();
			expect(screen.queryByTestId('hub-access-none')).not.toBeInTheDocument();
		});

		it('lists a credential pinned to this version, not one pinned to another', async () => {
			resetCredentialsStore([
				makeMockCredential({
					credential_id: 'cred_stripe_this',
					name: 'Stripe this version',
					api: { vendor: 'stripe', name: 'stripe-api', version: '2024-01-01' },
				}),
				makeMockCredential({
					credential_id: 'cred_stripe_other',
					name: 'Stripe other version',
					api: { vendor: 'stripe', name: 'stripe-api', version: '2023-06-01' },
				}),
			]);
			renderAt('/library/workspace/stripe/stripe-api/2024-01-01');

			expect(
				await screen.findByRole('button', { name: /^View Stripe this version(\s|$)/ }),
			).toBeVisible();
			expect(
				screen.queryByRole('button', { name: /^View Stripe other version(\s|$)/ }),
			).not.toBeInTheDocument();
			expect(screen.queryByTestId('hub-access-none')).not.toBeInTheDocument();
		});

		it('flags a missing credential when the only one is pinned to another version', async () => {
			resetCredentialsStore([
				makeMockCredential({
					credential_id: 'cred_stripe_other',
					name: 'Stripe other version',
					api: { vendor: 'stripe', name: 'stripe-api', version: '2023-06-01' },
				}),
			]);
			renderAt('/library/workspace/stripe/stripe-api/2024-01-01');

			expect(await screen.findByTestId('hub-access-none')).toHaveTextContent(
				/no agent can call it/i,
			);
		});

		describe('whether a credential is needed', () => {
			const STRIPE_SPEC_URL = '/apis/stripe/stripe-api/2024-01-01/openapi';

			it('flags a missing credential when the spec requires a scheme', async () => {
				resetCredentialsStore([]);
				renderAt('/library/workspace/stripe/stripe-api/2024-01-01');

				expect(await screen.findByTestId('hub-access-none')).toHaveTextContent(
					/no agent can call it/i,
				);
				expect(screen.queryByTestId('hub-access-no-auth')).not.toBeInTheDocument();
				expect(screen.getByTestId('hub-access-add-credential')).toBeInTheDocument();
			});

			it('says no credential is needed when schemes are declared but not required', async () => {
				resetCredentialsStore([]);
				worker.use(
					http.get(STRIPE_SPEC_URL, () =>
						HttpResponse.json({
							openapi: '3.1.0',
							info: { title: 'Stripe', version: '2024-01-01' },
							components: { securitySchemes: { bearerAuth: { type: 'http' } } },
							security: [],
							paths: { '/v1/charges': { get: { operationId: 'GetCharges' } } },
						}),
					),
				);
				renderAt('/library/workspace/stripe/stripe-api/2024-01-01');

				const state = await screen.findByTestId('hub-access-no-auth');
				expect(state).toHaveTextContent('No credential needed');
				expect(state).toHaveTextContent(/none of its operations require one/i);
				expect(screen.queryByTestId('hub-access-none')).not.toBeInTheDocument();
				// A credential is optional here, so it can still be added — and
				// an agent can be given access without one.
				expect(screen.getByTestId('hub-access-add-credential')).toBeInTheDocument();
				expect(screen.getByTestId('hub-access-give-agent-access')).toBeVisible();
				expect(screen.queryByTestId('hub-access-bind-agent')).not.toBeInTheDocument();
			});

			it('says no credential is needed, and offers none, when nothing is declared', async () => {
				resetCredentialsStore([]);
				renderAt('/library/workspace/bigco/big-api/1');

				const state = await screen.findByTestId('hub-access-no-auth');
				expect(state).toHaveTextContent('No credential needed');
				expect(state).toHaveTextContent(/doesn’t use authentication/i);
				expect(screen.queryByTestId('hub-access-none')).not.toBeInTheDocument();
				expect(screen.queryByTestId('hub-access-add-credential')).not.toBeInTheDocument();
				expect(screen.queryByTestId('hub-access-bind-agent')).not.toBeInTheDocument();
				expect(screen.getByTestId('hub-access-give-agent-access')).toBeVisible();
			});
		});

		describe('Give an agent access (no credential needed)', () => {
			const BIGCO = '/library/workspace/bigco/big-api/1';

			/** Record every `POST /credentials` body, passing through to the mock. */
			function recordCreates(): unknown[] {
				const bodies: unknown[] = [];
				worker.events.on('request:start', ({ request }) => {
					if (
						request.method === 'POST' &&
						new URL(request.url).pathname === '/credentials'
					)
						void request
							.clone()
							.json()
							.then((b: unknown) => bodies.push(b));
				});
				return bodies;
			}
			afterEach(() => worker.events.removeAllListeners());

			it('creates the no-auth credential only when Bind is confirmed, then grants the rules', async () => {
				const user = userEvent.setup();
				resetCredentialsStore([]);
				const bodies = recordCreates();
				const ruleBodies: unknown[] = [];
				worker.events.on('request:start', ({ request }) => {
					if (request.method === 'PUT' && request.url.includes('/permissions'))
						void request
							.clone()
							.json()
							.then((b: unknown) => ruleBodies.push(b));
				});
				renderAt(BIGCO);

				await user.click(await screen.findByTestId('hub-access-give-agent-access'));
				const dialog = await screen.findByRole('dialog', { name: 'Bind to an agent' });
				expect(within(dialog).getByTestId('bind-agent-create-note')).toHaveTextContent(
					'BigCo (no auth)',
				);
				// Opening the dialog creates nothing.
				expect(bodies).toHaveLength(0);

				await user.click(
					await within(dialog).findByRole('checkbox', { name: 'support-agent' }),
				);
				// Nothing preselected: Bind waits for a rules choice.
				expect(within(dialog).getByTestId('bind-agent-confirm')).toBeDisabled();
				await user.click(
					within(dialog).getByRole('radio', { name: /Allow all operations/ }),
				);
				await user.click(within(dialog).getByTestId('bind-agent-confirm'));

				await waitFor(() => expect(bodies).toHaveLength(1));
				// No secret, and for any version like every Add credential default.
				expect(bodies[0]).toEqual({
					type: 'no_auth',
					name: 'BigCo (no auth)',
					provider: 'static',
					api: { vendor: 'bigco', name: 'big-api' },
				});
				await waitFor(() => expect(ruleBodies).toHaveLength(1));
				expect(ruleBodies[0]).toEqual([{ effect: 'allow', path: '.*' }]);
				await waitFor(() => expect(dialog).not.toBeVisible());
				// The card lists the credential and the agent — not blocked.
				const access = screen.getByTestId('hub-access');
				expect(
					await within(access).findByRole('button', {
						name: /^View BigCo \(no auth\)(\s|$)/,
					}),
				).toBeInTheDocument();
				const agent = await within(access).findByRole('link', { name: /support-agent/ });
				await waitFor(() =>
					expect(within(agent).queryByTestId('hub-access-agent-checking')).toBeNull(),
				);
				expect(within(agent).queryByTestId('hub-access-agent-blocked')).toBeNull();
			});

			it('cancelling leaves no credential behind', async () => {
				const user = userEvent.setup();
				resetCredentialsStore([]);
				const bodies = recordCreates();
				renderAt(BIGCO);
				await user.click(await screen.findByTestId('hub-access-give-agent-access'));
				const dialog = await screen.findByRole('dialog', { name: 'Bind to an agent' });
				await user.click(within(dialog).getByRole('button', { name: 'Cancel' }));
				await waitFor(() => expect(dialog).not.toBeVisible());
				expect(bodies).toHaveLength(0);
			});

			it('is not offered on a draft API, which claims nothing about credentials', async () => {
				resetCredentialsStore([]);
				renderAt('/library/workspace/adyen/pos-terminal-management-api/1');
				expect(await screen.findByTestId('hub-access-draft')).toHaveTextContent(/draft/);
				expect(screen.queryByText('No credential needed')).toBeNull();
				expect(screen.queryByTestId('hub-access-give-agent-access')).toBeNull();
			});

			it("reuses the API's existing no-auth credential instead of creating another", async () => {
				const user = userEvent.setup();
				resetCredentialsStore([
					makeMockCredential({
						credential_id: 'cred_big_other',
						name: 'BigCo key',
						api: { vendor: 'bigco', name: 'big-api', version: '' },
					}),
					makeMockCredential({
						credential_id: 'cred_big_noauth',
						name: 'BigCo open access',
						type: CredentialType.NO_AUTH,
						details: {},
						api: { vendor: 'bigco', name: 'big-api', version: '' },
					}),
				]);
				const bodies = recordCreates();
				renderAt(BIGCO);

				// A covering credential already exists, so the card lists it; the
				// Bind action defaults to the no-auth one (no secret involved).
				await user.click(await screen.findByTestId('hub-access-bind-agent'));
				const dialog = await screen.findByRole('dialog', { name: 'Bind to an agent' });
				expect(within(dialog).getByLabelText('Credential')).toHaveValue('cred_big_noauth');
				expect(bodies).toHaveLength(0);
			});

			it('surfaces a failed create in the dialog and binds nothing', async () => {
				const user = userEvent.setup();
				resetCredentialsStore([]);
				worker.use(
					http.post('/credentials', () =>
						HttpResponse.json(
							{ detail: 'Credential store unavailable' },
							{ status: 500 },
						),
					),
				);
				renderAt(BIGCO);

				await user.click(await screen.findByTestId('hub-access-give-agent-access'));
				const dialog = await screen.findByRole('dialog', { name: 'Bind to an agent' });
				await user.click(
					await within(dialog).findByRole('checkbox', { name: 'support-agent' }),
				);
				await user.click(within(dialog).getByRole('radio', { name: /Read-only/ }));
				await user.click(within(dialog).getByTestId('bind-agent-confirm'));
				expect(await within(dialog).findByRole('alert')).toHaveTextContent(
					"Couldn't create the no-auth credential",
				);
				expect(dialog).toBeVisible();
			});

			it('is not offered to a viewer who can neither create credentials nor bind', async () => {
				resetCredentialsStore([]);
				worker.use(
					http.get('/users/me', () =>
						HttpResponse.json({
							id: 'usr_member',
							email: 'member@test.local',
							first_name: 'Mem',
							last_name: 'Ber',
							permissions: ['agents:write'],
							must_change_password: false,
							created_at: '2026-01-01T00:00:00Z',
							updated_at: null,
						}),
					),
				);
				renderWithProviders(
					<AuthProvider>
						<ApiDetailPage />
					</AuthProvider>,
					{ route: BIGCO, path: PATH },
				);

				// Binds, but can't create the no-auth credential it would need.
				const state = await screen.findByTestId('hub-access-no-auth');
				expect(state).toHaveTextContent('No credential needed');
				expect(state).toHaveTextContent(/needs access bound to it/i);
				expect(screen.queryByTestId('hub-access-give-agent-access')).toBeNull();
			});
		});

		it('lays the Overview out: Who can use it across, then Notes | Calls', async () => {
			// Calls are admin-only; the default `/users/me` mock is an org:admin
			// (served for its mock token).
			setToken('mock-access-token');
			renderWithProviders(
				<AuthProvider>
					<ApiDetailPage />
				</AuthProvider>,
				{ route: '/library/workspace/stripe/stripe-api/2024-01-01', path: PATH },
			);

			const blocks = await screen.findByTestId('hub-overview-blocks');
			// Who can use it spans the Overview, Agents and Credentials in columns.
			const access = within(blocks).getByTestId('hub-access');
			expect(access.parentElement).toBe(blocks);
			expect(await within(access).findByTestId('hub-access-columns')).toHaveClass(
				'md:grid-cols-2',
			);
			// Then the pair: Calls first in DOM (the narrow order), Notes moved
			// left on wide screens.
			const pair = within(blocks).getByTestId('hub-overview-pair');
			expect(pair).toHaveClass('lg:grid-cols-2', 'items-stretch');
			const usage = await within(pair).findByTestId('hub-usage');
			const notes = await within(pair).findByTestId('hub-notes');
			expect(usage.compareDocumentPosition(notes) & Node.DOCUMENT_POSITION_FOLLOWING).toBe(
				Node.DOCUMENT_POSITION_FOLLOWING,
			);
			expect(notes).toHaveClass('lg:order-first');
			expect(access.compareDocumentPosition(pair) & Node.DOCUMENT_POSITION_FOLLOWING).toBe(
				Node.DOCUMENT_POSITION_FOLLOWING,
			);
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
			// Only ACTIVE agents: pending, disabled, archived and rejected are left out.
			const option = await within(dialog).findByRole('checkbox', {
				name: 'support-agent',
			});
			for (const inactive of ['spammy-bot', 'legacy-scraper', 'inbox-triage-bot']) {
				expect(within(dialog).queryByText(inactive)).not.toBeInTheDocument();
			}
			expect(within(dialog).getByTestId('bind-agent-confirm')).toBeDisabled();

			await user.click(option);
			// Still disabled: nothing is preselected for the rules.
			expect(within(dialog).getByTestId('bind-agent-confirm')).toBeDisabled();
			await user.click(within(dialog).getByRole('radio', { name: /Read-only/ }));
			await user.click(within(dialog).getByRole('button', { name: 'Bind to agent' }));

			// Same pattern as credential create/edit: close + a success toast.
			await waitFor(() => expect(dialog).not.toBeVisible());
			const toast = await screen.findByTestId('toast');
			expect(toast).toHaveTextContent('Credential bound');
			expect(toast).toHaveTextContent(
				'Bound “Stripe live” to support-agent, with access rules.',
			);
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

		it('shows a bound agent with no allow rule as Blocked in "Who can use it"', async () => {
			resetAgentsStore();
			seedCredentialBindings([
				{ agent_id: 'agnt_active_1', credential_id: 'cred_stripe_live', permissions: [] },
			]);
			renderAt('/library/workspace/stripe/stripe-api/2024-01-01');
			const access = await screen.findByTestId('hub-access');
			const agent = await within(access).findByRole('link', { name: /support-agent/ });
			expect(await within(agent).findByTestId('hub-access-agent-blocked')).toHaveTextContent(
				'Blocked',
			);
			resetAgentsStore();
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
			expect(
				await screen.findByRole('button', { name: /^View Stripe test(\s|$)/ }),
			).toBeVisible();
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

		it('tells same-name credentials apart in the rows and the bind picker', async () => {
			const user = userEvent.setup();
			resetCredentialsStore([
				makeMockCredential({
					credential_id: 'cred_same_aaaaaa',
					name: 'Stripe key',
					api: { vendor: 'stripe', name: 'stripe-api', version: '' },
					details: { hint: '••••4242' },
				}),
				makeMockCredential({
					credential_id: 'cred_same_bbbbbb',
					name: 'Stripe key',
					api: { vendor: 'stripe', name: 'stripe-api', version: '' },
					details: { hint: '••••9999' },
				}),
			]);
			renderAt('/library/workspace/stripe/stripe-api/2024-01-01');

			const access = await screen.findByTestId('hub-access');
			const hints = await within(access).findAllByTestId('hub-access-credential-hint');
			expect(hints.map((h) => h.textContent)).toEqual(['••••4242', '••••9999']);
			expect(
				within(access).getByRole('button', { name: /^View Stripe key ••••9999(\s|$)/ }),
			).toBeVisible();
			expect(within(access).getByRole('heading', { name: 'Credentials' })).toBeVisible();

			await user.click(await screen.findByTestId('hub-access-bind-agent'));
			const dialog = await screen.findByRole('dialog', { name: 'Bind to an agent' });
			expect(within(dialog).getByRole('group', { name: 'Agents' })).toBeVisible();
			expect(
				within(dialog)
					.getAllByRole('option')
					.map((o) => o.textContent),
			).toEqual(['Stripe key · ••••4242', 'Stripe key · ••••9999']);
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
