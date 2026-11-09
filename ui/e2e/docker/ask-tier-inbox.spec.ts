import { test, expect, type APIRequestContext, type Locator, type Page } from '@playwright/test';
import { authHeaders, TOKEN_STORAGE_KEY, uniqueSuffix } from './helpers';
import { provisionAdminOwnedAgent, type AgentIdentity } from './agent-flow';

/**
 * The ask tier's human loop in the UI (real backend).
 *
 * An agent's call that matches an Ask (`require-approval`) rule is held by the
 * real broker; the UI must then show it where a decider looks — the bell, the
 * waiting card on the agent and the pending-count badge — let them decide it
 * in the review pop-up (opened from the card or from the broker's
 * `review_url`), and clear every one of those signals once the hold settles:
 * approved (the agent reads the result), denied with a reason, or expired by
 * the worker's sweep. A viewer the server does not show it to (not the agent's
 * owner, not an org admin) sees none of them, whatever their jobs permissions.
 *
 * Needs the ask-tier stack (app + standalone broker + recorded upstream), not
 * the plain `make start-app` boot, so it skips unless ASK_E2E_BROKER_URL and
 * ASK_E2E_UPSTREAM_URL are set. Run it on its own ports and Postgres:
 *
 *   E2E_DIR=/tmp/ask-e2e-2b PG_PORT=55550 PG_CONTAINER=pg-ask-2b \
 *     APP_PORT=55551 BROKER_PORT=55552 UPSTREAM_PORT=55553 \
 *     APPROVAL_TTL_S=60 E2E_BUILD_UI=1 tests/e2e_manual/ask_tier/stack.sh up
 *   cd ui && E2E_BASE_URL=http://127.0.0.1:55551 \
 *     ASK_E2E_BROKER_URL=http://127.0.0.1:55552 ASK_E2E_UPSTREAM_URL=http://127.0.0.1:55553 \
 *     E2E_DB_PORT=55550 E2E_DB_NAME=jentic_ask APPROVAL_TTL_S=60 \
 *     npx playwright test -c playwright.docker.config.ts ask-tier-inbox
 *   E2E_DIR=/tmp/ask-e2e-2b PG_CONTAINER=pg-ask-2b APP_PORT=55551 BROKER_PORT=55552 \
 *     UPSTREAM_PORT=55553 tests/e2e_manual/ask_tier/stack.sh down
 *
 * APPROVAL_TTL_S must match the stack's: the expiry case waits it out.
 */

const BROKER = process.env.ASK_E2E_BROKER_URL ?? '';
const UPSTREAM = process.env.ASK_E2E_UPSTREAM_URL ?? '';
const TTL_S = Number(process.env.APPROVAL_TTL_S ?? '60');

test.skip(
	!BROKER || !UPSTREAM,
	'needs the ask-tier stack (ASK_E2E_BROKER_URL, ASK_E2E_UPSTREAM_URL)',
);
test.describe.configure({ mode: 'serial' });

const API = { vendor: 'ask-e2e-ui', name: 'ask-tier-e2e', version: '1.0.0' };

/** GET /items and POST /orders are held for review; everything else runs. */
const ASK_RULES = [
	{ effect: 'require-approval', methods: ['GET'], path: '^/items$', match_mode: 'regex' },
	{ effect: 'require-approval', methods: ['POST'], path: '^/orders$', match_mode: 'regex' },
	{ effect: 'allow', path: '.*', match_mode: 'regex' },
];

interface Held {
	heldExecutionId: string;
	/** The agent whose call was held — the Agents page selects it by this id. */
	agentId: string;
	reviewPath: string;
}

/** Poll a held execution until its `execution_status` is one of `statuses`. */
async function waitHeld(
	request: APIRequestContext,
	heldExecutionId: string,
	statuses: string[],
	timeoutMs = 60_000,
): Promise<Record<string, unknown>> {
	const deadline = Date.now() + timeoutMs;
	let held: Record<string, unknown> = {};
	while (Date.now() < deadline) {
		const res = await request.get(`/executions/holds/${heldExecutionId}`, {
			headers: authHeaders(),
		});
		expect(res.ok(), `GET /executions/holds/${heldExecutionId}: ${res.status()}`).toBeTruthy();
		held = await res.json();
		if (statuses.includes(String(held.execution_status))) return held;
		await new Promise((r) => setTimeout(r, 500));
	}
	throw new Error(
		`held execution ${heldExecutionId} never reached ${statuses.join('/')}; last ${String(held.execution_status)}`,
	);
}

