import { test, expect, type APIRequestContext, type Page } from '@playwright/test';
import { authHeaders, TOKEN_STORAGE_KEY, uniqueSuffix } from './helpers';
import { provisionAdminOwnedAgent, type AgentIdentity } from './agent-flow';

/**
 * The ask tier's human loop in the UI (real backend).
 *
 * An agent's call that matches an Ask (`require-approval`) rule is held by the
 * real broker; the UI must then show it where a decider looks — the
 * Notifications inbox, the Agents page "Waiting for you" section and the
 * pending-count badges — and clear every one of those signals once the hold
 * settles: approved (the agent reads the result), denied with a reason, or
 * expired by the worker's sweep. A viewer who cannot decide it sees none of
 * them.
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

test.skip(!BROKER || !UPSTREAM, 'needs the ask-tier stack (ASK_E2E_BROKER_URL, ASK_E2E_UPSTREAM_URL)');
test.describe.configure({ mode: 'serial' });

const API = { vendor: 'ask-e2e-ui', name: 'ask-tier-e2e', version: '1.0.0' };

/** GET /items and POST /orders are held for review; everything else runs. */
const ASK_RULES = [
	{ effect: 'require-approval', methods: ['GET'], path: '^/items$', match_mode: 'regex' },
	{ effect: 'require-approval', methods: ['POST'], path: '^/orders$', match_mode: 'regex' },
	{ effect: 'allow', path: '.*', match_mode: 'regex' },
];

interface Held {
	approvalId: string;
	jobId: string;
	reviewPath: string;
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
	const result = await (await request.get(`/jobs/${jobId}/result`, { headers: authHeaders() })).json();
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
	const reviewUrl = new URL(body.approval.review_url as string);
	return {
		approvalId: body.approval.id as string,
		jobId: body.job_id as string,
		// The SPA path behind the router basename.
		reviewPath: reviewUrl.pathname,
	};
}

/** Every held-call signal a decider sees, for one agent. */
function signals(page: Page, agentName: string) {
	return {
		navBadge: page
			.getByRole('navigation', { name: 'Primary' })
			.getByLabel(/calls? awaiting your approval/),
		waiting: page.getByRole('region', { name: 'Waiting for you' }),
		waitingRow: page
			.getByRole('region', { name: 'Waiting for you' })
			.getByRole('listitem')
			.filter({ hasText: agentName }),
		approvalsEntry: page.getByRole('button', { name: /^Approvals/ }),
	};
}

async function openInbox(page: Page) {
	await page.getByRole('button', { name: /^Notifications/ }).click();
	return page.getByRole('dialog', { name: /Notifications/ });
}

async function expectSignalsShown(page: Page, agentName: string): Promise<void> {
	await page.goto('/app/agents');
	const s = signals(page, agentName);
	await expect(s.waitingRow).toBeVisible();
	await expect(s.waitingRow).toContainText('wants to make');
	await expect(s.navBadge).toBeVisible();
	await expect(s.approvalsEntry).toContainText(/\d/);
	const inbox = await openInbox(page);
	await expect(inbox.getByText(`${agentName} is waiting for you to approve`)).toBeVisible();
	await page.keyboard.press('Escape');
}

async function expectSignalsClear(page: Page, agentName: string, timeout = 15_000) {
	const s = signals(page, agentName);
	await expect(s.waitingRow).toHaveCount(0, { timeout });
	await expect(s.navBadge).toHaveCount(0, { timeout });
	await expect(s.approvalsEntry).toHaveAccessibleName('Approvals', { timeout });
	const inbox = await openInbox(page);
	await expect(inbox.getByText(`${agentName} is waiting for you to approve`)).toHaveCount(0, {
		timeout,
	});
	await page.keyboard.press('Escape');
}

let agent: AgentIdentity;

test.beforeAll(async ({ request }) => {
	test.setTimeout(180_000);
	await importAskApi(request);
	agent = await provisionAdminOwnedAgent(request, { name: `ask-ui-agent-${uniqueSuffix()}` });
	await bindWithAskRules(request, agent.clientId);
});

