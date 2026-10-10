import { test, expect, type APIRequestContext, type Browser, type Page } from '@playwright/test';
import {
	authHeaders,
	bearer,
	bindCredentialToAgent,
	captureConsoleErrors,
	createApiKeyCredential,
	createMember,
	dismissFirstRunFor,
	importInlineApi,
	promoteLatestRevision,
	replaceAgentPermissions,
	replaceBindingRules,
	signInPageAs,
	uniqueSuffix,
} from './helpers';
import { provisionAdminOwnedAgent, type AgentIdentity } from './agent-flow';
import { setAgentOwner } from './db';

/**
 * The redesigned Agents page against the real backend: the agent strip and its
 * picker, the sticky agent card, the "Can call" rows and cards, the Add APIs
 * flow's Access step, permission-gated verbs, and the connect-request entry
 * points (`?approve=` and "Waiting for you") over the new layout.
 *
 * One rich agent (four bound APIs, one of them Blocked) is shared by the
 * read-mostly cases; cases that write make their own agents.
 */

test.describe.configure({ mode: 'serial' });

const ALLOW_ALL = [{ effect: 'allow', match_mode: 'regex', path: '.*' }];

/** A minimal OpenAPI doc with an API-key scheme, so a connect session can target it. */
function apiKeySpec(title: string, host: string): string {
	return JSON.stringify({
		openapi: '3.0.0',
		info: { title, version: '1.0.0' },
		// Its own host: a live revision owns its host, so reruns must not share one.
		servers: [{ url: `https://${host}` }],
		components: {
			securitySchemes: { apiKey: { type: 'apiKey', in: 'header', name: 'X-Api-Key' } },
		},
		security: [{ apiKey: [] }],
		paths: {
			'/get': {
				get: {
					operationId: 'sampleGet',
					summary: 'sample',
					responses: { '200': { description: 'ok' } },
				},
			},
		},
	});
}

interface BoundApi {
	vendor: string;
	apiName: string;
	title: string;
	credentialId: string;
	credentialName: string;
}

/** Import an API, make a credential for it, and bind it to `agentId`. */
async function bindNewApi(
	request: APIRequestContext,
	agentId: string,
	label: string,
	rules: Record<string, unknown>[] | null,
): Promise<BoundApi> {
	const sfx = uniqueSuffix();
	const vendor = `e2e-${label}-${sfx}.example.com`;
	const apiName = `${label}-${sfx}`;
	const title = `E2e ${label} ${sfx}`;
	await importInlineApi(request, { vendor, apiName, title });
	const credentialName = `e2e-${label}-key-${sfx}`;
	const credentialId = await createApiKeyCredential(request, {
		name: credentialName,
		vendor,
		apiName,
		apiVersion: '1.0.0',
	});
	await bindCredentialToAgent(request, agentId, credentialId);
	if (rules) await replaceBindingRules(request, credentialId, agentId, rules);
	return { vendor, apiName, title, credentialId, credentialName };
}

/** The strip tab for an agent. */
function stripTab(page: Page, agentId: string) {
	return page.getByTestId('agent-strip').locator(`[role="tab"][data-agent-id="${agentId}"]`);
}

/** The row for a bound API in the "Can call" list, by its (unique) credential name. */
function rowFor(page: Page, credentialName: string) {
	return page.getByTestId('api-tile').filter({ hasText: credentialName });
}

