/**
 * The Agents surface offers only the verbs the caller's permissions allow, and
 * sends no request the server would refuse them:
 *
 * - Approve / Deny (banner, the selected agent's state banner, the dock), the
 *   dock's serving toggle and Archive, the dock sheets' writes (API key
 *   generate/revoke, Settings rename/archive), Add APIs and "Create here" need
 *   `agents:write` or `org:admin`.
 * - The usage aggregate (`/monitoring/usage`: stat strip, credential inventory)
 *   needs `org:admin`.
 * - "Recent changes" (`/audit`) needs `audit:read` or `org:admin`.
 * - A binding's pause, resume and unbind (tile and sidebar) and the agent's
 *   permission edit need `agents:write` or `org:admin`; the sidebar's credential
 *   edit, delete, connect and rule edit need `credentials:write` or `org:admin`.
 * - The first-agent landing offers manual creation, and says "you approve it",
 *   only with `agents:write` or `org:admin`.
 * - The credential inventory says it lists the whole workspace only to an
 *   `org:admin`.
 */
import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import { http, HttpResponse } from 'msw';
import { page } from 'vitest/browser';
import { renderWithProviders, screen, waitFor, within, userEvent } from '@/__tests__/test-utils';
import { worker } from '@/mocks/browser';
import { clearToken, setToken } from '@/shared/api';
import { AuthProvider, useOptionalCurrentUser } from '@/shared/auth';
import { Toaster } from '@/shared/ui';
import { clearAgentsStore, resetAgentsStore } from '@/modules/agents/mocks/handlers';
import {
	makeMockCredential,
	resetApisStore,
	resetCredentialsStore,
} from '@/shared/credentials/mocks/handlers';
import { CredentialType, type ApiResponse } from '@/shared/credentials/api';
import AgentsPage from '@/modules/agents/pages/AgentsPage';
import { ActorAuditPanel } from '@/modules/agents/components/detail/ActorAuditPanel';

/** A member's effective permissions with the usual defaults. */
const MEMBER_DEFAULTS = [
	'agents:read',
	'agents:write',
	'apis:read',
	'audit:read',
	'credentials:read',
	'credentials:write',
	'events:read',
	'executions:read',
	'jobs:read',
];
const without = (...drop: string[]) => MEMBER_DEFAULTS.filter((p) => !drop.includes(p));

const VIEWERS: Record<string, readonly string[]> = {
	admin: ['org:admin'],
	'member with defaults': MEMBER_DEFAULTS,
	'member without agents:write': without('agents:write'),
	'member without events:read': without('events:read'),
	'member without jobs:read': without('jobs:read'),
};