async function waitJob(
	request: APIRequestContext,
	jobId: string,
	statuses: string[],
	timeoutMs = 60_000,
): Promise<Record<string, unknown>> {
	const deadline = Date.now() + timeoutMs;
	let job: Record<string, unknown> = {};
	while (Date.now() < deadline) {
		const res = await request.get(`/jobs/${jobId}`, { headers: authHeaders() });
		expect(res.ok(), `GET /jobs/${jobId}: ${res.status()}`).toBeTruthy();
		job = await res.json();
		if (statuses.includes(String(job.status))) return job;
		await new Promise((r) => setTimeout(r, 500));
	}
	throw new Error(`job ${jobId} never reached ${statuses.join('/')}; last ${String(job.status)}`);
}

/** Import the upstream's spec under {@link API} and promote it, once. */
async function importAskApi(request: APIRequestContext): Promise<void> {
	const base = `/apis/${API.vendor}/${API.name}/${API.version}`;
	const existing = await request.get(base, { headers: authHeaders() });
	if (existing.ok() && (await existing.json()).current_revision_id) return;
	const res = await request.post('/apis', {
		headers: authHeaders(),
		data: { sources: [{ type: 'url', url: `${UPSTREAM}/specs/ask.json`, vendor: API.vendor }] },
	});
	expect(res.status(), `import: ${await res.text()}`).toBe(202);
	const jobId = (await res.json()).job_id as string;
	const job = await waitJob(request, jobId, ['completed', 'failed'], 120_000);
	expect(job.status, JSON.stringify(job)).toBe('completed');
	const result = await (
		await request.get(`/jobs/${jobId}/result`, { headers: authHeaders() })
	).json();
	const revision = result.revisions[0];
	// The spec imports under the vendor given and its own title-derived name.
	const at = `/apis/${revision.api.vendor}/${revision.api.name}/${revision.api.version}`;
	const promoted = await request.post(`${at}/revisions/${revision.revision_id}:promote`, {
		headers: authHeaders(),
	});
	expect(promoted.ok(), `promote: ${await promoted.text()}`).toBeTruthy();
	API.name = revision.api.name;
	API.version = revision.api.version;
}

/** A static bearer credential for the API, bound to the agent with the ask rules. */
async function bindWithAskRules(request: APIRequestContext, agentId: string): Promise<void> {
	const created = await request.post('/credentials', {
		headers: authHeaders(),
		data: {
			type: 'bearer_token',
			name: `ask-ui-${uniqueSuffix()}`,
			api: API,
			provider: 'static',
			token: 'upstream-secret-ui',
		},
	});
	expect(created.status(), `create credential: ${await created.text()}`).toBe(201);
	const credentialId = (await created.json()).credential.credential_id as string;
	const bound = await request.post(`/agents/${agentId}/credentials`, {
		headers: authHeaders(),
		data: { credential_id: credentialId },
	});
	expect(bound.status(), `bind: ${await bound.text()}`).toBe(201);
	const rules = await request.put(`/credentials/${credentialId}/agents/${agentId}/permissions`, {
		headers: authHeaders(),
		data: ASK_RULES,
	});
	expect(rules.ok(), `set rules: ${await rules.text()}`).toBeTruthy();
}

/** The agent calls the real broker; an Ask rule answers 202 with the hold. */
async function hold(
	request: APIRequestContext,
	agent: AgentIdentity,
	method: 'GET' | 'POST',
	path: string,
): Promise<Held> {
	const res = await request.fetch(`${BROKER}/${UPSTREAM}${path}`, {
		method,
		headers: {
			authorization: `Bearer ${agent.accessToken}`,
			...(method === 'POST' ? { 'content-type': 'application/json' } : {}),
		},
		data: method === 'POST' ? JSON.stringify({ n: 1 }) : undefined,
	});
	expect(res.status(), `expected a 202 hold: ${await res.text()}`).toBe(202);
	const body = await res.json();
	expect(body.status).toBe('held');
	const reviewUrl = new URL(body.review_url as string);
	const heldExecutionId = body.held_execution_id as string;
	const detail = await request.get(`/executions/holds/${heldExecutionId}`, {
		headers: authHeaders(),
	});
	expect(
		detail.ok(),
		`GET /executions/holds/${heldExecutionId}: ${detail.status()}`,
	).toBeTruthy();
	return {
		heldExecutionId,
		agentId: (await detail.json()).agent_id as string,
		// The SPA path behind the router basename.
		reviewPath: reviewUrl.pathname,
	};
}

