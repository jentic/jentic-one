import { http, HttpResponse } from 'msw';

/**
 * Default "happy path" MSW handlers.
 *
 * Relative paths work because OpenAPI.BASE is '' (empty string) and
 * the hand-written fetchJson() also uses relative URLs.
 * If BASE is ever set to an absolute URL, update these to match.
 */
export const handlers = [
	// ── Auth & health ───────────────────────────────────────────────
	http.get('/health', () => HttpResponse.json({ status: 'ok' })),

	http.get('/user/me', () => HttpResponse.json({ logged_in: true, username: 'admin' })),

	http.post('/user/login', () => HttpResponse.json({ logged_in: true, username: 'admin' })),

	http.post('/user/logout', () => HttpResponse.json({ logged_out: true })),

	http.get('/version', () => HttpResponse.json({ current: '0.3.0', latest: '0.3.0' })),

	// ── Dashboard data ──────────────────────────────────────────────
	http.get('/apis', () => HttpResponse.json({ data: [], total: 0, page: 1 })),

	http.get('/toolkits', () => HttpResponse.json([])),

	http.get('/workflows', ({ request }) => {
		// `/workflows` returns a bare array by default (every consumer
		// outside the workspace grid expects this). When the workspace
		// pagination fan-out passes `page`/`limit`, switch to the
		// `{data, total, page, limit, total_pages}` envelope.
		const url = new URL(request.url);
		const paged = url.searchParams.has('page') || url.searchParams.has('limit');
		return HttpResponse.json(
			paged ? { data: [], total: 0, page: 1, limit: 20, total_pages: 1 } : [],
		);
	}),

	http.get('/traces', () => HttpResponse.json({ traces: [], total: 0 })),

	// ── Toolkit detail ──────────────────────────────────────────────
	http.get('/toolkits/:id', ({ params }) =>
		HttpResponse.json({
			id: params.id,
			name: 'Test Toolkit',
			description: 'A test toolkit',
			simulate: false,
			disabled: false,
			keys: [],
			credentials: [],
		}),
	),

	http.get('/toolkits/:id/keys', () => HttpResponse.json({ keys: [] })),

	http.get('/toolkits/:id/access-requests', () => HttpResponse.json([])),

	http.get('/toolkits/:id/credentials', () => HttpResponse.json([])),

	// ── Toolkit mutations ───────────────────────────────────────────
	http.post('/toolkits', () =>
		HttpResponse.json({ id: 'new-tk', name: 'New Toolkit', description: '', credentials: [] }),
	),

	http.patch('/toolkits/:id', ({ params }) =>
		HttpResponse.json({
			id: params.id,
			name: 'Updated Toolkit',
			description: 'Updated',
			disabled: false,
			credentials: [],
		}),
	),

	http.delete('/toolkits/:id', () => new HttpResponse(null, { status: 204 })),

	http.post('/toolkits/:id/keys', () =>
		HttpResponse.json({
			id: 'new-key',
			key: 'jntc_test_generated_key_123',
			prefix: 'jntc_test',
			label: null,
			created_at: Math.floor(Date.now() / 1000),
		}),
	),

	http.delete('/toolkits/:id/keys/:keyId', () => new HttpResponse(null, { status: 204 })),

	http.post('/toolkits/:id/access-requests', () =>
		HttpResponse.json({
			id: 'req-new',
			status: 'pending',
			approve_url: '/approve/test-tk/req-new',
		}),
	),

	http.get('/toolkits/:id/credentials/:credId/permissions', () => HttpResponse.json([])),

	http.put('/toolkits/:id/credentials/:credId/permissions', () =>
		HttpResponse.json({ ok: true }),
	),

	http.delete('/toolkits/:id/credentials/:credId', () => new HttpResponse(null, { status: 204 })),

	// ── Credentials ─────────────────────────────────────────────────
	http.get('/credentials', () => HttpResponse.json({ data: [], total: 0 })),

	http.get('/credentials/:id', ({ params }) =>
		HttpResponse.json({
			id: params.id,
			label: 'Test Credential',
			api_id: 'test-api',
			auth_type: 'bearer',
		}),
	),

	http.post('/credentials', async ({ request }) => {
		const body = (await request.json()) as Record<string, unknown>;
		return HttpResponse.json({ id: 'cred-new', ...body });
	}),

	http.patch('/credentials/:id', async ({ params, request }) => {
		const body = (await request.json()) as Record<string, unknown>;
		return HttpResponse.json({ id: params.id, ...body });
	}),

	http.delete('/credentials/:id', () => new HttpResponse(null, { status: 204 })),

	// ── API detail (for credential form) ────────────────────────────
	http.get('/apis/:id', ({ params }) =>
		HttpResponse.json({
			id: params.id,
			name: 'Test API',
			source: 'local',
			security_schemes: { bearerAuth: { type: 'http', scheme: 'bearer' } },
		}),
	),

	// ── Search & inspect ──────────────────────────────────────────
	http.get('/search', () => HttpResponse.json([])),
	// Mirror the real backend shape returned by `src/routers/capability.py`:
	// `parameters` is a dict keyed by location (NOT an array), and auth
	// lives under `auth` (NOT `auth_instructions`). Two early bugs in
	// InspectPanel were silently rendering nothing because the mock here
	// had drifted from the real response — keep them aligned.
	http.get('/inspect/:id', () =>
		HttpResponse.json({
			id: 'GET/api.example.com/test',
			method: 'GET',
			url: 'https://api.example.com/test',
			name: 'getTest',
			summary: 'Test operation',
			parameters: { query: [{ name: 'limit', required: false, description: 'Page size' }] },
			auth: [{ scheme: 'apiKey', type: 'api_key', in: 'header', name: 'X-API-Key' }],
			api: { id: 'example.com', name: 'example.com' },
			_links: {
				self: '/inspect/GET%2Fapi.example.com%2Ftest',
				upstream: 'https://api.example.com/test',
			},
		}),
	),

	// ── Catalog ─────────────────────────────────────────────────────
	http.get('/catalog', () => HttpResponse.json([])),
	http.get('/catalog/:id', () =>
		HttpResponse.json({
			id: 'test-api',
			name: 'Test API',
			spec_url: 'https://example.com/openapi.json',
		}),
	),
	http.post('/catalog/refresh', () => HttpResponse.json({ status: 'ok' })),
	http.post('/import', () =>
		HttpResponse.json({ results: [{ status: 'success', api_id: 'imported-api' }] }),
	),

	// ── Approval ──────────────────────────────────────────────────
	http.get('/toolkits/:id/access-requests/:reqId', ({ params }) =>
		HttpResponse.json({
			id: params.reqId,
			toolkit_id: params.id,
			type: 'grant',
			status: 'pending',
			reason: 'Need access',
			created_at: Math.floor(Date.now() / 1000),
			payload: { credential_id: 'cred-1', api_id: 'test-api', rules: [] },
		}),
	),
	http.post('/toolkits/:id/access-requests/:reqId/approve', () =>
		HttpResponse.json({ status: 'approved' }),
	),
	http.post('/toolkits/:id/access-requests/:reqId/deny', () =>
		HttpResponse.json({ status: 'denied' }),
	),

	// ── Jobs ──────────────────────────────────────────────────────
	http.get('/jobs', () => HttpResponse.json({ items: [], total: 0 })),
	http.delete('/jobs/:id', () => new HttpResponse(null, { status: 204 })),

	// ── OAuth brokers ─────────────────────────────────────────────
	http.get('/oauth-brokers', () => HttpResponse.json([])),
	http.post('/oauth-brokers', async ({ request }) => {
		const body = (await request.json()) as Record<string, unknown>;
		return HttpResponse.json({ id: 'broker-new', ...body });
	}),

	// ── API operations ────────────────────────────────────────────
	http.get('/apis/:id/operations', () => HttpResponse.json({ items: [], total: 0 })),

	// ── Default API key ─────────────────────────────────────────────
	http.post('/default-api-key/generate', () =>
		HttpResponse.json({ key: 'jntc_test_key_abc123' }),
	),

	// ── User creation ───────────────────────────────────────────────
	http.post('/user/create', () => HttpResponse.json({ username: 'admin' })),
];
