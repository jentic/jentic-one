import { http, HttpResponse } from 'msw';
import {
	CredentialType,
	type Credential,
	type CredentialCreateRequest,
	type CredentialUpdateRequest,
} from '@/shared/credentials/api';
import type { ApiResponse, CatalogEntryResponse } from '@/shared/api';

/**
 * In-memory credentials store for MSW (mocked dev — Mode A — and browser/e2e
 * tests). Faithful to the real jentic-one contract: cursor-paginated list
 * envelope, redacted reads, one-time secret on create, connect → authorize_url.
 *
 * Tests can reset and seed this store via `resetCredentialsStore`.
 */
let store: Credential[] = [];
let seq = 0;

// When false, the mocked connect flow returns an authorize_url but never
// "completes" the connection (no provider_account_ref / updated_at bump). Lets
// e2e exercise the abandoned-connect path deterministically. Defaults to true
// so existing flows (mocked dev, other specs) still auto-complete.
let connectAutoCompletes = true;

/** Toggle whether the mocked connect flow auto-completes. For tests. */
export function setConnectAutoCompletes(value: boolean): void {
	connectAutoCompletes = value;
}

function redact(body: CredentialCreateRequest, id: string, now: string): Credential {
	const details: Record<string, unknown> = {};
	if (body.type === 'api_key') {
		details.location = (body as { location?: string }).location ?? 'header';
		details.field_name = (body as { field_name?: string }).field_name;
		details.hint = '••••';
	} else if (body.type === 'bearer_token' || body.type === 'oauth2' || body.type === 'basic') {
		details.hint = '••••';
	}
	return {
		credential_id: id,
		name: body.name,
		type: body.type as CredentialType,
		provider: (body as { provider?: string }).provider ?? 'manual',
		api: {
			vendor: body.api.vendor,
			name: body.api.name ?? 'default',
			version: body.api.version ?? '1.0.0',
		},
		active: true,
		details,
		provider_account_ref: null,
		created_at: now,
		updated_at: null,
	};
}

function secretFor(body: CredentialCreateRequest): Record<string, unknown> {
	switch (body.type) {
		case 'bearer_token':
			return { token: (body as { token?: string }).token };
		case 'api_key':
			return { key: (body as { key?: string }).key };
		case 'basic':
			return {
				username: (body as { username?: string }).username,
				password: (body as { password?: string }).password,
			};
		case 'oauth2':
			return { client_secret: (body as { client_secret?: string }).client_secret };
		default:
			return {};
	}
}

/** Reset (and optionally seed) the mock store. For tests. */
export function resetCredentialsStore(seed: Credential[] = []): void {
	store = [...seed];
	seq = seed.length;
	connectAutoCompletes = true;
}

/** Build a redacted credential for seeding tests. */
export function makeMockCredential(overrides: Partial<Credential> = {}): Credential {
	seq += 1;
	return {
		credential_id: `cred_${seq}`,
		name: `Credential ${seq}`,
		type: CredentialType.BEARER_TOKEN,
		provider: 'manual',
		api: { vendor: 'acme', name: 'default', version: '1.0.0' },
		active: true,
		details: { hint: '••••' },
		provider_account_ref: null,
		created_at: '2026-01-01T00:00:00Z',
		updated_at: null,
		...overrides,
	};
}

/**
 * The vault as an operator would find it after a few weeks of use — the seed
 * mocked dev starts from.
 *
 * Every row is here to put one distinguishable state on screen at once, so the
 * surfaces that read this store (the Credentials page, the inventory sheet, an
 * agent's API tiles, the bound-agents roster) can be walked without first
 * creating anything:
 *
 *   - `cred_slack_1` / `cred_github_1` are the two ids the agents fixture binds
 *     to `agnt_active_1`, so the tiles, the roster and this list agree on one
 *     set of secrets. They are also the only **bound** rows, which is what
 *     gives the inventory's `Unbound (N)` filter something to subtract.
 *   - all five credential types appear, so the type filter never lands on an
 *     empty result, and each one's plain-language auth placement is legible: a
 *     key in a header, a key in a query parameter, Basic, both OAuth states
 *     (managed-and-connected vs awaiting its first sign-in), and SigV4.
 *   - `cred_zendesk_legacy` is deactivated, so the `Inactive` clause shows.
 *
 * `resetCredentialsStore()` with no seed still empties the store, so tests that
 * want an empty vault (or their own fixtures) are unaffected by this.
 */
