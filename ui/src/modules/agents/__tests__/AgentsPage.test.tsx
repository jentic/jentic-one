import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { http, HttpResponse } from 'msw';
import { page } from 'vitest/browser';
import { useLocation } from 'react-router';
import {
	renderWithProviders,
	screen,
	waitFor,
	within,
	userEvent,
	checkA11y,
	createErrorHandler,
} from '@/__tests__/test-utils';
import { worker } from '@/mocks/browser';
import { setToken } from '@/shared/api';
import { useOpenConnectRequests } from '@/shared/credentials/api';
import { Toaster } from '@/shared/ui';
import { AuthProvider } from '@/shared/auth';
import {
	clearAgentsStore,
	resetAgentsStore,
	seedCredentialBindings,
	seedExtraAgents,
	selfRegisterAgent,
} from '@/modules/agents/mocks/handlers';
import { dismissFirstRun } from '@/modules/agents/lib/firstRun';
import {
	makeMockCredential,
	resetApisStore,
	resetConnectSessionsStore,
	resetCredentialsStore,
	seedMockAgentConnectSession,
} from '@/shared/credentials/mocks/handlers';
import {
	CredentialType,
	OAUTH_CONNECT_MESSAGE_TYPE,
	type ApiResponse,
	type Credential,
} from '@/shared/credentials/api';
import AgentsPage from '@/modules/agents/pages/AgentsPage';
import { resetOrphanPurgeAttemptsForTest } from '@/modules/agents/api/hooks';

/** Surfaces the router's current search string so specs can assert `?agent=`. */
function LocationProbe() {
	const location = useLocation();
	return (
		<>
			<div data-testid="location-search">{location.search}</div>
			<div data-testid="location-path">{location.pathname}</div>
		</>
	);
}

/** Serve `GET /users/me` for the test token, so an `AuthProvider` resolves a
 * viewer instead of dropping the token on a 401. */
