import { test, expect, type APIRequestContext, type Page } from '@playwright/test';
import { authHeaders, getAdminUserId } from './helpers';
import type { AgentIdentity } from './agent-flow';
import {
	agentBindings,
	agentConnect,
	agentStatus,
	approveDialog,
	FAKE_OAUTH,
	getCredential,
	importOAuthApi,
	newAgent,
	oauthConnectBody,
	openApproveLink,
	upstreamsReachable,
	type ApiRef,
	type ConnectCreated,
} from './connect-helpers';

/**
 * `awaiting_app`: an agent asks for an OAuth API (the fake OAuth server's
 * authorization-code API) that has no app to connect through. The approver
 * brings their own OAuth client, or binds a credential they already hold —
 * only when its granted scopes cover the request. A narrower credential is
 * re-authorized only when no other agent is bound to it; otherwise the
 * review names the agents using it and asks for a new credential. The
 * shared-app option is an enterprise surface and never shows in OSS.
 *
 * The tests build on each other (one API, one credential), so they run in
 * order.
 */
test.describe.configure({ mode: 'serial' });

let api: ApiRef;
let first: AgentIdentity;
let credentialId: string;

test.beforeAll(async ({ request }) => {
	test.skip(!(await upstreamsReachable(request)), 'connect e2e upstreams are not running');
	await request.post(`${FAKE_OAUTH}/control/reset`);
});

test.beforeEach(async ({ request }) => {
	test.slow();
	api ??= await importOAuthApi(request);
});

/** Finish the vendor sign-in the confirm started: follow its authorize URL. */
async function completeVendorSignIn(page: Page, authorizeUrl: string): Promise<void> {
	expect(authorizeUrl.startsWith(`${FAKE_OAUTH}/authorize`)).toBe(true);
	const vendor = await page.context().newPage();
	await vendor.goto(authorizeUrl);
	await vendor.waitForLoadState('load');
	await vendor.close();
}

/** Click through review → rules on the open dialog. */
async function continueToRules(page: Page): Promise<void> {
	const dialog = approveDialog(page);
	await dialog.getByRole('button', { name: 'Continue' }).click();
	await expect(dialog.getByText('Permission rules')).toBeVisible();
}

async function newAwaitingSession(
	request: APIRequestContext,
	label: string,
	scopes: string[],
): Promise<{ agent: AgentIdentity; session: ConnectCreated }> {
	const agent = await newAgent(request, label);
	const session = await agentConnect(request, agent, oauthConnectBody(api, scopes, label));
	expect(session.resolved_flow).toBe('awaiting_app');
	return { agent, session };
}

test('waiting signals include an awaiting_app request, one row per agent', async ({
	page,
	request,
}) => {
	const { agent } = await newAwaitingSession(request, 'waiting', ['read']);
	// A second ask from the same agent, for a different API, collapses into its row.
	const other = await importOAuthApi(request);
	await agentConnect(request, agent, oauthConnectBody(other, ['read'], 'second ask'));

	await page.goto('/app/agents');
	const waiting = page.getByRole('region', { name: 'Waiting for you' });
	const row = waiting.getByRole('listitem').filter({ hasText: agent.name });
	await expect(row).toHaveCount(1);
	await expect(row).toContainText(
		`wants to connect ${api.vendor}/${api.name} and ${other.vendor}/${other.name}`,
	);
	await expect(row.getByRole('link')).toHaveCount(2);

	await page.getByRole('button', { name: /^Notifications/ }).click();
	await expect(
		page.getByText(
			`${agent.name} is waiting for you to connect ${api.vendor}/${api.name} and ${other.vendor}/${other.name}`,
		),
	).toBeVisible();
});

test('bring your own OAuth client: the review offers it, without a shared-app option, and connects', async ({
	page,
	request,
}) => {
	const created = await newAwaitingSession(request, 'ownclient', ['read']);
	first = created.agent;

	await openApproveLink(page, created.session);
	const dialog = approveDialog(page);
	await expect(
		dialog.getByText('uses OAuth, and there is no OAuth app to connect through yet.'),
	).toBeVisible();
	const method = dialog.getByRole('radiogroup');
	await expect(method.getByRole('radio', { name: /Bring your own OAuth client/ })).toBeChecked();
	// OSS has no shared-app surface.
	await expect(dialog.getByText('Register as a shared app')).toHaveCount(0);
	// The spec's scopes, with the agent's ask pre-selected.
	await expect(dialog.getByText('1 of 3 selected')).toBeVisible();
	await expect(dialog.getByText('requested', { exact: true })).toHaveCount(1);

	await continueToRules(page);
	await dialog.getByRole('button', { name: 'Continue' }).click();
	await dialog.getByLabel('Client ID').fill('e2e-own-client');
	const secret = dialog.getByLabel('Client secret');
	await expect(secret).toHaveAttribute('type', 'password');
	await expect(secret).toHaveAttribute('autocomplete', 'off');
	await secret.fill('e2e-own-secret');
	await expect(dialog.getByRole('textbox', { name: 'Callback URL' })).toHaveValue(
		/\/credentials\/oauth\/callback$/,
	);

	const confirmed = page.waitForResponse((r) =>
		r.url().includes(`/connect-sessions/${created.session.session_id}:confirm`),
	);
	await dialog.getByRole('button', { name: 'Continue to sign-in' }).click();
	const res = await confirmed;
	expect(res.status(), await res.text()).toBe(200);
	const body = await res.json();
	expect(await res.text()).not.toContain('e2e-own-secret');
	await completeVendorSignIn(page, body.authorize_url);

	await expect(dialog.getByText(`Connected to ${api.vendor}/${api.name}`)).toBeVisible({
		timeout: 20_000,
	});
	const status = await agentStatus(request, first, created.session);
	expect(status.status).toBe('connected');
	credentialId = status.credential_id!;
	const credential = await getCredential(request, credentialId);
	expect(credential.created_by).toBe(await getAdminUserId(request));
	const bindings = await agentBindings(request, first.clientId);
	expect(bindings.map((b) => b.credential_id)).toContain(credentialId);
});