function devCredentialsSeed(): Credential[] {
	const base = {
		provider: 'static',
		active: true,
		provider_account_ref: null,
		updated_at: null,
	} as const;
	return [
		{
			...base,
			credential_id: 'cred_slack_1',
			name: 'Slack bot token',
			type: CredentialType.BEARER_TOKEN,
			api: { vendor: 'slack.com', name: 'default', version: '1.0.0' },
			catalog_api_id: 'slack.com',
			details: { hint: '••••xoxb' },
			created_at: '2026-03-02T09:12:00Z',
		},
		{
			...base,
			credential_id: 'cred_github_1',
			name: 'GitHub PAT',
			type: CredentialType.BEARER_TOKEN,
			api: { vendor: 'github', name: 'github-api', version: '1.1.4' },
			catalog_api_id: 'github.com',
			details: { hint: '••••ghp7' },
			created_at: '2026-02-18T14:40:00Z',
			updated_at: '2026-07-01T08:05:00Z',
		},
		{
			...base,
			credential_id: 'cred_stripe_1',
			name: 'Stripe live key',
			type: CredentialType.API_KEY,
			api: { vendor: 'stripe', name: 'stripe-api', version: '2024-01-01' },
			catalog_api_id: 'stripe.com',
			details: { location: 'header', field_name: 'X-Api-Key', hint: '••••4242' },
			created_at: '2026-01-21T11:02:00Z',
		},
		{
			...base,
			credential_id: 'cred_nyt_1',
			name: 'NYT article search key',
			type: CredentialType.API_KEY,
			api: { vendor: 'nytimes.com', name: 'article_search', version: '1.0.0' },
			catalog_api_id: 'nytimes.com/article_search',
			details: { location: 'query', field_name: 'api-key', hint: '••••9f1c' },
			created_at: '2026-05-09T16:30:00Z',
		},
		{
			...base,
			credential_id: 'cred_bigco_1',
			name: 'BigCo reporting service account',
			type: CredentialType.BASIC,
			api: { vendor: 'bigco', name: 'big-api', version: '1' },
			details: { hint: 'svc-reporting / ••••' },
			created_at: '2026-04-14T07:55:00Z',
		},
		{
			...base,
			credential_id: 'cred_sheets_1',
			name: 'Google Sheets (Pipedream)',
			type: CredentialType.OAUTH2,
			provider: 'pipedream',
			// A managed grant that has been through Connect: the card reads
			// "Managed via Pipedream" and carries the Connected badge.
			provider_account_ref: 'apn_mock_sheets',
			api: { vendor: 'googleapis.com', name: 'sheets', version: 'v4' },
			details: { connected: true, scopes: ['spreadsheets.readonly'] },
			created_at: '2026-06-01T10:15:00Z',
			updated_at: '2026-08-22T09:00:00Z',
		},
		{
			...base,
			credential_id: 'cred_zoom_1',
			name: 'Zoom OAuth app',
			type: CredentialType.OAUTH2,
			provider: 'direct_oauth2',
			// Stored but never signed in, so the sign-in-needed path is on screen
			// (and the card's Connect button is the primary action).
			api: { vendor: 'zoom.us', name: 'default', version: '2.0.0' },
			details: {
				client_id: 'zoom-client-6f2a',
				token_url: 'https://zoom.us/oauth/token',
				grant_type: 'authorization_code',
				scopes: ['meeting:read', 'user:read'],
				connected: false,
			},
			created_at: '2026-08-30T13:20:00Z',
		},
		{
			...base,
			credential_id: 'cred_aws_1',
			name: 'AWS reporting signer',
			type: CredentialType.SIGV4,
			api: { vendor: 'amazonaws.com', name: 's3', version: '2006-03-01' },
			details: { aws_region: 'eu-west-1', aws_access_key_id: 'AKIA••••7QDX' },
			created_at: '2026-02-02T08:00:00Z',
		},
		{
			...base,
			credential_id: 'cred_zendesk_legacy',
			name: 'Retired Zendesk token',
			type: CredentialType.BEARER_TOKEN,
			active: false,
			api: { vendor: 'zendesk.com', name: 'support', version: '2.0.0' },
			details: { hint: '••••zd44' },
			created_at: '2025-11-11T12:00:00Z',
			updated_at: '2026-07-19T15:45:00Z',
		},
	];
}

