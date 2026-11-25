import { describe, it, expect, beforeEach, vi } from 'vitest';
import { http, HttpResponse } from 'msw';
import { worker } from '@/mocks/browser';
import { renderWithProviders, screen, within, userEvent, checkA11y } from '@/__tests__/test-utils';
import { setToken } from '@/shared/api';
import { AuthProvider } from '@/shared/auth';
import { resetCredentialsStore, makeMockCredential } from '@/shared/credentials/mocks/handlers';
import { EditCredentialSheet } from '@/shared/credentials/components/EditCredentialSheet';

/**
 * The read-mostly "Bound agents" section inside the edit-credential sheet
 * (theme 5 phase 5a): which agents may use this credential, with suspended
 * state and a link to each agent's tab on the flat Agents surface. Read-only by
 * design — binding management lives on each agent's API sidebar there.
 *
 * `GET /credentials/{id}/agents` is stubbed per-test here (the shared tier
 * must not reach into a feature module's mock store), mirroring the generated
 * `CredentialAgentListResponse` shape.
 */
function seedBoundAgents(
	credentialId: string,
	rows: Array<{
		agent_id: string;
		agent_name: string;
		owner_id?: string | null;
		suspended?: boolean;
		suspended_reason?: string | null;
		last_used_at?: string | null;
		expires_at?: string | null;
		expired?: boolean;
		expired_reason?: string | null;
		max_idle_period?: string | null;
		idle_expires_at?: string | null;
		idle_warning?: boolean;
		after_expiry?: 'hold' | 'refuse';
		max_quota?: number | null;
		quota_remaining?: number | null;
	}>,
) {
	worker.use(
		http.get('/credentials/:cid/agents', ({ params }) => {
			if (params.cid !== credentialId) {
				return HttpResponse.json({ data: [], has_more: false, next_cursor: null });
			}
			return HttpResponse.json({
				data: rows.map((r) => ({
					agent_id: r.agent_id,
					agent_name: r.agent_name,
					...('owner_id' in r && { owner_id: r.owner_id }),
					bound_at: '2026-05-02T09:00:00Z',
					rule_set_id: null,
					status: 'active',
					suspended: r.suspended ?? false,
					suspended_reason: r.suspended_reason ?? null,
					...('last_used_at' in r && { last_used_at: r.last_used_at }),
					...('max_idle_period' in r && {
						max_idle_period: r.max_idle_period,
						idle_expires_at: r.idle_expires_at ?? null,
						expired_reason: r.expired_reason ?? null,
						expired: r.expired ?? false,
						idle_warning: r.idle_warning ?? false,
					}),
					...('expires_at' in r && {
						expires_at: r.expires_at,
						expired: r.expired ?? false,
					}),
					...('max_quota' in r && {
						max_quota: r.max_quota,
						quota_remaining: r.quota_remaining ?? null,
						expired: r.expired ?? false,
						expired_reason: r.expired_reason ?? null,
					}),
					...('after_expiry' in r && { after_expiry: r.after_expiry }),
				})),
				has_more: false,
				next_cursor: null,
			});
		}),
	);
}

function renderSheet(credentialId: string, onNavigateAway?: () => void) {
	return renderWithProviders(
		<EditCredentialSheet
			credentialId={credentialId}
			open
			onClose={() => {}}
			onNavigateAway={onNavigateAway}
		/>,
	);
}

