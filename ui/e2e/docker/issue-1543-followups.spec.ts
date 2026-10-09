import { createServer, type Server } from 'http';
import type { AddressInfo } from 'net';
import { test, expect, type Browser, type Page } from '@playwright/test';
import {
	attachNewRuleSet,
	authHeaders,
	bearer,
	bindCredentialToAgent,
	captureConsoleErrors,
	createApiKeyCredential,
	createMember,
	dismissFirstRunFor,
	importInlineApi,
	promoteLatestRevision,
	replaceBindingRules,
	setUserPermissions,
	signInPageAs,
	uniqueSuffix,
} from './helpers';
import { provisionAdminOwnedAgent } from './agent-flow';

/**
 * The #1543 follow-ups, against the real backend: each surface states only what
 * the viewer can know and offers only what the server will accept.
 *
 * Permission-gated cases need a viewer who LACKS a permission, so they sign a
 * second, invited member into its own browser context next to the admin one.
 */

const ALLOW_ALL = [{ effect: 'allow', match_mode: 'regex', path: '.*' }];

/** A browser page signed in as `token`, with none of the admin's storage. */
async function pageAs(browser: Browser, baseURL: string, token: string): Promise<Page> {
	const context = await browser.newContext({
		baseURL,
		storageState: { cookies: [], origins: [] },
	});
	const page = await context.newPage();
	await signInPageAs(page, token);
	return page;
}

function baseUrlOf(info: { project: { use: { baseURL?: string } } }): string {
	const url = info.project.use.baseURL;
	if (!url) throw new Error('the e2e project has no baseURL');
	return url;
}

/** Open an agent's access sidebar from its (single) API tile. */
async function openOnlyTile(page: Page, agentId: string): Promise<void> {
	await page.goto(`/app/agents?agent=${agentId}`);
	await page
		.getByRole('button', { name: /open access details/ })
		.first()
		.click();
	await expect(page.getByRole('button', { name: /^Unbind/ }).first()).toBeVisible();
}

test('a binding governed by a rule set reads its set on the API hub, not its dormant inline rules', async ({
	page,
	request,
}) => {
	test.slow();
	const errors = captureConsoleErrors(page);
	const sfx = uniqueSuffix();
	const vendor = `e2e-hub-${sfx}`;
	const apiName = `hub-${sfx}`;
	await importInlineApi(request, { vendor, apiName });
	const credentialId = await createApiKeyCredential(request, {
		name: `e2e-hub-cred-${sfx}`,
		vendor,
		apiName,
		apiVersion: '1.0.0',
	});

	// Governed: its inline list is empty (default-deny), its attached set allows
	// everything — the broker evaluates the set.
	const governed = await provisionAdminOwnedAgent(request, { name: `e2e-governed-${sfx}` });
	await bindCredentialToAgent(request, governed.clientId, credentialId);
	await attachNewRuleSet(request, credentialId, governed.clientId, {
		name: `e2e-allow-all-${sfx}`,
		rules: ALLOW_ALL,
	});
	// The control: inline rules, none of them an allow — this one IS blocked.
	const blocked = await provisionAdminOwnedAgent(request, { name: `e2e-inline-${sfx}` });
	await bindCredentialToAgent(request, blocked.clientId, credentialId);

	await page.goto(`/app/library/workspace/${vendor}/${apiName}/1.0.0`);
	const access = page.getByTestId('hub-access');
	const governedRow = access.getByRole('link', { name: new RegExp(governed.name) });
	const blockedRow = access.getByRole('link', { name: new RegExp(blocked.name) });

	await expect(blockedRow.getByTestId('hub-access-agent-blocked')).toBeVisible();
	// Settled before asserting the absence, or "still checking" would pass it.
	await expect(governedRow.getByTestId('hub-access-agent-checking')).toHaveCount(0);
	await expect(governedRow).toBeVisible();
	await expect(governedRow.getByTestId('hub-access-agent-blocked')).toHaveCount(0);

	expect(errors, `unexpected console errors:\n${errors.join('\n')}`).toEqual([]);
});