resetCredentialsStore(devCredentialsSeed());

// ---------------------------------------------------------------------------
// Guided picker store: workspace APIs + their served OpenAPI specs, public
// catalog entries, and the canned specs we serve when the credential form
// fetches a catalog `spec_url`. All in-memory; reset/seed for tests.
// ---------------------------------------------------------------------------

export interface MockApiRow {
	row: ApiResponse;
	spec: Record<string, unknown>;
}

let apisStore: MockApiRow[] = [];
let catalogStore: { entry: CatalogEntryResponse }[] = [];
let catalogSpecStore: Record<string, Record<string, unknown>> = {};

/** Reset (and optionally seed) the picker stores. */
export function resetApisStore(
	apis: MockApiRow[] = [],
	catalog: { entry: CatalogEntryResponse; spec?: Record<string, unknown> }[] = [],
): void {
	apisStore = [...apis];
	catalogStore = catalog.map(({ entry }) => ({ entry: { ...entry } }));
	catalogSpecStore = {};
	for (const c of catalog) {
		if (c.spec && c.entry.spec_url) {
			// We index by the URL's final slug segment so handlers can route
			// without depending on the full URL parsing.
			const slug = c.entry.spec_url.split('/').slice(-2, -1)[0] ?? c.entry.api_id;
			catalogSpecStore[slug] = c.spec;
		}
	}
}

/** Build a mock workspace API row + spec. */
export function makeMockApi(
	overrides: Partial<{
		vendor: string;
		name: string;
		version: string;
		displayName: string;
		catalogApiId: string;
		securitySchemes: string[];
		spec: Record<string, unknown>;
	}> = {},
): MockApiRow {
	const vendor = overrides.vendor ?? 'acme';
	const name = overrides.name ?? 'default';
	const version = overrides.version ?? '1.0.0';
	const securitySchemes = overrides.securitySchemes ?? ['apiKey'];
	const spec =
		overrides.spec ??
		({
			openapi: '3.0.0',
			info: { title: overrides.displayName ?? vendor, version },
			servers: [{ url: 'https://api.example.com' }],
			components: {
				securitySchemes: {
					ApiKeyAuth: { type: 'apiKey', in: 'header', name: 'X-Api-Key' },
				},
			},
		} as Record<string, unknown>);
	return {
		row: {
			_links: { current_revision: null, revisions: '', self: '' },
			api: { vendor, name, version, host: 'api.example.com' },
			catalog_api_id: overrides.catalogApiId ?? null,
			created_at: '2026-01-01T00:00:00Z',
			updated_at: '2026-01-01T00:00:00Z',
			current_revision_id: 'rev_1',
			description: null,
			display_name: overrides.displayName ?? `${vendor}/${name}`,
			icon_url: null,
			operation_count: 1,
			revision_count: 1,
			security_schemes: securitySchemes,
		},
		spec,
	};
}

/** Build a mock catalog entry + its served spec. */
export function makeMockCatalogEntry(
	overrides: Partial<{
		apiId: string;
		vendor: string;
		path: string;
		registered: boolean;
		spec: Record<string, unknown>;
	}> = {},
): { entry: CatalogEntryResponse; spec: Record<string, unknown> } {
	const apiId = overrides.apiId ?? 'mock.example';
	const spec =
		overrides.spec ??
		({
			openapi: '3.0.0',
			info: { title: apiId, version: '1.0.0' },
			components: {
				securitySchemes: {
					Bearer: { type: 'http', scheme: 'bearer' },
				},
			},
		} as Record<string, unknown>);
	return {
		entry: {
			api_id: apiId,
			vendor: overrides.vendor ?? apiId.split('.')[0] ?? apiId,
			path: overrides.path ?? `${apiId}/main/1.0.0`,
			spec_url: `https://mock-spec.test/${apiId}/openapi.json`,
			registered: overrides.registered ?? false,
			_links: {
				self: '',
				operations: '',
				import: '',
				github: null,
			},
		},
		spec,
	};
}

