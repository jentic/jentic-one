/**
 * WebhooksPage specs — the endpoint **list** console.
 *
 * Rendered under `AuthProvider` so the `webhooks:write` permission gate resolves
 * against the mocked `/users/me` admin user (same pattern as the Dashboard and
 * Monitor tests). The seeded token must match the root mock's `MOCK_TOKEN` or the
 * profile query never fires and every gated affordance stays hidden.
 *
 * The interaction model is now a **routed detail page** (not a drawer): the list
 * shows compact rows and clicking one NAVIGATES to `/webhooks/:endpointId`,
 * where Overview / Deliveries / Settings live (see WebhookEndpointDetailPage's
 * specs). These specs concentrate on the list-level properties that would be
 * genuinely dangerous to get wrong:
 *
 *  - clicking a row routes to that endpoint's detail page (a real link);
 *  - a signing secret is shown exactly once, and never appears in a list read;
 *  - the create form cannot express a combination the backend rejects;
 *  - a read-only viewer sees no mutating affordance on the list.
 *
 * This build ships outbound notifications only.
 */
import { describe, it, expect, beforeEach } from 'vitest';
import { http, HttpResponse } from 'msw';
import { page } from '@vitest/browser/context';
import { MemoryRouter, Routes, Route, useParams } from 'react-router';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor, within, userEvent } from '@/__tests__/test-utils';
import { worker } from '@/mocks/browser';
import { setToken } from '@/shared/api';
import { AuthProvider } from '@/shared/auth';
import { Toaster } from '@/shared/ui';
import { resetWebhooksStore, webhooksStoreEndpoints } from '@/modules/webhooks/mocks/handlers';
import WebhooksPage from '@/modules/webhooks/pages/WebhooksPage';

/** A sentinel detail route so a row click's navigation is observable. */
function DetailProbe() {
	const { endpointId } = useParams<{ endpointId: string }>();
	return <div data-testid="detail-probe">detail:{endpointId}</div>;
}

/**
 * Render the list page inside a real router with a sentinel detail route, so a
 * row's link navigates within the test rather than to a dead end. A fresh
 * QueryClient (retries off) mirrors `renderWithProviders`.
 */
function renderPage() {
	const queryClient = new QueryClient({
		defaultOptions: {
			queries: { retry: false, gcTime: 0 },
			mutations: { retry: false },
		},
	});
	return render(
		<QueryClientProvider client={queryClient}>
			<MemoryRouter initialEntries={['/webhooks']}>
				<AuthProvider>
					<Routes>
						<Route path="/webhooks" element={<WebhooksPage />} />
						<Route path="/webhooks/:endpointId" element={<DetailProbe />} />
					</Routes>
					<Toaster />
				</AuthProvider>
			</MemoryRouter>
		</QueryClientProvider>,
	);
}

/**
 * Wait until the `webhooks:write` gate has resolved.
 *
 * The endpoint list and the write-only affordances resolve from two independent
 * queries — the list from `GET /webhooks/endpoints`, the gate from the profile
 * query behind `AuthProvider`. The list can therefore paint while `canWrite` is
 * still false, so any spec that clicks a mutating control must wait for the gate
 * rather than for the list, or it races the profile fetch under load.
 */
async function waitForWriteAffordances(): Promise<void> {
	await screen.findByRole('button', { name: /New endpoint/i });
}

/** Re-render as a viewer holding only `webhooks:read`. */
function asReadOnlyUser() {
	worker.use(
		http.get('/users/me', () =>
			HttpResponse.json({
				id: '00000000-0000-0000-0000-000000000002',
				email: 'viewer@local',
				first_name: 'Read',
				last_name: 'Only',
				active: true,
				permissions: ['webhooks:read'],
				must_change_password: false,
				created_at: '2026-01-01T00:00:00Z',
				updated_at: null,
			}),
		),
	);
}