test("Edit and Delete are withheld from a bound credential whose owner the viewer can't read", async ({
	page,
	request,
	browser,
}, info) => {
	const sfx = uniqueSuffix();
	const member = await createMember(request, [
		'agents:read',
		'agents:write',
		'apis:read',
		'credentials:read',
		'credentials:write',
	]);
	// The admin's credential, bound to the MEMBER's agent: the member reads the
	// binding, but `GET /credentials` is owner-scoped and never returns the row.
	const credentialId = await createApiKeyCredential(request, {
		name: `e2e-admins-key-${sfx}`,
		vendor: `e2e-own-${sfx}.example.com`,
	});
	const agent = await provisionAdminOwnedAgent(request, {
		name: `e2e-members-agent-${sfx}`,
		ownerId: member.id,
	});
	await bindCredentialToAgent(request, agent.clientId, credentialId);

	// The admin can read the owner, so the verbs are there for it…
	await dismissFirstRunFor(page, agent.clientId);
	await openOnlyTile(page, agent.clientId);
	await expect(page.getByRole('button', { name: 'Edit credential' })).toBeVisible();
	await expect(page.getByText('Delete credential everywhere')).toBeVisible();

	// …and absent for the member, for whom the ownership is unknown. Unbinding is
	// the agent's own business and stays.
	const memberPage = await pageAs(browser, baseUrlOf(info), member.token);
	await dismissFirstRunFor(memberPage, agent.clientId);
	await openOnlyTile(memberPage, agent.clientId);
	await expect(memberPage.getByRole('button', { name: 'Edit credential' })).toHaveCount(0);
	await expect(memberPage.getByText('Delete credential everywhere')).toHaveCount(0);
	await expect(memberPage.getByText('Unbind from this agent').first()).toBeVisible();
	await memberPage.context().close();
});

test('the API grid degrades behind a status strip when GET /apis fails, and recovers on retry', async ({
	page,
	request,
}) => {
	const sfx = uniqueSuffix();
	const agent = await provisionAdminOwnedAgent(request, { name: `e2e-degrade-${sfx}` });
	const credentialId = await createApiKeyCredential(request, {
		name: `e2e-degrade-key-${sfx}`,
		vendor: `e2e-degrade-${sfx}.example.com`,
	});
	await bindCredentialToAgent(request, agent.clientId, credentialId);
	await dismissFirstRunFor(page, agent.clientId);

	// Only the registry LIST fails; every other read is the real backend's.
	const failApis = (route: Parameters<Parameters<Page['route']>[1]>[0]) =>
		route.request().method() === 'GET'
			? route.fulfill({ status: 500, contentType: 'application/problem+json', body: '{}' })
			: route.continue();
	await page.route((url) => url.pathname === '/apis', failApis);

	await page.goto(`/app/agents?agent=${agent.clientId}`);
	const strip = page.getByTestId('agent-apis-degraded');
	await expect(strip).toBeVisible();
	await expect(strip).toHaveText(/showing without their API details/);
	// The tile is still drawn from the binding itself, and the grid is not
	// replaced by the error card.
	await expect(page.getByRole('button', { name: /open access details/ })).toHaveCount(1);
	await expect(page.getByText(/Couldn.t load the credential details/)).toHaveCount(0);

	await page.unroute((url) => url.pathname === '/apis');
	await strip.getByRole('button', { name: 'Try again' }).click();
	await expect(strip).toHaveCount(0);
	await expect(page.getByRole('button', { name: /open access details/ })).toHaveCount(1);
});

test('a viewer without apis:read gets the grid and sends no GET /apis', async ({
	request,
	browser,
}, info) => {
	const sfx = uniqueSuffix();
	const member = await createMember(request, [
		'agents:read',
		'agents:write',
		'credentials:read',
		'credentials:write',
	]);
	const agent = await provisionAdminOwnedAgent(request, {
		name: `e2e-noapis-${sfx}`,
		ownerId: member.id,
	});
	const credentialId = await createApiKeyCredential(request, {
		name: `e2e-noapis-key-${sfx}`,
		vendor: `e2e-noapis-${sfx}.example.com`,
	});
	await bindCredentialToAgent(request, agent.clientId, credentialId);

	const memberPage = await pageAs(browser, baseUrlOf(info), member.token);
	await dismissFirstRunFor(memberPage, agent.clientId);
	const apisReads: string[] = [];
	memberPage.on('request', (req) => {
		if (new URL(req.url()).pathname === '/apis') apisReads.push(req.url());
	});
	await memberPage.goto(`/app/agents?agent=${agent.clientId}`);
	await expect(memberPage.getByRole('button', { name: /open access details/ })).toHaveCount(1);
	// A 403 is a standing fact, not a failure: no strip, no dead "Try again".
	await expect(memberPage.getByTestId('agent-apis-degraded')).toHaveCount(0);
	await expect(memberPage.getByText(/Couldn.t load the credential/)).toHaveCount(0);
	expect(apisReads).toEqual([]);
	await memberPage.context().close();
});

