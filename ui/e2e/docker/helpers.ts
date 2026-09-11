import * as fs from 'fs';
import { fileURLToPath } from 'url';
import { dirname, join } from 'path';
import { type Page, type APIRequestContext, expect } from '@playwright/test';

/**
 * Shared helpers for the REAL-backend e2e suite (Mode B).
 *
 * Unlike the mocked specs under `ui/e2e/*.spec.ts` (MSW, accept-any-credentials),
 * these run against the combined jentic-one app serving the SPA same-origin on
 * :8000 (`make start-app`) backed by real Postgres fixtures (`make start-fixtures`).
 *
 * Boot recipe (clean DB so the first-run, no-credential setup gate is present):
 *
 *   make destroy-fixtures && make start-fixtures && make start-app
 *   cd ui && npm run e2e:docker
 *
 * Auth model: no-credential first run. The platform boots with NO seeded admin;
 * the first operator account is created at runtime and self-closes the gate.
 * Verified against :8000:
 *   GET  /admin/health        -> {setup_required:true, next_step:"create_admin"}
 *   POST /users:create-admin  -> 200 {access_token,...} (one-time; 410 thereafter)
 *   GET  /admin/health        -> {setup_required:false, next_step:null} (after)
 *
 * Auth is set up once (Playwright `setup` projects) and reused via `storageState`,
 * the Playwright-recommended pattern — downstream specs start already-authenticated
 * instead of re-driving the login UI per test:
 *   - `bootstrap.setup.ts` clears the first-run gate by creating the first admin
 *     via the /setup UI and records its password to `playwright/.auth/bootstrap.json`.
 *   - `auth.setup.ts` logs in with that password and writes the browser
 *     `storageState` (the JWT lives in localStorage) to `playwright/.auth/admin.json`.
 *   - the `e2e` project sets `use.storageState` to that file, so every spec is
 *     authenticated without touching the login form. Negative-path specs opt out
 *     with `test.use({ storageState: { cookies: [], origins: [] } })`.
 */

const __dirname = dirname(fileURLToPath(import.meta.url));

/** Git-ignored scratch dir for auth artifacts (storageState + bootstrap record). */
export const AUTH_DIR = join(__dirname, 'playwright', '.auth');

/** Browser storage state (JWT in localStorage) the `e2e` project reuses. */
export const STORAGE_STATE_PATH = join(AUTH_DIR, 'admin.json');

/** What password the bootstrap project created the first admin with (cross-project handoff). */
export const BOOTSTRAP_STATE_PATH = join(AUTH_DIR, 'bootstrap.json');

/** localStorage key the SPA persists the Bearer JWT under (ui/src/shared/api/token-store.ts). */
export const TOKEN_STORAGE_KEY = 'jentic-one.access_token';

export interface BootstrapState {
	/** The password the first admin was created with by the bootstrap project. */
	adminPassword: string;
}

export function ensureAuthDir(): void {
	fs.mkdirSync(AUTH_DIR, { recursive: true });
}

export function loadBootstrapState(): BootstrapState | null {
	try {
		return JSON.parse(fs.readFileSync(BOOTSTRAP_STATE_PATH, 'utf-8'));
	} catch {
		return null;
	}
}

export function saveBootstrapState(state: BootstrapState): void {
	ensureAuthDir();
	fs.writeFileSync(BOOTSTRAP_STATE_PATH, JSON.stringify(state, null, 2));
}

/** The first admin the e2e suite creates at runtime (no-credential first run). */
export const SETUP_ADMIN = {
	email: 'admin@local',
	/** Password the suite creates the admin with (>= 12 chars, enforced by the UI + backend). */
	password: 'E2eAdminPass123!', // pragma: allowlist secret
} as const;

/**
 * Capture genuine app-code console errors, ignoring transient resource/network
 * noise. Mirrors the helper in the mocked specs so real-backend specs hold the
 * same "console stays clean" bar.
 */
export function captureConsoleErrors(page: Page): string[] {
	const errors: string[] = [];
	page.on('console', (msg) => {
		if (msg.type() !== 'error') return;
		const text = msg.text();
		if (text.includes('Failed to load resource')) return;
		if (text.includes('net::ERR_')) return;
		errors.push(text);
	});
	return errors;
}