function seedViewer(permissions: readonly string[]) {
	worker.use(
		http.get('/users/me', () =>
			HttpResponse.json({
				id: 'usr_viewer_1',
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

/** The binding-surface matrix: the page's viewers plus the two narrower members. */
const BINDING_VIEWERS: Record<string, readonly string[]> = {
	...VIEWERS,
	'member without credentials:write': without('credentials:write'),
	// Reads only — no write anywhere. `apis:read` is a READ and belongs here:
	// `GET /apis` requires it, and the page sends that request only to a viewer
	// who holds it (#1543), so a fixture that omits it models a viewer who cannot
	// resolve an API's display name at all — a different spec from this one.
	'member with reads only': [
		'agents:read',
		'apis:read',
		'credentials:read',
		'owner:credentials:read',
	],
};

const canManageAgents = (permissions: readonly string[]) =>
	permissions.includes('agents:write') || permissions.includes('org:admin');
const canWriteCredentials = (permissions: readonly string[]) =>
	permissions.includes('credentials:write') || permissions.includes('org:admin');

/** Workspace API row for the registry mock. */
function apiRow(vendor: string, displayName: string): ApiResponse {
	return {
		_links: { self: `/apis/${vendor}`, openapi: `/apis/${vendor}/openapi` },
		api: { vendor, name: 'default', version: '1.0.0' },
		catalog_api_id: null,
		created_at: '2026-01-01T00:00:00Z',
		current_revision_id: null,
		description: null,
		display_name: displayName,
		icon_url: null,
		operation_count: 10,
		revision_count: 1,
		security_schemes: [],
		updated_at: '2026-01-01T00:00:00Z',
	} as unknown as ApiResponse;
}

/** The credentials and APIs behind `agnt_active_1`'s seeded bindings: Slack is
 * live, GitHub is suspended. Both credentials are the viewer's own. */
function seedTiles() {
	resetCredentialsStore([
		makeMockCredential({
			credential_id: 'cred_slack_1',
			name: 'Slack bot token',
			type: CredentialType.BEARER_TOKEN,
			api: { vendor: 'slack.com', name: 'default', version: '1.0.0' },
			created_by: 'usr_viewer_1',
		}),
		makeMockCredential({
			credential_id: 'cred_github_1',
			name: 'GitHub PAT',
			type: CredentialType.BEARER_TOKEN,
			api: { vendor: 'github.com', name: 'default', version: '1.0.0' },
			created_by: 'usr_viewer_1',
		}),
	]);
	resetApisStore([
		{ row: apiRow('slack.com', 'Slack'), spec: {} },
		{ row: apiRow('github', 'GitHub'), spec: {} },
	]);
}

/** Every write the page sends to an agent's bindings (`METHOD path`). */
function trackBindingWrites(): string[] {
	const writes: string[] = [];
	worker.events.on('request:start', ({ request }) => {
		const path = new URL(request.url).pathname;
		if (request.method !== 'GET' && /^\/agents\/[^/]+\/credentials/.test(path)) {
			writes.push(`${request.method} ${path}`);
		}
	});
	return writes;
}

/** Every request path the page sends, in order. */
function trackRequests(): string[] {
	const paths: string[] = [];
	worker.events.on('request:start', ({ request }) => {
		paths.push(new URL(request.url).pathname);
	});
	return paths;
}

/** Renders once `/users/me` has answered, so a spec never reads a gate that is
 * still closed only because the viewer is loading. */
function ViewerReady() {
	return useOptionalCurrentUser() ? <span data-testid="viewer-ready" hidden /> : null;
}

/** Render the page and wait for the viewer's permissions to be known. */
async function renderReady(route: string) {
	const result = renderPage(route);
	await screen.findByTestId('viewer-ready');
	return result;
}

function renderPage(route: string) {
	return renderWithProviders(
		<AuthProvider>
			<ViewerReady />
			<AgentsPage />
			<Toaster />
		</AuthProvider>,
		{ route },
	);
}

beforeEach(async () => {
	await page.viewport(1280, 900);
	setToken('test-token');
	window.localStorage.clear();
	resetAgentsStore();
	resetCredentialsStore([]);
	resetApisStore([]);
});

afterEach(() => {
	worker.events.removeAllListeners();
	clearToken();
});

describe('approval verbs follow agents:write', () => {
	it.each(Object.entries(VIEWERS))('%s', async (_label, permissions) => {
		const canDecide = permissions.includes('agents:write') || permissions.includes('org:admin');
		seedViewer(permissions);
		renderPage('/?agent=agnt_pending_1');

		const banner = await screen.findByRole('region', { name: /Awaiting approval/i });
		// Review is open to anyone who can see the agent.
		expect(within(banner).getByRole('button', { name: /^Review / })).toBeInTheDocument();
		const state = await screen.findByTestId('agent-state-banner-pending');
		const dock = await screen.findByTestId('agent-dock');

		if (canDecide) {
			expect(within(banner).getByRole('button', { name: /^Approve / })).toBeInTheDocument();
			expect(within(banner).getByRole('button', { name: /^Deny / })).toBeInTheDocument();
			expect(within(state).getByTestId('state-banner-approve')).toBeInTheDocument();
			expect(within(state).getByTestId('state-banner-deny')).toBeInTheDocument();
			expect(within(dock).getByTestId('dock-approve')).toBeInTheDocument();
		} else {
			expect(within(banner).queryByRole('button', { name: /^Approve / })).toBeNull();
			expect(within(banner).queryByRole('button', { name: /^Deny / })).toBeNull();
			expect(within(state).queryByTestId('state-banner-approve')).toBeNull();
			expect(within(state).queryByTestId('state-banner-deny')).toBeNull();
			expect(state).toHaveTextContent('Someone who can manage agents needs to approve it.');
			expect(within(dock).queryByTestId('dock-approve')).toBeNull();
			expect(within(dock).getByTestId('dock-state-note')).toHaveTextContent(
				'Waiting for approval',
			);
		}
	});
});

describe("the dock's serving toggle and Archive follow agents:write", () => {
	it.each(Object.entries(VIEWERS))('%s', async (_label, permissions) => {
		const canManage = permissions.includes('agents:write') || permissions.includes('org:admin');
		seedViewer(permissions);
		await renderReady('/?agent=agnt_active_1');
		const dock = await screen.findByTestId('agent-dock');
		// The read affordances stay for everyone.
		expect(within(dock).getByRole('button', { name: 'Activity' })).toBeInTheDocument();

		if (canManage) {
			expect(await within(dock).findByTestId('dock-serving-toggle')).toBeInTheDocument();
			expect(
				within(dock).getByRole('button', { name: 'Archive support-agent' }),
			).toBeInTheDocument();
		} else {
			expect(await within(dock).findByTestId('dock-state-note')).toHaveTextContent(
				'Serving traffic',
			);
			expect(within(dock).queryByTestId('dock-serving-toggle')).toBeNull();
			expect(
				within(dock).queryByRole('button', { name: 'Archive support-agent' }),
			).toBeNull();
		}
	});

	it('a disabled agent reads as a note without agents:write', async () => {
		seedViewer(without('agents:write'));
		renderPage('/?agent=agnt_disabled_1');
		const dock = await screen.findByTestId('agent-dock');
		expect(await within(dock).findByTestId('dock-state-note')).toHaveTextContent(
			'Disabled — not serving traffic',
		);
		expect(within(dock).queryByTestId('dock-serving-toggle')).toBeNull();
	});
});

describe("the dock sheets' writes and Add APIs follow agents:write", () => {
	async function openDockSheet(name: string) {
		const user = userEvent.setup();
		const dock = await screen.findByTestId('agent-dock');
		await user.click(within(dock).getByRole('button', { name }));
		return within(await screen.findByTestId('sheet-primitive'));
	}

	it.each(Object.entries(VIEWERS))('API key sheet — %s', async (_label, permissions) => {
		const canManage = permissions.includes('agents:write') || permissions.includes('org:admin');
		seedViewer(permissions);
		await renderReady('/?agent=agnt_active_1');
		const sheet = await openDockSheet('API key');

		const generate = /^(Generate|Regenerate) API key for support-agent$/;
		if (canManage) {
			expect(await sheet.findByRole('button', { name: generate })).toBeInTheDocument();
			expect(sheet.queryByTestId('keys-need-permission')).toBeNull();
		} else {
			expect(await sheet.findByTestId('keys-need-permission')).toBeInTheDocument();
			expect(sheet.queryByRole('button', { name: generate })).toBeNull();
			expect(
				sheet.queryByRole('button', { name: 'Revoke API key for support-agent' }),
			).toBeNull();
		}
	});

	it.each(Object.entries(VIEWERS))('Settings sheet — %s', async (_label, permissions) => {
		const canManage = permissions.includes('agents:write') || permissions.includes('org:admin');
		seedViewer(permissions);
		await renderReady('/?agent=agnt_active_1');
		const sheet = await openDockSheet('Settings');

		if (canManage) {
			expect(await sheet.findByRole('button', { name: 'Save changes' })).toBeInTheDocument();
			expect(
				sheet.getByRole('button', { name: 'Archive support-agent' }),
			).toBeInTheDocument();
		} else {
			expect(
				await sheet.findByText('Renaming this agent needs permission to manage agents.'),
			).toBeInTheDocument();
			expect(sheet.queryByRole('button', { name: 'Save changes' })).toBeNull();
			expect(sheet.queryByRole('button', { name: 'Archive support-agent' })).toBeNull();
		}
	});

	it.each(Object.entries(VIEWERS))('Add APIs — %s', async (_label, permissions) => {
		const canManage = permissions.includes('agents:write') || permissions.includes('org:admin');
		seedViewer(permissions);
		await renderReady('/?agent=agnt_active_1');

		const add = await screen.findByRole('button', { name: 'Add APIs' });
		if (canManage) {
			expect(add).toBeEnabled();
		} else {
			expect(add).toBeDisabled();
			// Beside the tree's button, and as the card button's Tooltip.
			expect(
				screen.getAllByText('Adding APIs needs permission to manage agents.').length,
			).toBeGreaterThanOrEqual(1);
		}
	});
});

describe('"Create here" follows agents:write', () => {
	it.each(Object.entries(VIEWERS))('%s', async (_label, permissions) => {
		const canCreate = permissions.includes('agents:write') || permissions.includes('org:admin');
		const user = userEvent.setup();
		seedViewer(permissions);
		await renderReady('/?agent=agnt_active_1');
		await screen.findByTestId('agent-dock');

		await user.click(screen.getByRole('button', { name: 'New agent' }));
		const sheet = await screen.findByRole('dialog', { name: 'New agent' });
		const register = within(sheet).getByRole('tab', { name: 'Register from the CLI' });
		if (canCreate) {
			expect(within(sheet).getByRole('tab', { name: 'Create here' })).toHaveAttribute(
				'aria-selected',
				'true',
			);
		} else {
			expect(within(sheet).queryByRole('tab', { name: 'Create here' })).toBeNull();
			expect(register).toHaveAttribute('aria-selected', 'true');
			expect(within(sheet).getByTestId('new-agent-panel-register')).toHaveAttribute(
				'data-state',
				'active',
			);
		}
	});
});

describe('the usage aggregate is read only by org:admin', () => {
	it.each(Object.entries(VIEWERS))('%s', async (_label, permissions) => {
		const isAdmin = permissions.includes('org:admin');
		const paths = trackRequests();
		seedViewer(permissions);
		const user = userEvent.setup();
		renderPage('/?agent=agnt_active_1');
		await screen.findByTestId('agent-stat-strip');

		// The credential inventory reads the per-credential totals.
		await user.click(screen.getByRole('button', { name: 'Credentials' }));
		await screen.findByTestId('sheet-primitive');
		// Long enough for the strip's and the inventory's reads to have gone out.
		await new Promise((r) => setTimeout(r, 500));

		const usageReads = paths.filter((p) => p === '/monitoring/usage').length;
		if (isAdmin) expect(usageReads).toBeGreaterThan(0);
		else expect(usageReads).toBe(0);
	});
});

describe('"Recent changes" reads the audit log only with audit:read', () => {
	it.each([
		['admin', ['org:admin'], true],
		['member with defaults', MEMBER_DEFAULTS, true],
		['member without audit:read', without('audit:read'), false],
	] as const)('%s', async (_label, permissions, canRead) => {
		const paths = trackRequests();
		seedViewer(permissions);
		renderWithProviders(
			<AuthProvider>
				<ActorAuditPanel actorId="agnt_active_1" />
			</AuthProvider>,
		);

		if (canRead) {
			await waitFor(() => expect(paths).toContain('/audit'));
		} else {
			expect(
				await screen.findByText("This agent's change history needs audit access."),
			).toBeInTheDocument();
			await new Promise((r) => setTimeout(r, 300));
			expect(paths).not.toContain('/audit');
		}
	});
});

/** The Slack row's Manage access icon (its label may name the credential). */
const MANAGE_SLACK = /^Manage access for Slack/;

describe("a row's pause and resume follow agents:write", () => {
	it.each(Object.entries(BINDING_VIEWERS))('%s', async (_label, permissions) => {
		seedTiles();
		seedViewer(permissions);
		const writes = trackBindingWrites();
		const user = userEvent.setup();
		await renderReady('/?agent=agnt_active_1');
		await waitFor(() => expect(screen.getAllByTestId('api-tile')).toHaveLength(2));
		// Opening a row's access details is open to anyone who sees the agent.
		expect(screen.getByRole('button', { name: MANAGE_SLACK })).toBeInTheDocument();
		// Pause lives in the row's reveal: pin it open first.
		await user.click(screen.getByRole('button', { name: 'Slack details' }));
		await screen.findAllByTestId('api-row-reveal');

		if (canManageAgents(permissions)) {
			expect(screen.getByRole('button', { name: 'Pause Slack access' })).toBeInTheDocument();
			expect(
				screen.getByRole('button', { name: 'Resume GitHub access' }),
			).toBeInTheDocument();
		} else {
			expect(screen.queryByRole('button', { name: 'Pause Slack access' })).toBeNull();
			expect(screen.queryByRole('button', { name: 'Resume GitHub access' })).toBeNull();
		}
		expect(writes).toEqual([]);
	});

	it('pausing sends the suspend for a viewer with agents:write', async () => {
		seedTiles();
		seedViewer(MEMBER_DEFAULTS);
		const writes = trackBindingWrites();
		const user = userEvent.setup();
		await renderReady('/?agent=agnt_active_1');
		await user.click(await screen.findByRole('button', { name: 'Slack details' }));
		await user.click(await screen.findByRole('button', { name: 'Pause Slack access' }));
		await waitFor(() =>
			expect(writes).toContain('DELETE /agents/agnt_active_1/credentials/cred_slack_1'),
		);
	});

	/**
	 * Pins #1543 item 9. `GET /apis` needs `apis:read`; the registry join is an
	 * ENRICHMENT (it supplies an API's display name and operation count), never
	 * the source of the tiles themselves, which come from the agent's bindings.
	 * A viewer without the permission must still see the grid, not an error card
	 * whose "Try again" can never succeed — a 403 is a standing fact about the
	 * viewer, not a transient failure.
	 */
	it('still draws the tiles for a viewer who may not read /apis, and sends no request', async () => {
		seedTiles();
		const paths = trackRequests();
		// Everything a member holds except `apis:read`.
		seedViewer(without('apis:read'));
		await renderReady('/?agent=agnt_active_1');

		// The grid is intact — the bindings alone are enough to draw it.
		await waitFor(() => expect(screen.getAllByTestId('api-tile')).toHaveLength(2));
		// No dead end: no error card, no unreachable retry.
		expect(screen.queryByRole('button', { name: 'Try again' })).toBeNull();
		expect(
			screen.queryByText(/Couldn't load the credential and API details/),
		).not.toBeInTheDocument();
		// And the forbidden read is never attempted, so nothing 403s.
		await new Promise((r) => setTimeout(r, 300));
		expect(paths).not.toContain('/apis');
	});
});

describe("the access sidebar's verbs follow agents:write and credentials:write", () => {
	it.each(Object.entries(BINDING_VIEWERS))('%s', async (_label, permissions) => {
		seedTiles();
		seedViewer(permissions);
		const writes = trackBindingWrites();
		const user = userEvent.setup();
		await renderReady('/?agent=agnt_active_1');
		await user.click(await screen.findByRole('button', { name: MANAGE_SLACK }));
		const sidebar = within(await screen.findByRole('dialog', { name: 'Slack' }));
		// The rule tester and the credential id are reads, open to everyone.
		expect(await sidebar.findByRole('region', { name: 'Test a request' })).toBeInTheDocument();
		// The rules (an editor or the read-only list) have loaded.
		await sidebar.findByText('Permission rules for Slack bot token');

		const suspend = sidebar.queryByRole('button', {
			name: 'Suspend binding for Slack bot token',
		});
		const unbind = sidebar.queryByRole('button', {
			name: 'Unbind Slack bot token from support-agent',
		});
		const edit = sidebar.queryByRole('button', { name: 'Edit credential' });
		const remove = sidebar.queryByRole('button', {
			name: 'Delete credential Slack bot token org-wide',
		});

		if (canManageAgents(permissions)) {
			expect(suspend).toBeInTheDocument();
			expect(unbind).toBeInTheDocument();
		} else {
			expect(suspend).toBeNull();
			expect(unbind).toBeNull();
		}
		if (canWriteCredentials(permissions)) {
			expect(edit).toBeInTheDocument();
			expect(remove).toBeInTheDocument();
			expect(sidebar.getByRole('button', { name: /Save rules/ })).toBeInTheDocument();
			expect(sidebar.queryByTestId('binding-rules-read-only')).toBeNull();
		} else {
			expect(edit).toBeNull();
			expect(remove).toBeNull();
			expect(sidebar.queryByRole('button', { name: /Save rules/ })).toBeNull();
			const rules = sidebar.getByTestId('binding-rules-read-only');
			expect(rules).toHaveTextContent(
				'Changing these rules needs permission to manage credentials.',
			);
		}
		if (!canManageAgents(permissions) && !canWriteCredentials(permissions)) {
			expect(sidebar.queryByTestId('sidebar-danger-zone')).toBeNull();
		}
		expect(writes).toEqual([]);
	});
});

describe("a shared credential's binding rules are read-only for a non-owner", () => {
	it('a member with credentials:write sees the rules but cannot save them', async () => {
		seedTiles();
		// The Slack credential belongs to someone else and is shared with the viewer.
		resetCredentialsStore([
			makeMockCredential({
				credential_id: 'cred_slack_1',
				name: 'Slack bot token',
				type: CredentialType.BEARER_TOKEN,
				api: { vendor: 'slack.com', name: 'default', version: '1.0.0' },
				created_by: 'usr_someone_else',
			}),
		]);
		seedViewer(MEMBER_DEFAULTS);
		const writes = trackBindingWrites();
		const user = userEvent.setup();
		await renderReady('/?agent=agnt_active_1');
		await user.click(await screen.findByRole('button', { name: MANAGE_SLACK }));
		const sidebar = within(await screen.findByRole('dialog', { name: 'Slack' }));
		await sidebar.findByText('Permission rules for Slack bot token');
		expect(sidebar.queryByRole('button', { name: /Save rules/ })).toBeNull();
		expect(sidebar.getByTestId('binding-rules-read-only')).toHaveTextContent(
			"Only the credential's owner or an org admin can change these rules.",
		);
		expect(writes).toEqual([]);
	});
});

describe('"Finish connecting" follows credentials:write', () => {
	it.each(Object.entries(BINDING_VIEWERS))('%s', async (_label, permissions) => {
		seedTiles();
		resetCredentialsStore([
			makeMockCredential({
				credential_id: 'cred_slack_1',
				name: 'Slack OAuth',
				type: CredentialType.OAUTH2,
				api: { vendor: 'slack.com', name: 'default', version: '1.0.0' },
				created_by: 'usr_viewer_1',
				details: { grant_type: 'authorization_code', connected: false },
			}),
			makeMockCredential({
				credential_id: 'cred_github_1',
				name: 'GitHub PAT',
				type: CredentialType.BEARER_TOKEN,
				api: { vendor: 'github.com', name: 'default', version: '1.0.0' },
				created_by: 'usr_viewer_1',
			}),
		]);
		seedViewer(permissions);
		await renderReady('/?agent=agnt_active_1');
		await waitFor(() => expect(screen.getAllByTestId('api-tile')).toHaveLength(2));
		const tileConnect = screen.queryByRole('button', { name: /Finish connecting/ });
		if (canWriteCredentials(permissions)) {
			expect(tileConnect).toBeInTheDocument();
		} else {
			expect(tileConnect).toBeNull();
		}
	});
});

describe('the Permissions sheet edits permissions only with agents:write', () => {
	it.each(Object.entries(BINDING_VIEWERS))('%s', async (_label, permissions) => {
		seedViewer(permissions);
		const user = userEvent.setup();
		await renderReady('/?agent=agnt_active_1');
		const dock = await screen.findByTestId('agent-dock');
		await user.click(within(dock).getByRole('button', { name: 'Permissions' }));
		const sheet = within(await screen.findByTestId('sheet-primitive'));
		await sheet.findByRole('list', { name: 'Granted permissions' });

		const edit = sheet.queryByRole('button', { name: 'Edit permissions for support-agent' });
		if (canManageAgents(permissions)) expect(edit).toBeInTheDocument();
		else expect(edit).toBeNull();
	});
});

describe('the first-agent landing offers manual creation only with agents:write', () => {
	it.each(Object.entries(BINDING_VIEWERS))('%s', async (_label, permissions) => {
		clearAgentsStore();
		seedViewer(permissions);
		await renderReady('/');
		const landing = within(await screen.findByTestId('agents-empty-landing'));
		const stepper = within(landing.getByTestId('register-stepper'));

		if (canManageAgents(permissions)) {
			expect(
				landing.getByRole('button', { name: 'Create an agent manually' }),
			).toBeInTheDocument();
			expect(stepper.getByText('You approve it')).toBeInTheDocument();
			expect(landing.getByText(/shows up here for you to approve/)).toBeInTheDocument();
		} else {
			expect(landing.queryByRole('button', { name: 'Create an agent manually' })).toBeNull();
			expect(landing.queryByTestId('manual-card')).toBeNull();
			expect(stepper.queryByText('You approve it')).toBeNull();
			expect(stepper.getByText('It gets approved')).toBeInTheDocument();
			expect(stepper.getByText('By someone who can manage agents')).toBeInTheDocument();
			expect(
				landing.getByText(/pending until someone who can manage agents approves it/),
			).toBeInTheDocument();
		}
	});
});

describe("the credential inventory's header matches what the viewer lists", () => {
	it.each([
		['admin', ['org:admin'], true],
		['member with defaults', MEMBER_DEFAULTS, false],
		['member without agents:write', without('agents:write'), false],
	] as const)('%s', async (_label, permissions, isAdmin) => {
		seedViewer(permissions);
		const user = userEvent.setup();
		await renderReady('/?agent=agnt_active_1');
		await screen.findByTestId('agent-dock');
		await user.click(screen.getByRole('button', { name: 'Credentials' }));
		const sheet = within(await screen.findByRole('dialog', { name: 'Credentials' }));

		const workspaceWide =
			'Every credential in this workspace — any agent can be bound to them.';
		const own =
			'Your credentials and the ones shared with you — your agents can be bound to them.';
		if (isAdmin) {
			expect(sheet.getByText(workspaceWide)).toBeInTheDocument();
			expect(sheet.queryByText(own)).toBeNull();
		} else {
			expect(sheet.getByText(own)).toBeInTheDocument();
			expect(sheet.queryByText(workspaceWide)).toBeNull();
		}
	});
});
