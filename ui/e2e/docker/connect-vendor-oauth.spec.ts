import { test, expect, type Page } from '@playwright/test';
import {
	agentBindings,
	agentConnect,
	agentStatus,
	approveDialog,
	FAKE_DEVICE_VENDOR_KEY,
	FAKE_OAUTH,
	FAKE_VENDOR_KEY,
	newAgent,
	openApproveLink,
	upstreamsReachable,
} from './connect-helpers';

/**
 * Regression: approving a vendor-registry OAuth request (the `fakeas`
 * authorization-code and `fakedev` device-flow entries, both pointing at the
 * fake OAuth server) still runs end to end from the token-less link — scope
 * review, rules, vendor sign-in, connected and bound.
 */

test.beforeAll(async ({ request }) => {
	test.skip(!(await upstreamsReachable(request)), 'connect e2e upstreams are not running');
	await request.post(`${FAKE_OAUTH}/control/reset`);
});

test.beforeEach(() => {
	test.slow();
});

const RULES = [{ effect: 'allow', methods: ['GET'], path: '/me' }];

/** Review → rules → confirm, returning the confirm response body. */
async function approveScopes(page: Page, sessionId: string): Promise<Record<string, unknown>> {
	const dialog = approveDialog(page);
	await expect(dialog.getByText('What can this agent do?')).toBeVisible();
	await dialog.getByRole('button', { name: 'Continue' }).click();
	await expect(dialog.getByText('Permission rules')).toBeVisible();
	const confirmed = page.waitForResponse((r) =>
		r.url().includes(`/connect-sessions/${sessionId}:confirm`),
	);
	await dialog.getByRole('button', { name: 'Continue' }).click();
	const res = await confirmed;
	expect(res.status(), await res.text()).toBe(200);
	return res.json();
}

test('an authorization-code vendor request connects through the vendor sign-in', async ({
	page,
	request,
}) => {
	const agent = await newAgent(request, 'vendor-code');
	const session = await agentConnect(request, agent, {
		vendor: FAKE_VENDOR_KEY,
		requested_scopes: ['read'],
		reason: 'vendor regression',
		requested_permission_rules: RULES,
	});
	expect(session.resolved_flow).toBe('authorization_code');

	await openApproveLink(page, session);
	const dialog = approveDialog(page);
	await expect(dialog.getByText('Fake OAuth', { exact: true })).toBeVisible();
	await expect(dialog.getByText('vendor regression')).toBeVisible();
	// A vendor target has no API-target details (provenance, pinned hosts).
	await expect(dialog.getByText('Sent only to')).toHaveCount(0);

	const body = await approveScopes(page, session.session_id);
	expect(String(body.authorize_url)).toContain(`${FAKE_OAUTH}/authorize`);
	await expect(dialog.getByText(/approve on Fake OAuth/)).toBeVisible();
	const vendor = await page.context().newPage();
	await vendor.goto(String(body.authorize_url));
	await vendor.close();

	await expect(dialog.getByText('Connected to Fake OAuth')).toBeVisible({ timeout: 20_000 });
	const status = await agentStatus(request, agent, session);
	expect(status).toMatchObject({ status: 'connected', connected_as: '@fake-user' });
	expect((await agentBindings(request, agent.clientId)).map((b) => b.credential_id)).toContain(
		status.credential_id,
	);
});

test('a device-flow vendor request connects once the user approves the code', async ({
	page,
	request,
}) => {
	const agent = await newAgent(request, 'vendor-device');
	const session = await agentConnect(request, agent, {
		vendor: FAKE_DEVICE_VENDOR_KEY,
		requested_scopes: ['read'],
		reason: 'device regression',
		requested_permission_rules: RULES,
	});
	expect(session.resolved_flow).toBe('device_authorization');

	await openApproveLink(page, session);
	const body = await approveScopes(page, session.session_id);
	const userCode = String(body.user_code);
	const dialog = approveDialog(page);
	await expect(dialog.getByText(userCode)).toBeVisible();

	const approve = await request.post(
		`${FAKE_OAUTH}/control/device/approve?user_code=${encodeURIComponent(userCode)}`,
	);
	expect((await approve.json()).approved).toBe(true);

	await expect(dialog.getByText('Connected to Fake Device')).toBeVisible({ timeout: 30_000 });
	expect((await agentStatus(request, agent, session)).status).toBe('connected');
});
