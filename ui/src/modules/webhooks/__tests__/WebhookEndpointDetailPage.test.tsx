/**
 * WebhookEndpointDetailPage specs — the routed **detail page** for one endpoint.
 *
 * This page replaces the old slide-over drawer: clicking a list row navigates to
 * `/webhooks/:endpointId`, and Overview / Deliveries / Settings now live inline
 * in the page body (matching the Agents/Toolkits detail-page pattern). The active
 * sub-tab is deep-linked through `?tab=`, so these specs mount the page at a real
 * URL and assert against the route.
 *
 * Rendered under `AuthProvider` (so the `webhooks:write` gate resolves against
 * the mocked admin `/users/me`) with a real router carrying the `:endpointId`
 * param. The seeded token must match the root mock's `MOCK_TOKEN` or the profile
 * query never fires and every gated affordance stays hidden.
 *
 * The specs concentrate on the properties the detail page must not regress:
 *
 *  - the three tabs render, and `?tab=` deep-links straight to one;
 *  - Overview shows the aggregate delivery KPIs (from `/stats`);
 *  - Deliveries lists attempts, filters by status, and can resend a dead row;
 *  - Settings edits name / target URL / active inline (no drawer), surfaces the
 *    server's `target_url` rejection at the field, edits the subscription
 *    inline, hides the CIDR allowlist behind Advanced, rotates the secret
 *    (grace default), and can delete the endpoint;
 *  - the header carries no Edit affordance and there is no edit drawer;
 *  - an unknown id renders a not-found state, not a crash;
 *  - a read-only viewer sees no mutating affordance.
 */
import { describe, it, expect, beforeEach } from 'vitest';
import { http, HttpResponse } from 'msw';
import { page } from '@vitest/browser/context';
import { MemoryRouter, Routes, Route } from 'react-router';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor, within, userEvent } from '@/__tests__/test-utils';
import { worker } from '@/mocks/browser';
import { setToken } from '@/shared/api';
import { AuthProvider } from '@/shared/auth';
import { Toaster } from '@/shared/ui';
import { resetWebhooksStore, webhooksStoreEndpoints } from '@/modules/webhooks/mocks/handlers';
import WebhookEndpointDetailPage from '@/modules/webhooks/pages/WebhookEndpointDetailPage';

const ENDPOINT_ID = 'whe_000000000000000000000001';

/**
 * Render the detail page at a concrete `/webhooks/:endpointId` URL (optionally
 * with a `?tab=` deep-link) inside a real router, so `useParams`/`useSearchParams`
 * resolve. A fresh QueryClient (retries off) mirrors `renderWithProviders`.
 */
function renderDetail(initialUrl = `/webhooks/${ENDPOINT_ID}`) {
	const queryClient = new QueryClient({
		defaultOptions: {
			queries: { retry: false, gcTime: 0 },
			mutations: { retry: false },
		},
	});
	return render(
		<QueryClientProvider client={queryClient}>
			<MemoryRouter initialEntries={[initialUrl]}>
				<AuthProvider>
					<Routes>
						<Route
							path="/webhooks/:endpointId"
							element={<WebhookEndpointDetailPage />}
						/>
						<Route path="/webhooks" element={<div data-testid="list">list</div>} />
					</Routes>
					<Toaster />
				</AuthProvider>
			</MemoryRouter>
		</QueryClientProvider>,
	);
}