/** Every held-call signal a decider sees on the Agents page. */
function signals(page: Page) {
	return {
		navBadge: page
			.getByRole('navigation', { name: 'Primary' })
			.getByLabel(/calls? awaiting your approval/),
		// The waiting card on the selected agent.
		card: page.getByRole('article', { name: 'Waiting for you' }),
	};
}

/** The Agents page with `agentId` selected, so its waiting card can show. */
function agentPage(agentId: string): string {
	return `/app/agents?agent=${encodeURIComponent(agentId)}`;
}

async function openBell(page: Page) {
	await page.getByRole('button', { name: /^Notifications/ }).click();
	return page.getByRole('dialog', { name: /Notifications/ });
}

/** The bell's row for one of the agent's held calls: "<agent> wants to …". */
function bellRows(bell: Locator, agentName: string) {
	return bell.getByText(`${agentName} wants to`, { exact: false });
}

/** The review pop-up the card, the bell and the `review_url` all open. */
function reviewDialog(page: Page) {
	return page.getByRole('dialog', { name: 'Review call' });
}

/** Resolves once the pop-up's decision for `heldExecutionId` is accepted. */
function decided(page: Page, heldExecutionId: string) {
	return page.waitForResponse(
		(r) =>
			r.request().method() === 'POST' &&
			new URL(r.url()).pathname === `/executions/holds/${heldExecutionId}:decide` &&
			r.ok(),
	);
}

async function expectSignalsShown(page: Page, agentName: string, agentId: string): Promise<void> {
	await page.goto(agentPage(agentId));
	const s = signals(page);
	await expect(s.card).toBeVisible();
	await expect(s.card).toContainText('held for approval');
	await expect(s.navBadge).toBeVisible();
	const bell = await openBell(page);
	await expect(bellRows(bell, agentName).first()).toBeVisible();
	await page.keyboard.press('Escape');
}

async function expectSignalsClear(page: Page, agentName: string, timeout = 15_000) {
	const s = signals(page);
	await expect(s.card).toHaveCount(0, { timeout });
	await expect(s.navBadge).toHaveCount(0, { timeout });
	const bell = await openBell(page);
	await expect(bellRows(bell, agentName)).toHaveCount(0, { timeout });
	await page.keyboard.press('Escape');
}

let agent: AgentIdentity;

test.beforeAll(async ({ request }) => {
	test.setTimeout(180_000);
	await importAskApi(request);
	agent = await provisionAdminOwnedAgent(request, { name: `ask-ui-agent-${uniqueSuffix()}` });
	await bindWithAskRules(request, agent.clientId);
});

test('a held call shows on the bell, the badge and the waiting card, and approving it clears them', async ({
	page,
	request,
}) => {
	const held = await hold(request, agent, 'GET', '/items?limit=2');
	await expectSignalsShown(page, agent.name, held.agentId);

	// The card's "Review all" opens the pop-up on the agent's oldest call.
	await signals(page).card.getByRole('button', { name: 'Review all' }).click();
	await expect(page).toHaveURL(new RegExp(`[?&]review=${held.heldExecutionId}(&|$)`));
	const dialog = reviewDialog(page);
	await expect(dialog).toBeVisible();
	const approved = decided(page, held.heldExecutionId);
	await dialog.getByRole('button', { name: 'Approve and run' }).click();
	await approved;

	// The agent gets its result: the upstream response itself.
	const settled = await waitHeld(request, held.heldExecutionId, ['completed', 'failed']);
	expect(settled.execution_status).toBe('completed');
	const result = await request.get(`/executions/holds/${held.heldExecutionId}/result`, {
		headers: { authorization: `Bearer ${agent.accessToken}` },
	});
	expect(result.ok(), await result.text()).toBeTruthy();

	await page.goto(agentPage(held.agentId));
	await expectSignalsClear(page, agent.name);
});

