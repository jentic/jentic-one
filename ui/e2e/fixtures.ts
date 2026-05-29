import { type Page, type Route } from '@playwright/test';

// ---------------------------------------------------------------------------
// Strict local fixture types — no index signatures, no Partial<>.
// These match what the page components actually read, not the generated types.
// ---------------------------------------------------------------------------

interface FixtureToolkit {
	id: string;
	name: string;
	description: string;
	disabled: boolean;
	simulate: boolean;
	keys: { id: string; prefix: string; created_at: number }[];
	credentials: { credential_id: string; label: string; api_id: string }[];
}

interface FixtureTrace {
	id: string;
	toolkit_id: string;
	operation_id: string | null;
	workflow_id: string | null;
	status: string;
	http_status: number;
	duration_ms: number;
	created_at: number;
}

interface FixtureWorkflow {
	slug: string;
	name: string;
	description: string;
	steps: { id: string; operation: string; description: string }[];
	inputs: Record<string, unknown>;
	involved_apis: string[];
}

interface FixtureJob {
	id: string;
	kind: string;
	status: string;
	toolkit_id: string;
	created_at: number;
	result: unknown;
}

interface FixtureAccessRequest {
	id: string;
	toolkit_id: string;
	type: string;
	status: string;
	reason: string;
	created_at: number;
	payload: {
		credential_id?: string;
		api_id?: string;
		rules?: { effect: string; methods?: string[]; path?: string }[];
	};
}

// ---------------------------------------------------------------------------
// Console error capture
// ---------------------------------------------------------------------------

export function captureConsoleErrors(page: Page) {
	const errors: string[] = [];
	page.on('console', (msg) => {
		if (msg.type() === 'error') {
			const text = msg.text();
			if (text.includes('Failed to load resource') && text.includes('500')) return;
			if (text.includes('net::ERR_')) return;
			errors.push(text);
		}
	});
	return errors;
}

// ---------------------------------------------------------------------------
// Route helper — only intercept fetch/xhr, let navigations through
// ---------------------------------------------------------------------------

function isApiRequest(route: Route): boolean {
	return route.request().resourceType() === 'fetch' || route.request().resourceType() === 'xhr';
}

// ---------------------------------------------------------------------------
// SPA navigation helper
// Routes like /oauth-brokers, /toolkits/:id collide with Vite proxy.
// Load the SPA via / first, then use client-side navigation.
// ---------------------------------------------------------------------------

export async function navigateTo(page: Page, path: string) {
	const current = new URL(page.url());
	if (current.pathname === path) return;

	await page.evaluate((p) => {
		window.history.pushState({}, '', p);
		window.dispatchEvent(new PopStateEvent('popstate'));
	}, path);
	await page
		.getByRole('heading')
		.first()
		.waitFor({ timeout: 5000 })
		.catch(() => page.waitForLoadState('domcontentloaded'));
}

// ---------------------------------------------------------------------------
// Auth & health mocks
// ---------------------------------------------------------------------------

export async function mockAuthenticatedUser(page: Page) {
	await page.route('**/health', (route) => {
		if (!isApiRequest(route)) return route.continue();
		return route.fulfill({ json: { status: 'ok' } });
	});
	await page.route('**/user/me', (route) => {
		if (!isApiRequest(route)) return route.continue();
		return route.fulfill({
			json: { logged_in: true, username: 'admin', role: 'admin' },
		});
	});
	await page.route('**/version', (route) => {
		if (!isApiRequest(route)) return route.continue();
		return route.fulfill({ json: { version: '0.2.0-test' } });
	});
}

export async function mockSetupRequired(page: Page) {
	await page.route('**/health', (route) => {
		if (!isApiRequest(route)) return route.continue();
		return route.fulfill({ json: { status: 'setup_required' } });
	});
}

