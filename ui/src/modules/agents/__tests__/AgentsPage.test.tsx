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
import { Toaster } from '@/shared/ui';
import { resetAgentsStore, seedCredentialBindings } from '@/modules/agents/mocks/handlers';
import {
	makeMockCredential,
	resetApisStore,
	resetCredentialsStore,
} from '@/shared/credentials/mocks/handlers';
import {
	CredentialType,
	OAUTH_CONNECT_MESSAGE_TYPE,
	type ApiResponse,
	type Credential,
} from '@/shared/credentials/api';
import AgentsPage from '@/modules/agents/pages/AgentsPage';

/** Surfaces the router's current search string so specs can assert `?agent=`. */
function LocationProbe() {
	const location = useLocation();
	return <div data-testid="location-search">{location.search}</div>;
}

function renderPage(route = '/') {
	return renderWithProviders(
		<>
			<AgentsPage />
			<LocationProbe />
			<Toaster />
		</>,
		{ route },
	);
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
		resetAgentsStore();
		seedComposedStores();
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
		expect(await screen.findByText('No rules — all calls blocked')).toBeInTheDocument();
		expect(screen.getByText('Suspended · not serving')).toBeInTheDocument();
	});

	it('says each identity once — a credential named after its API drops off the tile', async () => {
		// The mark, the title and the meta line would otherwise print the same word
		// three times. A credential that names something else keeps its place.
		resetCredentialsStore([
			makeMockCredential({
				credential_id: 'cred_slack_1',
				name: 'Slack',
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
		renderPage('/?agent=agnt_active_1');

		const slackTile = (await screen.findByText('Slack')).closest(
			'[data-testid="api-tile"]',
		) as HTMLElement;
		expect(within(slackTile).getAllByText('Slack')).toHaveLength(1);
		const githubTile = screen
			.getByText('GitHub')
			.closest('[data-testid="api-tile"]') as HTMLElement;
		expect(within(githubTile).getByText('GitHub PAT')).toBeInTheDocument();

		// Every tile still stands the same height: what the suspension means
		// rides beside its chip rather than on a row the others reserve empty.
		expect(within(githubTile).getByText('Suspended · not serving')).toBeInTheDocument();
		const heights = screen
			.getAllByTestId('api-tile')
			.map((tile) => Math.round(tile.getBoundingClientRect().height));
		expect(new Set(heights).size).toBe(1);
	});

	it('drops a credential named after the API’s host, not just after its title', async () => {
		// A generically-named spec keeps the title and the host distinct, so matching
		// the title alone would let the host through twice.
		resetCredentialsStore([
			makeMockCredential({
				credential_id: 'cred_slack_1',
				name: 'slack.com',
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
		renderPage('/?agent=agnt_active_1');

		const slackTile = (await screen.findByText('Slack')).closest(
			'[data-testid="api-tile"]',
		) as HTMLElement;
		// The identity line states the host; the detail line does not repeat
		// it under the guise of a credential name.
		expect(slackTile).toHaveTextContent('slack.com · v1.0.0');
		expect(within(slackTile).getByTestId('tile-detail-slot')).not.toHaveTextContent(
			'slack.com',
		);

		const githubTile = screen
			.getByText('GitHub')
			.closest('[data-testid="api-tile"]') as HTMLElement;
		expect(within(githubTile).getByText('GitHub PAT')).toBeInTheDocument();
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

	// --- Stat strip: merged console vitals ----------------------------------

	it('merges the console vitals with the access stats in one quiet meta line', async () => {
		renderPage('/?agent=agnt_active_1');
		await screen.findByText('Slack');

		// Access clauses — the same tileStats math the grid draws from: 2 usable tiles,
		// 181 ops (the suspended binding's 912 excluded), 2 bound credentials.
		await waitFor(() => expect(stripFigure('configured')).toHaveTextContent('2 configured'));
		expect(stripFigure('operations')).toHaveTextContent('181 operations');
		expect(stripFigure('credentials')).toHaveTextContent('2 credentials');

		// Monitor clauses — the console header's sources (7-day usage rollup
		// + newest execution), rendered from the mocked rollups.
		await waitFor(() => expect(stripFigure('executions')).toHaveTextContent('1,204'));
		expect(stripFigure('executions')).toHaveTextContent('7d');
		expect(stripFigure('success-rate')).toHaveTextContent('99%');
		expect(stripFigure('last-activity')).toHaveTextContent('2m');

		// The line is the only stats surface, so each clause renders once.
		expect(screen.getAllByTestId('stat-configured')).toHaveLength(1);
		expect(screen.getAllByTestId('stat-operations')).toHaveLength(1);
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
		// …and no jump-off to the console: the dock's sheets carry everything
		// that page holds.
		expect(within(panel).queryByRole('link', { name: /Open console/ })).not.toBeInTheDocument();
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
					],
					has_more: false,
					next_cursor: null,
				}),
			),
			http.get('/agents/:id/credentials', () => HttpResponse.json({ data: [] })),
		);
		renderPage();
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

	it('shows the DCR quickstart when no agents are registered', async () => {
		worker.use(
			http.get('/agents', () =>
				HttpResponse.json({ data: [], has_more: false, next_cursor: null }),
			),
		);
		renderPage();

		expect(await screen.findByText('No agents yet')).toBeInTheDocument();
		// Both routes in: self-registration below, manual creation here — manual is
		// primary, because it ends with a working agent rather than a pending one.
		expect(screen.getByRole('button', { name: /Create an agent/ })).toBeInTheDocument();
		expect(screen.getByText('Register an agent from the command line')).toBeInTheDocument();
		// Pin the real CLI flag: `jentic register` takes --url, not --base-url (#1204).
		expect(screen.getByText(/jentic register --url /)).toBeInTheDocument();
		expect(screen.queryByText(/--base-url/)).not.toBeInTheDocument();
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

	it('has no critical a11y violations', async () => {
		const { container } = renderPage('/?agent=agnt_active_1');
		await screen.findByText('Slack');
		await screen.findByText('1 access rule');
		// Let the panel's entrance fade settle — axe measures contrast on the
		// rendered opacity, so a mid-animation snapshot reports false positives.
		await new Promise((resolve) => setTimeout(resolve, 400));
		await checkA11y(container);
	});

	// --- Create sheet (New agent lives at the strip's end) -------------------

	it('creates an agent with initial scopes included in the POST body', async () => {
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

		await user.click(screen.getByRole('button', { name: 'New agent' }));
		const sheet = await screen.findByRole('dialog', { name: 'Create agent' });
		await user.type(within(sheet).getByLabelText('Name'), 'scoped-agent');

		// The scopes section is an optional, collapsed disclosure.
		await user.click(within(sheet).getByRole('button', { name: /Initial scopes/ }));
		await user.click(await within(sheet).findByRole('button', { name: /Capabilities scopes/ }));
		await user.click(within(sheet).getByRole('checkbox', { name: 'capabilities:execute' }));

		await user.click(within(sheet).getByRole('button', { name: 'Create empty' }));
		expect(await screen.findByText('Agent created')).toBeInTheDocument();
		expect(postBody).toMatchObject({
			name: 'scoped-agent',
			scopes: ['capabilities:execute'],
		});
	});

	it('omits scopes from the POST body when none are selected', async () => {
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

		await user.click(screen.getByRole('button', { name: 'New agent' }));
		const sheet = await screen.findByRole('dialog', { name: 'Create agent' });
		await user.type(within(sheet).getByLabelText('Name'), 'plain-agent');
		await user.click(within(sheet).getByRole('button', { name: 'Create empty' }));

		expect(await screen.findByText('Agent created')).toBeInTheDocument();
		// The client normalises an empty selection to `scopes: null`.
		expect(postBody).toMatchObject({ name: 'plain-agent', scopes: null });
	});

	it('creating an agent flows straight into picking its APIs', async () => {
		const user = userEvent.setup();
		renderPage();
		await screen.findAllByText('inbox-triage-bot');

		await user.click(screen.getByRole('button', { name: 'New agent' }));
		const sheet = await screen.findByRole('dialog', { name: 'Create agent' });
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

		await user.click(screen.getByRole('button', { name: 'New agent' }));
		const sheet = await screen.findByRole('dialog', { name: 'Create agent' });
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
