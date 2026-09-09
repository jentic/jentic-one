import { describe, it, expect, beforeEach } from 'vitest';
import { page } from 'vitest/browser';
import { http, HttpResponse } from 'msw';
import { renderWithProviders, screen, within, userEvent, checkA11y } from '@/__tests__/test-utils';
import { worker } from '@/mocks/browser';
import { setToken, type EventResponse } from '@/shared/api';
import { Toaster } from '@/shared/ui';
import { AgentStreamProvider, useAgentStream } from '@/shared/lib/agentStream';
import { resetSettingsStore } from '@/modules/settings/mocks/handlers';
import { OAuthClientsSection } from '@/modules/settings/pages/OAuthClientsSection';

function renderSection(route = '/settings') {
	return renderWithProviders(
		<>
			<OAuthClientsSection />
			<Toaster />
		</>,
		{ route },
	);
}

describe('OAuthClientsSection', () => {
	beforeEach(async () => {
		// The roster swaps to stacked cards below `sm` (640px); these specs
		// assert the desktop table grammar, so pin a desktop viewport.
		await page.viewport(1280, 900);
		setToken('test-token');
		resetSettingsStore();
	});

	// ------------------------------------------------------------------
	// Roster (ClientsTable)
	// ------------------------------------------------------------------

	it('defaults to the Active segment: working fleet only, pending/denied/inactive hidden', async () => {
		renderSection();

		// The admin-registered confidential client, with its §4.8 grant count.
		expect(await screen.findByText('Internal Dashboard')).toBeInTheDocument();
		expect(
			screen.getByRole('button', { name: 'View grants for Internal Dashboard' }),
		).toHaveTextContent('2');
		// Pending, denied, and deactivated rows stay out of the default view.
		expect(screen.queryByText('Cursor')).not.toBeInTheDocument();
		expect(screen.queryByText('Sketchy Tool')).not.toBeInTheDocument();
		expect(screen.queryByText('Legacy App')).not.toBeInTheDocument();
	});

	it('status segments carry live counts and Inactive surfaces the approved+inactive zombie (#1312)', async () => {
		const user = userEvent.setup();
		renderSection();
		await screen.findByText('Internal Dashboard');

		// Live counts over the full include_inactive pool.
		expect(screen.getByRole('button', { name: 'All 4' })).toBeInTheDocument();
		expect(screen.getByRole('button', { name: 'Active 1' })).toBeInTheDocument();
		expect(screen.getByRole('button', { name: 'Pending 1' })).toBeInTheDocument();
		expect(screen.getByRole('button', { name: 'Denied 1' })).toBeInTheDocument();
		expect(screen.getByRole('button', { name: 'Inactive 1' })).toBeInTheDocument();

		// The zombie — approved but deactivated, invisible to both queue
		// filters — lives under the Inactive segment with its chip.
		await user.click(screen.getByRole('button', { name: 'Inactive 1' }));
		expect(await screen.findByText('Legacy App')).toBeInTheDocument();
		expect(screen.getByText('Inactive')).toBeInTheDocument();
		expect(screen.queryByText('Internal Dashboard')).not.toBeInTheDocument();
	});

	it('never offers Reactivate on a denied row — its recovery routes to the queue', async () => {
		const user = userEvent.setup();
		renderSection();
		await screen.findByText('Internal Dashboard');

		// The denied row's kebab: Review in queue, but NO Reactivate (a PATCH
		// active=true would leave approval_status=denied — still gate-blocked).
		await user.click(screen.getByRole('button', { name: 'Denied 1' }));
		await screen.findByText('Sketchy Tool');
		await user.click(screen.getByRole('button', { name: 'Actions for Sketchy Tool' }));
		expect(
			await screen.findByRole('menuitem', { name: 'Review in queue Sketchy Tool' }),
		).toBeInTheDocument();
		expect(screen.queryByRole('menuitem', { name: /Reactivate/ })).not.toBeInTheDocument();
		await user.keyboard('{Escape}');

		// The approved+inactive zombie DOES get Reactivate — the one state
		// where a plain PATCH active=true actually un-blocks the client.
		await user.click(screen.getByRole('button', { name: 'Inactive 1' }));
		await screen.findByText('Legacy App');
		await user.click(screen.getByRole('button', { name: 'Actions for Legacy App' }));
		expect(
			await screen.findByRole('menuitem', { name: 'Reactivate Legacy App' }),
		).toBeInTheDocument();
	});

	it('"Review in queue" on a denied row lands on the queue tab, Denied slice', async () => {
		const user = userEvent.setup();
		renderSection();
		await screen.findByText('Internal Dashboard');

		await user.click(screen.getByRole('button', { name: 'Denied 1' }));
		await screen.findByText('Sketchy Tool');
		await user.click(screen.getByRole('button', { name: 'Actions for Sketchy Tool' }));
		await user.click(
			await screen.findByRole('menuitem', { name: 'Review in queue Sketchy Tool' }),
		);

		// Queue tab, denied filter: the row re-offers Approve without Deny.
		expect(await screen.findByText('Sketchy Tool')).toBeInTheDocument();
		expect(screen.getByRole('button', { name: 'Approve' })).toBeInTheDocument();
		expect(screen.queryByRole('button', { name: 'Deny' })).not.toBeInTheDocument();
	});

	it('has no critical a11y violations on the clients tab', async () => {
		const { container } = renderSection();
		await screen.findByText('Internal Dashboard');
		await checkA11y(container);
	});

	// ------------------------------------------------------------------
	// Approval queue
	// ------------------------------------------------------------------

	it('carries the pending count on the Approval queue tab label', async () => {
		renderSection();
		// One seeded pending registration → the tab badge shows 1.
		const tab = await screen.findByRole('tab', { name: /Approval queue/ });
		expect(await within(tab).findByText('1')).toBeInTheDocument();
	});

	it('deep-links to the queue via ?tab=queue and leads with the verifiable origins', async () => {
		renderSection('/settings?tab=queue');

		// Origins-first (the #1264 anti-spoofing posture): the headline is the
		// redirect-URI origins + software_id, with the type/status chips. The
		// custom-scheme URI (WHATWG opaque origin) must render as
		// scheme://host — NOT the literal "null" `URL.origin` serialises to.
		const origins = await screen.findByText(
			'http://localhost:33418, cursor://anysphere.cursor-mcp',
		);
		expect(origins.textContent).not.toContain('null');
		const row = origins.closest('h3');
		expect(row).not.toBeNull();
		expect(within(row as HTMLElement).getByText('Public')).toBeInTheDocument();
		expect(within(row as HTMLElement).getByText('DCR')).toBeInTheDocument();
		expect(within(row as HTMLElement).getByText('Agent consent')).toBeInTheDocument();
		expect(within(row as HTMLElement).getByText('Pending')).toBeInTheDocument();
		expect(screen.getByText('com.cursor.ide')).toBeInTheDocument();

		// The attacker-chosen display name is demoted to labelled secondary text.
		expect(screen.getByText(/Self-reported name:/)).toBeInTheDocument();
		expect(screen.getByText('Cursor')).toBeInTheDocument();
		expect(within(row as HTMLElement).queryByText('Cursor')).not.toBeInTheDocument();
	});

	it('approves a pending registration and empties the queue (D7 pending→approved)', async () => {
		const user = userEvent.setup();
		renderSection('/settings?tab=queue');
		await screen.findByText('Cursor');

		await user.click(screen.getByRole('button', { name: 'Approve' }));

		expect(await screen.findByText('Cursor approved')).toBeInTheDocument();
		expect(await screen.findByText('No pending registrations')).toBeInTheDocument();

		// The approved client now shows on the Clients tab, active.
		await user.click(screen.getByRole('tab', { name: /Clients/ }));
		expect(await screen.findByText('Cursor')).toBeInTheDocument();
	});

	it('denies a pending registration via the reason dialog (D7 pending→denied)', async () => {
		const user = userEvent.setup();
		renderSection('/settings?tab=queue');
		await screen.findByText('Cursor');

		await user.click(screen.getByRole('button', { name: 'Deny' }));
		const dialog = await screen.findByRole('dialog');
		await user.type(
			within(dialog).getByLabelText('Reason (optional)'),
			'unknown redirect URIs',
		);
		await user.click(within(dialog).getByRole('button', { name: 'Deny' }));

		expect(await screen.findByText('Cursor denied')).toBeInTheDocument();
		expect(await screen.findByText('No pending registrations')).toBeInTheDocument();
	});

	it('settles the actionable rail row on deny success (the deny-arm settle mirror)', async () => {
		// A deny emits no `oauth_client.*` SSE event (§4.8/D7) — the approve arm
		// gets its rail settle from the `oauth_client.approved` event, so the
		// deny mutation must mirror it locally via the stream context. Mount the
		// section INSIDE the provider with the actionable registration in the
		// backlog and watch the row acknowledge on deny success.
		const registered: EventResponse = {
			_links: { self: '/events/evt_oauth_registered' },
			event_id: 'evt_oauth_registered',
			type: 'oauth_client.registered',
			severity: 'info' as EventResponse['severity'],
			summary: 'OAuth client registered: Cursor',
			requires_action: true,
			acknowledged: false,
			created_at: new Date().toISOString(),
			// The internal admin-row id — the deny mutation's settle key.
			data: { oauth_client_id: 'oac_pending_1' },
		};
		worker.use(
			http.get('/events', () =>
				HttpResponse.json({ data: [registered], has_more: false, next_cursor: null }),
			),
		);
		function SettleProbe() {
			const { events } = useAgentStream();
			const row = events.find((e) => e.type === 'oauth_client.registered');
			return <div data-testid="registered-acked">{row ? String(row.acknowledged) : ''}</div>;
		}
		const user = userEvent.setup();
		renderWithProviders(
			<AgentStreamProvider live={false}>
				<OAuthClientsSection />
				<Toaster />
				<SettleProbe />
			</AgentStreamProvider>,
			{ route: '/settings?tab=queue' },
		);
		await screen.findByText('Cursor');
		await expect.poll(() => screen.getByTestId('registered-acked').textContent).toBe('false');

		await user.click(screen.getByRole('button', { name: 'Deny' }));
		const dialog = await screen.findByRole('dialog');
		await user.click(within(dialog).getByRole('button', { name: 'Deny' }));

		expect(await screen.findByText('Cursor denied')).toBeInTheDocument();
		// The mutation's onSuccess settled the stream row locally.
		await expect.poll(() => screen.getByTestId('registered-acked').textContent).toBe('true');
	});

	it('keeps the deny reason draft across a casual dismiss (dialog-state rule)', async () => {
		const user = userEvent.setup();
		renderSection('/settings?tab=queue');
		await screen.findByText('Cursor');

		await user.click(screen.getByRole('button', { name: 'Deny' }));
		let dialog = await screen.findByRole('dialog');
		await user.type(within(dialog).getByLabelText('Reason (optional)'), 'half-typed thought');
		await user.click(within(dialog).getByRole('button', { name: 'Cancel' }));

		// Reopen for the SAME client: the half-typed draft survived the dismiss.
		await user.click(screen.getByRole('button', { name: 'Deny' }));
		dialog = await screen.findByRole('dialog');
		expect(within(dialog).getByLabelText('Reason (optional)')).toHaveValue(
			'half-typed thought',
		);
	});

	it('recovers a denied client: the Denied filter re-offers Approve (D7 denied→approved)', async () => {
		const user = userEvent.setup();
		renderSection('/settings?tab=queue');
		await screen.findByText('Cursor');

		// The seeded denied row lives under the Denied filter, without a Deny verb.
		await user.click(screen.getByRole('button', { name: 'Denied' }));
		expect(await screen.findByText('Sketchy Tool')).toBeInTheDocument();
		expect(screen.queryByRole('button', { name: 'Deny' })).not.toBeInTheDocument();

		await user.click(screen.getByRole('button', { name: 'Approve' }));
		expect(await screen.findByText('Sketchy Tool approved')).toBeInTheDocument();
		expect(await screen.findByText('No denied clients')).toBeInTheDocument();
	});

	it('collapses noisy queue-card metadata: scope "+N more" and redirect-URI disclosure', async () => {
		const user = userEvent.setup();
		renderSection('/settings?tab=queue');
		await screen.findByText('Cursor');

		// Sketchy Tool is seeded noisy: 6 scopes, 3 redirect URIs.
		await user.click(screen.getByRole('button', { name: 'Denied' }));
		await screen.findByText('Sketchy Tool');

		// Scopes render as a one-line summary — 4 preview chips, the rest
		// behind "+N more" (a chip wall must not out-shout the decision).
		expect(screen.getByText('apis:read')).toBeInTheDocument();
		expect(screen.queryByText('audit:read')).not.toBeInTheDocument();
		await user.click(screen.getByRole('button', { name: '+2 more' }));
		expect(screen.getByText('audit:read')).toBeInTheDocument();
		await user.click(screen.getByRole('button', { name: 'Show less' }));
		expect(screen.queryByText('audit:read')).not.toBeInTheDocument();

		// >2 redirect URIs collapse behind a disclosure — the headline's
		// origins already summarise them.
		expect(screen.queryByText('https://sketchy.example.com/cb')).not.toBeInTheDocument();
		await user.click(screen.getByRole('button', { name: 'Show 3 redirect URIs' }));
		expect(screen.getByText('https://sketchy.example.com/cb')).toBeInTheDocument();
	});

	it('has no critical a11y violations on the queue tab', async () => {
		const { container } = renderSection('/settings?tab=queue');
		await screen.findByText('Cursor');
		await checkA11y(container);
	});

	// ------------------------------------------------------------------
	// Detail sheet (metadata + grants + audit)
	// ------------------------------------------------------------------

	it('opens the detail sheet from the roster name: metadata and grants render', async () => {
		const user = userEvent.setup();
		renderSection();
		await screen.findByText('Internal Dashboard');

		await user.click(
			screen.getByRole('button', { name: 'View details for Internal Dashboard' }),
		);
		const sheet = await screen.findByTestId('sheet-primitive');

		// Metadata: consent model, client type, provenance, scope restriction.
		expect(within(sheet).getByText('User — consent acts as the user')).toBeInTheDocument();
		expect(within(sheet).getByText('Confidential (client secret)')).toBeInTheDocument();
		expect(within(sheet).getByText('Admin-registered')).toBeInTheDocument();
		expect(within(sheet).getByText('https://app.example.com/callback')).toBeInTheDocument();

		// Grants (via GET /admin/oauth-grants?client_id=…): the two active
		// consents render with their agents resolved from the directory.
		expect(await within(sheet).findByText('Invoice Bot')).toBeInTheDocument();
		expect(within(sheet).getByText('Support Triage')).toBeInTheDocument();

		// The opened sheet (a body portal — outside the render container)
		// carries no critical a11y violations either.
		await checkA11y(document.body);
	});

	it("offers Reactivate in a zombie's detail sheet (recovery from its own console)", async () => {
		const user = userEvent.setup();
		renderSection();
		await screen.findByText('Internal Dashboard');

		// The approved+inactive zombie must not be stranded: its own console
		// (the danger zone) carries the recovery verb.
		await user.click(screen.getByRole('button', { name: 'Inactive 1' }));
		await screen.findByText('Legacy App');
		await user.click(screen.getByRole('button', { name: 'View details for Legacy App' }));
		const sheet = await screen.findByTestId('sheet-primitive');

		await user.click(within(sheet).getByRole('button', { name: 'Reactivate Legacy App' }));
		expect(await screen.findByText('Legacy App reactivated')).toBeInTheDocument();
	});

	it('revoke honours can_revoke: enabled kill switch vs. disabled with explanation', async () => {
		const user = userEvent.setup();
		renderSection();
		await screen.findByText('Internal Dashboard');
		await user.click(
			screen.getByRole('button', { name: 'View details for Internal Dashboard' }),
		);
		const sheet = await screen.findByTestId('sheet-primitive');
		await within(sheet).findByText('Invoice Bot');

		// The G10 divergence: the caller can LIST ocg_2 but not revoke it —
		// the button disables instead of offering a 403.
		expect(
			within(sheet).getByRole('button', { name: 'Revoke grant ocg_2 (not permitted)' }),
		).toBeDisabled();

		// The revocable grant goes through the confirm dialog.
		await user.click(within(sheet).getByRole('button', { name: 'Revoke grant ocg_1' }));
		const dialog = await screen.findByRole('dialog', { name: 'Revoke this grant?' });
		await user.click(within(dialog).getByRole('button', { name: 'Revoke' }));
		expect(await screen.findByText('Grant revoked')).toBeInTheDocument();
	});

	it('surfaces the decision history — including the deny reason — in Recent changes', async () => {
		const user = userEvent.setup();
		renderSection();
		await screen.findByText('Internal Dashboard');

		// The denied client's audit trail carries the operator's deny reason,
		// which the old UI captured and then never showed anywhere.
		await user.click(screen.getByRole('button', { name: 'Denied 1' }));
		await screen.findByText('Sketchy Tool');
		await user.click(screen.getByRole('button', { name: 'View details for Sketchy Tool' }));
		const sheet = await screen.findByTestId('sheet-primitive');

		expect(await within(sheet).findByText('oauth_client.deny')).toBeInTheDocument();
		expect(within(sheet).getByText(/unknown redirect URIs/)).toBeInTheDocument();
	});

	// ------------------------------------------------------------------
	// Create/edit form sheet
	// ------------------------------------------------------------------

	it('creates a public agent-consent client (consent_model + token_endpoint_auth_method sent)', async () => {
		const user = userEvent.setup();
		renderSection();
		await screen.findByText('Internal Dashboard');

		await user.click(screen.getByRole('button', { name: 'Add client' }));
		const sheet = await screen.findByTestId('sheet-primitive');
		await user.type(within(sheet).getByLabelText('Name'), 'mcp-bridge');
		await user.type(
			within(sheet).getByLabelText('Redirect URI 1'),
			'https://bridge.example.com/cb',
		);
		await user.selectOptions(within(sheet).getByLabelText('Client type'), 'public');
		await user.selectOptions(within(sheet).getByLabelText('Consent model'), 'agent');
		await user.click(within(sheet).getByRole('button', { name: 'Create' }));

		// The mock store echoes what the form SENT: the new roster row carries
		// the Public (token_endpoint_auth_method=none) and Agent-consent chips.
		const name = await screen.findByRole('button', { name: 'View details for mcp-bridge' });
		const row = name.closest('tr');
		expect(row).not.toBeNull();
		expect(within(row as HTMLElement).getByText('Public')).toBeInTheDocument();
		expect(within(row as HTMLElement).getByText('Agent consent')).toBeInTheDocument();

		// Public clients are PKCE-only: no one-time secret dialog.
		expect(screen.queryByText(/Copy this secret now/)).not.toBeInTheDocument();
	});

	it('un-restricting scopes on edit PATCHes ["*"] (tri-state), not a silent null no-op', async () => {
		const user = userEvent.setup();
		// Record the PATCH payload, then fall through to the store handler so
		// the flow stays end-to-end (returning undefined defers to the next
		// matching handler).
		const captured: { body?: Record<string, unknown> } = {};
		worker.use(
			http.patch('/admin/oauth-clients/:id', async ({ request }) => {
				captured.body = (await request.clone().json()) as Record<string, unknown>;
				return undefined;
			}),
		);
		renderSection();
		await screen.findByText('Internal Dashboard');

		// Internal Dashboard is seeded WITH a restriction (apis:read) —
		// unchecking the box must reset it, not silently change nothing.
		await user.click(screen.getByRole('button', { name: 'Actions for Internal Dashboard' }));
		await user.click(await screen.findByRole('menuitem', { name: 'Edit Internal Dashboard' }));
		const sheet = await screen.findByTestId('sheet-primitive');
		await user.click(within(sheet).getByRole('checkbox', { name: 'Restrict allowed scopes' }));
		await user.click(within(sheet).getByRole('button', { name: 'Update' }));
		await screen.findByText('OAuth client updated');

		// The tri-state sentinel rides the wire (null would mean NO CHANGE)…
		expect(captured.body?.allowed_scopes).toEqual(['*']);
		// …and the create-only fields never ride along on PATCH.
		expect(captured.body).not.toHaveProperty('consent_model');
		expect(captured.body).not.toHaveProperty('token_endpoint_auth_method');

		// End-to-end: the store applied the reset, so the detail sheet reads
		// Unrestricted. (Wait out the edit sheet's exit animation first.)
		await expect.poll(() => screen.queryByTestId('sheet-primitive')).toBeNull();
		await user.click(
			screen.getByRole('button', { name: 'View details for Internal Dashboard' }),
		);
		const detail = await screen.findByTestId('sheet-primitive');
		expect(await within(detail).findByText('Unrestricted')).toBeInTheDocument();
	});

	it('shows the one-time secret for a new confidential client (and wipes it on close)', async () => {
		const user = userEvent.setup();
		renderSection();
		await screen.findByText('Internal Dashboard');

		await user.click(screen.getByRole('button', { name: 'Add client' }));
		const sheet = await screen.findByTestId('sheet-primitive');
		await user.type(within(sheet).getByLabelText('Name'), 'server-app');
		await user.type(
			within(sheet).getByLabelText('Redirect URI 1'),
			'https://server.example.com/cb',
		);
		await user.click(within(sheet).getByRole('button', { name: 'Create' }));

		// Confidential default → the one-time secret dialog.
		expect(await screen.findByText('ocs_mock_secret_once')).toBeInTheDocument();
		await user.click(screen.getByRole('button', { name: 'Done' }));
		// Sensitive-data exception: the secret does not survive the close.
		expect(screen.queryByText('ocs_mock_secret_once')).not.toBeInTheDocument();
	});

	it('keeps the create-form draft across a casual dismiss (dialog-state rule)', async () => {
		const user = userEvent.setup();
		renderSection();
		await screen.findByText('Internal Dashboard');

		await user.click(screen.getByRole('button', { name: 'Add client' }));
		let sheet = await screen.findByTestId('sheet-primitive');
		await user.type(within(sheet).getByLabelText('Name'), 'half-typed-app');
		// The opened form sheet passes axe too (body portal, so check the body).
		await checkA11y(document.body);
		await user.click(within(sheet).getByRole('button', { name: 'Cancel' }));
		// Let the exit animation finish so the reopen starts from 'closed'.
		await expect.poll(() => screen.queryByTestId('sheet-primitive')).toBeNull();

		// Reopen: the draft survived — the sheet is mounted persistently, not
		// conditionally (the old implementation lost drafts here).
		await user.click(screen.getByRole('button', { name: 'Add client' }));
		sheet = await screen.findByTestId('sheet-primitive');
		expect(within(sheet).getByLabelText('Name')).toHaveValue('half-typed-app');
	});
});
