import { http, HttpResponse } from 'msw';
import {
	CredentialType,
	type Credential,
	type CredentialCreateRequest,
	type CredentialUpdateRequest,
} from '@/shared/credentials/api';
import type {
	ConfirmRequest,
	ConnectRequest,
	SessionStatus,
	StatusResponse,
	VendorAuthCapabilities,
	VendorSummary,
} from '@/shared/credentials/api/vendors-types';
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

// ---------------------------------------------------------------------------
// Agent-driven OAuth connect flow: vendors registry, per-vendor auth
// capabilities, and the connect-session lifecycle
// (`POST /integrations:connect` → review → `:confirm` → `/status` →
// `:cancel`). Faithful to the hardened wire contract: the review GET,
// `:confirm`, `/status`, and `:cancel` are ALL poll_token-gated, and a
// missing session or a token mismatch both surface as 403 problem+json
// (no session-id enumeration oracle). Reset/seed for tests via
// `resetConnectSessionsStore` and friends.
// ---------------------------------------------------------------------------

export interface MockConnectSession {
	session_id: string;
	poll_token: string;
	state: string;
	vendor_key: string;
	credential_id: string;
	requested_by_actor_id: string;
	requested_scopes: string[];
	requested_permission_rules: NonNullable<ConfirmRequest['permission_rules']>;
	reason: string | null;
	/** Status served by `/status` polls. Tests flip it to drive terminal states. */
	status: StatusResponse;
	/** Captured `:confirm` request bodies, newest last — for assertions. */
	confirmBodies: ConfirmRequest[];
}

let connectSessionsStore: MockConnectSession[] = [];
let connectSessionSeq = 0;
let vendorsStore: VendorSummary[] = [];
let vendorCapabilitiesStore: Record<string, VendorAuthCapabilities> = {};
// Which challenge shape `:confirm` answers with. Device is the default —
// it's the flow the wizard's awaiting step renders most of.
let confirmChallengeKind: 'device_authorization' | 'authorization_code' = 'device_authorization';

/** Reset the connect-session mock state (sessions, vendors, capabilities). */
export function resetConnectSessionsStore(): void {
	connectSessionsStore = [];
	connectSessionSeq = 0;
	vendorsStore = [];
	vendorCapabilitiesStore = {};
	confirmChallengeKind = 'device_authorization';
}

/** Seed the `/vendors` registry. */
export function setMockVendors(vendors: VendorSummary[]): void {
	vendorsStore = [...vendors];
}

/** Seed a vendor's `/vendors/{key}/auth-capabilities` payload. */
export function setMockVendorCapabilities(key: string, caps: VendorAuthCapabilities): void {
	vendorCapabilitiesStore[key] = caps;
}

/** Choose which challenge shape `:confirm` returns. */
export function setMockConfirmChallengeKind(
	kind: 'device_authorization' | 'authorization_code',
): void {
	confirmChallengeKind = kind;
}

/** Read the session store (e.g. to assert captured `:confirm` bodies). */
export function getMockConnectSessions(): readonly MockConnectSession[] {
	return connectSessionsStore;
}

/** Drive a session's `/status` poll result (e.g. flip it to a terminal state). */
export function setMockConnectSessionStatus(
	sessionId: string,
	status: Partial<StatusResponse> & { status: SessionStatus },
): void {
	const session = connectSessionsStore.find((s) => s.session_id === sessionId);
	if (!session) return;
	session.status = { ...session.status, ...status };
}

const problem = (status: number, title: string, detail: string) =>
	HttpResponse.json(
		{ type: 'about:blank', title, detail, status, instance: null },
		{ status, headers: { 'Content-Type': 'application/problem+json' } },
	);

/**
 * Poll-token gate shared by the session-scoped routes. Missing session and
 * token mismatch are indistinguishable on the wire (both 403), matching
 * the backend's enumeration-oracle guard.
 */
function gateSession(
	sessionId: string | readonly string[] | undefined,
	requestUrl: string,
): MockConnectSession | ReturnType<typeof problem> {
	const id = String(sessionId);
	const token = new URL(requestUrl).searchParams.get('poll_token');
	const session = connectSessionsStore.find((s) => s.session_id === id);
	if (!session || !token || token !== session.poll_token) {
		return problem(403, 'Forbidden', 'Unknown session or invalid poll token.');
	}
	return session;
}

function isMockSession(v: unknown): v is MockConnectSession {
	return typeof v === 'object' && v != null && 'poll_token' in v;
}