export async function mockNotLoggedIn(page: Page) {
	await page.route('**/health', (route) => {
		if (!isApiRequest(route)) return route.continue();
		return route.fulfill({ json: { status: 'ok' } });
	});
	await page.route('**/user/me', (route) => {
		if (!isApiRequest(route)) return route.continue();
		return route.fulfill({ json: { logged_in: false } });
	});
}

// ---------------------------------------------------------------------------
// Common endpoint mocks
// ---------------------------------------------------------------------------

export async function mockDashboard(page: Page) {
	await page.route('**/apis?*', (route) => {
		if (!isApiRequest(route)) return route.continue();
		return route.fulfill({ json: { data: [], total: 0, page: 1 } });
	});
	await page.route('**/toolkits', (route) => {
		if (!isApiRequest(route)) return route.continue();
		return route.fulfill({ json: [] });
	});
	await page.route('**/workflows', (route) => {
		if (!isApiRequest(route)) return route.continue();
		return route.fulfill({ json: [] });
	});
	await page.route('**/traces?*', (route) => {
		if (!isApiRequest(route)) return route.continue();
		return route.fulfill({ json: { traces: [], total: 0 } });
	});
}

export async function mockSearch(page: Page) {
	await page.route('**/search?*', (route) => {
		if (!isApiRequest(route)) return route.continue();
		return route.fulfill({ json: [] });
	});
}

export async function mockCatalog(page: Page) {
	await page.route('**/apis?*', (route) => {
		if (!isApiRequest(route)) return route.continue();
		return route.fulfill({ json: { data: [], total: 0, page: 1 } });
	});
	await page.route('**/catalog?*', (route) => {
		if (!isApiRequest(route)) return route.continue();
		return route.fulfill({ json: [] });
	});
	await page.route('**/catalog', (route) => {
		if (!isApiRequest(route)) return route.continue();
		return route.fulfill({ json: [] });
	});
}

export async function mockToolkits(page: Page) {
	await page.route('**/toolkits', (route) => {
		if (!isApiRequest(route)) return route.continue();
		return route.fulfill({ json: [] });
	});
}

export async function mockToolkitDetail(page: Page, id = 'test-tk') {
	await page.route(`**/toolkits/${id}`, (route) => {
		if (!isApiRequest(route)) return route.continue();
		const data: FixtureToolkit = {
			id,
			name: 'Test Toolkit',
			description: 'A test toolkit',
			disabled: false,
			simulate: false,
			keys: [],
			credentials: [],
		};
		return route.fulfill({ json: data });
	});
	await page.route(`**/toolkits/${id}/keys`, (route) => {
		if (!isApiRequest(route)) return route.continue();
		return route.fulfill({ json: { keys: [] } });
	});
	await page.route(`**/toolkits/${id}/access-requests*`, (route) => {
		if (!isApiRequest(route)) return route.continue();
		return route.fulfill({ json: [] });
	});
	await page.route(`**/toolkits/${id}/credentials`, (route) => {
		if (!isApiRequest(route)) return route.continue();
		return route.fulfill({ json: [] });
	});
}

export async function mockCredentials(page: Page) {
	await page.route('**/credentials', (route) => {
		if (!isApiRequest(route)) return route.continue();
		return route.fulfill({ json: { data: [], total: 0 } });
	});
	await page.route('**/credentials?*', (route) => {
		if (!isApiRequest(route)) return route.continue();
		return route.fulfill({ json: { data: [], total: 0 } });
	});
}

export async function mockCredentialForm(page: Page) {
	await page.route('**/apis?*', (route) => {
		if (!isApiRequest(route)) return route.continue();
		return route.fulfill({ json: { data: [], total: 0, page: 1 } });
	});
}

export async function mockOAuthBrokers(page: Page) {
	await page.route('**/oauth-brokers', (route) => {
		if (!isApiRequest(route)) return route.continue();
		return route.fulfill({ json: [] });
	});
}

export async function mockTraces(page: Page) {
	await page.route('**/traces?*', (route) => {
		if (!isApiRequest(route)) return route.continue();
		return route.fulfill({ json: { traces: [], total: 0 } });
	});
	await page.route('**/traces', (route) => {
		if (!isApiRequest(route)) return route.continue();
		return route.fulfill({ json: { traces: [], total: 0 } });
	});
}

