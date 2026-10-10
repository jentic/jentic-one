import { test, expect, type Locator, type Page } from '@playwright/test';
import {
	authHeaders,
	bearer,
	dismissFirstRunFor,
	importInlineApi,
	promoteLatestRevision,
	replaceAgentPermissions,
	uniqueSuffix,
} from './helpers';
import { provisionAdminOwnedAgent } from './agent-flow';

/**
 * Reviewing an agent's connect request in a sheet shorter than the review: the
 * decision (Not now / Reject / Continue) stays pinned to the sheet's bottom edge
 * while the review above it scrolls, instead of scrolling away with it.
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
				get: {
					operationId: 'listItems',
					summary: 'List items',
					responses: { '200': { description: 'ok' } },
				},
			},
		},
	});
}

/** Is `el` fully inside the viewport? */
async function inViewport(page: Page, el: Locator): Promise<boolean> {
	const box = await el.boundingBox();
	const vp = page.viewportSize();
	return box != null && vp != null && box.y >= 0 && box.y + box.height <= vp.height;
}

test('the approval’s decision buttons stay in view while the review scrolls', async ({
	page,
	request,
}) => {
	test.setTimeout(120_000);
	const sfx = uniqueSuffix();
	const vendor = `e2e-sticky-${sfx}`;
	const apiName = `sticky-${sfx}`;
	await importInlineApi(request, {
		vendor,
		apiName,
		content: apiKeySpec(`Sticky ${sfx}`, `${vendor}.example.com`),
	});
	await promoteLatestRevision(request, { vendor, name: apiName, version: '1.0.0' });
	const agent = await provisionAdminOwnedAgent(request, { name: `e2e-sticky-${sfx}` });
	await replaceAgentPermissions(request, agent.clientId, ['credentials:connect']);
	const res = await request.post('/integrations:connect', {
		headers: bearer(agent.accessToken),
		data: {
			api: { vendor, name: apiName, version: '1.0.0' },
			reason: 'A long enough review to scroll in a short sheet. '.repeat(8),
			requested_permission_rules: [
				{ effect: 'allow', methods: ['GET'], path: '/items', match_mode: 'exact' },
				{ effect: 'allow', methods: ['GET'], path: '/items/{id}', match_mode: 'exact' },
				{ effect: 'deny', methods: ['DELETE'], path: '.*', match_mode: 'regex' },
			],
		},
	});
	expect(res.status(), await res.text()).toBe(201);
	const sessionId = ((await res.json()) as { session_id: string }).session_id;

	// Short enough that the review cannot fit above its buttons.
	await page.setViewportSize({ width: 1280, height: 560 });
	await dismissFirstRunFor(page, agent.clientId);
	await page.goto(`/app/agents?agent=${agent.clientId}&approve=${sessionId}`);
	const dialog = page.getByRole('dialog', { name: /^Approve integration$/ });
	await expect(dialog).toBeVisible();
	const decide = dialog.getByRole('button', { name: /^(Continue|Approve)/ }).first();
	await expect(decide).toBeVisible();

	// The body really scrolls here, or this proves nothing.
	const body = dialog.locator('.overflow-y-auto').first();
	const scrolls = await body.evaluate((el) => el.scrollHeight > el.clientHeight + 40);
	expect(scrolls, 'the review should overflow a 560px sheet').toBe(true);

	await body.evaluate((el) => el.scrollTo({ top: 0 }));
	expect(await inViewport(page, decide)).toBe(true);
	await body.evaluate((el) => el.scrollTo({ top: el.scrollHeight }));
	expect(await inViewport(page, decide)).toBe(true);
	await expect(dialog.getByRole('button', { name: /^Not now$/ })).toBeVisible();

	await request.post(`/connect-sessions/${sessionId}:reject`, {
		headers: authHeaders(),
		data: { reason: 'e2e cleanup' },
	});
});
