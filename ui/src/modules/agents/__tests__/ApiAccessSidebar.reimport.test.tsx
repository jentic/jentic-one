/**
 * Action rules across a re-import. The registry gives every revision new
 * operation ids (`op_…`), so a rule naming one stops matching once the API is
 * re-imported; the spec's `operationId` (`postMessage`) stays. By action saves
 * the `operationId` where the credential reaches no other API declaring it,
 * names a stale id by what it was, and moves it to this version in one click
 * — and the agent's API row says "Rules out of date" without the panel open.
 */
import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import { http, HttpResponse } from 'msw';
import { page } from 'vitest/browser';
import { renderWithProviders, screen, waitFor, within, userEvent } from '@/__tests__/test-utils';
import { worker } from '@/mocks/browser';
import { setToken } from '@/shared/api';
import { Toaster } from '@/shared/ui';
import { resetAgentsStore, setBindingPermissions } from '@/modules/agents/mocks/handlers';
import {
	makeMockCredential,
	resetApisStore,
	resetConnectSessionsStore,
	resetCredentialsStore,
} from '@/shared/credentials/mocks/handlers';
import { CredentialType, type ApiResponse } from '@/shared/credentials/api';
import { registryOperationId } from '@/shared/credentials/lib/specOperationIds';
import AgentsPage from '@/modules/agents/pages/AgentsPage';

const API = 'slack.com/default/1.0.0';
const REV_NOW = '6f1b0b7e-0000-4000-8000-000000000002';
const REV_OLD = '6f1b0b7e-0000-4000-8000-000000000001';

/** The current revision's ids, in the registry's shape. */
const POST_NOW = `op_${'a'.repeat(40)}`;
const LIST_NOW = `op_${'b'.repeat(40)}`;

const SPEC = (extra: Record<string, unknown> = {}) => ({
	openapi: '3.0.0',
	info: { title: 'Slack', version: '1.0.0' },
	paths: {
		'/chat.postMessage': {
			post: { operationId: 'postMessage', summary: 'Send a message' },
		},
		'/conversations.list': {
			get: { operationId: 'listConversations', summary: 'List channels' },
		},
		...extra,
	},
});

/** The earlier revision: it also had an action this version dropped. */
const OLD_SPEC = SPEC({
	'/chat.delete': { post: { operationId: 'deleteMessage', summary: 'Delete a message' } },
});

function apiRow(vendor: string, name: string, title: string, revision: string | null): ApiResponse {
	return {
		_links: { self: `/apis/${vendor}`, openapi: `/apis/${vendor}/openapi` },
		api: { vendor, name, version: '1.0.0' },
		catalog_api_id: null,
		created_at: '2026-01-01T00:00:00Z',
		current_revision_id: revision,
		description: null,
		display_name: title,
		icon_url: null,
		operation_count: 2,
		revision_count: 2,
		security_schemes: [],
		updated_at: '2026-01-01T00:00:00Z',
	} as unknown as ApiResponse;
}

function op(id: string, method: string, path: string, name: string) {
	return {
		operation_id: id,
		method,
		path,
		name,
		description: null,
		tags: [],
		deprecated: false,
		revision_id: REV_NOW,
		_links: {},
	};
}

/** The registry behind the Slack tile: its operations, its two revisions and
 * their documents. `others` are further live Slack APIs the credential covers. */
