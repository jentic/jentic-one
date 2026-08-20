/**
 * WebhooksPage specs.
 *
 * Rendered under `AuthProvider` so the `webhooks:write` permission gate resolves
 * against the mocked `/users/me` admin user (same pattern as the Dashboard and
 * Monitor tests). The seeded token must match the root mock's `MOCK_TOKEN` or the
 * profile query never fires and every gated affordance stays hidden.
 *
 * The specs concentrate on the properties that would be genuinely dangerous to
 * get wrong, rather than on rendering trivia:
 *
 *  - a signing secret is shown exactly once, and never appears in a list read;
 *  - the create form cannot express a combination the backend rejects;
 *  - a read-only viewer sees no mutating affordance.
 *
 * This build ships outbound notifications only.
 */
import { describe, it, expect, beforeEach } from 'vitest';
import { http, HttpResponse } from 'msw';
import { page } from '@vitest/browser/context';
import { renderWithProviders, screen, waitFor, within, userEvent } from '@/__tests__/test-utils';
import { worker } from '@/mocks/browser';
import { setToken } from '@/shared/api';
import { AuthProvider } from '@/shared/auth';
import { Toaster } from '@/shared/ui';
import { resetWebhooksStore, webhooksStoreEndpoints } from '@/modules/webhooks/mocks/handlers';
import WebhooksPage from '@/modules/webhooks/pages/WebhooksPage';

function renderPage() {
	return renderWithProviders(
		<AuthProvider>
			<WebhooksPage />
			<Toaster />
		</AuthProvider>,
	);
}

