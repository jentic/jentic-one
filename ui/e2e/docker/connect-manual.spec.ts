import { test, expect } from '@playwright/test';
import { authHeaders, getAdminUserId, submitLogin } from './helpers';
import {
	agentBindings,
	agentConnect,
	agentStatus,
	apiKeyConnectBody,
	approveDialog,
	bindingRules,
	createUser,
	getCredential,
	importSmokeApi,
	loginToken,
	newAgent,
	openApproveLink,
	SMOKE_UPSTREAM,
	upstreamsReachable,
	type ApiRef,
} from './connect-helpers';

/**
 * The `manual_*` approve flow for an API target (the smoke upstream's
 * `X-Api-Key` scheme): what the review shows, the at-least-one-rule gate,
 * secret entry → a connected, bound credential with the reviewed rules, a
 * review that went stale under the approver, and a viewer who may see a
 * request but not approve it.
 */

let api: ApiRef;

test.beforeAll(async ({ request }) => {
	test.skip(!(await upstreamsReachable(request)), 'connect e2e upstreams are not running');
});

test.beforeEach(async ({ request }) => {
	test.slow();
	api ??= await importSmokeApi(request);
});

test('the review shows provenance, agent and owner, scheme, pinned hosts and reason', async ({
	page,
	request,
}) => {
	const agent = await newAgent(request, 'review');
	const session = await agentConnect(
		request,
		agent,
		apiKeyConnectBody(api, 'to call /auth/api-key'),
	);
	expect(session.resolved_flow).toBe('manual_api_key');

	await openApproveLink(page, session);
	const dialog = approveDialog(page);
	// Who asks, and for whom.
	await expect(dialog.getByText(agent.name, { exact: true })).toBeVisible();
	await expect(dialog.getByText(agent.clientId)).toBeVisible();
	await expect(dialog.getByText(/^Owner/)).toHaveText(/^Owner\s+Admin User$/);
	await expect(dialog.getByText('to call /auth/api-key')).toBeVisible();
	// Where the spec came from.
	await expect(dialog.getByText(`${api.vendor}/${api.name}@${api.version}`)).toBeVisible();
	await expect(dialog.getByText('Agent- or user-submitted spec')).toBeVisible();
	await expect(dialog.getByText(`${SMOKE_UPSTREAM}/specs/live.json`)).toBeVisible();
	// What the secret is and where it may go.
	await expect(dialog.getByText('API key in the X-Api-Key header')).toBeVisible();
	await expect(dialog.getByRole('list', { name: 'Pinned server hosts' })).toHaveText(
		SMOKE_UPSTREAM,
	);

	// The rules page starts from the agent's minimal rule and needs at least one.
	await dialog.getByRole('button', { name: 'Continue' }).click();
	const rules = dialog.getByRole('list', { name: 'Rules, in evaluation order' });
	await expect(rules.getByText('requested by agent')).toBeVisible();
	await expect(rules).toContainText('/auth/api-key');
	await rules.getByRole('button', { name: 'Delete rule' }).click();
	await expect(
		dialog.getByText(
			"Add at least one rule. With no rules the agent can't call anything on this API.",
		),
	).toBeVisible();
	await expect(dialog.getByRole('button', { name: 'Continue' })).toBeDisabled();
	await expect(dialog.getByRole('button', { name: 'Skip & continue' })).toHaveCount(0);
});

