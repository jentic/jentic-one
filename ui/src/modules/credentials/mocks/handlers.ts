import { http, HttpResponse } from 'msw';
import {
	CredentialType,
	type Credential,
	type CredentialCreateRequest,
	type CredentialUpdateRequest,
} from '@/modules/credentials/api';
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
		const body = (await request.json()) as CredentialUpdateRequest;
		const next: Credential = {
			...store[idx],
			name: (body as { name?: string }).name ?? store[idx].name,
			active: (body as { active?: boolean }).active ?? store[idx].active,
			updated_at: new Date().toISOString(),
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