describe('BoundAgentsSection (edit-credential sheet)', () => {
	beforeEach(() => {
		setToken('test-token');
	});

	it('lists the agents bound to the credential, each linking to its tab on the Agents surface', async () => {
		resetCredentialsStore([
			makeMockCredential({ credential_id: 'cred_slack_1', name: 'Slack bot token' }),
		]);
		seedBoundAgents('cred_slack_1', [
			{ agent_id: 'agnt_active_1', agent_name: 'support-agent' },
		]);
		const { container } = renderSheet('cred_slack_1');

		expect(await screen.findByText('Bound agents (1)')).toBeInTheDocument();
		const row = await screen.findByTestId('bound-agent-row');
		// The agent resolves to its human name and links to itself AS SELECTED on
		// the flat surface, where its API tiles — the bindings this list is about
		// — are managed.
		const link = within(row).getByRole('link', { name: 'support-agent' });
		expect(link).toHaveAttribute('href', '/agents?agent=agnt_active_1');
		// A healthy binding carries no suspended chip.
		expect(within(row).queryByTestId('bound-agent-suspended')).not.toBeInTheDocument();
		expect(within(row).getByText(/^bound /)).toBeInTheDocument();
		// Read-only: no bind/unbind affordances in this phase.
		expect(
			screen.queryByRole('button', { name: /unbind|bind credential/i }),
		).not.toBeInTheDocument();

		await checkA11y(container);
	});

	it('shows when each agent last used the credential, and which never have', async () => {
		resetCredentialsStore([
			makeMockCredential({ credential_id: 'cred_slack_1', name: 'Slack bot token' }),
		]);
		seedBoundAgents('cred_slack_1', [
			{
				agent_id: 'agnt_active_1',
				agent_name: 'support-agent',
				last_used_at: new Date(Date.now() - 3 * 86_400_000).toISOString(),
			},
			{ agent_id: 'agnt_idle_1', agent_name: 'idle-agent', last_used_at: null },
			// An older server that doesn't report it: nothing is claimed.
			{ agent_id: 'agnt_old_1', agent_name: 'legacy-agent' },
		]);
		renderSheet('cred_slack_1');

		const rows = await screen.findAllByTestId('bound-agent-row');
		const rowFor = (name: string) => rows.find((r) => within(r).queryByText(name))!;
		expect(
			within(rowFor('support-agent')).getByTestId('bound-agent-last-used'),
		).toHaveTextContent(/^used 3d ago ·/);
		expect(within(rowFor('idle-agent')).getByTestId('bound-agent-last-used')).toHaveTextContent(
			/^never used ·/,
		);
		expect(
			within(rowFor('legacy-agent')).queryByTestId('bound-agent-last-used'),
		).not.toBeInTheDocument();
	});

	it('asks the host to stand aside when a link is followed in this tab', async () => {
		// The hosts on the Agents surface cover the very tab the link selects, so
		// an in-tab click has to dismiss them. A modified click opens a second tab
		// and must leave this one as it was.
		const onNavigateAway = vi.fn();
		resetCredentialsStore([
			makeMockCredential({ credential_id: 'cred_slack_1', name: 'Slack bot token' }),
		]);
		seedBoundAgents('cred_slack_1', [
			{ agent_id: 'agnt_active_1', agent_name: 'support-agent' },
		]);
		const user = userEvent.setup();
		renderSheet('cred_slack_1', onNavigateAway);

		const link = within(await screen.findByTestId('bound-agent-row')).getByRole('link', {
			name: 'support-agent',
		});

		// A modified click is the one case the router leaves to the browser, which
		// in this runner means navigating the test frame itself. The default is
		// swallowed for that click only — what is under test is the handler.
		const swallow = (event: Event): void => event.preventDefault();
		document.addEventListener('click', swallow, true);
		try {
			await user.keyboard('{Meta>}');
			await user.click(link);
			await user.keyboard('{/Meta}');
		} finally {
			document.removeEventListener('click', swallow, true);
		}
		expect(onNavigateAway).not.toHaveBeenCalled();

		await user.click(link);
		expect(onNavigateAway).toHaveBeenCalledTimes(1);
	});

	it('marks suspended bindings distinctly', async () => {
		resetCredentialsStore([
			makeMockCredential({ credential_id: 'cred_github_1', name: 'GitHub PAT' }),
		]);
		seedBoundAgents('cred_github_1', [
			{ agent_id: 'agnt_active_1', agent_name: 'support-agent', suspended: true },
		]);
		renderSheet('cred_github_1');

		const row = await screen.findByTestId('bound-agent-row');
		expect(within(row).getByTestId('bound-agent-suspended')).toHaveTextContent(/^Suspended$/);
	});

	it('says when a binding was suspended because its API was deleted', async () => {
		resetCredentialsStore([
			makeMockCredential({ credential_id: 'cred_github_1', name: 'GitHub PAT' }),
		]);
		seedBoundAgents('cred_github_1', [
			{
				agent_id: 'agnt_active_1',
				agent_name: 'support-agent',
				suspended: true,
				suspended_reason: 'api_deleted',
			},
		]);
		renderSheet('cred_github_1');

		const row = await screen.findByTestId('bound-agent-row');
		expect(within(row).getByTestId('bound-agent-suspended')).toHaveTextContent(
			'Suspended · API deleted',
		);
	});

	it('shows an honest empty state when no agent is bound', async () => {
		resetCredentialsStore([
			makeMockCredential({ credential_id: 'cred_unbound_1', name: 'Unused key' }),
		]);
		seedBoundAgents('cred_unbound_1', []);
		renderSheet('cred_unbound_1');

		expect(await screen.findByTestId('bound-agents-empty')).toHaveTextContent(
			/no agents are bound to this credential/i,
		);
	});
});