/**
 * Test-only hooks the mocked e2e specs drive via `window` (seed deterministic
 * fixtures, then clear caches). Aggregated by the shared MSW root
 * (`src/mocks/handlers.ts` → `installE2eTestHooks`) so the app root stays
 * module-agnostic — never imported by `main.tsx` directly. DEV + MSW only;
 * tree-shaken from production builds.
 */
export const credentialsE2eHooks = {
	resetCredentialsStore,
	resetApisStore,
	makeMockApi,
	makeMockCredential,
	setConnectAutoCompletes,
};

export const credentialsHandlers = [
	http.get('/credentials', ({ request }) => {
		const url = new URL(request.url);
		const vendor = url.searchParams.get('vendor');
		const filtered = vendor ? store.filter((c) => c.api.vendor === vendor) : store;
		return HttpResponse.json({
			data: filtered,
			has_more: false,
			next_cursor: null,
		});
	}),

	http.post('/credentials', async ({ request }) => {
		const body = (await request.json()) as CredentialCreateRequest;
		seq += 1;
		const id = `cred_${seq}`;
		const now = new Date().toISOString();
		const credential = redact(body, id, now);
		store.push(credential);
		return HttpResponse.json({ credential, secret: secretFor(body) }, { status: 201 });
	}),

	http.get('/credentials/providers', () => {
		return HttpResponse.json({
			providers: [
				{
					id: 'static',
					label: 'Static',
					managed: false,
					types: ['bearer_token', 'api_key', 'basic', 'oauth2'],
					configured: true,
					callback_url: null,
				},
				{
					id: 'direct_oauth2',
					label: 'Direct Oauth2',
					managed: true,
					types: ['oauth2'],
					configured: true,
					callback_url: 'http://localhost:8000/credentials/oauth/callback',
				},
			],
		});
	}),

	http.get('/credentials/:id', ({ params }) => {
		const cred = store.find((c) => c.credential_id === params.id);
		if (!cred) return new HttpResponse(null, { status: 404 });
		return HttpResponse.json(cred);
	}),

	http.patch('/credentials/:id', async ({ params, request }) => {
		const idx = store.findIndex((c) => c.credential_id === params.id);
		if (idx === -1) return new HttpResponse(null, { status: 404 });
		const body = (await request.json()) as CredentialUpdateRequest & {
			field_name?: string;
			location?: string;
			key?: string;
			token?: string;
			client_secret?: string;
			password?: string;
		};
		const cur = store[idx];
		const det = (cur.details ?? {}) as { field_name?: string; location?: string };
		// field_name/location are immutable after create (#589): a *changed*
		// value is rejected; a matching echo is tolerated as a no-op.
		if (body.field_name !== undefined && body.field_name !== det.field_name) {
			return HttpResponse.json({ type: 'immutable_field' }, { status: 409 });
		}
		if (body.location !== undefined && body.location !== det.location) {
			return HttpResponse.json({ type: 'immutable_field' }, { status: 409 });
		}
		// updated_at moves iff something was actually persisted (#739).
		const changed =
			body.name !== undefined ||
			body.active !== undefined ||
			body.server_variables !== undefined ||
			body.key !== undefined ||
			body.token !== undefined ||
			body.client_secret !== undefined ||
			body.password !== undefined;
		const next: Credential = {
			...cur,
			name: (body as { name?: string }).name ?? cur.name,
			active: (body as { active?: boolean }).active ?? cur.active,
			...(changed ? { updated_at: new Date().toISOString() } : {}),
		};
		store[idx] = next;
		return HttpResponse.json(next);
	}),

	http.delete('/credentials/:id', ({ params }) => {
		const before = store.length;
		store = store.filter((c) => c.credential_id !== params.id);
		if (store.length === before) return new HttpResponse(null, { status: 404 });
		return new HttpResponse(null, { status: 204 });
	}),

	http.post('/credentials/:id/connect', async ({ params }) => {
		const id = String(params.id);
		const cred = store.find((c) => c.credential_id === id);
		const managed = cred?.provider === 'pipedream';

		// Simulate the user completing the hosted sign-in: shortly after the
		// connect link is opened, the backend callback would persist the
		// connection. We mirror that here so the page's poll observes a result.
		// Skipped when `connectAutoCompletes` is off, so tests can exercise the
		// abandoned-connect (cancel/timeout) path.
		if (connectAutoCompletes) {
			setTimeout(() => {
				const target = store.find((c) => c.credential_id === id);
				if (target) {
					target.provider_account_ref = managed ? `apn_${seq}_mock` : 'connected';
					target.updated_at = new Date().toISOString();
				}
			}, 600);
		}

		const authorizeUrl = managed
			? `https://pipedream.com/connect/mock-token?credential=${id}`
			: `https://provider.example.com/oauth/authorize?credential=${id}&state=mock-state`;
		return HttpResponse.json({ authorize_url: authorizeUrl, state: 'mock-state' });
	}),

	// ---------------------------------------------------------------------------
	// Guided add-credential flow: workspace APIs (`/apis`), catalog
	// (`/catalog`), per-API OpenAPI doc (`/apis/.../openapi`), catalog import
	// (`/catalog/{id}:import`), and an in-memory passthrough for the catalog
	// `spec_url` so tests run offline.
	//
	// These handlers cooperate with the discover module's `/apis` + `/catalog`
	// mocks: when the credentials picker store is empty (the default), each
	// handler `return`s `undefined` so MSW falls through to the next matcher
	// (discover's). When a test seeds via `resetApisStore([...], [...])`, our
	// handlers respond and the seeded data flows into the picker.
	// ---------------------------------------------------------------------------
	http.get('/apis', () => {
		if (apisStore.length === 0) return undefined;
		return HttpResponse.json({
			data: apisStore.map(({ row }) => row),
			has_more: false,
			next_cursor: null,
		});
	}),

	http.get('/apis/:vendor/:name/:version/openapi', ({ params }) => {
		if (apisStore.length === 0) return undefined;
		const key = `${params.vendor}/${params.name}/${params.version}`;
		const entry = apisStore.find(
			(a) => `${a.row.api.vendor}/${a.row.api.name}/${a.row.api.version}` === key,
		);
		if (!entry) return new HttpResponse(null, { status: 404 });
		return HttpResponse.json(entry.spec);
	}),

	http.get('/catalog', ({ request }) => {
		if (catalogStore.length === 0) return undefined;
		const url = new URL(request.url);
		const q = (url.searchParams.get('q') ?? '').trim().toLowerCase();
		const filtered = catalogStore.filter((e) => {
			if (!q) return true;
			return [e.entry.api_id, e.entry.vendor, e.entry.path]
				.filter(Boolean)
				.some((v) => String(v).toLowerCase().includes(q));
		});
		return HttpResponse.json({
			data: filtered.map((c) => c.entry),
			catalog_total: catalogStore.length,
			registered_count: catalogStore.filter((c) => c.entry.registered).length,
			has_more: false,
			next_cursor: null,
		});
	}),

	http.get('/catalog/:apiId', ({ params }) => {
		if (catalogStore.length === 0) return undefined;
		const entry = catalogStore.find((c) => c.entry.api_id === params.apiId);
		if (!entry) return new HttpResponse(null, { status: 404 });
		return HttpResponse.json(entry.entry);
	}),

	http.post('/catalog/:apiId\\:import', ({ params }) => {
		if (catalogStore.length === 0) return undefined;
		const entry = catalogStore.find((c) => c.entry.api_id === params.apiId);
		if (!entry) return new HttpResponse(null, { status: 404 });
		entry.entry.registered = true;
		// Mirror the real `ApiImportResponse` shape ({ job_id, status, _links })
		// so this mock can't mask a future shape mismatch.
		return HttpResponse.json(
			{
				job_id: 'mock-import-job',
				status: 'queued',
				_links: { self: `/jobs/mock-import-job` },
			},
			{ status: 202 },
		);
	}),

	// Catalog spec URLs are public-internet (raw.githubusercontent.com in prod).
	// The mock store keeps a canned spec per URL so the picker hook resolves
	// schemes offline in tests.
	http.get('https://mock-spec.test/:slug/openapi.json', ({ params }) => {
		const spec = catalogSpecStore[String(params.slug)];
		if (!spec) return new HttpResponse(null, { status: 404 });
		return HttpResponse.json(spec);
	}),
];