export async function mockTraceDetail(page: Page, id = 'trace-1') {
	await page.route(`**/traces/${id}`, (route) => {
		if (!isApiRequest(route)) return route.continue();
		const data: FixtureTrace = {
			id,
			toolkit_id: 'test-tk',
			operation_id: 'listUsers',
			workflow_id: null,
			status: 'ok',
			http_status: 200,
			duration_ms: 120,
			created_at: Math.floor(Date.now() / 1000) - 60,
		};
		return route.fulfill({ json: data });
	});
}

export async function mockWorkflows(page: Page) {
	// `/workflows` returns a bare array by default (every consumer outside
	// the workspace grid expects this). When the workspace pagination
	// fan-out passes `page`/`limit`, switch to the
	// `{data, total, page, limit, total_pages}` envelope. Mirrors the MSW
	// handler in `ui/src/__tests__/mocks/handlers.ts`.
	await page.route('**/workflows', (route) => {
		if (!isApiRequest(route)) return route.continue();
		return route.fulfill({ json: [] });
	});
	await page.route('**/workflows?*', (route) => {
		if (!isApiRequest(route)) return route.continue();
		const url = new URL(route.request().url());
		const paged = url.searchParams.has('page') || url.searchParams.has('limit');
		return route.fulfill({
			json: paged ? { data: [], total: 0, page: 1, limit: 20, total_pages: 1 } : [],
		});
	});
}

export async function mockWorkflowDetail(page: Page, slug = 'test-workflow') {
	await page.route(`**/workflows/${slug}`, (route) => {
		if (!isApiRequest(route)) return route.continue();
		const data: FixtureWorkflow = {
			slug,
			name: 'Test Workflow',
			description: 'A test workflow',
			steps: [
				{ id: 'step-1', operation: 'doSomething', description: 'First step' },
				{ id: 'step-2', operation: 'doMore', description: 'Second step' },
			],
			inputs: { input1: { type: 'string', required: true } },
			involved_apis: ['test-api'],
		};
		return route.fulfill({ json: data });
	});
}

export async function mockJobs(page: Page) {
	await page.route('**/jobs?*', (route) => {
		if (!isApiRequest(route)) return route.continue();
		return route.fulfill({ json: { items: [], total: 0 } });
	});
	await page.route('**/jobs', (route) => {
		if (!isApiRequest(route)) return route.continue();
		return route.fulfill({ json: { items: [], total: 0 } });
	});
}

export async function mockJobDetail(page: Page, id = 'job-1') {
	await page.route(`**/jobs/${id}`, (route) => {
		if (!isApiRequest(route)) return route.continue();
		const data: FixtureJob = {
			id,
			kind: 'execute',
			status: 'complete',
			toolkit_id: 'test-tk',
			created_at: Math.floor(Date.now() / 1000) - 300,
			result: { success: true },
		};
		return route.fulfill({ json: data });
	});
}

export async function mockApproval(page: Page, toolkitId = 'tk-1', reqId = 'req-1') {
	await page.route(`**/toolkits/${toolkitId}`, (route) => {
		if (!isApiRequest(route)) return route.continue();
		return route.fulfill({
			json: { id: toolkitId, name: 'Test Toolkit' },
		});
	});
	await page.route(`**/toolkits/${toolkitId}/access-requests/${reqId}`, (route) => {
		if (!isApiRequest(route)) return route.continue();
		const data: FixtureAccessRequest = {
			id: reqId,
			toolkit_id: toolkitId,
			type: 'grant',
			status: 'pending',
			reason: 'Need access for testing',
			created_at: Math.floor(Date.now() / 1000) - 120,
			payload: {
				credential_id: 'cred-1',
				api_id: 'github.com',
				rules: [],
			},
		};
		return route.fulfill({ json: data });
	});
}