test('denying with a reason from the review_url clears the signals and tells the agent why', async ({
	page,
	request,
}) => {
	const held = await hold(request, agent, 'POST', '/orders');
	await expectSignalsShown(page, agent.name, held.agentId);

	// The review_url the broker returned opens the same pop-up.
	await page.goto(held.reviewPath);
	await expect(page).toHaveURL(new RegExp(`[?&]review=${held.heldExecutionId}(&|$)`));
	const dialog = reviewDialog(page);
	await dialog.getByLabel(/^Reason \(optional\)/).fill('not this order');
	const denied = decided(page, held.heldExecutionId);
	await dialog.getByRole('button', { name: 'Deny', exact: true }).click();
	await denied;
	// The badge settles behind the pop-up, without a reload.
	await expect(signals(page).navBadge).toHaveCount(0);

	await waitHeld(request, held.heldExecutionId, ['failed']);
	const result = await request.get(`/executions/holds/${held.heldExecutionId}/result`, {
		headers: authHeaders(),
	});
	const problem = await result.json();
	expect(problem.type).toBe('approval_denied');
	expect(String(problem.detail)).toContain('not this order');
	expect(problem.held_execution?.id).toBe(held.heldExecutionId);

	await page.goto(agentPage(held.agentId));
	await expectSignalsClear(page, agent.name);
});

test('an expired hold clears the signals without a reload', async ({ page, request }) => {
	// The window, then the worker's sweep (about a minute of idle ticks), then
	// the live event or the inbox's own refresh.
	test.setTimeout((TTL_S + 240) * 1000);
	const held = await hold(request, agent, 'GET', '/items?limit=3');
	await expectSignalsShown(page, agent.name, held.agentId);

	const expired = await waitHeld(request, held.heldExecutionId, ['failed'], (TTL_S + 180) * 1000);
	expect(expired.state).toBe('expired');
	await expectSignalsClear(page, agent.name, 60_000);
});

test('a viewer the server does not show the held call to sees none of the signals', async ({
	browser,
	request,
}) => {
	const held = await hold(request, agent, 'GET', '/items?limit=4');
	const password = 'User-Passw0rd-123!'; // pragma: allowlist secret
	// Neither member owns the agent, so the server's scoping leaves the held
	// call out of their list — with or without jobs permissions, which play no
	// part.
	for (const permissions of [
		['agents:read', 'events:read'],
		['agents:read', 'jobs:read', 'jobs:write', 'events:read'],
	]) {
		const email = `ask-ui-viewer-${uniqueSuffix()}@ask-e2e.test`;
		const created = await request.post('/users', {
			headers: authHeaders(),
			data: { email, first_name: 'Viewer', last_name: 'E2e', permissions },
		});
		expect(created.status(), await created.text()).toBe(201);
		const redeemed = await request.post('/users:redeem-invite', {
			data: { invite_token: (await created.json()).invite_token, password },
		});
		expect(redeemed.ok(), await redeemed.text()).toBeTruthy();
		const token = (await redeemed.json()).access_token as string;

		const context = await browser.newContext({ storageState: undefined });
		await context.addInitScript(([key, value]) => window.localStorage.setItem(key, value), [
			TOKEN_STORAGE_KEY,
			token,
		] as const);
		const viewer = await context.newPage();
		// Give the sources their first read before asserting the absence: the
		// agents list and the held-executions list (the live event stream never
		// lets the network go idle).
		const firstReads = [
			viewer.waitForResponse((r) => new URL(r.url()).pathname === '/agents' && r.ok()),
			viewer.waitForResponse((r) => new URL(r.url()).pathname === '/executions/holds'),
		];
		await viewer.goto(agentPage(held.agentId));
		await expect(viewer.getByRole('heading', { level: 1, name: 'Agents' })).toBeVisible();
		await Promise.all(firstReads);
		const s = signals(viewer);
		await expect(s.card).toHaveCount(0);
		await expect(s.navBadge).toHaveCount(0);
		const bell = await openBell(viewer);
		await expect(bellRows(bell, agent.name)).toHaveCount(0);
		await context.close();
	}

	// Leave nothing pending for later runs.
	const denied = await request.post(`/executions/holds/${held.heldExecutionId}:decide`, {
		headers: authHeaders(),
		data: { decision: 'deny', reason: 'e2e cleanup' },
	});
	expect(denied.ok(), await denied.text()).toBeTruthy();
});