test('the credential inventory reads the agent roster only for a viewer with agents:read', async ({
	page,
	request,
	browser,
}, info) => {
	const member = await createMember(request, ['credentials:read', 'credentials:write']);
	const sfx = uniqueSuffix();
	await request
		.post('/credentials', {
			headers: bearer(member.token),
			data: {
				type: 'api_key',
				name: `e2e-roster-key-${sfx}`,
				api: { vendor: `e2e-roster-${sfx}.example.com` },
				key: 'sk-e2e',
				location: 'header',
				field_name: 'X-Api-Key',
			},
		})
		.then((res) => expect(res.status()).toBe(201));

	// The admin holds agents:read: the roster is read and "Unbound" offered.
	await page.goto('/app/agents?credentials=1');
	const adminUsage = page.getByRole('group', { name: 'Filter by agent usage' });
	await expect(adminUsage.getByRole('button', { name: /^Unbound/ })).toBeVisible();

	const memberPage = await pageAs(browser, baseUrlOf(info), member.token);
	const rosterReads: string[] = [];
	memberPage.on('request', (req) => {
		if (new URL(req.url()).pathname === '/agents') rosterReads.push(req.url());
	});
	await memberPage.goto('/app/agents?credentials=1');
	await expect(memberPage.getByText(`e2e-roster-key-${sfx}`)).toBeVisible();
	const usage = memberPage.getByRole('group', { name: 'Filter by agent usage' });
	await expect(usage.getByRole('button', { name: 'Any agent' })).toBeVisible();
	await expect(usage.getByRole('button', { name: /^Unbound/ })).toHaveCount(0);
	// No "used by N agents" claim built from a roster it could not read.
	await expect(memberPage.getByTestId('cred-used-by')).toHaveCount(0);
	expect(rosterReads).toEqual([]);
	await memberPage.context().close();
});

test('OAuth Connect and "Finish setup" are offered only with credentials:write', async ({
	request,
	browser,
}, info) => {
	const member = await createMember(request, [
		'agents:read',
		'credentials:read',
		'credentials:write',
	]);
	const sfx = uniqueSuffix();
	const name = `e2e-oauth-${sfx}`;
	// The member's own unfinished authorization-code sign-in.
	const created = await request.post('/credentials', {
		headers: bearer(member.token),
		data: {
			type: 'oauth2',
			name,
			api: { vendor: `e2e-oauth-${sfx}.example.com` },
			grant_type: 'authorization_code',
			authorize_url: 'https://auth.example.com/authorize',
			token_url: 'https://auth.example.com/token',
			client_id: 'e2e-client',
			client_secret: 'e2e-client-secret', // pragma: allowlist secret
		},
	});
	expect(created.status(), await created.text()).toBe(201);

	const memberPage = await pageAs(browser, baseUrlOf(info), member.token);
	const bellRow = async () => {
		await memberPage.getByRole('button', { name: /^Notifications/ }).click();
		const dialog = memberPage.getByRole('dialog', { name: /Notifications/ });
		const row = dialog
			.getByRole('listitem')
			.filter({ hasText: `${name} sign-in isn't finished` });
		await expect(row).toBeVisible();
		return row;
	};

	// A writer: Connect on the card, Finish setup in the bell.
	await memberPage.goto('/app/agents?credentials=1');
	await expect(memberPage.getByRole('button', { name: `Connect ${name}` })).toBeVisible();
	await expect(memberPage.getByRole('button', { name: 'Add credential' })).toBeVisible();
	await memberPage.keyboard.press('Escape');
	await expect((await bellRow()).getByRole('link', { name: 'Finish setup' })).toBeVisible();

	// The same member as a reader: the row stays, the verbs go.
	await setUserPermissions(request, member.id, ['agents:read', 'credentials:read']);
	await memberPage.goto('/app/agents?credentials=1');
	await expect(memberPage.getByText(name).first()).toBeVisible();
	await expect(memberPage.getByRole('button', { name: `Connect ${name}` })).toHaveCount(0);
	await expect(memberPage.getByRole('button', { name: 'Add credential' })).toHaveCount(0);
	await memberPage.keyboard.press('Escape');
	await expect((await bellRow()).getByRole('link', { name: 'Finish setup' })).toHaveCount(0);
	await memberPage.context().close();
});