/** Read the admin first-run gate state from the backend. */
export async function getHealth(
	request: APIRequestContext,
): Promise<{ setup_required: boolean; next_step: string | null }> {
	const res = await request.get('/admin/health');
	expect(res.ok(), `GET /admin/health failed: ${res.status()}`).toBeTruthy();
	return res.json();
}

/** Fill the first-run setup form (create the first admin) and submit. */
export async function submitSetup(page: Page, email: string, password: string): Promise<void> {
	await expect(page.getByRole('heading', { name: 'Welcome to Jentic One' })).toBeVisible();
	await page.getByLabel('Email').fill(email);
	// "Password" is a substring of "Confirm password" — pin the field with exact.
	// The show/hide toggle button carries a "Show password" name, so getByLabel
	// (which matches the form control, not the button) stays unambiguous.
	await page.getByLabel('Password', { exact: true }).fill(password);
	await page.getByLabel('Confirm password').fill(password);
	await page.getByRole('button', { name: 'Create admin account' }).click();
}

/** Fill the login form and submit. Does not assert what comes next. */
export async function submitLogin(page: Page, email: string, password: string): Promise<void> {
	await expect(page.getByRole('heading', { name: 'Sign in to Jentic One' })).toBeVisible();
	await page.getByLabel('Email').fill(email);
	// Target the password input by role, not getByLabel — the show/hide toggle
	// button carries a "…password" accessible name and would collide.
	await page.getByRole('textbox', { name: 'Password' }).fill(password);
	// `exact` avoids matching the loading label "Signing in…", which contains
	// "Sign in" as a substring (Playwright role-name matching is substring by
	// default). The button starts idle, so this is belt-and-suspenders.
	await page.getByRole('button', { name: 'Sign in', exact: true }).click();
}

/** Complete the change-password form (Account → change password, or an invited
 * user's forced first-run rotation — AuthGuard still gates on must_change_password). */
export async function changePassword(
	page: Page,
	currentPassword: string,
	newPassword: string,
): Promise<void> {
	await expect(page.getByRole('heading', { name: 'Set a new password' })).toBeVisible();
	// Labels are distinct; the toggle button's name is "Show password", so
	// getByLabel is unambiguous. "New password" is a substring of "Confirm new
	// password", hence exact for that one.
	await page.getByLabel('Current password').fill(currentPassword);
	await page.getByLabel('New password', { exact: true }).fill(newPassword);
	await page.getByLabel('Confirm new password').fill(newPassword);
	await page.getByRole('button', { name: 'Set password' }).click();
}

// ── Self-seeding helpers (page.request, create-then-assert) ────────────────
//
// A clean fixtures DB is EMPTY — unlike the mocked specs (MSW serves fixtures
// like `inbox-triage-bot`), the real backend lists return `{data:[]}`. So each
// per-surface spec creates what it asserts on, via the public API, before
// driving the UI. This keeps specs hermetic (no shared seed data / cross-spec
// ordering) per Playwright's recommended isolation.
//
// `page.request` (APIRequestContext) does NOT read the browser localStorage the
// SPA stores the JWT in, so these helpers attach the Bearer token explicitly,
// recovered from the storageState the auth setup project wrote. Contracts here
// were captured live against :8000 (clean DB) — see each helper.

/** Read the admin JWT the auth setup project persisted into storageState. */
export function readAccessToken(): string {
	const raw = fs.readFileSync(STORAGE_STATE_PATH, 'utf-8');
	const state = JSON.parse(raw) as {
		origins: { localStorage: { name: string; value: string }[] }[];
	};
	const token = state.origins
		.flatMap((o) => o.localStorage)
		.find((kv) => kv.name === TOKEN_STORAGE_KEY)?.value;
	if (!token) {
		throw new Error(
			`No ${TOKEN_STORAGE_KEY} in ${STORAGE_STATE_PATH} — did the auth setup project run?`,
		);
	}
	return token;
}

/** Bearer auth headers for `page.request` calls (the JWT is not auto-attached). */
export function authHeaders(): Record<string, string> {
	return { authorization: `Bearer ${readAccessToken()}`, 'content-type': 'application/json' };
}

