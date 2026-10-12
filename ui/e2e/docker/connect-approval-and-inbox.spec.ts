import { test, expect, type APIRequestContext } from '@playwright/test';
import {
	authHeaders,
	bearer,
	dismissFirstRunFor,
	importInlineApi,
	promoteLatestRevision,
	replaceAgentPermissions,
	uniqueSuffix,
} from './helpers';
import { provisionAdminOwnedAgent, type AgentIdentity } from './agent-flow';

/**
 * An agent's connect request, reviewed the way an owner meets it: the approval
 * opens on its own over the Agents page (no credential inventory behind it), a
 * finished connect closes itself after a short countdown, and the bell's
 * "Mark all as read" sticks across a reload.
 */

function apiKeySpec(title: string, host: string): string {
	return JSON.stringify({
		openapi: '3.0.0',
		info: { title, version: '1.0.0' },
		servers: [{ url: `https://${host}` }],
		components: {
			securitySchemes: { apiKey: { type: 'apiKey', in: 'header', name: 'X-Api-Key' } },
		},
		security: [{ apiKey: [] }],
		paths: {
			'/items': {
				get: { operationId: 'listItems', responses: { '200': { description: 'ok' } } },
			},
		},
	});
}

async function openRequest(
	request: APIRequestContext,
): Promise<{ agent: AgentIdentity; sessionId: string } | null> {
	const sfx = uniqueSuffix();
	const vendor = `e2e-alone-${sfx}`;
	const apiName = `alone-${sfx}`;
	await importInlineApi(request, {
		vendor,
		apiName,
		content: apiKeySpec(`Alone ${sfx}`, `${vendor}.example.com`),
	});
	await promoteLatestRevision(request, { vendor, name: apiName, version: '1.0.0' });
	const agent = await provisionAdminOwnedAgent(request, { name: `e2e-alone-${sfx}` });
	await replaceAgentPermissions(request, agent.clientId, ['credentials:connect']);
	const res = await request.post('/integrations:connect', {
		headers: bearer(agent.accessToken),
		data: {
			api: { vendor, name: apiName, version: '1.0.0' },
			reason: 'e2e',
			requested_permission_rules: [
				{ effect: 'allow', methods: ['GET'], path: '/items', match_mode: 'exact' },
			],
		},
	});
	if (res.status() === 404) return null; // API-target connects refused here
	expect(res.status(), await res.text()).toBe(201);
	return { agent, sessionId: ((await res.json()) as { session_id: string }).session_id };
}

test('an approval link opens on its own, and a finished connect closes itself', async ({
	page,
	request,
}) => {
	test.setTimeout(120_000);
	const opened = await openRequest(request);
	test.skip(opened == null, 'this deployment refuses API-target connect sessions');
	const { agent, sessionId } = opened!;

	await page.setViewportSize({ width: 1280, height: 900 });
	await dismissFirstRunFor(page, agent.clientId);
	await page.goto(`/app/agents?agent=${agent.clientId}&approve=${sessionId}`);
	const approval = page.getByRole('dialog', { name: /^Approve integration$/ });
	await expect(approval).toBeVisible();
	// Only the approval: no credential inventory opened underneath it.
	await expect(page.getByRole('heading', { name: 'Credentials' })).toHaveCount(0);

	await approval.getByRole('button', { name: /^Continue$/ }).click();
	await approval.getByRole('button', { name: /^Continue$/ }).click();
	await approval.getByLabel(/^API key/).fill('sk-e2e-not-a-real-key');
	await approval.getByRole('button', { name: 'Connect' }).click();
	await expect(approval.getByText(/^Connected to /)).toBeVisible();
	await expect(approval.getByTestId('auto-close-countdown')).toBeVisible();

	// It closes by itself, back onto the Agents page — not the inventory.
	await expect(approval).toBeHidden({ timeout: 9_000 });
	await expect(page.getByRole('heading', { name: 'Credentials' })).toHaveCount(0);
	await expect(page.getByRole('heading', { level: 1, name: 'Agents' })).toBeVisible();
});

test('Mark all as read clears the bell and holds across a reload', async ({ page, request }) => {
	const opened = await openRequest(request);
	test.skip(opened == null, 'this deployment refuses API-target connect sessions');
	await page.goto('/app/agents');
	const bell = page.getByRole('button', { name: /^Notifications \(\d+ unread\)$/ });
	await expect(bell).toBeVisible();
	await bell.click();
	await page.getByRole('button', { name: 'Mark all as read' }).click();
	await expect(page.getByRole('button', { name: 'Notifications', exact: true })).toBeVisible();

	await page.reload();
	await expect(page.getByRole('heading', { level: 1, name: 'Agents' })).toBeVisible();
	await expect(page.getByRole('button', { name: 'Notifications', exact: true })).toBeVisible();

	// Leave nothing open for later runs.
	await request.post(`/connect-sessions/${opened!.sessionId}:reject`, {
		headers: authHeaders(),
		data: { reason: 'e2e cleanup' },
	});
});