test('entering the key connects a credential bound to the agent with the reviewed rules', async ({
	page,
	request,
}) => {
	const agent = await newAgent(request, 'secret');
	const session = await agentConnect(request, agent, apiKeyConnectBody(api, 'secret entry'));

	await openApproveLink(page, session);
	const dialog = approveDialog(page);
	await dialog.getByRole('button', { name: 'Continue' }).click();
	await dialog.getByRole('button', { name: 'Continue' }).click();

	// Secret entry: a write-only password field with autocomplete off.
	const key = dialog.getByLabel('API key');
	await expect(key).toHaveAttribute('type', 'password');
	await expect(key).toHaveAttribute('autocomplete', 'off');
	await expect(dialog.getByText('Sent as API key in the X-Api-Key header.')).toBeVisible();
	await key.fill('sk-e2e-manual-secret');
	const confirmed = page.waitForResponse((r) =>
		r.url().includes(`/connect-sessions/${session.session_id}:confirm`),
	);
	await dialog.getByRole('button', { name: 'Connect' }).click();
	const confirmRes = await confirmed;
	expect(confirmRes.status()).toBe(200);
	expect(await confirmRes.text()).not.toContain('sk-e2e-manual-secret');
	await expect(dialog.getByText(/connected/i).first()).toBeVisible();

	// Agent side: connected, on a credential that is bound with the rule.
	const status = await agentStatus(request, agent, session);
	expect(status.status).toBe('connected');
	const credentialId = status.credential_id!;
	expect(credentialId).toBeTruthy();
	const credential = await getCredential(request, credentialId);
	expect(credential.type).toBe('api_key');
	expect(credential.created_by).toBe(await getAdminUserId(request));
	expect(JSON.stringify(credential)).not.toContain('sk-e2e-manual-secret');
	const bindings = await agentBindings(request, agent.clientId);
	expect(bindings.map((b) => b.credential_id)).toContain(credentialId);
	const rules = await bindingRules(request, credentialId, agent.clientId);
	expect(JSON.stringify(rules)).toContain('/auth/api-key');

	// No longer waiting.
	await dialog.getByRole('button', { name: 'Close' }).last().click();
	await page
		.getByRole('dialog', { name: 'Credentials' })
		.getByRole('button', { name: 'Close' })
		.click();
	await expect(
		page
			.getByRole('region', { name: 'Waiting for you' })
			.getByText(`${agent.name} wants to connect`),
	).toHaveCount(0);
});

test('a review that changed under the approver is refused and shown again', async ({
	page,
	request,
}) => {
	const agent = await newAgent(request, 'stale');
	const session = await agentConnect(request, agent, apiKeyConnectBody(api, 'stale review'));

	await openApproveLink(page, session);
	const dialog = approveDialog(page);
	await dialog.getByRole('button', { name: 'Continue' }).click();

	// The agent changes hands while the approver is on the rules page: the
	// owner is part of the reviewed digest.
	const other = await createUser(request, ['credentials:read']);
	const patched = await request.patch(`/agents/${agent.clientId}`, {
		headers: authHeaders(),
		data: { owner_id: other.id },
	});
	expect(patched.ok(), `re-own failed: ${await patched.text()}`).toBeTruthy();

	await dialog.getByRole('button', { name: 'Continue' }).click();
	await dialog.getByLabel('API key').fill('sk-e2e-stale');
	const confirmed = page.waitForResponse((r) =>
		r.url().includes(`/connect-sessions/${session.session_id}:confirm`),
	);
	await dialog.getByRole('button', { name: 'Connect' }).click();
	const res = await confirmed;
	expect(res.status()).toBe(409);
	expect((await res.json()).type).toBe('review_stale');

	// Back on the review, with the reason and the fresh owner.
	await expect(
		dialog.getByText(
			'This request changed while you were reviewing it. Check the details again before you approve.',
		),
	).toBeVisible();
	await expect(dialog.getByText(/^Owner/)).toContainText(new RegExp(`Owner Limited|${other.id}`));
	expect((await agentStatus(request, agent, session)).status).toBe('pending');

	// Approving the fresh review goes through.
	await dialog.getByRole('button', { name: 'Continue' }).click();
	await dialog.getByRole('button', { name: 'Continue' }).click();
	await dialog.getByLabel('API key').fill('sk-e2e-stale');
	await dialog.getByRole('button', { name: 'Connect' }).click();
	await expect
		.poll(async () => (await agentStatus(request, agent, session)).status)
		.toBe('connected');
});

