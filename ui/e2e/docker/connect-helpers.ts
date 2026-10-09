import { type APIRequestContext, type Page, type Request, expect } from '@playwright/test';
import { authHeaders, uniqueSuffix } from './helpers';
import { type AgentIdentity, provisionAdminOwnedAgent } from './agent-flow';

/**
 * Helpers for the connect-session approve specs (`connect-*.spec.ts`).
 *
 * The agent side is driven over the control API as the agent itself (its
 * jwt-bearer access token from {@link provisionAdminOwnedAgent}); the
 * operator side is the real SPA. Two loopback upstreams back these specs,
 * both started next to the app:
 *
 *   - the smoke upstream (`python -m tests.harness.smoke_upstream`), whose
 *     live spec declares API key, bearer, basic and OAuth client-credentials
 *     schemes — the `manual_*` target;
 *   - the fake OAuth server (`python -m tests.harness.fake_oauth`), an
 *     authorization-code API that is its own authorization server — the
 *     `awaiting_app` target and the `fakeas` / `fakedev` vendor entries.
 *
 * The app must run with `control.connect.manual_flows_enabled: true`, admit
 * 127.0.0.0/8 through ingest and broker egress, and reach the fake OAuth
 * server from its OAuth calls (`python -m tests.harness.e2e_app`). When the
 * upstreams are not reachable the specs skip (see {@link upstreamsReachable}).
 */

/** The smoke upstream's base URL (`E2E_SMOKE_UPSTREAM`, default :8084). */
export const SMOKE_UPSTREAM = process.env.E2E_SMOKE_UPSTREAM ?? 'http://127.0.0.1:8084';

/** The fake OAuth server's base URL (`E2E_FAKE_OAUTH`, default :8085). */
export const FAKE_OAUTH = process.env.E2E_FAKE_OAUTH ?? 'http://127.0.0.1:8085';

/** The `vendors.entries` key of the fake authorization-code vendor. */
export const FAKE_VENDOR_KEY = process.env.E2E_FAKE_VENDOR_KEY ?? 'fakeas';

/** The `vendors.entries` key of the fake device-flow vendor. */
export const FAKE_DEVICE_VENDOR_KEY = process.env.E2E_FAKE_DEVICE_VENDOR_KEY ?? 'fakedev';

export interface ApiRef {
	vendor: string;
	name: string;
	version: string;
}

export interface ConnectCreated {
	session_id: string;
	approval_url: string;
	poll_token: string;
	resolved_flow: string;
}

export interface ConnectStatus {
	status: string;
	error_code: string | null;
	credential_id: string | null;
	connected_as: string | null;
}

async function reachable(request: APIRequestContext, url: string): Promise<boolean> {
	try {
		return (await request.get(url, { timeout: 2000 })).ok();
	} catch {
		return false;
	}
}

/** True when both loopback upstreams answer their health checks. */
export async function upstreamsReachable(request: APIRequestContext): Promise<boolean> {
	return (
		(await reachable(request, `${SMOKE_UPSTREAM}/health`)) &&
		(await reachable(request, `${FAKE_OAUTH}/health`))
	);
}

/**
 * Import a spec by URL, wait for the job, and promote its revision so the
 * API has the live revision `:connect` resolves against. Returns the API
 * reference with the registry's slugged vendor. Idempotent across specs: a
 * repeat import of the same name is a no-op once it is live.
 */