/** Wait until the `webhooks:write` gate has resolved (its actions painted). */
async function waitForWriteAffordances(): Promise<void> {
	await screen.findByRole('button', { name: 'Send test' });
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

describe('WebhookEndpointDetailPage', () => {
	beforeEach(async () => {
		await page.viewport(1280, 900);
		// Must match the root mock's MOCK_TOKEN so `/users/me` authenticates.
		setToken('mock-access-token');
		resetWebhooksStore();
	});

	it('renders the endpoint header and the three detail tabs, defaulting to Overview', async () => {
		renderDetail();

		// The header carries the endpoint name (title) and target URL (subtitle).
		expect(
			await screen.findByRole('heading', { name: 'slack-ops-alerts' }),
		).toBeInTheDocument();
		expect(
			screen.getByText('https://hooks.example.com/services/T000/B000/XXXX'),
		).toBeInTheDocument();

		// The three tabs are present, and Overview is the default selection.
		const tabs = screen.getByRole('tablist', { name: /Endpoint detail sections/i });
		expect(within(tabs).getByRole('tab', { name: /Overview/i })).toHaveAttribute(
			'aria-selected',
			'true',
		);
		expect(within(tabs).getByRole('tab', { name: /Deliveries/i })).toBeInTheDocument();
		expect(within(tabs).getByRole('tab', { name: /Settings/i })).toBeInTheDocument();

		// A back link to the list is present.
		expect(screen.getByRole('link', { name: /All webhooks/i })).toBeInTheDocument();
	});

	it('shows the aggregate delivery KPIs on Overview (from /stats)', async () => {
		renderDetail();
		await screen.findByRole('heading', { name: 'slack-ops-alerts' });

		// The KPI strip is a labelled group with the four delivery-health cards.
		const strip = await screen.findByRole('group', { name: /Delivery health/i });
		expect(within(strip).getByText(/Total deliveries/i)).toBeInTheDocument();
		expect(within(strip).getByText(/Last 24h/i)).toBeInTheDocument();
		expect(within(strip).getByText(/Success rate/i)).toBeInTheDocument();
		expect(within(strip).getByText(/Avg response/i)).toBeInTheDocument();
		// The aggregate resolves from /stats: the two seeded deliveries surface as
		// numeric KPI values (total and last-24h are both 2).
		await waitFor(() => expect(within(strip).getAllByText('2').length).toBeGreaterThan(0));
	});

	it('deep-links straight to a sub-tab via ?tab=', async () => {
		renderDetail(`/webhooks/${ENDPOINT_ID}?tab=deliveries`);

		// The Deliveries tab is selected on load, no click needed.
		const tabs = await screen.findByRole('tablist', { name: /Endpoint detail sections/i });
		expect(within(tabs).getByRole('tab', { name: /Deliveries/i })).toHaveAttribute(
			'aria-selected',
			'true',
		);
		// And its content — the Message attempts table — is showing.
		expect(await screen.findByText('Message attempts')).toBeInTheDocument();
	});

	it('switches tabs and pushes ?tab= so the sub-tab is bookmarkable', async () => {
		const user = userEvent.setup();
		renderDetail();
		const tabs = await screen.findByRole('tablist', { name: /Endpoint detail sections/i });

		await user.click(within(tabs).getByRole('tab', { name: /Settings/i }));
		expect(within(tabs).getByRole('tab', { name: /Settings/i })).toHaveAttribute(
			'aria-selected',
			'true',
		);
		// The Settings content (Configuration section) is now shown.
		expect(await screen.findByText('Configuration')).toBeInTheDocument();
	});

	it('lists delivery attempts and filters by status, keeping the dead-lettered row', async () => {
		const user = userEvent.setup();
		renderDetail(`/webhooks/${ENDPOINT_ID}?tab=deliveries`);
		await screen.findByText('Message attempts');

		// Scope to the delivery log region (the error text also appears inside a
		// hidden tooltip popup, so an unscoped query for it matches twice; scoping
		// to the region and asserting the status badges keeps the intent clear).
		const table = await screen.findByRole('region', { name: /Webhook delivery log/i });

		// Both seeded rows show, including the dead-lettered failure kept for triage.
		expect(await within(table).findByText('succeeded')).toBeInTheDocument();
		expect(await within(table).findByText('dead-lettered')).toBeInTheDocument();

		// Filtering to Succeeded hides the dead row.
		const filter = screen.getByRole('group', { name: /Filter deliveries by status/i });
		await user.click(within(filter).getByRole('button', { name: 'Succeeded' }));
		await waitFor(() => expect(within(table).queryByText('dead-lettered')).toBeNull());
		expect(within(table).getByText('succeeded')).toBeInTheDocument();
	});

	it('renders an expressive label + tooltip for a categorised delivery error', async () => {
		renderDetail(`/webhooks/${ENDPOINT_ID}?tab=deliveries`);
		await screen.findByText('Message attempts');
		const table = await screen.findByRole('region', { name: /Webhook delivery log/i });

		// The allowlist-blocked row (seeded as `blocked_by_allowlist`) renders the
		// human label, not the raw enum, and carries the remediation hint on an
		// accessible cell — reachable by keyboard/screen reader via its aria-label.
		const allowlistCell = await within(table).findByRole('button', {
			name: /Blocked by IP allowlist/i,
		});
		expect(allowlistCell).toHaveTextContent('Blocked by IP allowlist');
		expect(allowlistCell).toHaveAccessibleName(/allowed CIDR ranges/i);
		expect(allowlistCell).toHaveAccessibleName(/Settings → Advanced/i);
		// The raw category string is never shown to the user.
		expect(within(table).queryByText('blocked_by_allowlist')).toBeNull();

		// The dead-lettered HTTP row (seeded as `http_error_500`) reads "HTTP 500",
		// not the internal `http_error_500` token.
		const httpCell = await within(table).findByRole('button', { name: /HTTP 500/i });
		expect(httpCell).toHaveTextContent('HTTP 500');
		expect(within(table).queryByText('http_error_500')).toBeNull();
	});

	it('resends a dead-lettered delivery from the Deliveries tab', async () => {
		const user = userEvent.setup();
		renderDetail(`/webhooks/${ENDPOINT_ID}?tab=deliveries`);
		await waitForWriteAffordances();
		const table = await screen.findByRole('region', { name: /Webhook delivery log/i });
		const deadCell = await within(table).findByText('dead-lettered');

		const deadRow = deadCell.closest('tr') as HTMLElement;
		await user.click(within(deadRow).getByRole('button', { name: 'Resend' }));

		// Requeued: the row goes back to pending and stops being dead.
		await waitFor(() => expect(within(table).queryByText('dead-lettered')).toBeNull());
	});

	it('edits the subscription inline from Settings and saves it', async () => {
		const user = userEvent.setup();
		renderDetail(`/webhooks/${ENDPOINT_ID}?tab=settings`);
		await waitForWriteAffordances();
		await screen.findByText('Subscribed events');

		// The endpoint is subscribed to two types → the picker is in specific mode
		// with both ticked. Drop one and save via the inline Save events button.
		const credential = await screen.findByRole('checkbox', { name: 'Credential expired' });
		expect(credential).toHaveAttribute('aria-checked', 'true');
		await user.click(credential);

		await user.click(await screen.findByRole('button', { name: 'Save events' }));

		await waitFor(() => {
			const row = webhooksStoreEndpoints().find((e) => e.endpoint_id === ENDPOINT_ID);
			expect(row?.event_types).toEqual(['execution.failed']);
		});
		// No secret is ever revealed on a config edit.
		expect(screen.queryByText(/only time this secret is shown/i)).toBeNull();
	});

	it('keeps the CIDR allowlist tucked behind the Advanced disclosure', async () => {
		const user = userEvent.setup();
		renderDetail(`/webhooks/${ENDPOINT_ID}?tab=settings`);
		await waitForWriteAffordances();

		// The allowlist lives inside the Advanced "IP / CIDR allowlist" disclosure
		// (a native <details>), collapsed by default — assert it's tucked there.
		const summary = await screen.findByText('IP / CIDR allowlist');
		const details = summary.closest('details');
		expect(details).not.toBeNull();
		expect(details).not.toHaveAttribute('open');

		// Opening the disclosure reveals the CIDR field's add-range input.
		await user.click(summary);
		await waitFor(() => expect(details).toHaveAttribute('open'));
		expect(screen.getByLabelText('Add an IP or CIDR range')).toBeInTheDocument();
	});

	it('defaults rotation to a grace period and only warns when revoking now', async () => {
		const user = userEvent.setup();
		renderDetail(`/webhooks/${ENDPOINT_ID}?tab=settings`);
		await waitForWriteAffordances();

		await user.click(await screen.findByRole('button', { name: 'Rotate secret' }));
		const dialog = await screen.findByRole('dialog');
		// The graceful path is the default; the destructive one is opt-in.
		expect(within(dialog).queryByText(/starts failing at once/i)).toBeNull();
		await user.click(within(dialog).getByText(/Revoke the previous secret immediately/i));
		expect(within(dialog).getByText(/starts failing at once/i)).toBeInTheDocument();
	});

	it('edits name and target URL inline from Settings and saves them (no drawer)', async () => {
		const user = userEvent.setup();
		renderDetail(`/webhooks/${ENDPOINT_ID}?tab=settings`);
		await waitForWriteAffordances();
		await screen.findByText('Configuration');

		// The Configuration section is directly editable: the name/URL inputs are
		// prefilled from the endpoint, with no "Edit" button opening a drawer.
		expect(screen.queryByRole('button', { name: 'Edit' })).toBeNull();
		const nameField = (await screen.findByLabelText('Name')) as HTMLInputElement;
		await waitFor(() => expect(nameField.value).toBe('slack-ops-alerts'));
		const urlField = screen.getByLabelText('Target URL') as HTMLInputElement;
		expect(urlField.value).toBe('https://hooks.example.com/services/T000/B000/XXXX');

		// Save is hidden until the form is dirty; editing reveals it.
		expect(screen.queryByRole('button', { name: 'Save changes' })).toBeNull();
		await user.clear(nameField);
		await user.type(nameField, 'slack-ops-renamed');
		await user.clear(urlField);
		await user.type(urlField, 'https://hooks.example.com/services/T111/B111/YYYY');
		await user.click(await screen.findByRole('button', { name: 'Save changes' }));

		await waitFor(() => {
			const row = webhooksStoreEndpoints().find((e) => e.endpoint_id === ENDPOINT_ID);
			expect(row?.name).toBe('slack-ops-renamed');
			expect(row?.target_url).toBe('https://hooks.example.com/services/T111/B111/YYYY');
		});
		// No secret is ever revealed on a config edit.
		expect(screen.queryByText(/only time this secret is shown/i)).toBeNull();
	});

	it('surfaces the server target_url rejection inline at the field', async () => {
		const user = userEvent.setup();
		renderDetail(`/webhooks/${ENDPOINT_ID}?tab=settings`);
		await waitForWriteAffordances();
		await screen.findByText('Configuration');

		const urlField = (await screen.findByLabelText('Target URL')) as HTMLInputElement;
		await waitFor(() =>
			expect(urlField.value).toBe('https://hooks.example.com/services/T000/B000/XXXX'),
		);
		// A structurally-valid URL that the egress guard refuses (mock rejects the
		// `blocked.internal` host with a `target_url …` 400). The reason must land
		// at the field, not vanish into a toast.
		await user.clear(urlField);
		await user.type(urlField, 'https://blocked.internal/hook');
		await user.click(await screen.findByRole('button', { name: 'Save changes' }));

		const fieldError = await screen.findByRole('alert');
		expect(fieldError).toHaveTextContent(/target_url is not allowed/i);
		// The bad value was rejected, so the store keeps the original URL.
		const row = webhooksStoreEndpoints().find((e) => e.endpoint_id === ENDPOINT_ID);
		expect(row?.target_url).toBe('https://hooks.example.com/services/T000/B000/XXXX');
	});

	it('has no Edit affordance in the page header and no edit drawer', async () => {
		renderDetail();
		await waitForWriteAffordances();

		// Send test stays; the pencil/Edit action is gone entirely.
		expect(screen.getByRole('button', { name: 'Send test' })).toBeInTheDocument();
		expect(screen.queryByRole('button', { name: /Edit slack-ops-alerts/i })).toBeNull();
		// No slide-over edit sheet is mounted anywhere on the page.
		expect(screen.queryByRole('dialog', { name: /Edit webhook endpoint/i })).toBeNull();
	});

	it('deletes the endpoint and navigates back to the list', async () => {
		const user = userEvent.setup();
		renderDetail(`/webhooks/${ENDPOINT_ID}?tab=settings`);
		await waitForWriteAffordances();

		await user.click(await screen.findByRole('button', { name: /Delete slack-ops-alerts/i }));
		const dialog = await screen.findByRole('dialog');
		// The confirm requires typing/acknowledging, then deletes.
		await user.click(within(dialog).getByRole('button', { name: /Delete endpoint/i }));

		// The endpoint is gone from the store, and the page routed back to the list.
		await waitFor(() =>
			expect(
				webhooksStoreEndpoints().find((e) => e.endpoint_id === ENDPOINT_ID),
			).toBeUndefined(),
		);
		expect(await screen.findByTestId('list')).toBeInTheDocument();
	});

	it('renders a not-found state for an unknown endpoint id', async () => {
		renderDetail('/webhooks/whe_does_not_exist');
		expect(await screen.findByText(/Endpoint not found/i)).toBeInTheDocument();
		// Still offers a way back to the list.
		expect(screen.getByRole('link', { name: /All webhooks/i })).toBeInTheDocument();
	});

	it('hides every mutating affordance from a read-only viewer', async () => {
		asReadOnlyUser();
		renderDetail(`/webhooks/${ENDPOINT_ID}?tab=settings`);
		await screen.findByRole('heading', { name: 'slack-ops-alerts' });
		await screen.findByText('Configuration');

		expect(screen.queryByRole('button', { name: 'Send test' })).toBeNull();
		expect(screen.queryByRole('button', { name: /Edit slack-ops-alerts/i })).toBeNull();
		expect(screen.queryByRole('button', { name: 'Rotate secret' })).toBeNull();
		expect(screen.queryByRole('button', { name: /Delete endpoint/i })).toBeNull();
		// The subscription is shown read-only (badges), not as an editor.
		expect(screen.queryByRole('button', { name: 'Save events' })).toBeNull();
	});
});