describe('BoundAgentsSection — whose agents the list holds', () => {
	beforeEach(() => {
		setToken('test-token');
	});

	function seedViewer(id: string, permissions: string[]) {
		worker.use(
			http.get('/users/me', () =>
				HttpResponse.json({
					id,
					email: 'viewer@local',
					first_name: 'View',
					last_name: 'Er',
					active: true,
					permissions,
					must_change_password: false,
					created_at: '2026-01-01T00:00:00Z',
					updated_at: null,
				}),
			),
		);
	}

	function renderAuthed(credentialId: string) {
		return renderWithProviders(
			<AuthProvider>
				<EditCredentialSheet credentialId={credentialId} open onClose={() => {}} />
			</AuthProvider>,
		);
	}

	it.each([
		['the owner', 'usr_owner', [] as string[]],
		['an org:admin', 'usr_admin', ['org:admin']],
	])('reads unscoped for %s', async (_label, id, permissions) => {
		seedViewer(id, permissions);
		resetCredentialsStore([
			makeMockCredential({ credential_id: 'cred_team_1', created_by: 'usr_owner' }),
		]);
		seedBoundAgents('cred_team_1', []);
		renderAuthed('cred_team_1');

		expect(await screen.findByText('Bound agents (0)')).toBeInTheDocument();
		expect(await screen.findByTestId('bound-agents-empty')).toHaveTextContent(
			'No agents are bound to this credential.',
		);
	});

	it.each([
		[
			"the credential's owner offers it only on their own agents",
			'usr_owner',
			['agents:write'],
			['own-agent'],
		],
		[
			'an org:admin offers it on every agent',
			'usr_admin',
			['org:admin'],
			['own-agent', 'other-agent', 'unclaimed-agent'],
		],
	])('Renew: %s', async (_label, id, permissions, renewable) => {
		// The server renews only for the agent's owner (or an admin) on top of the
		// credential's: a Renew on someone else's agent would always 404.
		seedViewer(id, permissions);
		resetCredentialsStore([
			makeMockCredential({ credential_id: 'cred_team_1', created_by: 'usr_owner' }),
		]);
		seedBoundAgents('cred_team_1', [
			{
				agent_id: 'agnt_own',
				agent_name: 'own-agent',
				owner_id: 'usr_owner',
				expires_at: null,
			},
			{
				agent_id: 'agnt_other',
				agent_name: 'other-agent',
				owner_id: 'usr_other',
				expires_at: null,
			},
			{
				agent_id: 'agnt_unclaimed',
				agent_name: 'unclaimed-agent',
				owner_id: null,
				expires_at: null,
			},
		]);
		renderAuthed('cred_team_1');

		expect(await screen.findByText('Bound agents (3)')).toBeInTheDocument();
		const offered = screen
			.getAllByTestId('bound-agent-row')
			.filter((row) => within(row).queryByTestId('bound-agent-renew') != null)
			.map((row) => within(row).getByRole('link').textContent);
		expect(offered).toEqual(renewable);
	});

	it('says "your agents" to a viewer the credential is shared with', async () => {
		seedViewer('usr_sharee', []);
		resetCredentialsStore([
			makeMockCredential({ credential_id: 'cred_team_1', created_by: 'usr_owner' }),
		]);
		seedBoundAgents('cred_team_1', []);
		renderAuthed('cred_team_1');

		expect(await screen.findByText('Your bound agents (0)')).toBeInTheDocument();
		expect(screen.getByText(/Other users' agents aren't listed\./)).toBeInTheDocument();
		expect(await screen.findByTestId('bound-agents-empty')).toHaveTextContent(
			'None of your agents are bound to this credential.',
		);
	});
});