test('a right-to-left name carrying a direction override is isolated from the copy around it', async ({
	page,
	request,
}) => {
	const sfx = uniqueSuffix();
	// Hebrew, an RLO (U+202E) that is never closed, and length: the override
	// would otherwise run to the end of the heading and reverse its copy.
	const name = `סוכן-‮בדיקה-${sfx}-${'x'.repeat(60)}`;
	const agent = await provisionAdminOwnedAgent(request, { name });
	await dismissFirstRunFor(page, agent.clientId);

	await page.goto(`/app/agents?agent=${agent.clientId}`);
	const heading = page.getByRole('heading', { name: /can reach nothing yet/ });
	await expect(heading).toBeVisible();
	const isolate = heading.locator('bdi[dir="auto"]');
	await expect(isolate).toHaveText(name);
	expect(await isolate.evaluate((el) => getComputedStyle(el).unicodeBidi)).toBe('isolate');
	// The product copy sits OUTSIDE the isolate, as its own run.
	expect(
		await heading.evaluate((el) =>
			Array.from(el.childNodes)
				.filter((n) => n.nodeType === Node.TEXT_NODE)
				.map((n) => n.textContent)
				.join(''),
		),
	).toContain('can reach nothing yet');

	// The strip tab carries the name on its own `dir="auto"` element.
	const tabName = page
		.getByRole('tab', { name: new RegExp(sfx) })
		.locator('span[dir="auto"]', { hasText: sfx });
	await expect(tabName).toHaveCount(1);
	expect(await tabName.evaluate((el) => getComputedStyle(el).unicodeBidi)).toBe('isolate');
});

/**
 * The broker is its own surface (`JENTIC__APPS=broker`), outside the combined
 * app. CI starts one on :8100 with loopback egress allowed; a local run without
 * it skips this test rather than failing.
 */
const BROKER_URL = process.env.E2E_BROKER_URL ?? 'http://127.0.0.1:8100';

test.describe('credential access through the broker', () => {
	let upstream: Server;
	let upstreamPort = 0;

	test.beforeAll(async ({ request }) => {
		const health = await request.get(`${BROKER_URL}/health`).catch(() => null);
		test.skip(!health?.ok(), `no broker at ${BROKER_URL} — start one with JENTIC__APPS=broker`);
		upstream = createServer((_req, res) => {
			res.writeHead(200, { 'content-type': 'application/json' });
			res.end('{"ok":true}');
		});
		await new Promise<void>((resolve) => upstream.listen(0, '127.0.0.1', resolve));
		upstreamPort = (upstream.address() as AddressInfo).port;
	});

	test.afterAll(async () => {
		if (upstream) await new Promise((resolve) => upstream.close(resolve));
	});

	test('the rail names the agent behind a credential access and opens the inventory', async ({
		page,
		request,
	}) => {
		test.slow();
		const sfx = uniqueSuffix();
		const vendor = `e2e-broker-${sfx}`;
		const apiName = `loop-${sfx}`;
		// A unique path so reruns against one DB never make the URL ambiguous.
		const path = `/e2e-${sfx}/ping`;
		await importInlineApi(request, {
			vendor,
			apiName,
			content: JSON.stringify({
				openapi: '3.0.0',
				info: { title: apiName, version: '1.0.0' },
				servers: [{ url: `http://127.0.0.1:${upstreamPort}` }],
				paths: {
					[path]: {
						get: { operationId: 'ping', responses: { '200': { description: 'ok' } } },
					},
				},
			}),
		});
		await promoteLatestRevision(request, { vendor, name: apiName, version: '1.0.0' });
		const credentialName = `e2e-broker-key-${sfx}`;
		const credentialId = await createApiKeyCredential(request, {
			name: credentialName,
			vendor,
			apiName,
			apiVersion: '1.0.0',
		});
		const agent = await provisionAdminOwnedAgent(request, { name: `e2e-caller-${sfx}` });
		await bindCredentialToAgent(request, agent.clientId, credentialId);
		await replaceBindingRules(request, credentialId, agent.clientId, ALLOW_ALL);

		const call = await request.get(`${BROKER_URL}/http://127.0.0.1:${upstreamPort}${path}`, {
			headers: { authorization: `Bearer ${agent.accessToken}` },
		});
		expect(call.status(), await call.text()).toBe(200);

		// The stored summary names the agent, not its id…
		const events = await request.get('/events?limit=20', { headers: authHeaders() });
		const accessed = (
			(await events.json()) as { data: { type: string; summary: string }[] }
		).data.find((e) => e.type === 'credential.accessed' && e.summary.includes(credentialName));
		expect(accessed?.summary).toContain(`accessed by '${agent.name}'`);
		expect(accessed?.summary).not.toContain(agent.clientId);

		// …and so does the rail row, which opens the credential inventory rather
		// than an agent page.
		await page.goto('/app/agents');
		await page.getByRole('button', { name: /^Show live activity/ }).click();
		const rail = page.getByRole('complementary', { name: 'Activity' });
		const row = rail.getByRole('link', {
			name: new RegExp(`${credentialName}.*accessed by .${agent.name}`),
		});
		await expect(row).toBeVisible();
		await row.click();
		await expect(page).toHaveURL(/\/app\/agents\?credentials=1/);
		await expect(page.getByTestId('sheet-primitive')).toBeVisible();
	});
});