export async function importLiveApi(
	request: APIRequestContext,
	opts: { url: string; vendor: string; apiName: string },
): Promise<ApiRef> {
	const res = await request.post('/apis', {
		headers: authHeaders(),
		data: {
			sources: [{ type: 'url', url: opts.url, vendor: opts.vendor, api_name: opts.apiName }],
		},
	});
	expect(res.status(), `import ${opts.url} failed: ${await res.text()}`).toBe(202);
	const jobId = (await res.json()).job_id as string;
	await expect
		.poll(
			async () => {
				const j = await request.get(`/jobs/${jobId}`, { headers: authHeaders() });
				return j.ok() ? ((await j.json()).status as string) : 'unknown';
			},
			{ message: `import job ${jobId} never completed`, timeout: 60_000 },
		)
		.toMatch(/succeeded|completed|done/);

	const list = await request.get('/apis', { headers: authHeaders() });
	const rows = (await list.json()).data as Array<{
		api: ApiRef;
		current_revision_id: string | null;
	}>;
	const row = rows.find((r) => r.api.name === opts.apiName);
	expect(row, `imported API ${opts.apiName} not listed`).toBeTruthy();
	const api = { vendor: row!.api.vendor, name: row!.api.name, version: row!.api.version };
	const base = `/apis/${api.vendor}/${api.name}/${api.version}`;
	const revs = await request.get(`${base}/revisions`, { headers: authHeaders() });
	const latest = (
		(await revs.json()).data as Array<{ revision_id: string; is_current: boolean }>
	)[0];
	if (!latest.is_current) {
		const promote = await request.post(`${base}/revisions/${latest.revision_id}:promote`, {
			headers: authHeaders(),
		});
		expect(promote.ok(), `promote failed: ${await promote.text()}`).toBeTruthy();
	}
	return api;
}

/** The smoke upstream's live spec, imported once per spec file under a unique name. */
export function importSmokeApi(request: APIRequestContext): Promise<ApiRef> {
	return importLiveApi(request, {
		url: `${SMOKE_UPSTREAM}/specs/live.json`,
		vendor: 'smoke.test',
		apiName: `live-${uniqueSuffix()}`,
	});
}

/** The fake OAuth server's authorization-code API, under a unique name. */
export function importOAuthApi(request: APIRequestContext): Promise<ApiRef> {
	return importLiveApi(request, {
		url: `${FAKE_OAUTH}/specs/oauth.json`,
		vendor: 'fakeapi.test',
		apiName: `oauth-${uniqueSuffix()}`,
	});
}

/** A fresh, active, admin-owned agent with its own access token. */
export function newAgent(request: APIRequestContext, label: string): Promise<AgentIdentity> {
	return provisionAdminOwnedAgent(request, { name: `e2e-${label}-${uniqueSuffix()}` });
}

function agentHeaders(agent: AgentIdentity): Record<string, string> {
	return { authorization: `Bearer ${agent.accessToken}`, 'content-type': 'application/json' };
}

/** `POST /integrations:connect` as the agent. Asserts 201. */
export async function agentConnect(
	request: APIRequestContext,
	agent: AgentIdentity,
	body: Record<string, unknown>,
): Promise<ConnectCreated> {
	const res = await request.post('/integrations:connect', {
		headers: agentHeaders(agent),
		data: body,
	});
	expect(res.status(), `:connect failed: ${await res.text()}`).toBe(201);
	return res.json();
}

/** `POST /integrations:connect` as the agent, returning the raw response. */
export async function agentConnectRaw(
	request: APIRequestContext,
	agent: AgentIdentity,
	body: Record<string, unknown>,
): Promise<{ status: number; body: Record<string, unknown> }> {
	const res = await request.post('/integrations:connect', {
		headers: agentHeaders(agent),
		data: body,
	});
	return { status: res.status(), body: await res.json() };
}

/** The agent's view of its session, via its poll token. */
export async function agentStatus(
	request: APIRequestContext,
	agent: AgentIdentity,
	session: ConnectCreated,
): Promise<ConnectStatus> {
	const res = await request.get(
		`/connect-sessions/${session.session_id}/status?poll_token=${encodeURIComponent(session.poll_token)}`,
		{ headers: agentHeaders(agent) },
	);
	expect(res.ok(), `status failed: ${res.status()} ${await res.text()}`).toBeTruthy();
	return res.json();
}

/** A manual API-key request against the smoke upstream's `X-Api-Key` scheme. */
export function apiKeyConnectBody(api: ApiRef, reason: string): Record<string, unknown> {
	return {
		api,
		auth_type: 'apiKeyAuth',
		reason,
		requested_permission_rules: [{ effect: 'allow', methods: ['GET'], path: '/auth/api-key' }],
	};
}