function seedRegistry(others: Array<{ name: string; title: string; spec: object }> = []) {
	resetApisStore([
		{ row: apiRow('slack.com', 'default', 'Slack', REV_NOW), spec: SPEC() },
		...others.map((o) => ({
			row: apiRow('slack.com', o.name, o.title, 'rev_other'),
			spec: o.spec as Record<string, unknown>,
		})),
	]);
	worker.use(
		http.get('/apis/:vendor/:name/:version/operations', ({ params }) =>
			`${params.vendor}/${params.name}/${params.version}` === API
				? HttpResponse.json({
						data: [
							op(POST_NOW, 'post', '/chat.postMessage', 'Send a message'),
							op(LIST_NOW, 'get', '/conversations.list', 'List channels'),
						],
						has_more: false,
						next_cursor: null,
					})
				: HttpResponse.json({ status: 404 }, { status: 404 }),
		),
		http.get('/apis/slack.com/default/1.0.0/revisions', () =>
			HttpResponse.json({
				data: [
					{
						revision_id: REV_NOW,
						is_current: true,
						state: 'published',
						promoted_at: '2026-11-02T10:00:00Z',
						created_at: '2026-11-02T09:00:00Z',
					},
					{
						revision_id: REV_OLD,
						is_current: false,
						state: 'archived',
						promoted_at: '2026-11-01T10:00:00Z',
						created_at: '2026-11-01T09:00:00Z',
					},
				],
				has_more: false,
				next_cursor: null,
			}),
		),
		http.get('/apis/slack.com/default/1.0.0/revisions/:rid/openapi', ({ params }) =>
			HttpResponse.json(params.rid === REV_NOW ? SPEC() : OLD_SPEC),
		),
	);
}

/** Every rule list saved (each save is an applied edit session), in order. */
function recordSaves(): unknown[][] {
	const bodies: unknown[][] = [];
	worker.events.on('request:start', ({ request }) => {
		if (request.method === 'POST' && request.url.includes('/integrations:edit-connection')) {
			void request
				.clone()
				.json()
				.then((b: { rules?: { set?: unknown[] } }) => {
					if (b.rules?.set) bodies.push(b.rules.set);
				});
		}
	});
	return bodies;
}

const PATH_ALLOW = {
	effect: 'allow' as const,
	match_mode: 'prefix' as const,
	methods: ['POST', 'GET'],
	path: '/',
};

function renderPage() {
	return renderWithProviders(
		<>
			<AgentsPage />
			<Toaster />
		</>,
		{ route: '/?agent=agnt_active_1' },
	);
}