/**
 * GET /users/me → the authenticated admin's user id. With the no-credential
 * first-run flow the admin id is a runtime-generated ksuid (not a fixed seed),
 * so anything that needs the admin's identity.sub (e.g. assigning agent
 * ownership) must resolve it live instead of hardcoding a constant.
 */
export async function getAdminUserId(request: APIRequestContext): Promise<string> {
	const res = await request.get('/users/me', { headers: authHeaders() });
	expect(res.ok(), `GET /users/me failed: ${res.status()} ${await res.text()}`).toBeTruthy();
	const id = (await res.json()).id as string;
	expect(id, 'GET /users/me returned no id').toBeTruthy();
	return id;
}

/** A short unique suffix so repeated runs against a persistent DB don't collide. */
export function uniqueSuffix(): string {
	return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 6)}`;
}

/** POST /service-accounts → 201. Returns the created service-account id. */
export async function createServiceAccount(
	request: APIRequestContext,
	name: string,
	description = 'created by e2e',
): Promise<string> {
	const res = await request.post('/service-accounts', {
		headers: authHeaders(),
		data: { name, description },
	});
	expect(res.status(), `createServiceAccount failed: ${await res.text()}`).toBe(201);
	return (await res.json()).id;
}

/**
 * POST /credentials → 201 (requires the credential-at-rest encryption keyset,
 * see config/local.yaml). Returns the credential id. Defaults to a bearer token.
 */
export async function createBearerCredential(
	request: APIRequestContext,
	name: string,
	vendor = 'httpbin.org',
): Promise<string> {
	const res = await request.post('/credentials', {
		headers: authHeaders(),
		data: { type: 'bearer_token', name, api: { vendor }, token: 'e2e-secret-value' },
	});
	expect(res.status(), `createBearerCredential failed: ${await res.text()}`).toBe(201);
	return (await res.json()).credential.credential_id;
}

/** Where an api_key / header credential is injected on upstream calls. */
export type CredentialLocation = 'header' | 'query' | 'cookie';

/**
 * POST /credentials (api_key) → 201. The discriminated body requires
 * `key` + `location` + `field_name` (see ApiKeyCreateRequest). Returns the id.
 */
export async function createApiKeyCredential(
	request: APIRequestContext,
	opts: {
		name: string;
		vendor?: string;
		apiName?: string;
		apiVersion?: string;
		key?: string;
		location?: CredentialLocation;
		fieldName?: string;
	},
): Promise<string> {
	const res = await request.post('/credentials', {
		headers: authHeaders(),
		data: {
			type: 'api_key',
			name: opts.name,
			api: {
				vendor: opts.vendor ?? 'httpbin.org',
				...(opts.apiName ? { name: opts.apiName } : {}),
				...(opts.apiVersion ? { version: opts.apiVersion } : {}),
			},
			provider: 'static',
			key: opts.key ?? 'sk-e2e-apikey',
			location: opts.location ?? 'header',
			field_name: opts.fieldName ?? 'X-Api-Key',
		},
	});
	expect(res.status(), `createApiKeyCredential failed: ${await res.text()}`).toBe(201);
	return (await res.json()).credential.credential_id;
}

/**
 * PATCH /credentials/{id} → 200. Rotates/updates a credential. `type` is the
 * discriminator and is always required; pass only the fields to change. Returns
 * the redacted view (secrets are never echoed back).
 */
export async function updateCredential(
	request: APIRequestContext,
	credentialId: string,
	body: Record<string, unknown> & { type: string },
): Promise<{ active: boolean; name: string; updated_at: string | null }> {
	const res = await request.patch(`/credentials/${credentialId}`, {
		headers: authHeaders(),
		data: body,
	});
	expect(res.status(), `updateCredential failed: ${await res.text()}`).toBe(200);
	return res.json();
}

/**
 * POST /credentials with a deliberately invalid body → expects a 4xx. Returns
 * the status so negative-path specs can assert the exact validation contract
 * (instead of weakening the happy-path helpers). Does NOT throw on non-201.
 */
export async function postCredentialRaw(
	request: APIRequestContext,
	data: Record<string, unknown>,
): Promise<{ status: number; body: string }> {
	const res = await request.post('/credentials', { headers: authHeaders(), data });
	return { status: res.status(), body: await res.text() };
}

/**
 * PUT /agents/{id}/scopes → 200. Replaces ALL scopes for an agent (bulk
 * replace, see replaceAgentScopes). This is the public-API path that landed
 * with #517 — it is what lets a DCR agent acquire `capabilities:execute`
 * without a direct DB write. Returns the resulting scope list.
 */
export async function replaceAgentScopes(
	request: APIRequestContext,
	agentId: string,
	scopes: string[],
): Promise<string[]> {
	const res = await request.put(`/agents/${agentId}/scopes`, {
		headers: authHeaders(),
		data: { scopes },
	});
	expect(res.status(), `replaceAgentScopes failed: ${await res.text()}`).toBe(200);
	return (await res.json()).scopes;
}

/** POST /agents/{id}/credentials → 201. Binds a credential directly to an agent. Returns the binding id. */
export async function bindCredentialToAgent(
	request: APIRequestContext,
	agentId: string,
	credentialId: string,
): Promise<string> {
	const res = await request.post(`/agents/${agentId}/credentials`, {
		headers: authHeaders(),
		data: { credential_id: credentialId },
	});
	expect(res.status(), `bindCredentialToAgent failed: ${await res.text()}`).toBe(201);
	return (await res.json()).id;
}

/** Minimal but valid OpenAPI doc used to seed the local registry via import. */
export function sampleOpenApiSpec(title: string): string {
	return JSON.stringify({
		openapi: '3.0.0',
		info: { title, version: '1.0.0' },
		servers: [{ url: 'https://httpbin.org' }],
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

/**
 * POST /apis (inline source) → 202 with a queued job, then poll /jobs/{id}
 * until the ingest completes. Returns once the API is registered. Import is
 * ASYNC on the real backend (unlike the synchronous MSW mock).
 */
export async function importInlineApi(
	request: APIRequestContext,
	opts: { vendor: string; apiName: string; title?: string },
): Promise<void> {
	const res = await request.post('/apis', {
		headers: authHeaders(),
		data: {
			sources: [
				{
					type: 'inline',
					content: sampleOpenApiSpec(opts.title ?? opts.apiName),
					filename: `${opts.apiName}.json`,
					vendor: opts.vendor,
					api_name: opts.apiName,
				},
			],
		},
	});
	expect(res.status(), `importInlineApi failed: ${await res.text()}`).toBe(202);
	const jobId = (await res.json()).job_id as string;

	// Poll the job to a terminal state (the suite runs serial, so a short poll
	// loop is fine and keeps the spec deterministic instead of racing the UI).
	// A COLD worker on a fresh DB can take ~25s for the first import (model/
	// parser warmup), so budget generously — the importing specs call
	// test.slow() to widen their per-test timeout to match.
	await expect
		.poll(
			async () => {
				const j = await request.get(`/jobs/${jobId}`, { headers: authHeaders() });
				return j.ok() ? (await j.json()).status : 'unknown';
			},
			{
				message: `import job ${jobId} never completed`,
				timeout: 60_000,
				intervals: [250, 500, 1000, 2000],
			},
		)
		.toMatch(/succeeded|completed|done/);
}

/**
 * POST /access-requests → 202 (status: pending). Returns the request id.
 *
 * The backend dedups pending requests on (actor, resource_type, action,
 * resource_id), so concurrent specs that file the same resource_type+action
 * collide with 409 access_request_duplicate_pending. We default resource_id to
 * a unique value per call so each spec owns an independent pending request
 * (hermetic, no cross-spec coupling); callers can pin it for assertions.
 */
export async function fileAccessRequest(
	request: APIRequestContext,
	opts: { reason?: string; resourceType?: string; action?: string; resourceId?: string } = {},
): Promise<string> {
	const res = await request.post('/access-requests', {
		headers: authHeaders(),
		data: {
			reason: opts.reason ?? 'e2e access request',
			items: [
				{
					resource_type: opts.resourceType ?? 'credential',
					action: opts.action ?? 'bind',
					resource_id: opts.resourceId ?? `e2e-res-${uniqueSuffix()}`,
				},
			],
		},
	});
	expect(res.status(), `fileAccessRequest failed: ${await res.text()}`).toBe(202);
	return (await res.json()).id;
}