export const connectSessionsHandlers = [
	http.get('/vendors', () => HttpResponse.json({ data: vendorsStore })),

	http.get('/vendors/:key/auth-capabilities', ({ params }) => {
		const key = String(params.key);
		const caps = vendorCapabilitiesStore[key];
		if (caps) return HttpResponse.json(caps);
		// Default: a scope-less vendor advertising the current mock flow —
		// enough for the wizard to run end to end without per-test stubs.
		return HttpResponse.json({
			vendor: key,
			display_name: key,
			flows: [{ kind: confirmChallengeKind }],
			scopes: [],
		} satisfies VendorAuthCapabilities);
	}),

	http.post('/integrations:connect', async ({ request }) => {
		const body = (await request.json()) as ConnectRequest;
		connectSessionSeq += 1;
		const sessionId = `sess_mock_${connectSessionSeq}`;
		const pollToken = `ptok_mock_${connectSessionSeq}`;
		// The backend mints the pending credential row at `:connect` time —
		// mirror it so the credentials list shows the pending shell.
		const pendingCredential = makeMockCredential({
			name: `${body.vendor} (connecting…)`,
			type: CredentialType.OAUTH2,
			provider: 'direct_oauth2',
			api: { vendor: body.vendor, name: 'default', version: '1.0.0' },
			details: { grant_type: 'device_code', connected: false },
		});
		store.push(pendingCredential);
		const credentialId = pendingCredential.credential_id;
		connectSessionsStore.push({
			session_id: sessionId,
			poll_token: pollToken,
			state: 'created',
			vendor_key: body.vendor,
			credential_id: credentialId,
			requested_by_actor_id: body.agent_id ?? 'usr_mock_owner',
			requested_scopes: body.requested_scopes ?? [],
			requested_permission_rules: [],
			reason: null,
			status: {
				status: 'pending',
				connected_as: null,
				credential_id: null,
				bound_scopes: null,
				error_code: null,
			},
			confirmBodies: [],
		});
		return HttpResponse.json(
			{
				session_id: sessionId,
				// The real backend emits an absolute URL to the SPA's
				// credentials page; an absolute mock URL keeps the shape
				// without hardcoding the client's basename here.
				approval_url: `https://jentic.example.test/credentials?approve=${sessionId}&poll_token=${pollToken}`,
				poll_token: pollToken,
				resolved_flow: confirmChallengeKind,
			},
			{ status: 201 },
		);
	}),

	http.get('/connect-sessions/:sessionId', ({ params, request }) => {
		const gated = gateSession(params.sessionId, request.url);
		if (!isMockSession(gated)) return gated;
		const caps = vendorCapabilitiesStore[gated.vendor_key];
		const requested = new Set(gated.requested_scopes);
		return HttpResponse.json({
			session_id: gated.session_id,
			state: gated.state,
			vendor_key: gated.vendor_key,
			vendor_display_name: caps?.display_name ?? gated.vendor_key,
			resolved_flow: confirmChallengeKind,
			requested_by_actor_id: gated.requested_by_actor_id,
			scopes: (caps?.scopes ?? []).map((s) => ({
				name: s.name,
				classification: s.classification,
				default: s.default,
				requested: requested.has(s.name),
				description: s.description,
			})),
			reason: gated.reason,
			requested_permission_rules: gated.requested_permission_rules,
			api_reference: { vendor: gated.vendor_key, name: gated.vendor_key, version: '1.0.0' },
		});
	}),

	http.post('/connect-sessions/:sessionId\\:confirm', async ({ params, request }) => {
		const gated = gateSession(params.sessionId, request.url);
		if (!isMockSession(gated)) return gated;
		const body = (await request.json()) as ConfirmRequest;
		gated.confirmBodies.push(body);
		gated.state = 'confirmed';
		gated.status = { ...gated.status, status: 'polling' };
		if (confirmChallengeKind === 'authorization_code') {
			return HttpResponse.json({
				kind: 'authorization_code',
				authorize_url: `https://vendor.example.test/oauth/authorize?session=${gated.session_id}`,
			});
		}
		return HttpResponse.json({
			kind: 'device_authorization',
			user_code: 'MOCK-1234',
			verification_uri: 'https://vendor.example.test/device',
			verification_uri_complete: null,
			poll_interval_seconds: 1,
		});
	}),

	http.get('/connect-sessions/:sessionId/status', ({ params, request }) => {
		const gated = gateSession(params.sessionId, request.url);
		if (!isMockSession(gated)) return gated;
		return HttpResponse.json(gated.status);
	}),

	http.post('/connect-sessions/:sessionId\\:cancel', ({ params, request }) => {
		const gated = gateSession(params.sessionId, request.url);
		if (!isMockSession(gated)) return gated;
		// Cancel cascades the session AND its pending credential, like the
		// backend's `_mark_terminal` unhappy path.
		connectSessionsStore = connectSessionsStore.filter(
			(s) => s.session_id !== gated.session_id,
		);
		store = store.filter((c) => c.credential_id !== gated.credential_id);
		return new HttpResponse(null, { status: 204 });
	}),
];

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
	resetConnectSessionsStore,
	setMockVendors,
	setMockVendorCapabilities,
	setMockConfirmChallengeKind,
	setMockConnectSessionStatus,
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
		return HttpResponse.json({
			kind: 'authorization_code',
			authorize_url: authorizeUrl,
			state: 'mock-state',
		});
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

	// Verified-vendor registry + agent-driven connect-session lifecycle —
	// `/vendors`, `/vendors/{key}/auth-capabilities`, `POST
	// /integrations:connect`, and the poll_token-gated `/connect-sessions/*`
	// routes. Defined above; folded in here so the root registry keeps its
	// single credentials line.
	...connectSessionsHandlers,
];