/** An OAuth request against the fake OAuth API. */
export function oauthConnectBody(
	api: ApiRef,
	scopes: string[],
	reason: string,
): Record<string, unknown> {
	return {
		api,
		requested_scopes: scopes,
		reason,
		requested_permission_rules: [{ effect: 'allow', methods: ['GET'], path: '/me' }],
	};
}

/** Open the token-less approve link the agent relays, as the signed-in operator. */
export async function openApproveLink(page: Page, session: ConnectCreated): Promise<void> {
	const url = new URL(session.approval_url);
	await page.goto(`${url.pathname}${url.search}`);
	await expect(page.getByRole('dialog', { name: 'Approve integration' })).toBeVisible();
}

/** The approve dialog. */
export function approveDialog(page: Page) {
	return page.getByRole('dialog', { name: 'Approve integration' });
}

/** Record every non-GET request the page sends to the connect-session routes. */
export function recordConnectWrites(page: Page): Request[] {
	const seen: Request[] = [];
	page.on('request', (req) => {
		if (req.method() !== 'GET' && req.url().includes('/connect-sessions/')) seen.push(req);
	});
	return seen;
}

/** Credential as the admin sees it (`GET /credentials/{id}`). */
export async function getCredential(
	request: APIRequestContext,
	credentialId: string,
): Promise<Record<string, unknown>> {
	const res = await request.get(`/credentials/${credentialId}`, { headers: authHeaders() });
	expect(res.ok(), `GET credential failed: ${res.status()} ${await res.text()}`).toBeTruthy();
	return res.json();
}

/** The agent's credential bindings (`GET /agents/{id}/credentials`). */
export async function agentBindings(
	request: APIRequestContext,
	agentId: string,
): Promise<Array<Record<string, unknown>>> {
	const res = await request.get(`/agents/${agentId}/credentials`, { headers: authHeaders() });
	expect(res.ok(), `GET bindings failed: ${res.status()} ${await res.text()}`).toBeTruthy();
	const body = await res.json();
	return (Array.isArray(body) ? body : body.data) as Array<Record<string, unknown>>;
}

/**
 * Create a user with exactly `permissions`, redeem the invite with a
 * password, and return the email + password to sign in with.
 */
export async function createUser(
	request: APIRequestContext,
	permissions: string[],
): Promise<{ id: string; email: string; password: string }> {
	const email = `e2e-${uniqueSuffix()}@local.test`;
	const password = 'E2eUserPass123!'; // pragma: allowlist secret
	const created = await request.post('/users', {
		headers: authHeaders(),
		data: { email, first_name: 'Owner', last_name: 'Limited', permissions },
	});
	expect(created.status(), `create user failed: ${await created.text()}`).toBe(201);
	const body = await created.json();
	const redeem = await request.post('/users:redeem-invite', {
		headers: { 'content-type': 'application/json' },
		data: { invite_token: body.invite_token, password },
	});
	expect(redeem.ok(), `redeem invite failed: ${await redeem.text()}`).toBeTruthy();
	return { id: body.user.id as string, email, password };
}

/** `POST /auth/login` → access token, for API calls as another user. */
export async function loginToken(
	request: APIRequestContext,
	email: string,
	password: string,
): Promise<string> {
	const res = await request.post('/auth/login', {
		headers: { 'content-type': 'application/json' },
		data: { email, password },
	});
	expect(res.ok(), `login failed: ${await res.text()}`).toBeTruthy();
	return (await res.json()).access_token as string;
}

/** The permission rules on an agent's binding to a credential. */
export async function bindingRules(
	request: APIRequestContext,
	credentialId: string,
	agentId: string,
): Promise<Array<Record<string, unknown>>> {
	const res = await request.get(`/credentials/${credentialId}/agents/${agentId}/permissions`, {
		headers: authHeaders(),
	});
	expect(res.ok(), `GET binding rules failed: ${res.status()} ${await res.text()}`).toBeTruthy();
	const body = await res.json();
	return (Array.isArray(body) ? body : (body.rules ?? body.data)) as Array<
		Record<string, unknown>
	>;
}