/** Open a row's access sidebar and return it (named by the API's display title). */
async function manageAccess(page: Page, row: ReturnType<typeof rowFor>) {
	const manage = row.getByTestId('row-manage-access');
	const label = (await manage.getAttribute('aria-label')) ?? '';
	const title = /^Manage access for (.+?)(?: \(|$)/.exec(label)?.[1];
	if (!title) throw new Error(`unexpected Manage access label: ${label}`);
	await manage.click();
	const sheet = page.getByRole('dialog', { name: title });
	await expect(sheet).toBeVisible();
	return sheet;
}

async function openAgent(page: Page, agentId: string): Promise<void> {
	await dismissFirstRunFor(page, agentId);
	await page.goto(`/app/agents?agent=${agentId}`);
	await expect(page.getByTestId('agent-card')).toBeVisible();
}

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

let rich: AgentIdentity;
let apis: BoundApi[] = [];
let blockedApi: BoundApi;

test('setup: an agent with four bound APIs, one of them Blocked', async ({ request }) => {
	test.setTimeout(240_000);
	rich = await provisionAdminOwnedAgent(request, { name: `e2e-rich-${uniqueSuffix()}` });
	for (const label of ['alpha', 'bravo', 'charlie']) {
		apis.push(await bindNewApi(request, rich.clientId, label, ALLOW_ALL));
	}
	blockedApi = await bindNewApi(request, rich.clientId, 'blocked', null);
	apis = [...apis, blockedApi];
});

test('the strip: arrows move focus, Enter switches, overflow opens the picker, ⌘K filters and Esc returns focus', async ({
	page,
	request,
}) => {
	test.setTimeout(180_000);
	const errors = captureConsoleErrors(page);
	// Enough agents that a 1280px strip overflows.
	const sfx = uniqueSuffix();
	const fleet: AgentIdentity[] = [];
	for (let i = 0; i < 14; i++) {
		fleet.push(await provisionAdminOwnedAgent(request, { name: `e2e-fleet-${sfx}-${i}` }));
	}
	await page.setViewportSize({ width: 1280, height: 900 });
	await openAgent(page, fleet[0].clientId);

	// Arrows move focus along the tablist; Enter makes the focused tab current.
	const first = stripTab(page, fleet[0].clientId);
	await expect(first).toHaveAttribute('aria-selected', 'true');
	await first.focus();
	await page.keyboard.press('ArrowRight');
	const focusedId = await page.evaluate(
		() => document.activeElement?.getAttribute('data-agent-id') ?? null,
	);
	expect(focusedId).not.toBeNull();
	expect(focusedId).not.toBe(fleet[0].clientId);
	await page.keyboard.press('Enter');
	await expect(stripTab(page, focusedId!)).toHaveAttribute('aria-selected', 'true');
	await expect(page).toHaveURL(new RegExp(`agent=${focusedId}`));

	// Overflow: the strip ends in a "+N more" button that opens the picker.
	const more = page.getByTestId('strip-more');
	await expect(more).toHaveText(/^\+\d+$/);
	await more.click();
	const picker = page.getByRole('dialog', { name: 'Switch agent' });
	await expect(picker).toBeVisible();
	await expect(picker.getByRole('combobox', { name: 'Find an agent' })).toBeFocused();
	await page.keyboard.press('Escape');
	await expect(picker).toBeHidden();
	await expect(more).toBeFocused();

	// ⌘K (Ctrl K off Apple) opens it from anywhere; typing filters; Enter selects.
	await page.locator('body').click({ position: { x: 5, y: 5 } });
	await page.keyboard.press('ControlOrMeta+k');
	await expect(picker).toBeVisible();
	const target = fleet[11];
	await picker.getByRole('combobox', { name: 'Find an agent' }).fill(target.name);
	await expect(picker.getByRole('option')).toHaveCount(1);
	await page.keyboard.press('Enter');
	await expect(picker).toBeHidden();
	await expect(page).toHaveURL(new RegExp(`agent=${target.clientId}`));
	await expect(stripTab(page, target.clientId)).toHaveAttribute('aria-selected', 'true');

	expect(errors, `unexpected console errors:\n${errors.join('\n')}`).toEqual([]);
});

test('the agent card sticks under the strip on scroll and unsticks at the top', async ({
	page,
}) => {
	// A short viewport so four rows overflow it.
	await page.setViewportSize({ width: 1280, height: 520 });
	await openAgent(page, rich.clientId);
	await expect(page.getByTestId('api-tile')).toHaveCount(apis.length);
	const card = page.getByTestId('agent-card');
	await expect(card).toHaveAttribute('data-stuck', 'false');

	await page.evaluate(() => document.getElementById('app-scroll')?.scrollTo({ top: 600 }));
	await expect(card).toHaveAttribute('data-stuck', 'true');
	// Stuck, the stats fold away and the grabber can peek them back.
	await expect(card).toHaveAttribute('data-stats', 'folded');

	await page.evaluate(() => document.getElementById('app-scroll')?.scrollTo({ top: 0 }));
	await expect(card).toHaveAttribute('data-stuck', 'false');
});

test('rows pin open with their chevron and Esc unpins; Manage access opens the sidebar, on the rules when Blocked', async ({
	page,
}) => {
	const errors = captureConsoleErrors(page);
	await page.setViewportSize({ width: 1280, height: 900 });
	await openAgent(page, rich.clientId);

	const row = rowFor(page, apis[0].credentialName);
	await expect(row).toBeVisible();
	await expect(row).not.toHaveAttribute('data-pinned', /.*/);
	await row.getByTestId('row-toggle').click();
	await expect(row).toHaveAttribute('data-pinned', 'true');
	await expect(row.getByTestId('api-row-reveal')).toBeVisible();
	await page.keyboard.press('Escape');
	await expect(row).not.toHaveAttribute('data-pinned', /.*/);

	// An allowed binding: the sidebar opens on its overview.
	const sheet = await manageAccess(page, row);
	await expect(sheet.getByRole('button', { name: /^Unbind/ }).first()).toBeVisible();
	await page.keyboard.press('Escape');
	await expect(sheet).toBeHidden();

	// A Blocked binding (no rules): Manage access lands on "Add rule".
	const blocked = rowFor(page, blockedApi.credentialName);
	await expect(blocked).toHaveAttribute('data-status', 'blocked-no-rules');
	const blockedSheet = await manageAccess(page, blocked);
	await expect(blockedSheet.locator('[data-rule-add]')).toBeFocused();
	await page.keyboard.press('Escape');

	expect(errors, `unexpected console errors:\n${errors.join('\n')}`).toEqual([]);
});

test('the list/cards lens persists across a reload', async ({ page }) => {
	await page.setViewportSize({ width: 1280, height: 900 });
	await openAgent(page, rich.clientId);
	const lens = page.getByTestId('api-view-toggle');
	await expect(lens.getByRole('radio', { name: 'List view' })).toHaveAttribute(
		'aria-checked',
		'true',
	);
	await lens.getByRole('radio', { name: 'Cards view' }).click();
	await expect(page.getByTestId('api-card')).toHaveCount(apis.length);

	await page.reload();
	await expect(page.getByTestId('api-card')).toHaveCount(apis.length);
	await expect(
		page.getByTestId('api-view-toggle').getByRole('radio', { name: 'Cards view' }),
	).toHaveAttribute('aria-checked', 'true');

	// Back to the list, so later cases (and runs) start where the default is.
	await page.getByTestId('api-view-toggle').getByRole('radio', { name: 'List view' }).click();
	await expect(page.getByTestId('api-tile')).toHaveCount(apis.length);
});

test('Add APIs → Access: Set up later warns first; a preset binds and the row is not left Blocked', async ({
	page,
	request,
}) => {
	test.setTimeout(180_000);
	const errors = captureConsoleErrors(page);
	const sfx = uniqueSuffix();
	const agent = await provisionAdminOwnedAgent(request, { name: `e2e-access-${sfx}` });
	const vendor = `e2e-access-${sfx}.example.com`;
	const apiName = `access-${sfx}`;
	await importInlineApi(request, { vendor, apiName });
	await createApiKeyCredential(request, {
		name: `e2e-access-key-${sfx}`,
		vendor,
		apiName,
		apiVersion: '1.0.0',
	});

	await page.setViewportSize({ width: 1280, height: 900 });
	await openAgent(page, agent.clientId);
	await page.getByTestId('card-add-api').click();
	const tray = page.getByRole('dialog', { name: 'Add APIs' });
	await expect(tray).toBeVisible();
	await tray.getByRole('checkbox', { name: new RegExp(`/${apiName}@1\\.0\\.0`) }).click();
	await tray.getByRole('button', { name: 'Continue' }).click();

	await page.getByRole('button', { name: 'Use this credential' }).click();
	const step = page.getByTestId('queue-access-step');
	await expect(step).toBeVisible();

	// Set up later is a confirm, not a click-through: it says every call is denied.
	await step.getByRole('button', { name: 'Set up later' }).click();
	const confirm = step.getByTestId('queue-later-confirm');
	await expect(confirm).toContainText('every call is denied');
	await confirm.getByRole('button', { name: 'Keep setting up' }).click();
	await expect(confirm).toBeHidden();

	// A preset answers it; adding binds AND saves the rules.
	await step.getByRole('radio', { name: /Allow all operations/ }).check();
	await step.getByRole('button', { name: new RegExp(`^Add `) }).click();
	await expect(page.getByTestId('queue-done-pane')).toBeVisible();
	await expect(page.getByTestId('queue-done-pane')).toContainText('1 API added');
	await page.getByRole('button', { name: 'Done' }).click();

	const row = rowFor(page, `e2e-access-key-${sfx}`);
	await expect(row).toBeVisible();
	await expect(row).not.toHaveAttribute('data-status', /^blocked/);
	await expect(row).not.toContainText('Blocked');

	expect(errors, `unexpected console errors:\n${errors.join('\n')}`).toEqual([]);
});

test('a member without agents:write or credentials:write gets no binding verbs', async ({
	browser,
	request,
}, info) => {
	const member = await createMember(request, ['agents:read', 'apis:read', 'credentials:read']);
	// Owner-scoped reads: the member sees the agent once it owns it.
	const agent = await provisionAdminOwnedAgent(request, {
		name: `e2e-viewer-${uniqueSuffix()}`,
	});
	const api = await bindNewApi(request, agent.clientId, 'viewer', ALLOW_ALL);
	await setAgentOwner(agent.clientId, member.id);

	const viewer = await pageAs(browser, baseUrlOf(info), member.token);
	await viewer.setViewportSize({ width: 1280, height: 900 });
	await openAgent(viewer, agent.clientId);

	const row = rowFor(viewer, api.credentialName);
	await expect(row).toBeVisible();
	await expect(viewer.getByTestId('card-add-api')).toBeDisabled();
	const sheet = await manageAccess(viewer, row);
	await expect(sheet.getByRole('button', { name: /^Unbind/ })).toHaveCount(0);
	await expect(sheet.getByRole('button', { name: /^Pause/ })).toHaveCount(0);
	await expect(sheet.locator('[data-rule-add]')).toHaveCount(0);
	await viewer.context().close();
});

test('an agent connect request: "Waiting for you" lists it, and ?approve= opens the approval over the page', async ({
	page,
	request,
}) => {
	test.setTimeout(120_000);
	const sfx = uniqueSuffix();
	const vendor = `e2e-connect-${sfx}`;
	const apiName = `connect-${sfx}`;
	await importInlineApi(request, {
		vendor,
		apiName,
		content: apiKeySpec(`E2e connect ${sfx}`, `${vendor}.example.com`),
	});
	// An inline import lands as a draft; a connect session targets the live revision.
	await promoteLatestRevision(request, { vendor, name: apiName, version: '1.0.0' });
	const agent = await provisionAdminOwnedAgent(request, { name: `e2e-connector-${sfx}` });
	await replaceAgentPermissions(request, agent.clientId, ['credentials:connect']);
	const res = await request.post('/integrations:connect', {
		headers: bearer(agent.accessToken),
		data: { api: { vendor, name: apiName, version: '1.0.0' }, reason: 'e2e' },
	});
	test.skip(
		res.status() === 404 || res.status() === 409,
		`this deployment cannot open an API-target connect session (${res.status()}: ${await res.text()})`,
	);
	expect(res.status(), await res.text()).toBe(201);
	const sessionId = ((await res.json()) as { session_id: string }).session_id;

	await page.setViewportSize({ width: 1280, height: 900 });
	await openAgent(page, agent.clientId);
	const waiting = page.getByRole('region', { name: 'Waiting for you' });
	await expect(waiting).toBeVisible();
	const review = waiting.getByRole('link', {
		name: new RegExp(`^Review ${agent.name}'s request to connect`),
	});
	await expect(review).toHaveAttribute('href', new RegExp(`approve=${sessionId}`));

	await page.goto(`/app/agents?agent=${agent.clientId}&approve=${sessionId}`);
	await expect(page.getByRole('dialog', { name: /^Approve integration$/ })).toBeVisible();
	await expect(page).not.toHaveURL(/approve=/);
	await expect(page).toHaveURL(new RegExp(`agent=${agent.clientId}`));
	// The redesigned page is still under it.
	await expect(page.getByTestId('agent-strip')).toBeAttached();

	// Leave nothing open for later runs.
	const rejected = await request.post(`/connect-sessions/${sessionId}:reject`, {
		headers: authHeaders(),
		data: { reason: 'e2e cleanup' },
	});
	expect(rejected.ok(), await rejected.text()).toBeTruthy();
});