async function openSlack(): Promise<HTMLElement> {
	renderPage();
	// A vendor-wide credential shows on every tile it serves.
	await screen.findAllByText('Slack bot token');
	const user = userEvent.setup();
	await user.click(screen.getByRole('button', { name: /^Manage access for Slack( \(|$)/ }));
	const dialog = await screen.findByRole('dialog', { name: 'Slack' });
	await waitFor(() => expect(within(dialog).getAllByRole('button')[0]).toHaveFocus());
	return dialog;
}

async function byAction(dialog: HTMLElement): Promise<HTMLElement> {
	const user = userEvent.setup();
	await user.click(await within(dialog).findByRole('button', { name: /Edit access/ }));
	const editor = await within(dialog).findByTestId('access-editor');
	await user.click(await within(editor).findByRole('button', { name: 'By action' }));
	const fold = within(editor).queryByRole('button', { name: /^Choose more actions/ });
	if (fold) await user.click(fold);
	return within(editor).findByTestId('manage-actions');
}

const choose = (scope: HTMLElement, action: string, effect: 'Allow' | 'Ask' | 'Deny') =>
	within(within(scope).getByRole('group', { name: `Access for ${action}` })).getByRole('button', {
		name: effect,
	});

describe('ApiAccessSidebar — action rules across a re-import', () => {
	beforeEach(async () => {
		await page.viewport(1440, 900);
		setToken('test-token');
		resetAgentsStore();
		resetConnectSessionsStore();
		resetCredentialsStore([
			makeMockCredential({
				credential_id: 'cred_slack_1',
				name: 'Slack bot token',
				type: CredentialType.BEARER_TOKEN,
				api: { vendor: 'slack.com', name: 'default', version: '1.0.0' },
			}),
		]);
	});

	afterEach(() => {
		worker.events.removeAllListeners('request:start');
		worker.events.removeAllListeners('response:mocked');
	});

	it('By action saves the spec operationId, which a re-import keeps', async () => {
		seedRegistry();
		setBindingPermissions('agnt_active_1', 'cred_slack_1', [PATH_ALLOW]);
		const saves = recordSaves();
		const user = userEvent.setup();
		const dialog = await openSlack();
		const actions = await byAction(dialog);
		await user.click(choose(actions, 'POST /chat.postMessage', 'Deny'));
		await user.click(within(dialog).getByTestId('manage-apply'));
		await user.click(within(dialog).getByRole('button', { name: /Save rules/ }));
		await waitFor(() => expect(saves).toHaveLength(1));
		expect(saves[0]).toContainEqual({ effect: 'deny', operations: ['postMessage'] });
	});

	it('a deny saved before a re-import is named as it was, and Update moves it to this version', async () => {
		seedRegistry();
		const oldPost = await registryOperationId(REV_OLD, '/chat.postMessage', 'POST');
		const oldDelete = await registryOperationId(REV_OLD, '/chat.delete', 'POST');
		setBindingPermissions('agnt_active_1', 'cred_slack_1', [
			{ effect: 'deny', operations: [oldPost, oldDelete] },
			PATH_ALLOW,
		]);
		const saves = recordSaves();
		const user = userEvent.setup();
		const dialog = await openSlack();

		const notice = await within(dialog).findByTestId('access-stale-actions');
		expect(notice).toHaveTextContent('2 actions in these rules are not in this version');
		// Named from the earlier revision's own document, not as a raw id.
		const names = await within(notice).findByTestId('access-stale-names');
		expect(names).toHaveTextContent(
			'POST /chat.postMessage · Send a message — from an earlier version',
		);
		expect(names).toHaveTextContent(
			'POST /chat.delete · Delete a message — from an earlier version · not in this version',
		);
		expect(names).not.toHaveTextContent('op_');
		expect(notice).toHaveTextContent(
			'Update moves the 1 action this version still has, with the same effect',
		);

		await user.click(within(notice).getByTestId('access-stale-update'));
		await waitFor(() => expect(saves).toHaveLength(1));
		// Same effect, now by the id a re-import keeps; the dropped action stays
		// for a person to settle.
		expect(saves[0]).toContainEqual({ effect: 'deny', operations: ['postMessage', oldDelete] });
		await waitFor(() =>
			expect(within(dialog).getByTestId('access-stale-actions')).toHaveTextContent(
				'1 action in these rules is not in this version',
			),
		);
	});

	it('setting a stale action again in By action retires its old id', async () => {
		seedRegistry();
		const oldPost = await registryOperationId(REV_OLD, '/chat.postMessage', 'POST');
		setBindingPermissions('agnt_active_1', 'cred_slack_1', [
			{ effect: 'deny', operations: [oldPost] },
			PATH_ALLOW,
		]);
		const saves = recordSaves();
		const user = userEvent.setup();
		const dialog = await openSlack();
		await within(dialog).findByTestId('access-stale-names');
		const actions = await byAction(dialog);
		// The stale deny is not in force, so the row reads unset.
		expect(
			within(
				within(actions).getByRole('group', { name: 'Access for POST /chat.postMessage' }),
			).getByRole('button', { name: 'Deny' }),
		).toHaveAttribute('aria-pressed', 'false');
		await user.click(choose(actions, 'POST /chat.postMessage', 'Deny'));
		expect(within(dialog).getByTestId('manage-apply-status')).toHaveTextContent(
			'1 change not applied',
		);
		await user.click(within(dialog).getByTestId('manage-apply'));
		await user.click(within(dialog).getByRole('button', { name: /Save rules/ }));
		await waitFor(() => expect(saves).toHaveLength(1));
		expect(saves[0]).toContainEqual({ effect: 'deny', operations: ['postMessage'] });
		expect(JSON.stringify(saves[0])).not.toContain(oldPost);
		await waitFor(() =>
			expect(within(dialog).queryByTestId('access-stale-actions')).toBeNull(),
		);
	});

	it('a saved Ask on an operationId another covered API declares too warns, and names it in each API', async () => {
		// The credential is vendor-wide, and Slack's Legacy API also declares
		// `postMessage`: the broker couldn't tell which one a rule meant.
		resetCredentialsStore([
			makeMockCredential({
				credential_id: 'cred_slack_1',
				name: 'Slack bot token',
				type: CredentialType.BEARER_TOKEN,
				api: { vendor: 'slack.com', name: '', version: '' },
			}),
		]);
		seedRegistry([{ name: 'legacy', title: 'Slack Legacy', spec: SPEC() }]);
		setBindingPermissions('agnt_active_1', 'cred_slack_1', [
			{ effect: 'require-approval', operations: ['postMessage'] },
			PATH_ALLOW,
		]);
		const saves = recordSaves();
		const user = userEvent.setup();
		const dialog = await openSlack();
		await user.click(await within(dialog).findByRole('button', { name: /Edit access/ }));
		const editor = await within(dialog).findByTestId('access-editor');
		await user.click(await within(editor).findByRole('button', { name: 'By action' }));
		// Read once the other API's document is: three reads after the panel opens.
		const warning = await within(editor).findByTestId(
			'manage-action-rule-ask-ambiguous',
			{},
			{ timeout: 5000 },
		);
		expect(warning).toHaveTextContent(
			'postMessage is also an action of Slack Legacy v1.0.0, which this credential reaches too',
		);
		expect(warning).toHaveTextContent('this Ask refuses the call instead of holding it');

		// Named in BOTH APIs, the Ask holds whichever one a call resolves to — by
		// this API's id alone, a call the broker resolves to Legacy would skip it
		// and fall through to the allow.
		await user.click(within(warning).getByTestId('manage-action-rule-ask-ambiguous-fix'));
		await user.click(within(dialog).getByRole('button', { name: /Save rules/ }));
		await waitFor(() => expect(saves).toHaveLength(1));
		const legacyPost = await registryOperationId('rev_other', '/chat.postMessage', 'POST');
		expect(saves[0]).toContainEqual({
			effect: 'require-approval',
			operations: [POST_NOW, legacyPost],
		});
	});

	it('a choice on an operationId another covered API shares is saved by its registry id in each API', async () => {
		resetCredentialsStore([
			makeMockCredential({
				credential_id: 'cred_slack_1',
				name: 'Slack bot token',
				type: CredentialType.BEARER_TOKEN,
				api: { vendor: 'slack.com', name: '', version: '' },
			}),
		]);
		// Legacy declares `postMessage` only.
		seedRegistry([
			{
				name: 'legacy',
				title: 'Slack Legacy',
				spec: { paths: { '/chat.postMessage': { post: { operationId: 'postMessage' } } } },
			},
		]);
		setBindingPermissions('agnt_active_1', 'cred_slack_1', [PATH_ALLOW]);
		const saves = recordSaves();
		// The other API's document answers which ids are shared.
		const legacyRead = new Promise<void>((resolve) => {
			worker.events.on('response:mocked', ({ request }) => {
				if (request.url.includes('/apis/slack.com/legacy/1.0.0/openapi')) resolve();
			});
		});
		const user = userEvent.setup();
		const dialog = await openSlack();
		const actions = await byAction(dialog);
		await legacyRead;
		await user.click(choose(actions, 'POST /chat.postMessage', 'Deny'));
		// Unique: still saved by `listConversations`; shared: by the registry id.
		await user.click(choose(actions, 'GET /conversations.list', 'Allow'));
		await user.click(within(dialog).getByTestId('manage-apply'));
		await user.click(within(dialog).getByRole('button', { name: /Save rules/ }));
		await waitFor(() => expect(saves).toHaveLength(1));
		// Shared: by the registry id in each API that declares it.
		const legacyPost = await registryOperationId('rev_other', '/chat.postMessage', 'POST');
		expect(saves[0]).toContainEqual({ effect: 'deny', operations: [POST_NOW, legacyPost] });
		expect(saves[0]).toContainEqual({ effect: 'allow', operations: ['listConversations'] });
	});

	it('a rule naming a shared action in this API only warns, Deny included, and names it in each API', async () => {
		// What an action set before the other API went live leaves: this API's
		// registry id alone. A call resolved to Legacy skips it.
		resetCredentialsStore([
			makeMockCredential({
				credential_id: 'cred_slack_1',
				name: 'Slack bot token',
				type: CredentialType.BEARER_TOKEN,
				api: { vendor: 'slack.com', name: '', version: '' },
			}),
		]);
		seedRegistry([
			{
				name: 'legacy',
				title: 'Slack Legacy',
				spec: { paths: { '/chat.postMessage': { post: { operationId: 'postMessage' } } } },
			},
		]);
		setBindingPermissions('agnt_active_1', 'cred_slack_1', [
			{ effect: 'deny', operations: [POST_NOW] },
			PATH_ALLOW,
		]);
		const saves = recordSaves();
		const user = userEvent.setup();
		const dialog = await openSlack();
		await user.click(await within(dialog).findByRole('button', { name: /Edit access/ }));
		const editor = await within(dialog).findByTestId('access-editor');
		await user.click(await within(editor).findByRole('button', { name: 'By action' }));
		const warning = await within(editor).findByTestId(
			'manage-action-rule-deny-partial',
			{},
			{ timeout: 5000 },
		);
		expect(warning).toHaveTextContent(
			'This rule names POST /chat.postMessage in this API only, but Slack Legacy v1.0.0 has the same action',
		);
		expect(warning).toHaveTextContent('a call the broker resolves there skips this rule');

		await user.click(within(warning).getByTestId('manage-action-rule-deny-partial-fix'));
		await user.click(within(dialog).getByRole('button', { name: /Save rules/ }));
		await waitFor(() => expect(saves).toHaveLength(1));
		const legacyPost = await registryOperationId('rev_other', '/chat.postMessage', 'POST');
		expect(saves[0]).toContainEqual({ effect: 'deny', operations: [POST_NOW, legacyPost] });
	});

	it('the API card says so too, in the cards view', async () => {
		seedRegistry();
		const oldPost = await registryOperationId(REV_OLD, '/chat.postMessage', 'POST');
		setBindingPermissions('agnt_active_1', 'cred_slack_1', [
			{ effect: 'deny', operations: [oldPost] },
			PATH_ALLOW,
		]);
		renderPage();
		await screen.findByText('Slack bot token');
		await userEvent.setup().click(screen.getByRole('radio', { name: 'Cards view' }));
		const slack = async () =>
			(await screen.findAllByTestId('api-card')).find((c) =>
				/Slack/.test(c.textContent ?? ''),
			);
		await waitFor(async () => expect(await slack()).toHaveTextContent('Out of date'));
		expect(await slack()).not.toHaveTextContent('Ready');
	});

	it('the agent’s API row says the rules are out of date, with no panel open', async () => {
		seedRegistry();
		const oldPost = await registryOperationId(REV_OLD, '/chat.postMessage', 'POST');
		setBindingPermissions('agnt_active_1', 'cred_slack_1', [
			{ effect: 'deny', operations: [oldPost] },
			PATH_ALLOW,
		]);
		renderPage();
		await screen.findByText('Slack bot token');
		const row = screen
			.getByRole('button', { name: /^Manage access for Slack( \(|$)/ })
			.closest('li') as HTMLElement;
		expect(await within(row).findByText('Rules out of date')).toBeInTheDocument();
		expect(within(row).queryByText('Ready')).toBeNull();
	});
});