/** The card for a named endpoint (each endpoint renders as its own block). */
async function cardFor(name: string): Promise<HTMLElement> {
	const heading = await screen.findByRole('button', {
		name: new RegExp(`delivery log for ${name}`, 'i'),
	});
	const card = heading.closest('div.rounded-xl');
	if (!card) throw new Error(`No endpoint card found for "${name}"`);
	return card as HTMLElement;
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

	it('lists notification endpoints and labels them as outbound', async () => {
		renderPage();
		await screen.findByText('slack-ops-alerts');
		expect(screen.getByText('outbound notification')).toBeInTheDocument();
	});

	it('offers “Send test” for a notification endpoint', async () => {
		renderPage();
		await waitForWriteAffordances();
		const notification = await cardFor('slack-ops-alerts');
		expect(within(notification).getByRole('button', { name: 'Send test' })).toBeInTheDocument();
	});

	it('reveals a new secret once, gated behind an explicit acknowledgement', async () => {
		const user = userEvent.setup();
		renderPage();
		await waitForWriteAffordances();

		await user.click(screen.getByRole('button', { name: /New endpoint/i }));
		await user.type(screen.getByLabelText('Name'), 'billing-relay');
		await user.type(screen.getByLabelText('Target URL'), 'https://example.com/hooks/jentic');
		await user.click(screen.getByRole('button', { name: 'Create endpoint' }));

		const dialog = await screen.findByRole('dialog');
		expect(within(dialog).getByText(/only time this secret is shown/i)).toBeInTheDocument();

		// Done stays disabled until the operator confirms they stored it — the
		// value is unrecoverable, so a stray dismissal must not be enough.
		const done = within(dialog).getByRole('button', { name: 'Done' });
		expect(done).toBeDisabled();
		await user.click(within(dialog).getByText(/stored this secret somewhere safe/i));
		expect(done).toBeEnabled();
		await user.click(done);

		await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
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

		// Notification: a target URL and the event-types picker (a labelled group,
		// no longer a free-text field).
		expect(screen.getByLabelText('Target URL')).toBeInTheDocument();
		expect(screen.getByRole('group', { name: 'Event types' })).toBeInTheDocument();
		// The picker offers real, described platform types — not a blank text box.
		expect(screen.getByRole('checkbox', { name: 'Credential expired' })).toBeInTheDocument();
		expect(screen.getByText(/A stored credential passed its expiry/i)).toBeInTheDocument();
	});

	it('treats an empty event-type selection as “all relayable events”', async () => {
		const user = userEvent.setup();
		renderPage();
		await waitForWriteAffordances();
		await user.click(screen.getByRole('button', { name: /New endpoint/i }));

		// With nothing selected, the picker states the subscribe-to-all default.
		expect(screen.getByText(/Subscribed to every relayable event/i)).toBeInTheDocument();

		// Selecting one flips the messaging and the created endpoint carries only it.
		await user.click(screen.getByRole('checkbox', { name: 'Execution failed' }));
		expect(screen.getByText(/1 event type selected/i)).toBeInTheDocument();

		await user.type(screen.getByLabelText('Name'), 'exec-alerts');
		await user.type(screen.getByLabelText('Target URL'), 'https://example.com/hooks/jentic');
		await user.click(screen.getByRole('button', { name: 'Create endpoint' }));

		const dialog = await screen.findByRole('dialog');
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
		// The guide must quote the real Standard-Webhooks headers and envelope.
		expect(within(dialog).getByText('webhook-signature')).toBeInTheDocument();
		// The relay code is collapsed behind a disclosure — open it, then assert the
		// verifier uses a constant-time comparison.
		await user.click(within(dialog).getByText(/Show the relay code/i));
		expect(within(dialog).getByText(/hmac\.compare_digest/i)).toBeInTheDocument();
	});

	it('edits an endpoint: opens prefilled, changes event types, and saves', async () => {
		const user = userEvent.setup();
		renderPage();
		await waitForWriteAffordances();

		const card = await cardFor('slack-ops-alerts');
		await user.click(within(card).getByRole('button', { name: 'Edit' }));

		// The edit sheet opens pre-filled from the endpoint, not blank.
		const nameField = screen.getByLabelText('Name') as HTMLInputElement;
		await waitFor(() => expect(nameField.value).toBe('slack-ops-alerts'));
		expect((screen.getByLabelText('Target URL') as HTMLInputElement).value).toBe(
			'https://hooks.example.com/services/T000/B000/XXXX',
		);
		// Its current subscription is reflected in the picker.
		expect(screen.getByRole('checkbox', { name: 'Credential expired' })).toHaveAttribute(
			'aria-checked',
			'true',
		);
		expect(screen.getByRole('checkbox', { name: 'Execution failed' })).toHaveAttribute(
			'aria-checked',
			'true',
		);

		// Drop one event type and rename, then save.
		await user.click(screen.getByRole('checkbox', { name: 'Credential expired' }));
		await user.clear(nameField);
		await user.type(nameField, 'slack-ops-renamed');
		await user.click(screen.getByRole('button', { name: 'Save changes' }));

		// No secret is ever revealed on an edit.
		await waitFor(() => {
			const row = webhooksStoreEndpoints().find((e) => e.name === 'slack-ops-renamed');
			expect(row).toBeDefined();
			expect(row?.event_types).toEqual(['execution.failed']);
		});
		expect(screen.queryByText(/only time this secret is shown/i)).toBeNull();
		expect(await screen.findByText('slack-ops-renamed')).toBeInTheDocument();
	});

	it('can pause an endpoint by toggling active off in the edit sheet', async () => {
		const user = userEvent.setup();
		renderPage();
		await waitForWriteAffordances();

		const card = await cardFor('slack-ops-alerts');
		await user.click(within(card).getByRole('button', { name: 'Edit' }));

		await user.click(screen.getByRole('checkbox', { name: 'Endpoint active' }));
		await user.click(screen.getByRole('button', { name: 'Save changes' }));

		await waitFor(() => {
			const row = webhooksStoreEndpoints().find((e) => e.name === 'slack-ops-alerts');
			expect(row?.active).toBe(false);
		});
	});

	it('opens a blank form for New endpoint even after editing an existing one', async () => {
		// Regression: the shared create/edit sheet used to keep the edited
		// endpoint's values when reopened via New endpoint, because create mode
		// never cleared the draft the edit had seeded.
		const user = userEvent.setup();
		renderPage();
		await waitForWriteAffordances();

		// Edit an existing endpoint (seeds the form), then dismiss without saving.
		const card = await cardFor('slack-ops-alerts');
		await user.click(within(card).getByRole('button', { name: 'Edit' }));
		const nameField = screen.getByLabelText('Name') as HTMLInputElement;
		await waitFor(() => expect(nameField.value).toBe('slack-ops-alerts'));
		await user.click(screen.getByRole('button', { name: 'Cancel' }));

		// Now open the create sheet — it must be blank, not carrying the edit.
		await user.click(screen.getByRole('button', { name: /New endpoint/i }));
		expect((screen.getByLabelText('Name') as HTMLInputElement).value).toBe('');
		expect((screen.getByLabelText('Target URL') as HTMLInputElement).value).toBe('');
		expect(screen.getByRole('checkbox', { name: 'Credential expired' })).toHaveAttribute(
			'aria-checked',
			'false',
		);
	});

	it('expands an endpoint to show its delivery log, including a dead-lettered row', async () => {
		const user = userEvent.setup();
		renderPage();
		await user.click(
			await screen.findByRole('button', { name: /Show delivery log for slack-ops-alerts/i }),
		);

		await screen.findByText('Delivery log');
		// The log is a separate query, so the rows arrive after the panel itself.
		expect(await screen.findByText('succeeded')).toBeInTheDocument();
		// Dead rows are kept rather than deleted so a failure can be diagnosed.
		expect(await screen.findByText('dead-lettered')).toBeInTheDocument();
		expect(screen.getByText('HTTP 500 from receiver')).toBeInTheDocument();
	});

	it('can resend a dead-lettered delivery', async () => {
		const user = userEvent.setup();
		renderPage();
		await waitForWriteAffordances();
		await user.click(
			await screen.findByRole('button', { name: /Show delivery log for slack-ops-alerts/i }),
		);
		await screen.findByText('dead-lettered');

		const deadRow = (await screen.findByText('HTTP 500 from receiver')).closest(
			'tr',
		) as HTMLElement;
		await user.click(within(deadRow).getByRole('button', { name: 'Resend' }));

		// Requeued: the row goes back to pending and stops being dead.
		await waitFor(() => expect(screen.queryByText('dead-lettered')).toBeNull());
	});

	it('defaults rotation to a grace period and only warns when revoking now', async () => {
		const user = userEvent.setup();
		renderPage();
		await waitForWriteAffordances();
		const card = await cardFor('slack-ops-alerts');
		await user.click(within(card).getByRole('button', { name: 'Rotate secret' }));

		const dialog = await screen.findByRole('dialog');
		// The graceful path is the default; the destructive one is opt-in.
		expect(within(dialog).queryByText(/starts failing at once/i)).toBeNull();
		await user.click(within(dialog).getByText(/Revoke the previous secret immediately/i));
		expect(within(dialog).getByText(/starts failing at once/i)).toBeInTheDocument();
	});

	it('hides every mutating affordance from a read-only viewer', async () => {
		asReadOnlyUser();
		renderPage();
		await screen.findByText('slack-ops-alerts');

		expect(screen.queryByRole('button', { name: /New endpoint/i })).toBeNull();
		expect(screen.queryByRole('button', { name: 'Edit' })).toBeNull();
		expect(screen.queryByRole('button', { name: 'Rotate secret' })).toBeNull();
		expect(screen.queryByRole('button', { name: 'Delete' })).toBeNull();
		expect(screen.queryByRole('button', { name: 'Send test' })).toBeNull();
		expect(screen.getByText(/read-only access/i)).toBeInTheDocument();
	});
});