describe('WebhooksPage', () => {
	beforeEach(async () => {
		await page.viewport(1280, 900);
		// Must match the root mock's MOCK_TOKEN so `/users/me` authenticates.
		setToken('mock-access-token');
		resetWebhooksStore();
	});

	it('lists notification endpoints without a redundant type pill', async () => {
		renderPage();
		await screen.findByText('slack-ops-alerts');
		// There is only one webhook type, so no "outbound" pill clutters the row.
		expect(screen.queryByText('outbound')).toBeNull();
		// The row still shows where events are POSTed.
		expect(
			screen.getByText('https://hooks.example.com/services/T000/B000/XXXX'),
		).toBeInTheDocument();
	});

	it('navigates to the endpoint detail route when a row is clicked', async () => {
		const user = userEvent.setup();
		renderPage();

		// The row is a real link to the detail route (one keyboard/AT target).
		const link = await screen.findByRole('link', { name: /Open slack-ops-alerts/i });
		expect(link).toHaveAttribute('href', '/webhooks/whe_000000000000000000000001');

		await user.click(link);

		// The click routes to `/webhooks/:endpointId` — the detail probe renders
		// with the clicked endpoint's id.
		const probe = await screen.findByTestId('detail-probe');
		expect(probe).toHaveTextContent('detail:whe_000000000000000000000001');
	});

	it('shows a health overview strip aggregated from per-endpoint stats', async () => {
		renderPage();
		const strip = await screen.findByRole('region', { name: /Webhooks health overview/i });

		// The endpoint tile is exact from the list itself (never stats-gated):
		// two seeded endpoints, one active and one paused.
		expect(within(strip).getByText('Endpoints')).toBeInTheDocument();
		expect(within(strip).getByText('1 active · 1 paused')).toBeInTheDocument();

		// Delivery tiles roll up the per-endpoint /stats. In the last 24h endpoint 1
		// sent 2 (one succeeded, one dead-lettered); endpoint 2 sent none. So the
		// success rate is 50% and 2 deliveries need attention (1 dead + 1 retrying).
		await waitFor(() =>
			expect(within(strip).getByText('Success rate · 24h')).toBeInTheDocument(),
		);
		await waitFor(() => expect(within(strip).getByText('50%')).toBeInTheDocument());
		// The attention tile breaks its count down (1 dead-lettered + 1 retrying),
		// which is unambiguous where a bare "2" would collide with the endpoint tile.
		await waitFor(() =>
			expect(within(strip).getByText('1 dead · 1 retrying')).toBeInTheDocument(),
		);
	});

	it('surfaces status, delivery health, last-delivery time and event count on a card', async () => {
		renderPage();

		// The active endpoint's card: identity badge, active badge, a health pill
		// reflecting the dead-lettered delivery, a last-delivery relative time, and
		// its event count.
		const card = await screen.findByRole('link', { name: /Open slack-ops-alerts/i });
		// The card is led by the shared identity tile (monogram + accessible label).
		expect(
			within(card).getByRole('img', { name: /Webhook slack-ops-alerts/i }),
		).toBeInTheDocument();
		expect(within(card).getByText('active')).toBeInTheDocument();
		expect(within(card).getByText('2 event types')).toBeInTheDocument();
		// A relative last-delivery time appears once stats load (the seed's most
		// recent attempt is ~30m old); before that the card reads "never".
		await waitFor(() => expect(within(card).getByText(/\bago$/)).toBeInTheDocument());

		// Health is per-endpoint and text-carrying (never colour alone): the seed
		// has a dead-lettered delivery, so once stats load the pill says so and is
		// aria-labelled with the endpoint for out-of-context screen-reader use.
		await waitFor(() =>
			expect(
				within(card).getByLabelText(/Delivery health for slack-ops-alerts/i),
			).toHaveTextContent(/dead-lettered/i),
		);

		// The paused endpoint with no deliveries reads paused + idle, and "All
		// events" when it subscribes to none.
		const pausedCard = screen.getByRole('link', { name: /Open pagerduty-escalations/i });
		expect(within(pausedCard).getByText('paused')).toBeInTheDocument();
		expect(within(pausedCard).getByText('All events')).toBeInTheDocument();
		await waitFor(() =>
			expect(
				within(pausedCard).getByLabelText(/Delivery health for pagerduty-escalations/i),
			).toHaveTextContent(/No deliveries yet/i),
		);
	});

	it('renders endpoints as identity-led cards in a two-up grid', async () => {
		renderPage();

		// Both seeded endpoints render as their own card (a single link each), each
		// led by the shared AgentBadge identity tile — the Toolkits-style grid.
		await screen.findByRole('link', { name: /Open slack-ops-alerts/i });
		expect(
			screen.getByRole('link', { name: /Open pagerduty-escalations/i }),
		).toBeInTheDocument();
		const badges = screen.getAllByRole('img', { name: /^Webhook / });
		expect(badges.length).toBe(2);
	});

	it('reveals a new secret once, gated behind an explicit acknowledgement', async () => {
		const user = userEvent.setup();
		renderPage();
		await waitForWriteAffordances();

		await user.click(screen.getByRole('button', { name: /New endpoint/i }));
		await user.type(screen.getByLabelText('Name'), 'billing-relay');
		await user.type(screen.getByLabelText('Target URL'), 'https://example.com/hooks/jentic');
		await user.click(screen.getByRole('button', { name: 'Create endpoint' }));

		const dialog = await screen.findByRole('dialog', { name: /secret/i });
		expect(within(dialog).getByText(/only time this secret is shown/i)).toBeInTheDocument();

		// Done stays disabled until the operator confirms they stored it — the
		// value is unrecoverable, so a stray dismissal must not be enough.
		const done = within(dialog).getByRole('button', { name: 'Done' });
		expect(done).toBeDisabled();
		await user.click(within(dialog).getByText(/stored this secret somewhere safe/i));
		expect(done).toBeEnabled();
		await user.click(done);

		await waitFor(() => expect(screen.queryByRole('dialog', { name: /secret/i })).toBeNull());
		expect(await screen.findByText('billing-relay')).toBeInTheDocument();
	});

	it('never renders secret material for an endpoint that already exists', async () => {
		renderPage();
		await screen.findByText('slack-ops-alerts');
		// A read can't return one, so nothing on the list may look like a secret.
		expect(screen.queryByText(/whsec_/)).toBeNull();
	});

	it('shows the notification create fields, including the event-type picker', async () => {
		const user = userEvent.setup();
		renderPage();
		await waitForWriteAffordances();
		await user.click(screen.getByRole('button', { name: /New endpoint/i }));

		// Notification: a target URL and the event-types picker (the two-way mode
		// switch, no longer a free-text field).
		expect(screen.getByLabelText('Target URL')).toBeInTheDocument();
		expect(screen.getByRole('radio', { name: /Everything/i })).toBeInTheDocument();
		expect(screen.getByRole('radio', { name: /Only specific types/i })).toBeInTheDocument();
	});

	it('treats an empty event-type selection as “everything” and lets you narrow it', async () => {
		const user = userEvent.setup();
		renderPage();
		await waitForWriteAffordances();
		await user.click(screen.getByRole('button', { name: /New endpoint/i }));

		// Empty selection = "Everything" mode is the active choice by default.
		expect(screen.getByRole('radio', { name: /Everything/i })).toHaveAttribute(
			'aria-checked',
			'true',
		);

		// Switch to specific types, then tick one — the created endpoint carries it.
		await user.click(screen.getByRole('radio', { name: /Only specific types/i }));
		await user.click(await screen.findByRole('checkbox', { name: 'Execution failed' }));

		await user.type(screen.getByLabelText('Name'), 'exec-alerts');
		await user.type(screen.getByLabelText('Target URL'), 'https://example.com/hooks/jentic');
		await user.click(screen.getByRole('button', { name: 'Create endpoint' }));

		const dialog = await screen.findByRole('dialog', { name: /secret/i });
		await user.click(within(dialog).getByText(/stored this secret somewhere safe/i));
		await user.click(within(dialog).getByRole('button', { name: 'Done' }));

		await waitFor(() => {
			const created = webhooksStoreEndpoints().find((e) => e.name === 'exec-alerts');
			expect(created?.event_types).toEqual(['execution.failed']);
		});
	});

	it('offers the relay guide, with the real signature scheme and payload shape', async () => {
		const user = userEvent.setup();
		renderPage();
		await screen.findByText('slack-ops-alerts');

		await user.click(screen.getByRole('button', { name: 'Relay guide' }));
		const dialog = await screen.findByRole('dialog');
		expect(within(dialog).getByText('webhook-signature')).toBeInTheDocument();
		await user.click(within(dialog).getByText(/Show the relay code/i));
		expect(within(dialog).getByText(/hmac\.compare_digest/i)).toBeInTheDocument();
	});

	it('surfaces the server’s target-URL rejection reason on the field, not in a toast', async () => {
		// The backend refuses a disallowed URL at create with a `target_url …`
		// message; the form must pin that to the field. Client validation is
		// structural only, so a well-formed but disallowed URL reaches the server.
		worker.use(
			http.post('/webhooks/endpoints', () =>
				HttpResponse.json(
					{ detail: 'target_url is not allowed: resolves to a private address' },
					{ status: 400 },
				),
			),
		);
		const user = userEvent.setup();
		renderPage();
		await waitForWriteAffordances();

		await user.click(screen.getByRole('button', { name: /New endpoint/i }));
		await user.type(screen.getByLabelText('Name'), 'blocked');
		await user.type(screen.getByLabelText('Target URL'), 'https://10.0.0.1/hook');
		await user.click(screen.getByRole('button', { name: 'Create endpoint' }));

		// The reason is rendered as a field-level alert tied to the URL input.
		const alert = await screen.findByText(/target_url is not allowed/i);
		expect(alert).toBeInTheDocument();
		expect(screen.getByLabelText('Target URL') as HTMLInputElement).toHaveAttribute(
			'aria-invalid',
			'true',
		);
	});

	it('rejects a structurally-invalid URL client-side before any request', async () => {
		const user = userEvent.setup();
		renderPage();
		await waitForWriteAffordances();

		await user.click(screen.getByRole('button', { name: /New endpoint/i }));
		await user.type(screen.getByLabelText('Name'), 'typo');
		const urlField = screen.getByLabelText('Target URL');
		await user.click(urlField);
		await user.type(urlField, 'not-a-url');
		await user.click(screen.getByRole('button', { name: 'Create endpoint' }));

		expect(await screen.findByText(/Enter a full URL/i)).toBeInTheDocument();
		// Nothing was created — the client caught it.
		expect(webhooksStoreEndpoints().find((e) => e.name === 'typo')).toBeUndefined();
	});

	it('hides every mutating affordance from a read-only viewer on the list', async () => {
		asReadOnlyUser();
		renderPage();
		await screen.findByText('slack-ops-alerts');

		expect(screen.queryByRole('button', { name: /New endpoint/i })).toBeNull();
		// The row is still a link (read-only viewers can look at the detail).
		expect(screen.getByRole('link', { name: /Open slack-ops-alerts/i })).toBeInTheDocument();
		expect(screen.getByText(/read-only access/i)).toBeInTheDocument();
	});

	it('shows a create-first empty state (and no overview strip) when there are no endpoints', async () => {
		worker.use(http.get('/webhooks/endpoints', () => HttpResponse.json({ data: [] })));
		renderPage();
		await waitForWriteAffordances();

		// The strip is only meaningful with endpoints — a fresh workspace sees the
		// call-to-action instead of a row of zeroes.
		expect(await screen.findByText(/No webhook endpoints yet/i)).toBeInTheDocument();
		expect(screen.queryByRole('region', { name: /Webhooks health overview/i })).toBeNull();
		expect(
			screen.getByRole('button', { name: /Create your first endpoint/i }),
		).toBeInTheDocument();
	});
});