test('use an existing credential whose grant covers the request: bound without a sign-in', async ({
	page,
	request,
}) => {
	const { agent, session } = await newAwaitingSession(request, 'existing', ['read']);
	const credential = await getCredential(request, credentialId);

	await openApproveLink(page, session);
	const dialog = approveDialog(page);
	const option = dialog.getByRole('radio', { name: new RegExp(`Use ${credential.name}`) });
	await expect(option).toBeEnabled();
	await expect(dialog.getByText('Its sign-in covers the requested scopes (read).')).toBeVisible();
	await option.check();
	await continueToRules(page);
	const confirmed = page.waitForResponse((r) =>
		r.url().includes(`/connect-sessions/${session.session_id}:confirm`),
	);
	await dialog.getByRole('button', { name: 'Continue' }).click();
	expect((await confirmed).status()).toBe(200);
	await expect(dialog.getByText(`Connected to ${api.vendor}/${api.name}`)).toBeVisible();

	const status = await agentStatus(request, agent, session);
	expect(status).toMatchObject({ status: 'connected', credential_id: credentialId });
	expect((await agentBindings(request, agent.clientId)).map((b) => b.credential_id)).toContain(
		credentialId,
	);
});

test('a narrower credential used by other agents is never bound and cannot be widened', async ({
	page,
	request,
}) => {
	const { session } = await newAwaitingSession(request, 'narrow-shared', ['read', 'write']);
	const credential = await getCredential(request, credentialId);

	await openApproveLink(page, session);
	const dialog = approveDialog(page);
	await expect(
		dialog.getByRole('radio', { name: new RegExp(`Use ${credential.name}`) }),
	).toBeDisabled();
	await expect(
		dialog.getByText(
			'Granted read; the agent asked for read, write. Missing write, so binding it as is would fail upstream.',
		),
	).toBeVisible();
	const shared = dialog.getByText(/Used by 2 other agents/);
	await expect(shared).toBeVisible();
	await expect(shared).toContainText(first.name);
	await expect(shared).toContainText('Connect a new credential instead.');
	await expect(dialog.getByRole('radio', { name: /Re-authorize/ })).toHaveCount(0);
	// The approver's way forward is a new credential with exactly these scopes.
	await expect(dialog.getByRole('radio', { name: /Bring your own OAuth client/ })).toBeChecked();
});

test('a narrower credential no other agent uses can be re-authorized with more scopes', async ({
	page,
	request,
}) => {
	// Leave the credential with no bound agent.
	for (const b of await bindingsOf(request, credentialId)) {
		const res = await request.delete(`/agents/${b}/credentials/${credentialId}?purge=true`, {
			headers: authHeaders(),
		});
		expect(res.status(), await res.text()).toBe(204);
	}
	const { agent, session } = await newAwaitingSession(request, 'reauth', ['read', 'write']);
	const credential = await getCredential(request, credentialId);

	await openApproveLink(page, session);
	const dialog = approveDialog(page);
	const reauth = dialog.getByRole('radio', {
		name: new RegExp(`Re-authorize ${credential.name} with more scopes`),
	});
	await expect(reauth).toBeEnabled();
	await expect(
		dialog.getByText("No other agent uses it. You'll sign in again to grant write."),
	).toBeVisible();
	await reauth.check();
	await continueToRules(page);
	const confirmed = page.waitForResponse((r) =>
		r.url().includes(`/connect-sessions/${session.session_id}:confirm`),
	);
	await dialog.getByRole('button', { name: 'Continue' }).click();
	const res = await confirmed;
	expect(res.status(), await res.text()).toBe(200);
	const body = await res.json();
	expect(body.credential_id).toBe(credentialId);
	await expect(dialog.getByText(/Finish granting the extra scopes/)).toBeVisible();
	await completeVendorSignIn(page, body.authorize_url);

	const status = await agentStatus(request, agent, session);
	expect(status).toMatchObject({ status: 'connected', credential_id: credentialId });
	expect((await agentBindings(request, agent.clientId)).map((b) => b.credential_id)).toContain(
		credentialId,
	);

	// The re-consent widened the grant: the next ask for both scopes can bind it.
	const next = await newAwaitingSession(request, 'after-reauth', ['read', 'write']);
	const review = await request.get(`/connect-sessions/${next.session.session_id}`, {
		headers: authHeaders(),
	});
	const candidate = (
		(await review.json()).existing_credentials as Array<Record<string, unknown>>
	).find((c) => c.credential_id === credentialId);
	expect(candidate).toMatchObject({ can_bind: true, missing_scopes: [] });
	expect(candidate?.granted_scopes).toEqual(expect.arrayContaining(['read', 'write']));
});

/** Agent ids bound to a credential, via each agent's bindings. */
async function bindingsOf(request: APIRequestContext, credId: string): Promise<string[]> {
	const res = await request.get(`/credentials/${credId}/agents`, { headers: authHeaders() });
	expect(
		res.ok(),
		`GET credential agents failed: ${res.status()} ${await res.text()}`,
	).toBeTruthy();
	const body = await res.json();
	const rows = (Array.isArray(body) ? body : body.data) as Array<Record<string, unknown>>;
	return rows.map((r) => (r.agent_id ?? r.id) as string);
}