describe('BoundAgentsSection — when access ends', () => {
	const DAY = 86_400_000;
	const inDays = (n: number): string => new Date(Date.now() + n * DAY + 60_000).toISOString();

	beforeEach(() => {
		setToken('test-token');
		resetCredentialsStore([
			makeMockCredential({ credential_id: 'cred_gh_1', name: 'GitHub PAT' }),
		]);
	});

	/** Records each renewal's body. */
	function watchRenewals(): unknown[] {
		const bodies: unknown[] = [];
		worker.use(
			http.patch('/agents/:aid/credentials/:cid', async ({ params, request }) => {
				const body = (await request.json()) as { expires_at: string | null };
				bodies.push(body);
				return HttpResponse.json({
					id: 'acb_1',
					agent_id: params.aid,
					credential_id: params.cid,
					bound_at: '2026-05-02T09:00:00Z',
					suspended: false,
					expires_at: body.expires_at,
					expired: false,
				});
			}),
		);
		return bodies;
	}

	function rowFor(name: string): HTMLElement {
		const row = screen
			.getAllByTestId('bound-agent-row')
			.find((li) => within(li).queryByText(name) != null);
		if (!row) throw new Error(`no row for ${name}`);
		return row;
	}

	it('shows each end date, and marks an expired binding', async () => {
		seedBoundAgents('cred_gh_1', [
			{
				agent_id: 'agnt_old',
				agent_name: 'old-agent',
				expires_at: new Date(Date.now() - 2 * DAY - 60_000).toISOString(),
				expired: true,
			},
			{ agent_id: 'agnt_soon', agent_name: 'soon-agent', expires_at: inDays(3) },
			{ agent_id: 'agnt_open', agent_name: 'open-agent', expires_at: null },
		]);
		const { container } = renderSheet('cred_gh_1');
		await screen.findByText('Bound agents (3)');

		const old = rowFor('old-agent');
		expect(within(old).getByTestId('bound-agent-expired')).toHaveTextContent(/^Expired$/);
		expect(within(old).getByTestId('bound-agent-expiry')).toHaveTextContent(
			'Expired 2 days ago',
		);
		expect(
			within(old).getByRole('button', { name: /^Renew for \W?old-agent\W?$/ }),
		).toBeVisible();

		const soon = rowFor('soon-agent');
		expect(within(soon).queryByTestId('bound-agent-expired')).not.toBeInTheDocument();
		expect(within(soon).getByTestId('bound-agent-expiry')).toHaveTextContent(
			'Expires in 3 days',
		);

		const open = rowFor('open-agent');
		expect(within(open).queryByTestId('bound-agent-expiry')).not.toBeInTheDocument();
		await checkA11y(container);
	});

	it('renews from the row: the new end date runs from the save', async () => {
		const renewals = watchRenewals();
		seedBoundAgents('cred_gh_1', [
			{
				agent_id: 'agnt_old',
				agent_name: 'old-agent',
				expires_at: new Date(Date.now() - DAY).toISOString(),
				expired: true,
			},
		]);
		const user = userEvent.setup();
		renderSheet('cred_gh_1');

		const toggle = await screen.findByRole('button', { name: /^Renew for \W?old-agent\W?$/ });
		await user.click(toggle);
		expect(toggle).toHaveAttribute('aria-expanded', 'true');
		const save = screen.getByTestId('bound-agent-renew-control-save');
		// Nothing is preselected, so nothing can be saved yet.
		expect(save).toBeDisabled();

		await user.selectOptions(
			screen.getByTestId('bound-agent-renew-control-duration-preset'),
			'30d',
		);
		const before = Date.now();
		await user.click(save);
		await vi.waitFor(() => expect(renewals).toHaveLength(1));
		const at = Date.parse((renewals[0] as { expires_at: string }).expires_at);
		expect(at).toBeGreaterThanOrEqual(before + 30 * DAY);
		expect(at).toBeLessThanOrEqual(Date.now() + 30 * DAY);
		// The row's control closes once the renewal lands.
		await vi.waitFor(() =>
			expect(screen.queryByTestId('bound-agent-renew-control')).not.toBeInTheDocument(),
		);
	});

	it('clearing the expiry asks first; "Set an expiry" goes back to the control', async () => {
		const renewals = watchRenewals();
		seedBoundAgents('cred_gh_1', [
			{ agent_id: 'agnt_soon', agent_name: 'soon-agent', expires_at: inDays(3) },
		]);
		const user = userEvent.setup();
		renderSheet('cred_gh_1');

		await user.click(
			await screen.findByRole('button', { name: /^Change the expiry for \W?soon-agent\W?$/ }),
		);
		await user.selectOptions(
			screen.getByTestId('bound-agent-renew-control-duration-preset'),
			'custom',
		);
		await user.selectOptions(
			screen.getByTestId('bound-agent-renew-control-duration-unit'),
			'never',
		);
		await user.click(
			screen.getByRole('button', { name: /^Remove expiry for \W?soon-agent\W?$/ }),
		);

		const warning = await screen.findByTestId('no-expiry-warning');
		expect(warning).toHaveTextContent(
			'soon-agent will keep this access until someone removes it.',
		);
		const setExpiry = screen.getByTestId('no-expiry-set-expiry');
		await vi.waitFor(() => expect(setExpiry).toHaveFocus());
		await user.click(setExpiry);
		await vi.waitFor(() =>
			expect(screen.getByTestId('bound-agent-renew-control-duration-preset')).toHaveFocus(),
		);
		expect(renewals).toHaveLength(0);

		await user.click(
			screen.getByRole('button', { name: /^Remove expiry for \W?soon-agent\W?$/ }),
		);
		await user.click(await screen.findByTestId('no-expiry-confirm'));
		await vi.waitFor(() => expect(renewals).toEqual([{ expires_at: null }]));
	});

	it('Enter in a custom amount renews, and never submits the credential form around it', async () => {
		const renewals = watchRenewals();
		let credentialSaves = 0;
		worker.use(
			http.patch('/credentials/:id', () => {
				credentialSaves += 1;
				return undefined;
			}),
		);
		seedBoundAgents('cred_gh_1', [
			{ agent_id: 'agnt_soon', agent_name: 'soon-agent', expires_at: inDays(3) },
		]);
		const user = userEvent.setup();
		renderSheet('cred_gh_1');

		await user.click(
			await screen.findByRole('button', { name: /^Change the expiry for \W?soon-agent\W?$/ }),
		);
		await user.selectOptions(
			screen.getByTestId('bound-agent-renew-control-duration-preset'),
			'custom',
		);
		const amount = screen.getByTestId('bound-agent-renew-control-duration-amount');
		await user.clear(amount);
		await user.type(amount, '12{Enter}');

		await vi.waitFor(() => expect(renewals).toHaveLength(1));
		const at = Date.parse((renewals[0] as { expires_at: string }).expires_at);
		expect(at - Date.now()).toBeGreaterThan(11 * DAY);
		expect(at - Date.now()).toBeLessThanOrEqual(12 * DAY);
		expect(credentialSaves).toBe(0);
	});

	it('an end date the server refused says why, not "Unprocessable Entity"', async () => {
		worker.use(
			http.patch('/agents/:aid/credentials/:cid', () =>
				HttpResponse.json(
					{
						detail: [
							{
								type: 'value_error',
								loc: ['body', 'expires_at'],
								msg: 'Value error, expires_at must be in the future',
							},
						],
					},
					{ status: 422 },
				),
			),
		);
		seedBoundAgents('cred_gh_1', [
			{ agent_id: 'agnt_old', agent_name: 'old-agent', expires_at: null },
		]);
		const user = userEvent.setup();
		renderSheet('cred_gh_1');

		await user.click(await screen.findByRole('button', { name: /old-agent/, expanded: false }));
		await user.selectOptions(
			screen.getByTestId('bound-agent-renew-control-duration-preset'),
			'1h',
		);
		await user.click(screen.getByTestId('bound-agent-renew-control-save'));
		const control = screen.getByTestId('bound-agent-renew-control');
		expect(
			await within(control).findByText(
				/That end date had already passed when it reached the server/,
			),
		).toBeVisible();
		expect(within(control).getByText(/check this device’s clock/)).toBeVisible();
		expect(within(control).queryByText(/Unprocessable/)).not.toBeInTheDocument();
	});

	it('says when an idle binding ended, and renewing restarts its idle clock', async () => {
		const renewals = watchRenewals();
		seedBoundAgents('cred_gh_1', [
			{
				agent_id: 'agnt_idle',
				agent_name: 'idle-agent',
				max_idle_period: 'P14D',
				idle_expires_at: new Date(Date.now() - 60_000).toISOString(),
				expired: true,
				expired_reason: 'idle',
			},
		]);
		const user = userEvent.setup();
		renderSheet('cred_gh_1');

		const row = await screen.findByTestId('bound-agent-row');
		expect(within(row).getByTestId('bound-agent-expired')).toBeVisible();
		expect(within(row).getByTestId('bound-agent-expiry')).toHaveTextContent(
			'Expired: unused for 14 days',
		);
		await user.click(within(row).getByRole('button', { name: /^Renew for \W?idle-agent\W?$/ }));
		// The idle row starts on the binding's own limit.
		expect(
			(screen.getByTestId('bound-agent-renew-control-idle-preset') as HTMLSelectElement)
				.value,
		).toBe('14d');
		expect(screen.queryByTestId('bound-agent-renew-control-still-needed')).toBeNull();
		await user.selectOptions(
			screen.getByTestId('bound-agent-renew-control-duration-preset'),
			'30d',
		);
		await user.click(screen.getByTestId('bound-agent-renew-control-save'));
		await vi.waitFor(() => expect(renewals).toHaveLength(1));
		expect(renewals[0]).toHaveProperty('expires_at');
	});

	it('starts the warning box on the binding and saves only a change to it', async () => {
		const renewals = watchRenewals();
		seedBoundAgents('cred_gh_1', [
			{
				agent_id: 'agnt_warned',
				agent_name: 'warned-agent',
				max_idle_period: 'P14D',
				idle_expires_at: new Date(Date.now() + 5 * 86_400_000).toISOString(),
				idle_warning: true,
			},
		]);
		const user = userEvent.setup();
		renderSheet('cred_gh_1');

		const row = await screen.findByTestId('bound-agent-row');
		await user.click(within(row).getByTestId('bound-agent-renew'));
		const box = within(screen.getByTestId('bound-agent-renew-control-idle-warning')).getByRole(
			'checkbox',
		);
		expect(box).toBeChecked();
		await user.click(box);
		await user.click(screen.getByTestId('bound-agent-renew-control-save'));
		await vi.waitFor(() => expect(renewals).toEqual([{ idle_warning: false }]));
	});

	it('warns before an end date alone, and saves only that change', async () => {
		const renewals = watchRenewals();
		seedBoundAgents('cred_gh_1', [
			{
				agent_id: 'agnt_dated',
				agent_name: 'dated-agent',
				expires_at: new Date(Date.now() + 20 * 86_400_000).toISOString(),
			},
		]);
		const user = userEvent.setup();
		renderSheet('cred_gh_1');

		const row = await screen.findByTestId('bound-agent-row');
		await user.click(within(row).getByTestId('bound-agent-renew'));
		const box = within(screen.getByTestId('bound-agent-renew-control-idle-warning')).getByRole(
			'checkbox',
		);
		expect(box).not.toBeChecked();
		expect(box).toBeEnabled();
		await user.click(box);
		await user.click(screen.getByTestId('bound-agent-renew-control-save'));
		await vi.waitFor(() => expect(renewals).toEqual([{ idle_warning: true }]));
	});

	it('starts "After access ends" on the binding and saves only a change to it', async () => {
		const renewals = watchRenewals();
		seedBoundAgents('cred_gh_1', [
			{
				agent_id: 'agnt_asks',
				agent_name: 'asking-agent',
				expires_at: new Date(Date.now() + 20 * 86_400_000).toISOString(),
				after_expiry: 'refuse',
			},
		]);
		const user = userEvent.setup();
		renderSheet('cred_gh_1');

		const row = await screen.findByTestId('bound-agent-row');
		await user.click(within(row).getByTestId('bound-agent-renew'));
		const after = screen.getByTestId(
			'bound-agent-renew-control-after-expiry',
		) as HTMLSelectElement;
		expect(after.value).toBe('refuse');
		const save = screen.getByTestId('bound-agent-renew-control-save');
		expect(save).toBeDisabled();
		await user.selectOptions(after, 'hold');
		await user.click(save);
		await vi.waitFor(() => expect(renewals).toEqual([{ after_expiry: 'hold' }]));
	});

	it('says a single-use binding was used, and renewing restores the use', async () => {
		const renewals = watchRenewals();
		seedBoundAgents('cred_gh_1', [
			{ agent_id: 'agnt_fresh', agent_name: 'fresh-agent', max_quota: 1, quota_remaining: 1 },
			{
				agent_id: 'agnt_used',
				agent_name: 'used-agent',
				max_quota: 1,
				quota_remaining: 0,
				expired: true,
				expired_reason: 'uses',
			},
		]);
		const user = userEvent.setup();
		renderSheet('cred_gh_1');

		const [fresh, used] = await screen.findAllByTestId('bound-agent-row');
		expect(within(fresh).getByTestId('bound-agent-expiry')).toHaveTextContent(
			'Single use: not used yet',
		);
		expect(within(used).getByTestId('bound-agent-expiry')).toHaveTextContent(
			'Expired: already used once',
		);
		await user.click(
			within(used).getByRole('button', { name: /^Renew for \W?used-agent\W?$/ }),
		);
		expect(
			within(screen.getByTestId('bound-agent-renew-control-single-use')).getByRole(
				'checkbox',
			),
		).toBeChecked();
		expect(screen.queryByTestId('bound-agent-renew-control-still-needed')).toBeNull();
		await user.selectOptions(
			screen.getByTestId('bound-agent-renew-control-duration-preset'),
			'30d',
		);
		await user.click(screen.getByTestId('bound-agent-renew-control-save'));
		await vi.waitFor(() => expect(renewals).toHaveLength(1));
		expect(renewals[0]).toHaveProperty('expires_at');
	});

	it('sorts the longest-unused first on request', async () => {
		seedBoundAgents('cred_gh_1', [
			{ agent_id: 'agnt_recent', agent_name: 'recent', last_used_at: inDays(-1) },
			{ agent_id: 'agnt_never', agent_name: 'never', last_used_at: null },
			{ agent_id: 'agnt_old', agent_name: 'old', last_used_at: inDays(-40) },
		]);
		const user = userEvent.setup();
		renderSheet('cred_gh_1');
		await screen.findByText('Bound agents (3)');
		const names = (): string[] =>
			screen
				.getAllByTestId('bound-agent-row')
				.map((r) => r.querySelector('a')?.textContent ?? '');
		expect(names()).toEqual(['recent', 'never', 'old']);

		const toggle = screen.getByTestId('bound-agents-sort-unused');
		await user.click(toggle);
		expect(toggle).toHaveAttribute('aria-pressed', 'true');
		expect(names()).toEqual(['never', 'old', 'recent']);
	});
});