test('a request the approver cannot confirm blocks secret entry up front', async ({
	page,
	request,
}) => {
	const agent = await newAgent(request, 'disabled');
	const session = await agentConnect(request, agent, apiKeyConnectBody(api, 'disabled agent'));
	const disabled = await request.post(`/agents/${agent.clientId}:disable`, {
		headers: authHeaders(),
	});
	expect(disabled.ok(), `disable failed: ${await disabled.text()}`).toBeTruthy();

	await openApproveLink(page, session);
	const dialog = approveDialog(page);
	await expect(dialog.getByText(agent.name, { exact: true })).toBeVisible();
	await expect(
		dialog.getByText("This agent is disabled, so it can't be given a credential."),
	).toBeVisible();
	await expect(dialog.getByRole('button', { name: 'Continue' })).toHaveCount(0);
	await expect(dialog.getByRole('button', { name: 'Reject', exact: true })).toHaveCount(0);
	await expect(dialog.getByLabel('API key')).toHaveCount(0);
	await expect(dialog.getByRole('button', { name: 'Close' }).last()).toBeVisible();
});

test.describe('an owner without agents:write', () => {
	test.use({ storageState: { cookies: [], origins: [] } });

	test('is not asked to approve, cannot open the review, and is refused by the server', async ({
		page,
		request,
	}) => {
		const owner = await createUser(request, [
			'credentials:read',
			'credentials:write',
			'agents:read',
		]);
		const agent = await newAgent(request, 'noagentswrite');
		const reowned = await request.patch(`/agents/${agent.clientId}`, {
			headers: authHeaders(),
			data: { owner_id: owner.id },
		});
		expect(reowned.ok(), `re-own failed: ${await reowned.text()}`).toBeTruthy();
		const session = await agentConnect(request, agent, apiKeyConnectBody(api, 'limited owner'));

		// Signed out, the link goes through sign-in and lands on the review,
		// which this owner may not open.
		const link = new URL(session.approval_url);
		await page.goto(`${link.pathname}${link.search}`);
		await submitLogin(page, owner.email, owner.password);
		const dialog = approveDialog(page);
		await expect(dialog).toBeVisible();
		await expect(
			dialog.getByText("This request is no longer open, or it isn't yours to approve."),
		).toBeVisible();
		await expect(dialog.getByRole('button', { name: 'Continue' })).toHaveCount(0);
		await expect(dialog.getByLabel('API key')).toHaveCount(0);
		await dialog.getByRole('button', { name: 'Close' }).last().click();
		await page
			.getByRole('dialog', { name: 'Credentials' })
			.getByRole('button', { name: 'Close' })
			.click();
		// Nothing tells this owner the request is waiting for them.
		await expect(page.getByRole('heading', { name: 'Agents', exact: true })).toBeVisible();
		await expect(page.getByRole('region', { name: 'Waiting for you' })).toHaveCount(0);

		// The server holds the same line.
		const token = await loginToken(request, owner.email, owner.password);
		const headers = { authorization: `Bearer ${token}`, 'content-type': 'application/json' };
		const review = await request.get(`/connect-sessions/${session.session_id}`, { headers });
		expect(review.status()).toBe(403);
		const confirm = await request.post(`/connect-sessions/${session.session_id}:confirm`, {
			headers,
			data: {
				kind: 'api_key',
				key: 'sk-e2e-nope',
				permission_rules: [{ effect: 'allow', methods: ['GET'], path: '/auth/api-key' }],
				expected_agent_id: agent.clientId,
				digest: 'x',
			},
		});
		expect(confirm.status()).toBe(403);
		const reject = await request.post(`/connect-sessions/${session.session_id}:reject`, {
			headers,
		});
		expect(reject.status()).toBe(403);
		expect((await agentStatus(request, agent, session)).status).toBe('pending');
	});
});