test('a held call shows in the inbox, the badge and Waiting for you, and approving it clears them', async ({
	page,
	request,
}) => {
	const held = await hold(request, agent, 'GET', '/items?limit=2');
	await expectSignalsShown(page, agent.name);

	// The Waiting for you row deep-links to the review page.
	await signals(page, agent.name).waitingRow.getByRole('link', { name: /^Review/ }).click();
	await expect(page).toHaveURL(new RegExp(`${held.reviewPath}$`));
	await page.getByLabel('Reason (optional)').fill('fine by me');
	await page.getByRole('button', { name: 'Approve and run' }).click();
	await expect(page.getByText('Outcome')).toBeVisible();

	// The agent gets its result.
	const job = await waitJob(request, held.jobId, ['completed', 'failed']);
	expect(job.status).toBe('completed');
	const result = await request.get(`/jobs/${held.jobId}/result`, {
		headers: { authorization: `Bearer ${agent.accessToken}` },
	});
	expect(result.ok(), await result.text()).toBeTruthy();
	expect((await result.json()).http_status).toBe(200);

	await page.goto('/app/agents');
	await expectSignalsClear(page, agent.name);
});

test('denying with a reason from the review_url clears the signals and tells the agent why', async ({
	page,
	request,
}) => {
	const held = await hold(request, agent, 'POST', '/orders');
	await expectSignalsShown(page, agent.name);

	// The review_url the broker returned opens the same page.
	await page.goto(held.reviewPath);
	await page.getByLabel('Reason (optional)').fill('not this order');
	await page.getByRole('button', { name: 'Deny' }).click();
	await expect(page.getByText('not this order')).toBeVisible();
	// The badge settles on the review page itself, without a reload.
	await expect(signals(page, agent.name).navBadge).toHaveCount(0);

	await waitJob(request, held.jobId, ['failed']);
	const result = await request.get(`/jobs/${held.jobId}/result`, { headers: authHeaders() });
	const problem = await result.json();
	expect(problem.type).toBe('approval_denied');
	expect(String(problem.detail)).toContain('not this order');

	await page.goto('/app/agents');
	await expectSignalsClear(page, agent.name);
});

test('an expired hold clears the signals without a reload', async ({ page, request }) => {
	// The window, then the worker's sweep (about a minute of idle ticks), then
	// the live event or the inbox's own refresh.
	test.setTimeout((TTL_S + 240) * 1000);
	const held = await hold(request, agent, 'GET', '/items?limit=3');
	await expectSignalsShown(page, agent.name);

	await waitJob(request, held.jobId, ['failed'], (TTL_S + 180) * 1000);
	const approval = await (
		await request.get(`/executions/approvals/${held.approvalId}`, { headers: authHeaders() })
	).json();
	expect(approval.state).toBe('expired');
	await expectSignalsClear(page, agent.name, 60_000);
});

test('a viewer who cannot decide the held call sees none of the signals', async ({
	browser,
	request,
}) => {
	const held = await hold(request, agent, 'GET', '/items?limit=4');
	const password = 'User-Passw0rd-123!'; // pragma: allowlist secret
	// A member without jobs:write may read jobs but not decide; one with it but
	// not owning the agent is outside the list's reviewer scoping.
	for (const permissions of [
		['agents:read', 'jobs:read', 'events:read'],
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
		await context.addInitScript(
			([key, value]) => window.localStorage.setItem(key, value),
			[TOKEN_STORAGE_KEY, token] as const,
		);
		const viewer = await context.newPage();
		await viewer.goto('/app/agents');
		await expect(viewer.getByRole('heading', { level: 1, name: 'Agents' })).toBeVisible();
		// Give every source its first read before asserting the absence.
		await viewer.waitForLoadState('networkidle');
		const s = signals(viewer, agent.name);
		await expect(s.waiting).toHaveCount(0);
		await expect(s.navBadge).toHaveCount(0);
		const inbox = await openInbox(viewer);
		await expect(inbox.getByText(/is waiting for you to approve/)).toHaveCount(0);
		await context.close();
	}

	// Leave nothing pending for later runs.
	const denied = await request.post(`/executions/approvals/${held.approvalId}:decide`, {
		headers: authHeaders(),
		data: { decision: 'deny', reason: 'e2e cleanup' },
	});
	expect(denied.ok(), await denied.text()).toBeTruthy();
});