function seedViewer(permissions: string[], id = 'usr_viewer_1') {
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

/**
 * Render the page. Without `withAuth` there is no `AuthProvider`, so the viewer
 * is unknown — the default for specs that don't care who is looking. With it,
 * seed the viewer first ({@link seedViewer}).
 */
function renderPage(route = '/', { withAuth = false }: { withAuth?: boolean } = {}) {
	const ui = (
		<>
			<AgentsPage />
			<LocationProbe />
			<Toaster />
		</>
	);
	return renderWithProviders(withAuth ? <AuthProvider>{ui}</AuthProvider> : ui, { route });
}

/** Open the New agent panel over the fleet, on its "Create here" tab. */
async function openCreateHere(user: ReturnType<typeof userEvent.setup>): Promise<HTMLElement> {
	await user.click(screen.getByRole('button', { name: 'New agent' }));
	const sheet = await screen.findByRole('dialog', { name: 'New agent' });
	expect(within(sheet).getByRole('tab', { name: 'Create here' })).toHaveAttribute(
		'aria-selected',
		'true',
	);
	return sheet;
}

/** The strip pill (a real tab) for the given agent name. */
function stripTab(name: string): HTMLElement {
	return screen.getByRole('tab', { name: new RegExp(name) });
}

/** One figure in the selected agent's compact stat strip. */
function stripFigure(key: string): HTMLElement {
	return within(screen.getByTestId('agent-stat-strip')).getByTestId(`stat-${key}`);
}

/** The pending-approval banner (absent when nothing is pending). */
function approvalBanner(): HTMLElement {
	return screen.getByRole('region', { name: /Awaiting approval/i });
}

/** Await the banner before touching it: its `status=pending` slice is a separate
 * query from the strip's list, so it can land a beat later. */
function findApprovalBanner(): Promise<HTMLElement> {
	return screen.findByRole('region', { name: /Awaiting approval/i });
}

/** Workspace API row for the picker/registry mock. */
function apiRow(vendor: string, displayName: string, operationCount: number): ApiResponse {
	return {
		_links: { self: `/apis/${vendor}`, openapi: `/apis/${vendor}/openapi` },
		api: { vendor, name: 'default', version: '1.0.0' },
		catalog_api_id: null,
		created_at: '2026-01-01T00:00:00Z',
		current_revision_id: null,
		description: null,
		display_name: displayName,
		icon_url: null,
		operation_count: operationCount,
		revision_count: 1,
		security_schemes: [],
		updated_at: '2026-01-01T00:00:00Z',
	} as unknown as ApiResponse;
}

/** Minimal /agents row for handlers that page the list by hand. */
function agentRow(id: string, name: string) {
	return {
		id,
		name,
		description: null,
		status: 'active',
		owner_id: null,
		registered_by: 'self',
		parent_agent_id: null,
		approved_by: null,
		denial_reason: null,
		denied_by: null,
		created_at: new Date().toISOString(),
		approved_at: null,
		has_api_key: false,
	};
}

/** The two org credentials behind `agnt_active_1`'s seeded bindings. */
function seedComposedStores() {
	resetCredentialsStore([
		makeMockCredential({
			credential_id: 'cred_slack_1',
			name: 'Slack bot token',
			type: CredentialType.BEARER_TOKEN,
			api: { vendor: 'slack.com', name: 'default', version: '1.0.0' },
		}),
		makeMockCredential({
			credential_id: 'cred_github_1',
			name: 'GitHub PAT',
			type: CredentialType.BEARER_TOKEN,
			api: { vendor: 'github.com', name: 'default', version: '1.0.0' },
		}),
	]);
	resetApisStore([
		{ row: apiRow('slack.com', 'Slack', 181), spec: {} },
		// Vendor `github` (not `github.com`) is what the shared binding fixture
		// serves, so this row is the one the GitHub tile resolves against.
		{ row: apiRow('github', 'GitHub', 912), spec: {} },
	]);
}

describe('AgentsPage — flat agents surface', () => {
	beforeEach(async () => {
		// The strip wraps from `sm` up; these specs assert the desktop grammar.
		await page.viewport(1280, 900);
		setToken('test-token');
		window.localStorage.clear();
		resetAgentsStore();
		seedComposedStores();
		resetOrphanPurgeAttemptsForTest();
	});

	afterEach(() => {
		vi.restoreAllMocks();
	});

	// --- Strip: selection, URL sync, keyboard ------------------------------

	it('claims the whole page for the flat surface — no Agents/Service accounts toggle', async () => {
		renderPage();
		await screen.findAllByText('inbox-triage-bot');

		// No segmented toggle, no service-accounts roster, no roster create
		// button: the strip is the only tablist.
		expect(screen.queryByRole('button', { name: 'Service accounts' })).not.toBeInTheDocument();
		expect(
			screen.queryByRole('button', { name: 'New service account' }),
		).not.toBeInTheDocument();
		expect(screen.getByRole('tablist', { name: 'Agents' })).toBeInTheDocument();
	});

	it('opens the Approvals subsection from the page header', async () => {
		const user = userEvent.setup();
		renderPage();
		await screen.findAllByText('inbox-triage-bot');
		await user.click(screen.getByRole('button', { name: 'Approvals' }));
		await waitFor(() =>
			expect(screen.getByTestId('location-path')).toHaveTextContent('/agents/approvals'),
		);
	});

	it('renders every agent as a strip pill and names the longest-waiting in the banner', async () => {
		renderPage();
		await screen.findAllByText('inbox-triage-bot');

		for (const name of [
			'inbox-triage-bot',
			'release-notes-bot',
			'support-agent',
			'legacy-scraper',
			'spammy-bot',
		]) {
			expect(stripTab(name)).toBeInTheDocument();
		}
		// One agent is always selected (tabs pattern), even before any click.
		expect(screen.getAllByRole('tab', { selected: true })).toHaveLength(1);
		// ONE banner names the LONGEST-waiting pending agent — the page is created_at
		// DESC, so that is the LAST row — and folds the rest into a count.
		const banner = await findApprovalBanner();
		expect(within(banner).getByText('inbox-triage-bot')).toBeInTheDocument();
		expect(within(banner).queryByText('release-notes-bot')).not.toBeInTheDocument();
		expect(banner).toHaveTextContent(/waiting 47m for approval/);
		expect(banner).toHaveTextContent('and 1 more waiting');
	});

	it('groups pending agents at the head of the strip, separated from the fleet', async () => {
		const user = userEvent.setup();
		renderPage();
		await screen.findAllByText('inbox-triage-bot');

		// The group opener and the closing divider frame the pending pills.
		expect(screen.getByTestId('strip-pending-label')).toBeInTheDocument();
		expect(screen.getByTestId('strip-pending-divider')).toBeInTheDocument();
		// Pending pills come first in the tablist's DOM order.
		const tabs = screen.getAllByRole('tab');
		expect(tabs[0]).toHaveTextContent(/release-notes-bot|inbox-triage-bot/);
		expect(tabs[1]).toHaveTextContent(/release-notes-bot|inbox-triage-bot/);

		// Keyboard traversal crosses the divider seamlessly: the last pending
		// pill's ArrowRight lands on the first healthy pill.
		await user.click(stripTab('inbox-triage-bot'));
		await user.keyboard('{ArrowRight}');
		expect(stripTab('support-agent')).toHaveAttribute('aria-selected', 'true');
		expect(stripTab('support-agent')).toHaveFocus();

		// A filter that excludes every pending agent collapses the group.
		await user.keyboard('/');
		await user.type(screen.getByLabelText('Filter agents'), 'support');
		expect(screen.queryByTestId('strip-pending-label')).not.toBeInTheDocument();
		expect(screen.queryByTestId('strip-pending-divider')).not.toBeInTheDocument();
	});

	it('shows no banner and no pending group when nothing is pending', async () => {
		worker.use(
			http.get('/agents', () =>
				HttpResponse.json({
					data: [agentRow('agnt_active_only', 'solo-active-bot')],
					has_more: false,
					next_cursor: null,
				}),
			),
			http.get('/agents/:id/credentials', () => HttpResponse.json({ data: [] })),
		);
		// A lone active agent with no APIs would resume the first-run landing;
		// this spec is about the fleet view, so that suggestion was dismissed.
		dismissFirstRun('agnt_active_only');
		renderPage();
		await screen.findByRole('tab', { name: /solo-active-bot/ });

		// Zero reserved space: no banner region, no group furniture.
		expect(
			screen.queryByRole('region', { name: /Awaiting approval/i }),
		).not.toBeInTheDocument();
		expect(screen.queryByTestId('strip-pending-label')).not.toBeInTheDocument();
		expect(screen.queryByTestId('strip-pending-divider')).not.toBeInTheDocument();
	});

	it('writes the fallback selection into the URL on a bare landing', async () => {
		// The address bar always names the agent on screen: landing without `?agent=`
		// selects the first tab AND says so, so any copied URL is a real deep link.
		renderPage('/');
		await screen.findAllByText('inbox-triage-bot');

		// First tab is the newest pending agent (decisions first, newest first).
		expect(stripTab('release-notes-bot')).toHaveAttribute('aria-selected', 'true');
		// The fallback is written by an effect, so it lands a render after the tab.
		await waitFor(() =>
			expect(screen.getByTestId('location-search')).toHaveTextContent('agent=agnt_pending_2'),
		);
	});

	it('selecting a pill switches the surface in place and writes ?agent=', async () => {
		const user = userEvent.setup();
		renderPage();
		await screen.findAllByText('inbox-triage-bot');

		await user.click(stripTab('support-agent'));

		expect(screen.getByTestId('location-search')).toHaveTextContent('agent=agnt_active_1');
		expect(stripTab('support-agent')).toHaveAttribute('aria-selected', 'true');
		// The composed tile grid: workspace display names, credential lines,
		// and the honest binding facts (1 rule vs zero-rules + suspended). Each
		// tile's rule count is its own read, so both are awaited — one arriving
		// says nothing about the other.
		expect(await screen.findByText('Slack')).toBeInTheDocument();
		expect(screen.getByText('Slack bot token')).toBeInTheDocument();
		expect(await screen.findByText('1 access rule')).toBeInTheDocument();
		expect(screen.getByText('GitHub')).toBeInTheDocument();
		expect(screen.getByText('GitHub PAT')).toBeInTheDocument();
		// Suspended outranks Blocked in the status, so the rule-less binding keeps
		// its rules fact on the meta line (the status doesn't say it).
		expect(await screen.findByText('No rules — all calls blocked')).toBeInTheDocument();
		expect(screen.getByText('Suspended · not serving')).toBeInTheDocument();
	});

	it('labels the credential in the footer, on one line with the rules summary', async () => {
		renderPage('/?agent=agnt_active_1');

		const tileOf = (title: string) =>
			screen
				.getByRole('heading', { name: title })
				.closest('[data-testid="api-tile"]') as HTMLElement;
		await screen.findByRole('heading', { name: 'Slack' });
		const githubTile = tileOf('GitHub');

		// Visible: key icon, a muted "Credential" label, the name. Heard:
		// "Credential: <name>", with the details as its description.
		const credential = within(githubTile).getByTestId('tile-credential');
		expect(credential.querySelector('svg')).toHaveAttribute('aria-hidden', 'true');
		expect(credential).toHaveTextContent(/^Credential: GitHub PAT$/);
		expect(within(githubTile).getByTestId('tile-credential-label')).toHaveTextContent(
			/^GitHub PAT$/,
		);
		const trigger = credential.parentElement as HTMLElement;
		expect(trigger).toHaveAttribute('tabindex', '0');
		expect(
			document.getElementById(trigger.getAttribute('aria-describedby') ?? ''),
		).toHaveTextContent('Name: GitHub PAT');

		// One footer line: the credential, the separator, then the rules summary.
		const slot = within(githubTile).getByTestId('tile-detail-slot');
		const rulesText = await within(githubTile).findByTestId('tile-rules-summary');
		expect(rulesText).toHaveTextContent(/^No rules — all calls blocked$/);
		expect(slot).toContainElement(credential);
		expect(slot.textContent?.indexOf('·')).toBeGreaterThan(
			slot.textContent?.indexOf('GitHub PAT') ?? Infinity,
		);
		const credentialBox = credential.getBoundingClientRect();
		const rulesBox = rulesText.getBoundingClientRect();
		expect(Math.abs(credentialBox.top - rulesBox.top)).toBeLessThan(credentialBox.height);
		expect(rulesBox.left).toBeGreaterThan(credentialBox.right);

		// The header is unchanged: title and identity only, no chip for one credential.
		expect(within(githubTile).queryByTestId('tile-accounts-badge')).toBeNull();
		expect(
			within(githubTile).getByRole('heading', { name: 'GitHub' }).parentElement,
		).not.toContainElement(credential);

		// Every tile still stands the same height: what the suspension means
		// rides beside its chip rather than on a row the others reserve empty.
		expect(within(githubTile).getByText('Suspended · not serving')).toBeInTheDocument();
		const heights = screen
			.getAllByTestId('api-tile')
			.map((tile) => Math.round(tile.getBoundingClientRect().height));
		expect(new Set(heights).size).toBe(1);
	});

	it('deep link ?agent= preselects the agent and its grid', async () => {
		renderPage('/?agent=agnt_active_1');
		await screen.findAllByText('inbox-triage-bot');

		expect(stripTab('support-agent')).toHaveAttribute('aria-selected', 'true');
		expect(await screen.findByText('Slack')).toBeInTheDocument();
	});

	it('moves the selection with the arrow keys (selection follows focus)', async () => {
		const user = userEvent.setup();
		renderPage('/?agent=agnt_active_1');
		await screen.findAllByText('inbox-triage-bot');

		stripTab('support-agent').focus();
		await user.keyboard('{ArrowRight}');

		// Strip order is decisions-first then the working fleet, so the next
		// pill after the active agent is the disabled one.
		expect(stripTab('legacy-scraper')).toHaveAttribute('aria-selected', 'true');
		expect(stripTab('legacy-scraper')).toHaveFocus();
		expect(screen.getByTestId('location-search')).toHaveTextContent('agent=agnt_disabled_1');

		await user.keyboard('{ArrowLeft}');
		expect(stripTab('support-agent')).toHaveAttribute('aria-selected', 'true');
	});

	it('focuses the strip filter with / and narrows the pills', async () => {
		const user = userEvent.setup();
		renderPage();
		await screen.findAllByText('inbox-triage-bot');

		await user.keyboard('/');
		const filter = screen.getByLabelText('Filter agents');
		expect(filter).toHaveFocus();

		await user.type(filter, 'support');
		expect(stripTab('support-agent')).toBeInTheDocument();
		expect(screen.queryByRole('tab', { name: /legacy-scraper/ })).not.toBeInTheDocument();

		await user.clear(filter);
		await user.type(filter, 'zzz');
		expect(screen.getByText('No agents match your filter.')).toBeInTheDocument();
	});

	it('states its whole keyboard map in the page help, not in a permanent strip', async () => {
		// The map is documented where the surface explains itself, rather than in a
		// strip across the page foot that costs every operator screen height.
		const user = userEvent.setup();
		renderPage('/?agent=agnt_active_1');
		await screen.findByText('Slack');

		expect(screen.queryByTestId('keyboard-shortcuts-bar')).not.toBeInTheDocument();
		await user.click(screen.getByTestId('page-help-trigger'));
		const help = within(await screen.findByTestId('page-help-shortcuts'));
		for (const label of ['add API', 'new agent', 'search', 'close']) {
			expect(help.getByText(label)).toBeInTheDocument();
		}
	});

	it('honours a / n while nothing owns the keys', async () => {
		const user = userEvent.setup();
		renderPage('/?agent=agnt_active_1');
		await screen.findByText('Slack');

		// `a` opens the Add-APIs tray for the selected agent…
		await user.keyboard('a');
		const tray = await screen.findByRole('dialog', { name: 'Add APIs' });
		// …and cannot stack a second overlay on top of the first.
		await user.keyboard('n');
		expect(screen.queryByRole('dialog', { name: /New agent/i })).not.toBeInTheDocument();

		await user.click(within(tray).getByRole('button', { name: 'Cancel' }));
		await waitFor(() =>
			expect(screen.queryByRole('dialog', { name: 'Add APIs' })).not.toBeInTheDocument(),
		);

		// Typing is typing: the same letters in the filter never fire a verb.
		await user.click(screen.getByLabelText('Filter agents'));
		await user.keyboard('an');
		expect(screen.getByLabelText('Filter agents')).toHaveValue('an');
		expect(screen.queryByRole('dialog', { name: 'Add APIs' })).not.toBeInTheDocument();
	});

	it('leaves a unbound on an agent that cannot be given APIs', async () => {
		const user = userEvent.setup();
		renderPage('/?agent=agnt_pending_1');
		await screen.findAllByText('inbox-triage-bot');
		expect(await screen.findByRole('button', { name: 'Add APIs' })).toBeDisabled();

		// A no-op shortcut is worse than none — the hook is unbound, so the
		// key does nothing rather than opening a tray that can't bind.
		await user.keyboard('a');
		expect(screen.queryByRole('dialog', { name: 'Add APIs' })).not.toBeInTheDocument();
	});

	it('at 390px the strip scrolls horizontally instead of truncating', async () => {
		await page.viewport(390, 844);
		renderPage();
		await screen.findAllByText('inbox-triage-bot');

		const tablist = screen.getByRole('tablist', { name: 'Agents' });
		expect(getComputedStyle(tablist).overflowX).toBe('auto');
		// Pills never truncate mid-word — they keep their full label.
		expect(stripTab('inbox-triage-bot')).toHaveTextContent('inbox-triage-bot');
	});

	// --- Composition: the not-usable (dashed) case --------------------------

	it('renders an OAuth credential awaiting consent as a dashed tile with a gap hint', async () => {
		resetCredentialsStore([
			makeMockCredential({
				credential_id: 'cred_slack_1',
				name: 'Slack bot token',
				type: CredentialType.BEARER_TOKEN,
				api: { vendor: 'slack.com', name: 'default', version: '1.0.0' },
			}),
			makeMockCredential({
				credential_id: 'cred_github_1',
				name: 'GitHub PAT',
				type: CredentialType.BEARER_TOKEN,
				api: { vendor: 'github.com', name: 'default', version: '1.0.0' },
			}),
			// Interactive OAuth whose consent round-trip has not completed —
			// the one not-usable case the credential state can prove.
			makeMockCredential({
				credential_id: 'cred_stripe_oauth',
				name: 'Stripe OAuth',
				type: CredentialType.OAUTH2,
				api: { vendor: 'stripe.com', name: 'default', version: '1.0.0' },
				details: { grant_type: 'authorization_code', connected: false },
			}),
		]);
		seedCredentialBindings([
			{
				agent_id: 'agnt_active_1',
				credential_id: 'cred_stripe_oauth',
				name: 'Stripe OAuth',
				serves: [{ api_vendor: 'stripe.com', api_name: null, api_version: null }],
			},
		]);

		renderPage('/?agent=agnt_active_1');
		await screen.findByText('Slack');

		// The tile names the reason and carries the fix.
		expect(await screen.findByText(/Sign-in at stripe\.com unfinished/)).toBeInTheDocument();
		expect(screen.getByRole('button', { name: /Finish connecting/ })).toBeInTheDocument();
		const tiles = screen.getAllByTestId('api-tile');
		expect(tiles.filter((t) => t.dataset.notUsable === 'true')).toHaveLength(1);
		// The tab hints at the gap; the meta line counts it, warning-tinted,
		// ALONGSIDE the operations clause (never instead of it).
		expect(stripTab('support-agent')).toHaveTextContent('1 to set up');
		expect(stripFigure('needs-setup')).toHaveTextContent('1 to set up');
		expect(stripFigure('operations')).toBeInTheDocument();
	});

	// --- Stat strip: vitals + access stats -----------------------------------

	it('merges the vitals with the access stats in one quiet meta line', async () => {
		renderPage('/?agent=agnt_active_1');
		await screen.findByText('Slack');

		// Access clauses — the same tileStats math the grid draws from: 2 usable tiles,
		// 181 ops (the suspended binding's 912 excluded), 2 bound credentials.
		await waitFor(() => expect(stripFigure('configured')).toHaveTextContent('2 configured'));
		// Held on a skeleton until every tile's rules are read.
		await waitFor(() => expect(stripFigure('operations')).toHaveTextContent('181 operations'));
		expect(stripFigure('credentials')).toHaveTextContent('2 credentials');

		// Monitor clauses — the per-actor sources (7-day usage rollup
		// + newest execution), rendered from the mocked rollups.
		await waitFor(() => expect(stripFigure('executions')).toHaveTextContent('1,204'));
		expect(stripFigure('executions')).toHaveTextContent('7d');
		expect(stripFigure('success-rate')).toHaveTextContent('99%');
		expect(stripFigure('last-activity')).toHaveTextContent('2m');

		// The line is the only stats surface, so each clause renders once.
		expect(screen.getAllByTestId('stat-configured')).toHaveLength(1);
		expect(screen.getAllByTestId('stat-operations')).toHaveLength(1);
	});

	// --- Orphan bindings: a deleted credential's leftover link ---------------

	describe('a binding whose credential was deleted', () => {
		/** Record every purge the grid fires; the row stays, so a remount would see it. */
		function recordPurges(status = 204): string[] {
			const purges: string[] = [];
			worker.use(
				http.delete('/agents/:id/credentials/:cid', ({ params, request }) => {
					purges.push(
						`${String(params.cid)}?purge=${new URL(request.url).searchParams.get('purge')}`,
					);
					return new HttpResponse(null, { status });
				}),
			);
			return purges;
		}

		/** Every pathname the page requests while `run` is in flight. */
		async function recordRequests(run: () => Promise<void>): Promise<string[]> {
			const requested: string[] = [];
			const onRequest = ({ request }: { request: Request }) => {
				requested.push(new URL(request.url).pathname);
			};
			worker.events.on('request:start', onRequest);
			try {
				await run();
			} finally {
				worker.events.removeListener('request:start', onRequest);
			}
			return requested;
		}

		function seedOrphan(agentId: string) {
			// The backend keeps the binding after the credential delete (#1426),
			// enriched with no name and serving nothing.
			seedCredentialBindings([
				{ agent_id: agentId, credential_id: 'cred_deleted_9', name: null, serves: [] },
			]);
		}

		describe('seen by an org:admin (whose credentials list is the whole org)', () => {
			beforeEach(() => seedViewer(['org:admin']));

			it('is hidden: no tile, no remove verb, outside the credentials count', async () => {
				recordPurges();
				seedOrphan('agnt_active_1');
				renderPage('/?agent=agnt_active_1', { withAuth: true });
				await screen.findByText('Slack');

				// Two live credentials; the dead link unlocks nothing and isn't one.
				await waitFor(() =>
					expect(stripFigure('credentials')).toHaveTextContent('2 credentials'),
				);
				expect(screen.getAllByTestId('api-tile')).toHaveLength(2);
				expect(screen.queryByTestId('orphan-binding-tile')).not.toBeInTheDocument();
				expect(screen.queryByText('Credential deleted')).not.toBeInTheDocument();
				expect(screen.queryByRole('button', { name: /^Remove/ })).not.toBeInTheDocument();
			});

			it('never asks for the dead link’s rules (that read 404s)', async () => {
				recordPurges();
				seedOrphan('agnt_active_1');
				const requested = await recordRequests(async () => {
					renderPage('/?agent=agnt_active_1', { withAuth: true });
					await screen.findByText('1 access rule');
				});
				expect(requested.some((p) => p.includes('/credentials/cred_slack_1/agents/'))).toBe(
					true,
				);
				expect(requested.some((p) => p.includes('/credentials/cred_deleted_9/'))).toBe(
					false,
				);
			});

			it('leaves an agent whose only binding is an orphan on the empty state', async () => {
				recordPurges();
				seedOrphan('agnt_disabled_1');
				renderPage('/?agent=agnt_disabled_1', { withAuth: true });

				expect(
					await screen.findByText('legacy-scraper can reach nothing yet'),
				).toBeInTheDocument();
				await waitFor(() =>
					expect(stripFigure('credentials')).toHaveTextContent('0 credentials'),
				);
				expect(screen.queryByTestId('api-tile')).not.toBeInTheDocument();
			});

			it('purges it quietly, once per session — a failure is not retried on remount', async () => {
				// A 403 (read-only viewer) leaves the row in place, so a remount still sees it.
				const purges = recordPurges(403);
				seedOrphan('agnt_active_1');
				const first = renderPage('/?agent=agnt_active_1', { withAuth: true });
				await waitFor(() => expect(purges).toEqual(['cred_deleted_9?purge=true']));
				await screen.findByText('Slack');
				first.unmount();

				renderPage('/?agent=agnt_active_1', { withAuth: true });
				await screen.findByText('Slack');
				await waitFor(() =>
					expect(stripFigure('credentials')).toHaveTextContent('2 credentials'),
				);
				expect(purges).toEqual(['cred_deleted_9?purge=true']);
				// Silent either way: no toast, no error on the grid.
				expect(document.querySelector('[data-sonner-toast]')).toBeNull();
				expect(screen.queryByRole('alert')).not.toBeInTheDocument();
			});

			it('does not read a split deploy’s unenriched bindings as deleted', async () => {
				// A surface that can't reach the control DB returns EVERY binding with no
				// name and nothing served. The credentials are still in the admin's list,
				// so nothing is hidden and nothing is purged.
				const purges = recordPurges();
				seedCredentialBindings([
					{ agent_id: 'agnt_disabled_1', credential_id: 'cred_slack_1' },
					{ agent_id: 'agnt_disabled_1', credential_id: 'cred_github_1' },
				]);
				renderPage('/?agent=agnt_disabled_1', { withAuth: true });

				// The empty state only shows once the credentials list has drained.
				// Nothing is served, so there are no tiles, but both bindings are counted.
				expect(
					await screen.findByText('legacy-scraper can reach nothing yet'),
				).toBeInTheDocument();
				expect(stripFigure('credentials')).toHaveTextContent('2 credentials');
				expect(purges).toEqual([]);
			});
		});

		describe('seen by a non-admin (whose credentials list is only their own)', () => {
			beforeEach(() => seedViewer(['agents:read', 'agents:write', 'credentials:read']));

			it('is neither hidden nor purged — missing from their list is not proof', async () => {
				const purges = recordPurges();
				seedOrphan('agnt_active_1');
				const requested = await recordRequests(async () => {
					renderPage('/?agent=agnt_active_1', { withAuth: true });
					await screen.findByText('Slack');
					// Counted like any binding; it serves nothing, so it draws no tile.
					await waitFor(() =>
						expect(stripFigure('credentials')).toHaveTextContent('3 credentials'),
					);
				});
				expect(screen.getAllByTestId('api-tile')).toHaveLength(2);
				expect(purges).toEqual([]);
				// A binding serving nothing has no tile to show rules on, so none are read.
				expect(requested.some((p) => p.includes('/credentials/cred_deleted_9/'))).toBe(
					false,
				);
			});

			it('keeps a split deploy’s unenriched bindings, un-purged', async () => {
				const purges = recordPurges();
				seedCredentialBindings([
					{ agent_id: 'agnt_disabled_1', credential_id: 'cred_slack_1' },
					{ agent_id: 'agnt_disabled_1', credential_id: 'cred_github_1' },
				]);
				renderPage('/?agent=agnt_disabled_1', { withAuth: true });

				// The empty state only shows once the credentials list has drained.
				// Nothing is served, so there are no tiles, but both bindings are counted.
				expect(
					await screen.findByText('legacy-scraper can reach nothing yet'),
				).toBeInTheDocument();
				expect(stripFigure('credentials')).toHaveTextContent('2 credentials');
				expect(purges).toEqual([]);
			});
		});

		it('is neither hidden nor purged while the viewer is unknown', async () => {
			// No AuthProvider: who is looking is unknown, so nothing proves an orphan.
			const purges = recordPurges();
			seedOrphan('agnt_active_1');
			renderPage('/?agent=agnt_active_1');
			await screen.findByText('Slack');
			await waitFor(() =>
				expect(stripFigure('credentials')).toHaveTextContent('3 credentials'),
			);
			expect(purges).toEqual([]);
		});
	});

	it('states the panel identity once: APIs band + aria-label, no header h2', async () => {
		renderPage('/?agent=agnt_active_1');
		await screen.findByText('Slack');

		// The section still names the agent in the a11y tree…
		const panel = screen.getByRole('region', { name: 'APIs for support-agent' });
		// …while its VISIBLE heading is just the surface and its count: the selected
		// tab already states the name.
		expect(await within(panel).findByRole('heading', { name: 'APIs 2' })).toBeInTheDocument();
		expect(
			within(panel).queryByRole('heading', { name: 'support-agent' }),
		).not.toBeInTheDocument();
		// …and no status badge duplicates the pill's dot.
		expect(within(panel).queryByText('Active')).not.toBeInTheDocument();
		// …and no jump-off to another agent page: the dock's sheets carry the rest.
		for (const link of within(panel).queryAllByRole('link')) {
			expect(link.getAttribute('href')).not.toMatch(/\/agents\//);
		}
	});

	it('renders em-dashes when the monitor has no data and zeros without bindings', async () => {
		// A pending agent: no bindings (honest zeros) and no usage rollup, so success
		// rate and last activity read as em-dashes.
		renderPage('/?agent=agnt_pending_1');
		await screen.findAllByText('inbox-triage-bot');

		await waitFor(() => expect(stripFigure('configured')).toHaveTextContent('0'));
		expect(stripFigure('operations')).toHaveTextContent('0');
		expect(stripFigure('credentials')).toHaveTextContent('0');
		await waitFor(() => expect(stripFigure('executions')).toHaveTextContent('0'));
		expect(stripFigure('success-rate')).toHaveTextContent('—');
		expect(stripFigure('last-activity')).toHaveTextContent('—');
	});

	it('swaps the strip stats when the selection moves to another agent', async () => {
		const user = userEvent.setup();
		renderPage('/?agent=agnt_active_1');
		await screen.findByText('Slack');
		await waitFor(() => expect(stripFigure('executions')).toHaveTextContent('1,204'));

		await user.click(stripTab('legacy-scraper'));

		// The disabled agent's own rollup replaces the previous agent's figures —
		// nothing lingers across the switch; its empty feed reads as an em-dash.
		await waitFor(() => expect(stripFigure('executions')).toHaveTextContent('96'));
		expect(stripFigure('success-rate')).toHaveTextContent('74%');
		// The execution feed resolves on its own request, so the last-activity
		// clause settles independently of the rollup above it.
		await waitFor(() => expect(stripFigure('last-activity')).toHaveTextContent('—'));
		await waitFor(() => expect(stripFigure('configured')).toHaveTextContent('0'));
		expect(stripFigure('credentials')).toHaveTextContent('0');
	});

	it('omits the monitor figures when the endpoints are admin-gated (403)', async () => {
		worker.use(
			createErrorHandler('get', '/monitoring/usage', { status: 403 }),
			createErrorHandler('get', '/executions', { status: 403 }),
		);
		renderPage('/?agent=agnt_active_1');
		await screen.findByText('Slack');

		// The access figures still render from the bindings join…
		await waitFor(() => expect(stripFigure('configured')).toHaveTextContent('2'));
		// …but the gated monitor figures are omitted entirely (no em-dash
		// pretending the data exists, no error surface).
		expect(screen.queryByTestId('stat-executions')).not.toBeInTheDocument();
		expect(screen.queryByTestId('stat-success-rate')).not.toBeInTheDocument();
		expect(screen.queryByTestId('stat-last-activity')).not.toBeInTheDocument();
	});

	it('holds a long description to one line until the reader asks for the rest', async () => {
		const user = userEvent.setup();
		const long =
			'This agent handles support tickets end to end. '.repeat(8) +
			'And it keeps going well past any sensible width.';
		worker.use(
			http.get('*/agents', () =>
				HttpResponse.json({
					data: [{ ...agentRow('agnt_active_1', 'support-agent'), description: long }],
					has_more: false,
					next_cursor: null,
				}),
			),
		);
		renderPage('/?agent=agnt_active_1');
		await screen.findByText('Slack');

		// The description starts at one line, so the grid's position is not decided by
		// how much the operator typed. Scoped: the Settings sheet holds the same text.
		const line = within(
			screen.getByRole('region', { name: 'APIs for support-agent' }),
		).getByText(long);
		const lineHeight = parseFloat(getComputedStyle(line).lineHeight);
		expect(line.getBoundingClientRect().height).toBeLessThan(lineHeight * 2);

		// The rest is one click away, and going back is the same click.
		const more = screen.getByRole('button', { name: 'Show more' });
		await user.click(more);
		await waitFor(() =>
			expect(line.getBoundingClientRect().height).toBeGreaterThan(lineHeight * 2),
		);
		const less = screen.getByRole('button', { name: 'Show less' });
		expect(less).toHaveAttribute('aria-expanded', 'true');
		await user.click(less);
		await waitFor(() =>
			expect(line.getBoundingClientRect().height).toBeLessThan(lineHeight * 2),
		);
	});

	// --- Empty state / non-active treatment ---------------------------------

	it('shows the can-reach-nothing empty state and opens the Add-APIs tray', async () => {
		const user = userEvent.setup();
		renderPage('/?agent=agnt_disabled_1');
		await screen.findAllByText('inbox-triage-bot');

		expect(await screen.findByText('legacy-scraper can reach nothing yet')).toBeInTheDocument();
		// Disabled stops traffic, not editing — Add APIs stays live.
		const add = screen.getByRole('button', { name: 'Add APIs' });
		expect(add).toBeEnabled();
		await user.click(add);
		// Step 1 of the flow: pick the APIs. The credential comes after, in the
		// queue, which is why the tray says so up front.
		expect(await screen.findByRole('dialog', { name: 'Add APIs' })).toBeInTheDocument();
		expect(screen.getByText(/Each API gets a credential in this flow/)).toBeInTheDocument();
	});

	it('says disabled on the surface itself — no banner — and re-enables from the dock', async () => {
		const user = userEvent.setup();
		renderPage('/?agent=agnt_disabled_1');
		await screen.findAllByText('inbox-triage-bot');

		// Disabled is the one non-active state with no notice: the dock's red toggle
		// is both the statement and the way back, and the grid says it per tile.
		const toggle = await screen.findByTestId('dock-serving-toggle');
		expect(toggle).toHaveTextContent('Not serving');
		expect(screen.queryByTestId('agent-state-banner-disabled')).not.toBeInTheDocument();
		expect(screen.queryByText(/Not serving traffic\./)).not.toBeInTheDocument();

		// Not serving is never read-only — the toggle itself is live.
		await user.click(screen.getByRole('button', { name: /^Enable legacy-scraper/ }));
		await waitFor(() => expect(toggle).toHaveTextContent('Serving'));
	});

	it('crosses out the tabs whose verdict is settled and leaves pending upright', async () => {
		renderPage();
		await screen.findAllByText('inbox-triage-bot');

		// `disabled` has no banner, so its tab is the only place the strip states it:
		// struck through, with a glyph — a coloured dot alone can't be read.
		const struck = (name: string) =>
			getComputedStyle(within(stripTab(name)).getByText(name)).textDecorationLine;
		expect(struck('legacy-scraper')).toContain('line-through');
		expect(struck('spammy-bot')).toContain('line-through');
		// Pending is the one non-active state still in motion — crossing it out
		// would state the opposite of what is true — and active still serves.
		expect(struck('inbox-triage-bot')).toBe('none');
		expect(struck('support-agent')).toBe('none');
		// Every state carries exactly one glyph, active included, so all five
		// labels start at the same left edge.
		const glyphs = (name: string) => stripTab(name).querySelectorAll('svg').length;
		for (const name of ['legacy-scraper', 'inbox-triage-bot', 'support-agent', 'spammy-bot']) {
			expect(glyphs(name)).toBe(1);
		}
	});

	it('reads not-serving on every tile of a non-active agent, never Ready', async () => {
		// The only fixture agent WITH bindings, handed back as disabled: the grid
		// is what must read inactive, so the tiles carry it themselves.
		worker.use(
			http.get('*/agents', () =>
				HttpResponse.json({
					data: [{ ...agentRow('agnt_active_1', 'support-agent'), status: 'disabled' }],
					has_more: false,
					next_cursor: null,
				}),
			),
		);
		renderPage('/?agent=agnt_active_1');
		await screen.findByText('Slack');

		const tiles = screen.getAllByTestId('api-tile');
		expect(tiles).toHaveLength(2);
		expect(tiles.every((tile) => tile.dataset.notServing === 'true')).toBe(true);
		// No green claim anywhere on a grid that serves nothing; the suspended
		// binding keeps its own chip, which outranks the agent-level state.
		expect(screen.queryByText('Ready')).not.toBeInTheDocument();
		const chips = tiles.map((tile) => within(tile).getByTestId('tile-status-chip').textContent);
		expect(chips.sort()).toEqual(['Not serving', 'Suspended · not serving']);
	});

	it("reads Status unavailable (never Ready) when a binding's rules read fails, and Retry recovers", async () => {
		let failing = true;
		worker.use(
			http.get('*/credentials/:cid/agents/:aid/permissions', ({ params }) => {
				if (failing && params.cid === 'cred_slack_1') {
					return HttpResponse.json({ detail: 'boom' }, { status: 500 });
				}
				return undefined;
			}),
		);
		renderPage('/?agent=agnt_active_1');
		await screen.findByText('Slack');
		const retry = await screen.findByTestId('tile-status-retry', {}, { timeout: 5000 });
		const tile = retry.closest<HTMLElement>('[data-testid="api-tile"]');
		expect(tile).not.toBeNull();
		expect(within(tile as HTMLElement).getByTestId('tile-status-chip')).toHaveTextContent(
			'Status unavailable',
		);
		expect(within(tile as HTMLElement).queryByText('Ready')).not.toBeInTheDocument();

		failing = false;
		await userEvent.click(retry);
		await waitFor(() =>
			expect(within(tile as HTMLElement).getByTestId('tile-status-chip')).toHaveTextContent(
				'Ready',
			),
		);
	});

	it('blocks Add APIs with a reason on a pending agent and approves from the banner', async () => {
		const user = userEvent.setup();
		renderPage('/?agent=agnt_pending_1');
		await screen.findAllByText('inbox-triage-bot');

		expect(
			await screen.findByText(/Not serving traffic\. Approve it to let it authenticate\./),
		).toBeInTheDocument();
		expect(await screen.findByRole('button', { name: 'Add APIs' })).toBeDisabled();
		expect(screen.getByText('Approve this agent before giving it APIs.')).toBeInTheDocument();
		// The empty grid names the state's own blocker: telling a pending agent's
		// operator that the bind is what's missing points them at the wrong fix.
		expect(
			await screen.findByText(/a pending agent cannot authenticate either/),
		).toBeInTheDocument();

		await user.click(screen.getByRole('button', { name: 'Approve' }));
		expect(await screen.findByText('Agent approved')).toBeInTheDocument();
		await waitFor(() => {
			expect(screen.queryByText(/Waiting for approval/)).not.toBeInTheDocument();
		});
	});

	it('archived agents render no live bind control', async () => {
		worker.use(
			http.get('*/agents', () =>
				HttpResponse.json({
					data: [
						{
							id: 'agnt_archived_1',
							name: 'retired-bot',
							description: null,
							status: 'archived',
							owner_id: null,
							registered_by: 'self',
							parent_agent_id: null,
							approved_by: null,
							denial_reason: null,
							denied_by: null,
							created_at: new Date().toISOString(),
							approved_at: null,
							has_api_key: false,
						},
						// A fleet beside it: an org of history alone is a fresh workspace
						// (the zero-agents landing), not a fleet.
						{ ...agentRow('agnt_disabled_x', 'paused-bot'), status: 'disabled' },
					],
					has_more: false,
					next_cursor: null,
				}),
			),
			http.get('/agents/:id/credentials', () => HttpResponse.json({ data: [] })),
		);
		renderPage('/?agent=agnt_archived_1');
		await screen.findByRole('tab', { name: /retired-bot/ });

		expect(
			await screen.findByText(
				/This agent is retired\. Its bindings, grants and consents were swept\./,
			),
		).toBeInTheDocument();
		expect(
			await screen.findByText(
				'Archiving swept its credential bindings; an archived agent keeps no access.',
			),
		).toBeInTheDocument();
		expect(screen.queryByRole('button', { name: 'Add APIs' })).not.toBeInTheDocument();
	});

	// --- Pending-approval banner ---------------------------------------------

	it('Review selects the named agent in the strip and shows its Approve panel', async () => {
		const user = userEvent.setup();
		renderPage();
		await screen.findAllByText('inbox-triage-bot');

		await user.click(
			within(await findApprovalBanner()).getByRole('button', {
				name: 'Review inbox-triage-bot',
			}),
		);

		expect(screen.getByTestId('location-search')).toHaveTextContent('agent=agnt_pending_1');
		expect(stripTab('inbox-triage-bot')).toHaveAttribute('aria-selected', 'true');
		// The pending agent's panel shows Add-APIs disabled with Approve adjacent.
		expect(
			await screen.findByText(/Not serving traffic\. Approve it to let it authenticate\./),
		).toBeInTheDocument();
		expect(await screen.findByRole('button', { name: 'Add APIs' })).toBeDisabled();
		expect(screen.getByRole('button', { name: 'Approve' })).toBeEnabled();
	});

	it('approves from the banner, then advances to the next longest-waiting', async () => {
		const user = userEvent.setup();
		renderPage();
		await screen.findAllByText('inbox-triage-bot');

		await user.click(
			within(await findApprovalBanner()).getByRole('button', {
				name: 'Approve inbox-triage-bot',
			}),
		);

		expect(await screen.findByText('Agent approved')).toBeInTheDocument();
		// No stale name: the banner moves on to the remaining pending agent.
		await waitFor(() => {
			expect(within(approvalBanner()).getByText('release-notes-bot')).toBeInTheDocument();
		});
		expect(within(approvalBanner()).queryByText('inbox-triage-bot')).not.toBeInTheDocument();
		expect(approvalBanner()).not.toHaveTextContent('more waiting');
	});

	it('denies from the banner → requires a reason → banner advances', async () => {
		const user = userEvent.setup();
		renderPage();
		await screen.findAllByText('inbox-triage-bot');

		await user.click(
			within(await findApprovalBanner()).getByRole('button', {
				name: 'Deny inbox-triage-bot',
			}),
		);

		const dialog = await screen.findByRole('dialog');
		// Empty reason is blocked client-side.
		await user.click(within(dialog).getByRole('button', { name: 'Deny' }));
		expect(await within(dialog).findByText('A reason is required.')).toBeInTheDocument();

		await user.type(within(dialog).getByLabelText('Reason'), 'spam');
		await user.click(within(dialog).getByRole('button', { name: 'Deny' }));

		await waitFor(() => {
			expect(within(approvalBanner()).getByText('release-notes-bot')).toBeInTheDocument();
		});
		expect(within(approvalBanner()).queryByText('inbox-triage-bot')).not.toBeInTheDocument();
	});

	// --- Error / loading / first-run states ----------------------------------

	it('surfaces an error when the agents list fails', async () => {
		worker.use(createErrorHandler('get', '/agents', { status: 500 }));
		renderPage();
		expect(await screen.findByRole('alert')).toBeInTheDocument();
	});

	it('surfaces an error inside the panel when the bindings read fails', async () => {
		worker.use(createErrorHandler('get', '/agents/:id/credentials', { status: 500 }));
		renderPage('/?agent=agnt_active_1');
		await screen.findAllByText('inbox-triage-bot');
		expect(await screen.findByRole('alert')).toBeInTheDocument();
		// The strip itself stays usable.
		expect(stripTab('support-agent')).toBeInTheDocument();
	});

	it('the Add-APIs tray holds on a failed bindings read and retries in place', async () => {
		let bindingsHealthy = false;
		worker.use(
			http.get('/agents/:id/credentials', () =>
				bindingsHealthy
					? HttpResponse.json({ data: [] })
					: HttpResponse.json({ detail: 'Server error' }, { status: 500 }),
			),
		);
		const user = userEvent.setup();
		resetApisStore([{ row: apiRow('stripe.com', 'Stripe', 10), spec: {} }]);
		renderPage('/?agent=agnt_disabled_1');
		await screen.findAllByText('inbox-triage-bot');

		await user.click(await screen.findByRole('button', { name: 'Add APIs' }));
		const tray = await screen.findByRole('dialog', { name: 'Add APIs' });
		// Without the bindings every credential the agent holds would look unbound
		// and be offered again, so nothing continues until the read succeeds.
		expect(
			await within(tray).findByText(/Could not read which APIs legacy-scraper already has/),
		).toBeInTheDocument();
		await user.click(await within(tray).findByRole('checkbox', { name: /Stripe/ }));
		expect(within(tray).getByRole('button', { name: 'Continue' })).toBeDisabled();

		bindingsHealthy = true;
		await user.click(within(tray).getByRole('button', { name: /Try again/ }));
		await waitFor(() =>
			expect(
				within(tray).queryByText(/Could not read which APIs legacy-scraper already has/),
			).not.toBeInTheDocument(),
		);
		await waitFor(() =>
			expect(within(tray).getByRole('button', { name: 'Continue' })).toBeEnabled(),
		);
	});

	it('shows the zero-agents landing when no agents are registered', async () => {
		worker.use(
			http.get('/agents', () =>
				HttpResponse.json({ data: [], has_more: false, next_cursor: null }),
			),
		);
		const user = userEvent.setup();
		renderPage();

		// Agents is the app's home, so an empty fleet is a fresh workspace.
		expect(await screen.findByTestId('agents-empty-landing')).toBeInTheDocument();
		expect(screen.queryByTestId('agents-landing')).toBeNull();
		expect(screen.queryByTestId('agent-dock')).not.toBeInTheDocument();

		// The header's create button steps back for a fresh org; it opens the panel
		// on "Create here", as it always does.
		const create = screen.getByRole('button', { name: 'New agent' });
		expect(create).toHaveAttribute('data-emphasis', 'secondary');
		await user.click(create);
		const panel = await screen.findByRole('dialog', { name: 'New agent' });
		expect(within(panel).getByRole('tab', { name: 'Create here' })).toHaveAttribute(
			'aria-selected',
			'true',
		);
	});

	// --- Pagination honesty: guarded drain + fully-drained join sources ------

	it('keeps the fleet rendered and offers retry when a later agents page fails', async () => {
		const user = userEvent.setup();
		const listCursors: Array<string | null> = [];
		let secondPageHealthy = false;
		worker.use(
			http.get('/agents', ({ request }) => {
				const url = new URL(request.url);
				// The approval banner's `status=pending` poll shares the
				// endpoint; this spec tracks only the strip's cursor drain.
				if (url.searchParams.get('status') === 'pending') {
					return HttpResponse.json({ data: [], has_more: false, next_cursor: null });
				}
				const cursor = url.searchParams.get('cursor');
				listCursors.push(cursor);
				if (cursor === null) {
					return HttpResponse.json({
						data: [agentRow('agnt_page1', 'first-page-bot')],
						has_more: true,
						next_cursor: 'p2',
					});
				}
				if (!secondPageHealthy) {
					return HttpResponse.json({ detail: 'Server error' }, { status: 500 });
				}
				return HttpResponse.json({
					data: [agentRow('agnt_page2', 'second-page-bot')],
					has_more: false,
					next_cursor: null,
				});
			}),
			http.get('/agents/:id/credentials', () => HttpResponse.json({ data: [] })),
		);
		renderPage();

		// The failed later page must NOT nuke the loaded surface — the strip
		// keeps its pills and a compact inline notice owns the failure.
		expect(await screen.findByRole('tab', { name: /first-page-bot/ })).toBeInTheDocument();
		expect(await screen.findByText(/Couldn't load the rest of the fleet/)).toBeInTheDocument();

		// Regression: the error state must stop the eager drain, not re-fire it —
		// one first page plus one failed second page, and nothing more.
		const settledCalls = listCursors.length;
		await new Promise((resolve) => setTimeout(resolve, 300));
		expect(listCursors.length).toBe(settledCalls);
		expect(listCursors).toEqual([null, 'p2']);

		// Retry resumes the drain and completes the roster.
		secondPageHealthy = true;
		await user.click(screen.getByRole('button', { name: /Try again/ }));
		expect(await screen.findByRole('tab', { name: /second-page-bot/ })).toBeInTheDocument();
		await waitFor(() => {
			expect(
				screen.queryByText(/Couldn't load the rest of the fleet/),
			).not.toBeInTheDocument();
		});
	});

	/** Wire-shaped PENDING row for the banner's paged `status=pending` slice. */
	function pendingWireRow(id: string, name: string, minutesAgo: number) {
		return {
			...agentRow(id, name),
			status: 'pending',
			created_at: new Date(Date.now() - minutesAgo * 60_000).toISOString(),
		};
	}

	it('banner names the true longest-waiting agent from the LAST drained pending page, count exact', async () => {
		worker.use(
			http.get('/agents', ({ request }) => {
				const url = new URL(request.url);
				// Only the badge/banner's pending slice pages here; the strip's
				// status=all list falls through to the seeded store.
				if (url.searchParams.get('status') !== 'pending') return undefined;
				const cursor = url.searchParams.get('cursor');
				// Three DESC pages continuing ONE created_at sequence: the true
				// longest-waiting agent lives on page 3.
				if (cursor === null) {
					return HttpResponse.json({
						data: [
							pendingWireRow('agnt_pp1a', 'newest-pending-bot', 2),
							pendingWireRow('agnt_pp1b', 'page1-pending-bot', 10),
						],
						has_more: true,
						next_cursor: 'pend-2',
					});
				}
				if (cursor === 'pend-2') {
					return HttpResponse.json({
						data: [
							pendingWireRow('agnt_pp2a', 'page2-pending-bot', 25),
							pendingWireRow('agnt_pp2b', 'page2-older-bot', 40),
						],
						has_more: true,
						next_cursor: 'pend-3',
					});
				}
				return HttpResponse.json({
					data: [
						pendingWireRow('agnt_pp3a', 'page3-pending-bot', 60),
						pendingWireRow('agnt_pp3b', 'deep-page-oldest-bot', 90),
					],
					has_more: false,
					next_cursor: null,
				});
			}),
			http.get('/agents/:id/credentials', () => HttpResponse.json({ data: [] })),
		);
		renderPage();

		// Once the drain completes, the banner names the page-3 agent with an
		// EXACT fold-in count — no hedge left once the list is whole.
		await findApprovalBanner();
		await waitFor(() => {
			expect(within(approvalBanner()).getByText('deep-page-oldest-bot')).toBeInTheDocument();
		});
		expect(approvalBanner()).toHaveTextContent('and 5 more waiting');
		expect(approvalBanner()).not.toHaveTextContent('5+');
	});

	it('banner hedges the count while the pending drain is incomplete (failed later page)', async () => {
		worker.use(
			http.get('/agents', ({ request }) => {
				const url = new URL(request.url);
				if (url.searchParams.get('status') !== 'pending') return undefined;
				const cursor = url.searchParams.get('cursor');
				if (cursor === null) {
					return HttpResponse.json({
						data: [
							pendingWireRow('agnt_fp1a', 'newest-pending-bot', 5),
							pendingWireRow('agnt_fp1b', 'loaded-oldest-bot', 30),
						],
						has_more: true,
						next_cursor: 'pend-2',
					});
				}
				// The drain's second page fails (after retries) — the loaded
				// rows are a floor the banner must not present as exact.
				return HttpResponse.json({ detail: 'Server error' }, { status: 500 });
			}),
			http.get('/agents/:id/credentials', () => HttpResponse.json({ data: [] })),
		);
		renderPage();

		// Honest floor: the current-best candidate stays named and actionable,
		// but the fold-in count hedges — no exact claim, no false superlative.
		await findApprovalBanner();
		await waitFor(() => {
			expect(within(approvalBanner()).getByText('loaded-oldest-bot')).toBeInTheDocument();
		});
		await waitFor(() => {
			expect(approvalBanner()).toHaveTextContent('and 1+ more waiting');
		});
		expect(
			within(approvalBanner()).getByRole('button', { name: 'Approve loaded-oldest-bot' }),
		).toBeEnabled();
	});

	it('renders a second-page credential with its awaiting-consent state (drained join)', async () => {
		// The credential still waiting for consent lives on page 2, so a
		// first-page-only join would render its tile wrongly solid.
		worker.use(
			http.get('/credentials', ({ request }) => {
				const cursor = new URL(request.url).searchParams.get('cursor');
				if (cursor === null) {
					return HttpResponse.json({
						data: [
							makeMockCredential({
								credential_id: 'cred_slack_1',
								name: 'Slack bot token',
								type: CredentialType.BEARER_TOKEN,
								api: { vendor: 'slack.com', name: 'default', version: '1.0.0' },
							}),
							makeMockCredential({
								credential_id: 'cred_github_1',
								name: 'GitHub PAT',
								type: CredentialType.BEARER_TOKEN,
								api: { vendor: 'github.com', name: 'default', version: '1.0.0' },
							}),
						],
						has_more: true,
						next_cursor: 'page-2',
					});
				}
				return HttpResponse.json({
					data: [
						makeMockCredential({
							credential_id: 'cred_stripe_oauth',
							name: 'Stripe OAuth',
							type: CredentialType.OAUTH2,
							api: { vendor: 'stripe.com', name: 'default', version: '1.0.0' },
							details: { grant_type: 'authorization_code', connected: false },
						}),
					],
					has_more: false,
					next_cursor: null,
				});
			}),
		);
		seedCredentialBindings([
			{
				agent_id: 'agnt_active_1',
				credential_id: 'cred_stripe_oauth',
				name: 'Stripe OAuth',
				serves: [{ api_vendor: 'stripe.com', api_name: null, api_version: null }],
			},
		]);

		renderPage('/?agent=agnt_active_1');
		await screen.findByText('Slack');

		// The page-2 credential's tile is dashed with the honest reason + fix.
		expect(await screen.findByText(/Sign-in at stripe\.com unfinished/)).toBeInTheDocument();
		expect(screen.getByRole('button', { name: /Finish connecting/ })).toBeInTheDocument();
		const tiles = screen.getAllByTestId('api-tile');
		expect(tiles.filter((t) => t.dataset.notUsable === 'true')).toHaveLength(1);
		// And the derived hints count it: the tab's gap hint + the warning
		// clause on the meta line.
		expect(stripTab('support-agent')).toHaveTextContent('1 to set up');
		expect(stripFigure('needs-setup')).toHaveTextContent('1 to set up');
	});

	it('connecting from the inventory sheet clears the dashed tile and gap hint', async () => {
		// The mocked connect flips a mutable fixture, like the backend callback. The
		// cleared hint proves the DRAINED join was invalidated, not just the sheet's.
		let connected = false;
		const stripeCred = (): Credential =>
			makeMockCredential({
				credential_id: 'cred_stripe_oauth',
				name: 'Stripe OAuth',
				type: CredentialType.OAUTH2,
				api: { vendor: 'stripe.com', name: 'default', version: '1.0.0' },
				details: { grant_type: 'authorization_code', connected },
				provider_account_ref: connected ? 'connected' : null,
				updated_at: connected ? '2026-02-01T00:00:00Z' : null,
			});
		worker.use(
			http.get('/credentials', ({ request }) => {
				const cursor = new URL(request.url).searchParams.get('cursor');
				if (cursor === null) {
					return HttpResponse.json({
						data: [
							makeMockCredential({
								credential_id: 'cred_slack_1',
								name: 'Slack bot token',
								type: CredentialType.BEARER_TOKEN,
								api: { vendor: 'slack.com', name: 'default', version: '1.0.0' },
							}),
							stripeCred(),
						],
						has_more: true,
						next_cursor: 'page-2',
					});
				}
				return HttpResponse.json({
					data: [
						makeMockCredential({
							credential_id: 'cred_github_1',
							name: 'GitHub PAT',
							type: CredentialType.BEARER_TOKEN,
							api: { vendor: 'github.com', name: 'default', version: '1.0.0' },
						}),
					],
					has_more: false,
					next_cursor: null,
				});
			}),
			http.get('/credentials/:id', ({ params }) =>
				params.id === 'cred_stripe_oauth' ? HttpResponse.json(stripeCred()) : undefined,
			),
			http.post('/credentials/:id/connect', () => {
				// The user "completes" the hosted sign-in shortly after the
				// popup opens (after the flow's baseline read).
				setTimeout(() => {
					connected = true;
				}, 150);
				return HttpResponse.json({
					authorize_url: 'https://provider.example.com/oauth/authorize?state=mock',
					state: 'mock',
				});
			}),
		);
		seedCredentialBindings([
			{
				agent_id: 'agnt_active_1',
				credential_id: 'cred_stripe_oauth',
				name: 'Stripe OAuth',
				serves: [{ api_vendor: 'stripe.com', api_name: null, api_version: null }],
			},
		]);
		const fakePopup = { closed: false, close: (): void => {} };
		vi.spyOn(window, 'open').mockReturnValue(fakePopup as unknown as Window);

		const user = userEvent.setup();
		renderPage('/?agent=agnt_active_1');

		// The dashed tile + pill hint render first — the exact state whose
		// prescribed fix is connecting.
		expect(await screen.findByText(/Sign-in at stripe\.com unfinished/)).toBeInTheDocument();
		expect(stripTab('support-agent')).toHaveTextContent('1 to set up');

		// Open the page-level inventory sheet (the trigger lives on the
		// page header, not the dock) and connect the credential.
		await user.click(screen.getByRole('button', { name: 'Credentials' }));
		const sheet = within(await screen.findByTestId('sheet-primitive'));
		await user.click(await sheet.findByRole('button', { name: 'Connect Stripe OAuth' }));

		// Nudge the poll loop with the popup's advisory message (#598) so the
		// flow re-reads immediately instead of waiting a full poll tick.
		await waitFor(
			() => {
				window.dispatchEvent(
					new MessageEvent('message', {
						origin: window.location.origin,
						data: { type: OAUTH_CONNECT_MESSAGE_TYPE, status: 'ok' },
					}),
				);
				expect(screen.getAllByText('Connected').length).toBeGreaterThan(0);
			},
			{ timeout: 5000 },
		);

		// The fix under test: the surface underneath refreshes — the dashed
		// tile solidifies and the strip pill's gap hint clears.
		await waitFor(() => {
			expect(screen.queryByText(/Sign-in at stripe\.com unfinished/)).not.toBeInTheDocument();
		});
		expect(stripTab('support-agent')).not.toHaveTextContent('1 to set up');
		const tiles = screen.getAllByTestId('api-tile');
		expect(tiles.filter((t) => t.dataset.notUsable === 'true')).toHaveLength(0);
		// The sheet's own list updated too: the row now offers Reconnect.
		expect(
			await sheet.findByRole('button', { name: 'Reconnect Stripe OAuth' }),
		).toBeInTheDocument();
	});

	it('scopes the banner Approve spinner to the named agent', async () => {
		const user = userEvent.setup();
		// Hold the top banner's approval of agnt_pending_1 in flight behind a
		// gate so the pending state is observable deterministically.
		let releaseApprove!: () => void;
		const gate = new Promise<void>((resolve) => {
			releaseApprove = resolve;
		});
		worker.use(
			http.post('/agents/agnt_pending_1\\:approve', async () => {
				await gate;
				return HttpResponse.json({
					id: 'agnt_pending_1',
					name: 'inbox-triage-bot',
					status: 'active',
					created_at: new Date().toISOString(),
				});
			}),
		);
		// Select the OTHER pending agent; its panel owns a plain "Approve".
		renderPage('/?agent=agnt_pending_2');
		await screen.findByText(/Not serving traffic\. Approve it to let it authenticate\./);

		await user.click(
			within(await findApprovalBanner()).getByRole('button', {
				name: 'Approve inbox-triage-bot',
			}),
		);

		// The banner owns the in-flight spinner…
		await waitFor(() => {
			expect(
				within(approvalBanner()).getByRole('button', {
					name: 'Approve inbox-triage-bot',
				}),
			).toHaveAttribute('aria-busy', 'true');
		});
		// …while the selected (other) agent's panel button stays idle.
		const panelApprove = screen.getByRole('button', { name: 'Approve' });
		expect(panelApprove).not.toHaveAttribute('aria-busy');
		expect(panelApprove).toBeEnabled();

		releaseApprove();
		expect(await screen.findByText('Agent approved')).toBeInTheDocument();
	});

	it("denies the selected pending agent from its own panel, not only the banner's pick", async () => {
		const user = userEvent.setup();
		// The banner pins the longest-waiting agent (inbox-triage-bot); this one is
		// decided on its own panel.
		renderPage('/?agent=agnt_pending_2');
		const banner = await screen.findByTestId('agent-state-banner-pending');

		await user.click(within(banner).getByRole('button', { name: 'Deny' }));
		const dialog = await screen.findByRole('dialog', { name: 'Deny release-notes-bot' });
		// A reason is required before anything crosses the wire.
		await user.click(within(dialog).getByRole('button', { name: 'Deny' }));
		expect(await within(dialog).findByText('A reason is required.')).toBeInTheDocument();

		await user.type(within(dialog).getByLabelText('Reason'), 'Unknown publisher');
		await user.click(within(dialog).getByRole('button', { name: 'Deny' }));
		expect(await screen.findByTestId('agent-state-banner-rejected')).toHaveTextContent(
			'Reason: Unknown publisher',
		);
	});

	it('the Waiting for approval banner orders Approve before a tonal Deny and says what Approve grants', async () => {
		renderPage('/?agent=agnt_pending_2');
		const banner = await screen.findByTestId('agent-state-banner-pending');
		const buttons = within(banner).getAllByRole('button');
		expect(buttons.map((b) => b.textContent)).toEqual(['Approve', 'Deny']);
		expect(buttons[1].className).not.toContain('bg-danger');
		const copy = await within(banner).findByTestId('approval-grant-note');
		expect(copy).toHaveTextContent(/^Approving grants /);
		expect(buttons[0]).toHaveAccessibleDescription(copy.textContent!);
	});

	it('keeps the deny dialog open and toasts when the panel deny fails', async () => {
		const user = userEvent.setup();
		worker.use(createErrorHandler('post', '/agents/:id\\:deny', { status: 500 }));
		renderPage('/?agent=agnt_pending_2');
		const banner = await screen.findByTestId('agent-state-banner-pending');

		await user.click(within(banner).getByRole('button', { name: 'Deny' }));
		const dialog = await screen.findByRole('dialog', { name: 'Deny release-notes-bot' });
		await user.type(within(dialog).getByLabelText('Reason'), 'nope');
		await user.click(within(dialog).getByRole('button', { name: 'Deny' }));

		expect(await screen.findByText('Failed to deny the agent.')).toBeInTheDocument();
		expect(screen.getByRole('dialog', { name: 'Deny release-notes-bot' })).toBeInTheDocument();
	});

	it('names the reason and who denied a rejected agent', async () => {
		renderPage('/?agent=agnt_rejected_1');
		const banner = await screen.findByTestId('agent-state-banner-rejected');
		expect(banner).toHaveTextContent('Reason: Unverified publisher.');
		// The denier resolves through the actor directory, never a raw id.
		await waitFor(() => expect(banner).toHaveTextContent('Denied by Admin User'));
		expect(within(banner).queryByRole('button')).not.toBeInTheDocument();
	});

	it('requests the usage window with a next-minute-ceiled until bound (#913)', async () => {
		// The aggregate filters `started_at < until` strictly, so the request
		// must carry an explicit `until` PAST "now" — a floored (or absent →
		// server-floored) bound excludes the current partial minute, so fresh
		// executions show in the Activity feed but not in the 7-day figure.
		let captured: URLSearchParams | null = null;
		worker.use(
			http.get('/monitoring/usage', ({ request }) => {
				const url = new URL(request.url);
				if (url.searchParams.get('agent_id') !== 'agnt_active_1') return undefined;
				captured = url.searchParams;
				return undefined; // fall through to the module's fixture handler
			}),
		);
		const beforeSec = Math.floor(Date.now() / 1000);
		renderPage('/?agent=agnt_active_1');

		await waitFor(() => expect(captured).not.toBeNull());
		const params: URLSearchParams = captured!;
		const since = Number(params.get('since'));
		const until = Number(params.get('until'));
		expect(until % 60).toBe(0);
		expect(until).toBeGreaterThan(beforeSec);
		// Exact 7-day width: the backend derives its bucket tier from
		// `until - since`, so the window must not stretch past the tier edge.
		expect(until - since).toBe(7 * 86_400);
	});

	it('has no critical a11y violations', async () => {
		const { container } = renderPage('/?agent=agnt_active_1');
		await screen.findByText('Slack');
		await screen.findByText('1 access rule');
		// Let the panel's entrance fade settle — axe measures contrast on the
		// rendered opacity, so a mid-animation snapshot reports false positives.
		await new Promise((resolve) => setTimeout(resolve, 400));
		await checkA11y(container);
	});

	// --- Create sheet (New agent lives at the strip's end; carries optional
	//     initial permissions) ----------------------------------------------

	it('creates an agent with initial permissions included in the POST body', async () => {
		const user = userEvent.setup();
		let postBody: Record<string, unknown> | null = null;
		worker.use(
			http.post('/agents', async ({ request }) => {
				postBody = (await request.json()) as Record<string, unknown>;
				return HttpResponse.json(
					{
						id: 'agnt_new',
						name: postBody.name,
						description: postBody.description ?? null,
						status: 'active',
						created_at: new Date().toISOString(),
					},
					{ status: 201 },
				);
			}),
		);
		renderPage();
		await screen.findAllByText('inbox-triage-bot');

		const sheet = await openCreateHere(user);
		await user.type(within(sheet).getByLabelText('Name'), 'granted-agent');

		// The permissions section is an optional, collapsed disclosure.
		await user.click(within(sheet).getByRole('button', { name: /Initial permissions/ }));
		// Expand the Capabilities group, then tick one grantable permission.
		await user.click(
			await within(sheet).findByRole('button', { name: /Capabilities permissions/ }),
		);
		await user.click(within(sheet).getByRole('checkbox', { name: 'capabilities:execute' }));

		await user.click(within(sheet).getByRole('button', { name: 'Create empty' }));
		expect(await screen.findByText('Agent created')).toBeInTheDocument();
		expect(postBody).toMatchObject({
			name: 'granted-agent',
			permissions: ['capabilities:execute'],
		});
	});

	it('omits permissions from the POST body when none are selected', async () => {
		const user = userEvent.setup();
		let postBody: Record<string, unknown> | null = null;
		worker.use(
			http.post('/agents', async ({ request }) => {
				postBody = (await request.json()) as Record<string, unknown>;
				return HttpResponse.json(
					{
						id: 'agnt_new',
						name: postBody.name,
						status: 'active',
						created_at: new Date().toISOString(),
					},
					{ status: 201 },
				);
			}),
		);
		renderPage();
		await screen.findAllByText('inbox-triage-bot');

		const sheet = await openCreateHere(user);
		await user.type(within(sheet).getByLabelText('Name'), 'plain-agent');
		await user.click(within(sheet).getByRole('button', { name: 'Create empty' }));

		expect(await screen.findByText('Agent created')).toBeInTheDocument();
		// The client normalises an empty selection to `permissions: null`.
		expect(postBody).toMatchObject({ name: 'plain-agent', permissions: null });
	});

	it('creating an agent flows straight into picking its APIs', async () => {
		const user = userEvent.setup();
		renderPage();
		await screen.findAllByText('inbox-triage-bot');

		const sheet = await openCreateHere(user);
		await user.type(within(sheet).getByLabelText('Name'), 'chained-agent');
		await user.click(within(sheet).getByRole('button', { name: 'Create and add APIs' }));

		// A created agent can authenticate and still fail every call it makes, so the
		// tray opens on it rather than leaving the operator to find it in the strip.
		expect(await screen.findByText('Agent created')).toBeInTheDocument();
		await waitFor(() =>
			expect(stripTab('chained-agent')).toHaveAttribute('aria-selected', 'true'),
		);
		const tray = await screen.findByRole('dialog', { name: 'Add APIs' });
		expect(
			within(tray).getByText(/Pick what chained-agent should be able to call/),
		).toBeVisible();
	});

	it('Create empty selects the new agent and leaves the tray shut', async () => {
		const user = userEvent.setup();
		renderPage();
		await screen.findAllByText('inbox-triage-bot');

		const sheet = await openCreateHere(user);
		await user.type(within(sheet).getByLabelText('Name'), 'identity-only');
		await user.click(within(sheet).getByRole('button', { name: 'Create empty' }));

		// The de-emphasised exit still lands on the new agent's screen — the same
		// Add-APIs step is one click away there, so nothing is lost by taking it.
		expect(await screen.findByText('Agent created')).toBeInTheDocument();
		await waitFor(() =>
			expect(stripTab('identity-only')).toHaveAttribute('aria-selected', 'true'),
		);
		expect(await screen.findByRole('button', { name: 'Add APIs' })).toBeEnabled();
		expect(screen.queryByRole('dialog', { name: 'Add APIs' })).not.toBeInTheDocument();
	});
});

describe('AgentsPage — Add APIs: Back from the setup queue to the tray', () => {
	beforeEach(async () => {
		await page.viewport(1280, 900);
		setToken('test-token');
		window.localStorage.clear();
		resetAgentsStore();
		// Stripe is covered by an existing credential, so it can be added in one
		// confirm; the rest need a new credential.
		resetCredentialsStore([
			makeMockCredential({
				credential_id: 'cred_stripe_1',
				name: 'Stripe key',
				type: CredentialType.BEARER_TOKEN,
				api: { vendor: 'stripe.com', name: 'default', version: '1.0.0' },
			}),
		]);
		resetApisStore([
			{ row: apiRow('stripe.com', 'Stripe', 10), spec: {} },
			{ row: apiRow('notion.so', 'Notion', 10), spec: {} },
			{ row: apiRow('linear.app', 'Linear', 10), spec: {} },
			{ row: apiRow('acme.io', 'Acme', 10), spec: {} },
		]);
	});

	async function trayRow(name: string): Promise<HTMLElement> {
		const tray = await screen.findByRole('dialog', { name: 'Add APIs' });
		return within(tray).findByRole('checkbox', { name: new RegExp(name) });
	}

	function queueRows(): string[] {
		return screen
			.queryAllByTestId('queue-progress-row')
			.map(
				(r) =>
					`${r.textContent?.split(/Now|Waiting|Added|Not added|Failed/)[0]}:${r.dataset.status}`,
			);
	}

	it('keeps what was added, drops what was unticked and appends what was ticked', async () => {
		const user = userEvent.setup();
		// Disabled stops traffic, not editing — and this agent starts with no bindings.
		renderPage('/?agent=agnt_disabled_1');
		await user.click(await screen.findByRole('button', { name: 'Add APIs' }));
		for (const name of ['Stripe', 'Notion', 'Linear']) await user.click(await trayRow(name));
		const tray = screen.getByRole('dialog', { name: 'Add APIs' });
		await waitFor(() =>
			expect(within(tray).getByRole('button', { name: 'Continue' })).toBeEnabled(),
		);
		await user.click(within(tray).getByRole('button', { name: 'Continue' }));

		// Stripe: its one existing credential is confirmed and bound.
		expect(await screen.findByText('Set up 3 APIs')).toBeInTheDocument();
		await user.click(await screen.findByRole('button', { name: 'Use this credential' }));
		await waitFor(() => expect(queueRows()[0]).toBe('Stripe:added'));

		await user.click(screen.getByRole('button', { name: 'Back to APIs' }));

		// Back in the tray: the batch is still ticked, and the added API is locked.
		const back = await screen.findByRole('dialog', { name: 'Add APIs' });
		await waitFor(() => expect(within(back).getByLabelText('Search APIs')).toHaveFocus());
		const stripe = await trayRow('Stripe');
		expect(stripe).toHaveAttribute('aria-checked', 'true');
		expect(stripe).toBeDisabled();
		expect(within(stripe).getByText('Added')).toBeInTheDocument();
		expect(await trayRow('Notion')).toHaveAttribute('aria-checked', 'true');
		expect(await trayRow('Linear')).toHaveAttribute('aria-checked', 'true');

		await user.click(await trayRow('Linear'));
		await user.click(await trayRow('Acme'));
		await waitFor(() =>
			expect(within(back).getByRole('button', { name: 'Continue' })).toBeEnabled(),
		);
		await user.click(within(back).getByRole('button', { name: 'Continue' }));

		// Progress survives the round trip: Stripe stays added, Linear is gone, Acme
		// joins the end, and the pane (not the header) has focus again.
		await waitFor(() =>
			expect(queueRows()).toEqual(['Stripe:added', 'Notion:active', 'Acme:waiting']),
		);
		expect(screen.getByText('1 of 3 done')).toBeInTheDocument();
		await waitFor(() => expect(screen.getByTestId('queue-active-pane')).toHaveFocus());
	});

	it('closing the tray mid-edit closes the flow and the batch waits, unedited', async () => {
		const user = userEvent.setup();
		renderPage('/?agent=agnt_disabled_1');
		await user.click(await screen.findByRole('button', { name: 'Add APIs' }));
		for (const name of ['Notion', 'Linear']) await user.click(await trayRow(name));
		const tray = screen.getByRole('dialog', { name: 'Add APIs' });
		await waitFor(() =>
			expect(within(tray).getByRole('button', { name: 'Continue' })).toBeEnabled(),
		);
		await user.click(within(tray).getByRole('button', { name: 'Continue' }));
		await user.click(await screen.findByRole('button', { name: 'Back to APIs' }));

		await user.click(await trayRow('Linear'));
		const back = screen.getByRole('dialog', { name: 'Add APIs' });
		await user.click(within(back).getByRole('button', { name: 'Cancel' }));

		// The untick was never committed, so both still wait for next time.
		expect(
			await screen.findByRole('button', { name: 'Finish adding 2 APIs' }),
		).toBeInTheDocument();
	});

	it('after closing midway and reloading, only the API actually bound reads Added via', async () => {
		const user = userEvent.setup();
		const first = renderPage('/?agent=agnt_disabled_1');
		await user.click(await screen.findByRole('button', { name: 'Add APIs' }));
		for (const name of ['Stripe', 'Notion']) await user.click(await trayRow(name));
		const tray = screen.getByRole('dialog', { name: 'Add APIs' });
		await waitFor(() =>
			expect(within(tray).getByRole('button', { name: 'Continue' })).toBeEnabled(),
		);
		await user.click(within(tray).getByRole('button', { name: 'Continue' }));
		await user.click(await screen.findByRole('button', { name: 'Use this credential' }));
		await waitFor(() => expect(queueRows()[0]).toBe('Stripe:added'));
		await user.click(screen.getByRole('button', { name: 'Close for now' }));
		expect(
			await screen.findByRole('button', { name: 'Finish adding 1 API' }),
		).toBeInTheDocument();

		// A reload drops the unfinished batch; nothing about it is remembered.
		first.unmount();
		renderPage('/?agent=agnt_disabled_1');
		await user.click(await screen.findByRole('button', { name: 'Add APIs' }));
		const stripe = await trayRow('Stripe');
		expect(
			within(stripe).getByText('Added via Stripe key · add another credential'),
		).toBeInTheDocument();
		// Still pickable: another Stripe account can be added.
		expect(stripe).toBeEnabled();
		// Queued but never bound: a plain row with no hint.
		const notion = await trayRow('Notion');
		expect(notion).toBeEnabled();
		expect(within(notion).queryByText(/Added/)).not.toBeInTheDocument();
	});

	describe('a second account for an API the agent already reaches', () => {
		const sandbox = () =>
			makeMockCredential({
				credential_id: 'cred_stripe_2',
				name: 'Stripe sandbox',
				type: CredentialType.BEARER_TOKEN,
				api: { vendor: 'stripe.com', name: 'default', version: '1.0.0' },
			});
		const boundStripe = () =>
			seedCredentialBindings([
				{
					agent_id: 'agnt_disabled_1',
					credential_id: 'cred_stripe_1',
					name: 'Stripe key',
					serves: [{ api_vendor: 'stripe.com', api_name: 'default', api_version: null }],
				},
			]);

		async function queueStripe(user: ReturnType<typeof userEvent.setup>): Promise<void> {
			await user.click(await screen.findByRole('button', { name: 'Add APIs' }));
			await user.click(await trayRow('Stripe'));
			const tray = screen.getByRole('dialog', { name: 'Add APIs' });
			await waitFor(() =>
				expect(within(tray).getByRole('button', { name: 'Continue' })).toBeEnabled(),
			);
			await user.click(within(tray).getByRole('button', { name: 'Continue' }));
		}

		it('offers only the credential not bound yet, binds it, and explains the choice per call', async () => {
			resetCredentialsStore([
				makeMockCredential({
					credential_id: 'cred_stripe_1',
					name: 'Stripe key',
					type: CredentialType.BEARER_TOKEN,
					api: { vendor: 'stripe.com', name: 'default', version: '1.0.0' },
				}),
				sandbox(),
			]);
			boundStripe();
			const user = userEvent.setup();
			renderPage('/?agent=agnt_disabled_1');
			expect(await screen.findAllByTestId('api-tile')).toHaveLength(1);
			expect(screen.queryByTestId('tile-accounts-badge')).toBeNull();

			await queueStripe(user);
			const pane = await screen.findByTestId('queue-active-pane');
			expect(within(pane).getByTestId('queue-existing-accounts')).toHaveTextContent(
				'Added via Stripe key',
			);
			// The bound credential is not a choice: binding it again is a 409.
			expect(within(pane).queryByRole('radio', { name: /Stripe key/ })).toBeNull();
			expect(within(pane).getByRole('radio', { name: /Stripe sandbox/ })).toBeChecked();

			await user.click(within(pane).getByRole('button', { name: 'Use this credential' }));
			await waitFor(() => expect(queueRows()).toEqual(['Stripe:added']));
			await user.click(screen.getByRole('button', { name: 'Done' }));
			await waitFor(
				() =>
					expect(document.querySelector('[aria-modal="true"]:not([hidden])')).toBeNull(),
				{ timeout: 2000 },
			);

			await waitFor(() => expect(screen.getAllByTestId('api-tile')).toHaveLength(2));
			const labels = screen
				.getAllByTestId('tile-credential-label')
				.map((el) => el.textContent);
			expect(labels.sort()).toEqual(['Stripe key', 'Stripe sandbox']);
			// Each footer line labels its credential.
			const srNames = screen
				.getAllByTestId('tile-credential')
				.map((el) => el.textContent)
				.sort();
			expect(srNames).toEqual(['Credential: Stripe key', 'Credential: Stripe sandbox']);
			// No page-level banner: each tile carries a compact badge instead.
			expect(screen.queryByTestId('multi-account-note')).toBeNull();
			const badges = screen.getAllByTestId('tile-accounts-badge');
			expect(badges).toHaveLength(2);
			expect(badges[0]).toHaveTextContent('2 credentials');
			expect(badges[0]).not.toHaveTextContent(/account/i);
			// One API however many credentials: the heading and the stat line count
			// it once, and the strip still counts its two credentials.
			expect(screen.getByRole('heading', { name: 'APIs 1' })).toBeInTheDocument();
			expect(screen.getByTestId('stat-configured')).toHaveTextContent('1 configured');

			// Keyboard: the badge's trigger is focusable and described by the tooltip.
			const trigger = badges[0].parentElement as HTMLElement;
			trigger.focus();
			const tip = await screen.findByRole('tooltip');
			expect(tip).toHaveTextContent(
				"This agent has 2 credentials for Stripe. Unless one is scoped more narrowly, each call must name one with the Jentic-Credential-Id header; without it, the call is refused and lists the credentials to choose from. Open a tile to copy its credential's ID.",
			);
			expect(trigger).toHaveAttribute('aria-describedby', tip.id);
			await checkA11y(document.body);

			// The binding's sidebar explains the choice at more length in one quiet line,
			// beside the full id and the header that names this credential — both copyable.
			await user.click(screen.getAllByRole('button', { name: 'Manage Stripe access' })[0]);
			expect(await screen.findByTestId('multi-account-note')).toHaveTextContent(
				"This agent has 2 credentials for Stripe. A narrower one (for example, pinned to a version) is used automatically; otherwise each call must name one with the Jentic-Credential-Id header — copy this credential's ID or header below — or it is refused and lists the options. These rules apply only when this credential is the one chosen.",
			);
			const sidebar = screen.getByRole('region', { name: 'Credential' });
			const idRow = within(sidebar).getByTestId('credential-id-row');
			const credentialId = idRow.querySelector('code')?.textContent ?? '';
			expect(credentialId).toMatch(/^cred_stripe_[12]$/);
			await user.click(within(idRow).getByRole('button', { name: 'Copy the credential ID' }));
			expect(await navigator.clipboard.readText()).toBe(credentialId);
			expect(
				within(sidebar).getByText(`Jentic-Credential-Id: ${credentialId}`),
			).toBeVisible();
			await user.click(
				within(sidebar).getByRole('button', {
					name: 'Copy the Jentic-Credential-Id header',
				}),
			);
			expect(await navigator.clipboard.readText()).toBe(
				`Jentic-Credential-Id: ${credentialId}`,
			);
		});

		it('the mock binds a second credential for one API but 409s the same one twice', async () => {
			boundStripe();
			const bind = (credentialId: string) =>
				fetch('/agents/agnt_disabled_1/credentials', {
					method: 'POST',
					headers: { 'Content-Type': 'application/json' },
					body: JSON.stringify({ credential_id: credentialId }),
				});
			expect((await bind('cred_stripe_2')).status).toBe(201);
			expect((await bind('cred_stripe_1')).status).toBe(409);
		});

		it('with every covering credential bound, asks for a new one', async () => {
			boundStripe();
			const user = userEvent.setup();
			renderPage('/?agent=agnt_disabled_1');
			await queueStripe(user);
			const pane = await screen.findByTestId('queue-active-pane');
			expect(within(pane).queryByRole('radio')).toBeNull();
			expect(within(pane).getByRole('button', { name: 'Add credential' })).toBeEnabled();
		});

		it('is still owed until a NEW binding serves the API', async () => {
			resetCredentialsStore([
				makeMockCredential({
					credential_id: 'cred_stripe_1',
					name: 'Stripe key',
					type: CredentialType.BEARER_TOKEN,
					api: { vendor: 'stripe.com', name: 'default', version: '1.0.0' },
				}),
				sandbox(),
			]);
			boundStripe();
			const user = userEvent.setup();
			const { queryClient } = renderPage('/?agent=agnt_disabled_1');
			await queueStripe(user);
			await user.click(await screen.findByRole('button', { name: 'Close for now' }));
			// The account the agent already had does not settle the item.
			expect(
				await screen.findByRole('button', { name: 'Finish adding 1 API' }),
			).toBeInTheDocument();

			seedCredentialBindings([
				{
					agent_id: 'agnt_disabled_1',
					credential_id: 'cred_stripe_2',
					name: 'Stripe sandbox',
					serves: [{ api_vendor: 'stripe.com', api_name: 'default', api_version: null }],
				},
			]);
			await queryClient.invalidateQueries();
			expect(await screen.findByRole('button', { name: 'Add APIs' })).toBeInTheDocument();
		});
	});

	it('drops an owed API from "Finish adding" once the agent reaches it', async () => {
		const user = userEvent.setup();
		const { queryClient } = renderPage('/?agent=agnt_disabled_1');
		await user.click(await screen.findByRole('button', { name: 'Add APIs' }));
		for (const name of ['Stripe', 'Notion']) await user.click(await trayRow(name));
		const tray = screen.getByRole('dialog', { name: 'Add APIs' });
		await waitFor(() =>
			expect(within(tray).getByRole('button', { name: 'Continue' })).toBeEnabled(),
		);
		await user.click(within(tray).getByRole('button', { name: 'Continue' }));
		await user.click(await screen.findByRole('button', { name: 'Close for now' }));
		expect(
			await screen.findByRole('button', { name: 'Finish adding 2 APIs' }),
		).toBeInTheDocument();

		// Stripe gets bound some other way while the batch waits.
		seedCredentialBindings([
			{
				agent_id: 'agnt_disabled_1',
				credential_id: 'cred_stripe_1',
				name: 'Stripe key',
				serves: [{ api_vendor: 'stripe.com', api_name: 'default', api_version: null }],
			},
		]);
		await queryClient.invalidateQueries();

		expect(
			await screen.findByRole('button', { name: 'Finish adding 1 API' }),
		).toBeInTheDocument();
		// Re-entry lands on the queue with only what is still owed.
		await user.click(screen.getByRole('button', { name: 'Finish adding 1 API' }));
		expect(await screen.findByText('Set up 1 API')).toBeInTheDocument();
		expect(queueRows()).toEqual(['Notion:active']);
	});
});

describe('AgentsPage — the header follows the zero-agents landing', () => {
	beforeEach(async () => {
		await page.viewport(1280, 900);
		setToken('test-token');
		window.localStorage.clear();
		resetAgentsStore();
		clearAgentsStore();
	});

	const newAgent = () => screen.getByRole('button', { name: 'New agent' });

	it('is secondary while an arrival shows, and primary once the fleet takes over', async () => {
		const user = userEvent.setup();
		selfRegisterAgent('my-first-agent');
		renderPage();

		await screen.findByTestId('arrival-card');
		expect(newAgent()).toHaveAttribute('data-emphasis', 'secondary');
		expect(screen.queryByRole('button', { name: 'Create your first agent' })).toBeNull();

		const approve = screen.getByRole('button', { name: 'Approve my-first-agent' });
		await waitFor(() => expect(approve).toBeEnabled());
		await user.click(approve);
		await user.click(await screen.findByRole('button', { name: 'Skip for now' }));
		await screen.findByTestId('agent-dock');
		expect(newAgent()).toHaveAttribute('data-emphasis', 'primary');
	});

	it('is primary for an org whose only agents were denied or archived: it shows the fleet', async () => {
		seedExtraAgents([{ id: 'agnt_old', name: 'stray', status: 'rejected' }]);
		renderPage();

		await screen.findByTestId('agent-strip');
		expect(screen.queryByTestId('agents-empty-landing')).toBeNull();
		expect(newAgent()).toHaveAttribute('data-emphasis', 'primary');
	});

	it('is never secondary over the fleet, even while it loads', async () => {
		resetAgentsStore();
		renderPage();
		expect(newAgent()).toHaveAttribute('data-emphasis', 'primary');
		await screen.findByTestId('agent-dock');
		expect(newAgent()).toHaveAttribute('data-emphasis', 'primary');
	});

	it('390px: the landing header keeps New agent and Credentials on screen', async () => {
		await page.viewport(390, 844);
		renderPage();
		await screen.findByTestId('agents-empty-landing');

		for (const name of ['New agent', 'Credentials']) {
			const rect = screen.getByRole('button', { name }).getBoundingClientRect();
			expect(rect.left).toBeGreaterThanOrEqual(0);
			expect(rect.right).toBeLessThanOrEqual(390);
		}
	});
});

let inboxRead = false;

/** Reads the open connect requests the way the attention inbox does. */
function InboxReader() {
	const requests = useOpenConnectRequests();
	if (requests.data) inboxRead = true;
	return null;
}

describe('AgentsPage — agents waiting on a connect request', () => {
	beforeEach(async () => {
		await page.viewport(1280, 900);
		setToken('test-token');
		window.localStorage.clear();
		resetAgentsStore();
		seedComposedStores();
		resetOrphanPurgeAttemptsForTest();
		resetConnectSessionsStore();
	});

	afterEach(() => {
		vi.restoreAllMocks();
		resetConnectSessionsStore();
	});

	function waitingSection() {
		return screen.findByRole('region', { name: 'Waiting for you' });
	}

	it('shows nothing while no agent is waiting', async () => {
		renderPage();
		await screen.findAllByText('inbox-triage-bot');
		expect(screen.queryByRole('region', { name: 'Waiting for you' })).not.toBeInTheDocument();
	});

	it("collapses an agent's open requests into one row, with a Review link per request", async () => {
		const github = seedMockAgentConnectSession({
			agent_id: 'agnt_active_1',
			vendor_key: 'github',
			created_at: new Date(Date.now() - 20 * 60_000).toISOString(),
		});
		const slack = seedMockAgentConnectSession({
			agent_id: 'agnt_active_1',
			vendor_key: 'slack',
			state: 'polling',
			created_at: new Date(Date.now() - 5 * 60_000).toISOString(),
		});
		renderPage();

		const section = await waitingSection();
		const rows = within(section).getAllByRole('listitem');
		expect(rows).toHaveLength(1);
		expect(rows[0]).toHaveTextContent('support-agent');
		expect(rows[0]).toHaveTextContent('wants to connect github and slack');
		expect(rows[0]).toHaveTextContent(/waiting 20m/);
		const links = within(rows[0]).getAllByRole('link');
		expect(links.map((l) => l.textContent)).toEqual([
			'Review github for support-agent',
			'Review slack for support-agent',
		]);
		expect(links[0].getAttribute('href')).toContain(`approve=${github.session_id}`);
		expect(links[1].getAttribute('href')).toContain(`approve=${slack.session_id}`);
		for (const link of links) expect(link.getAttribute('href')).not.toContain('poll_token');
		await checkA11y(section);
	});

	it('lists requests only to a viewer who may approve them', async () => {
		seedMockAgentConnectSession({ agent_id: 'agnt_active_1', vendor_key: 'github' });
		// An owner holding credentials:write but not agents:write can list the
		// request but not open it, so it is not waiting for them.
		// The inbox reads the same query for any credentials reader, so the
		// cache holds the request even though this section never fetches it.
		seedViewer(['credentials:read', 'credentials:write', 'agents:read']);
		const { unmount } = renderWithProviders(
			<AuthProvider>
				<AgentsPage />
				<InboxReader />
			</AuthProvider>,
		);
		await screen.findAllByText('inbox-triage-bot');
		await waitFor(() => expect(inboxRead).toBe(true));
		expect(screen.queryByRole('region', { name: 'Waiting for you' })).not.toBeInTheDocument();
		unmount();

		seedViewer(['credentials:read', 'credentials:write', 'agents:read', 'agents:write']);
		renderPage('/', { withAuth: true });
		expect(await waitingSection()).toHaveTextContent('wants to connect github');
	});

	it('opens the request token-less from its Review link and strips the param', async () => {
		const seeded = seedMockAgentConnectSession({
			agent_id: 'agnt_active_1',
			vendor_key: 'github',
		});
		const user = userEvent.setup();
		// Another agent is selected; the link selects the requesting one.
		renderPage('/?agent=agnt_pending_2');

		const section = await waitingSection();
		await user.click(within(section).getByRole('link', { name: /Review support-agent/ }));

		const dialog = await screen.findByRole('dialog', { name: /^Approve integration$/ });
		// The review read succeeded with no poll token (the mock admits only
		// the owner path without one).
		expect(
			await within(dialog).findByText(/an agent is asking to connect/i),
		).toBeInTheDocument();
		await waitFor(() => {
			const params = new URLSearchParams(
				screen.getByTestId('location-search').textContent ?? '',
			);
			expect(params.has('approve')).toBe(false);
			expect(params.get('agent')).toBe('agnt_active_1');
		});
		expect(seeded.poll_token).toBeTruthy();
	});
});
