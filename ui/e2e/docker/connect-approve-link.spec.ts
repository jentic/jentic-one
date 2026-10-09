import { test, expect } from '@playwright/test';
import { authHeaders, captureConsoleErrors } from './helpers';
import {
	agentConnect,
	agentConnectRaw,
	agentStatus,
	apiKeyConnectBody,
	approveDialog,
	importSmokeApi,
	newAgent,
	openApproveLink,
	recordConnectWrites,
	upstreamsReachable,
	type ApiRef,
} from './connect-helpers';

/**
 * The token-less approve link an agent relays (`/app/agents?approve=<sid>`),
 * and the two ways an operator can leave it without connecting: "Not now"
 * (no server call, the request stays open) and "Reject" (`:reject`, the
 * agent sees `failed` / `rejected` and is cooled down). Also the activity
 * rail's `connect_session.created` row, which opens the same dialog.
 */

let api: ApiRef;

test.beforeAll(async ({ request }) => {
	test.skip(!(await upstreamsReachable(request)), 'connect e2e upstreams are not running');
});

test.beforeEach(async ({ request }) => {
	test.slow();
	api ??= await importSmokeApi(request);
});

test('the approve link opens the review token-less, strips its params, and sends no referrer', async ({
	page,
	request,
}) => {
	const errors = captureConsoleErrors(page);
	const agent = await newAgent(request, 'link');
	const session = await agentConnect(request, agent, apiKeyConnectBody(api, 'link test'));

	// The link the agent relays carries the session id and nothing else.
	const link = new URL(session.approval_url);
	expect(link.pathname).toBe('/app/agents');
	expect([...link.searchParams.keys()]).toEqual(['approve']);
	expect(session.approval_url).not.toContain(session.poll_token);

	const shell = page.waitForResponse(
		(r) => r.url().includes('/app/agents') && r.request().resourceType() === 'document',
	);
	await openApproveLink(page, session);
	expect((await shell).headers()['referrer-policy']).toBe('no-referrer');

	// The review read went out without a poll token.
	await expect(approveDialog(page).getByText(agent.name)).toBeVisible();
	await expect(page).not.toHaveURL(/approve=/);
	await expect(page).not.toHaveURL(/poll_token/);

	expect(errors, `unexpected console errors:\n${errors.join('\n')}`).toEqual([]);
});

test('an older link that still carries the poll token opens the review and drops both params', async ({
	page,
	request,
}) => {
	const agent = await newAgent(request, 'oldlink');
	const session = await agentConnect(request, agent, apiKeyConnectBody(api, 'old link'));

	await page.goto(
		`/app/agents?approve=${session.session_id}&poll_token=${encodeURIComponent(session.poll_token)}`,
	);
	await expect(approveDialog(page)).toBeVisible();
	await expect(approveDialog(page).getByText(agent.name, { exact: true })).toBeVisible();
	await expect(page).not.toHaveURL(/approve=|poll_token=/);
});

test('"Not now" closes the review with no server call and leaves the request open', async ({
	page,
	request,
}) => {
	const agent = await newAgent(request, 'notnow');
	const session = await agentConnect(request, agent, apiKeyConnectBody(api, 'not now test'));
	const writes = recordConnectWrites(page);

	await openApproveLink(page, session);
	await approveDialog(page).getByRole('button', { name: 'Not now' }).click();
	await expect(approveDialog(page)).toBeHidden();
	// Give a stray unmount cancel / beacon time to fire before asserting none did.
	await page.waitForTimeout(1000);
	expect(writes.map((r) => `${r.method()} ${r.url()}`)).toEqual([]);

	expect((await agentStatus(request, agent, session)).status).toBe('pending');
	const waiting = page.getByRole('region', { name: 'Waiting for you' });
	await expect(waiting.getByText(`${agent.name} wants to connect`)).toBeVisible();
});

test('"Reject" confirms, ends the session as rejected, and clears the waiting signals', async ({
	page,
	request,
}) => {
	const agent = await newAgent(request, 'reject');
	const session = await agentConnect(request, agent, apiKeyConnectBody(api, 'reject test'));

	await openApproveLink(page, session);
	await approveDialog(page).getByRole('button', { name: 'Reject', exact: true }).click();
	const confirm = page.getByRole('dialog', { name: 'Reject this request?' });
	await expect(confirm).toBeVisible();
	const rejected = page.waitForResponse((r) =>
		r.url().endsWith(`/connect-sessions/${session.session_id}:reject`),
	);
	await confirm.getByRole('button', { name: 'Reject request' }).click();
	expect((await rejected).status()).toBe(204);
	await expect(page.getByText('Request rejected').first()).toBeVisible();
	await expect(approveDialog(page)).toBeHidden();

	// The agent sees a terminal failure with the explicit reason, and its
	// repeat ask for the same target is cooled down.
	const status = await agentStatus(request, agent, session);
	expect(status).toMatchObject({ status: 'failed', error_code: 'rejected' });
	const again = await agentConnectRaw(request, agent, apiKeyConnectBody(api, 'again'));
	expect(again.status).toBe(429);
	expect(again.body.type).toBe('recently_rejected');

	// Neither the Agents page nor the inbox still lists it. (The approve
	// dialog is hosted on the credential inventory, which stays open.)
	await page
		.getByRole('dialog', { name: 'Credentials' })
		.getByRole('button', { name: 'Close' })
		.click();
	const waiting = page.getByRole('region', { name: 'Waiting for you' });
	await expect(waiting.getByText(`${agent.name} wants to connect`)).toHaveCount(0);
	await page.getByRole('button', { name: /^Notifications/ }).click();
	await expect(page.getByText(`${agent.name} is waiting for you to connect`)).toHaveCount(0);
});

test('the rail row for a new connect request opens it for approval', async ({ page, request }) => {
	const agent = await newAgent(request, 'rail');
	const session = await agentConnect(request, agent, apiKeyConnectBody(api, 'rail test'));

	// The event exists on the feed with the session id.
	const events = await request.get('/events?limit=50', { headers: authHeaders() });
	const created = ((await events.json()).data as Array<Record<string, unknown>>).find(
		(e) =>
			e.type === 'connect_session.created' &&
			(e.data as Record<string, unknown> | undefined)?.session_id === session.session_id,
	);
	expect(created, 'no connect_session.created event for the session').toBeTruthy();

	await page.goto('/app/library');
	await page.getByRole('button', { name: /^Show live activity/ }).click();
	const rail = page.getByRole('complementary', { name: 'Activity' });
	const row = rail.getByRole('link', { name: new RegExp(`${agent.name}.*asked to connect`) });
	await expect(row).toBeVisible();
	await row.click();
	await expect(approveDialog(page)).toBeVisible();
	await expect(approveDialog(page).getByText(agent.name)).toBeVisible();
	await expect(page).not.toHaveURL(/approve=/);
});
