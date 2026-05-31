import {
	OpenAPI,
	CatalogService,
	ToolkitsService,
	UserService,
	ObserveService,
	InspectService,
} from './generated';

// Path component of the active mount, derived from the backend-injected
// <base href>. Empty when unmounted; "/jentic" (no trailing slash) under
// `JENTIC_ROOT_PATH=/jentic`. Used by the generated OpenAPI client AND every
// hand-rolled fetch in this file — absolute paths starting with "/" bypass
// <base href> per the HTML spec, so every URL must be prefixed explicitly.
OpenAPI.BASE =
	typeof document !== 'undefined' ? new URL(document.baseURI).pathname.replace(/\/$/, '') : '';
OpenAPI.WITH_CREDENTIALS = true;

/** Prepend the active mount prefix to an API path. Same source of truth as the
 *  generated OpenAPI client. Use for every raw `fetch` call so navigations
 *  under a path-prefix mount (`JENTIC_ROOT_PATH=/jentic`) don't 404.
 *
 *  Always throws if `path` is not a valid app-relative path: missing leading
 *  slash (`health` → `/foohealth`), scheme (`https://...`), or
 *  protocol-relative (`//host/...`) would all produce a nonsensical prefixed
 *  URL. */
export function apiUrl(path: string): string {
	if (!path.startsWith('/') || path.startsWith('//') || /^[a-z][a-z0-9+.-]*:/i.test(path)) {
		throw new Error(
			`apiUrl() requires an app-relative path starting with "/", got: ${JSON.stringify(path)}`,
		);
	}
	return `${OpenAPI.BASE}${path}`;
}

export const api = {
	getMe: () => UserService.meUserMeGet(),
	login: (username: string, password: string) =>
		UserService.loginUserLoginPost({ requestBody: { username, password } }),
	logout: () => UserService.logoutUserLogoutPost(),
	createUser: (username: string, password: string) =>
		UserService.createUserUserCreatePost({ requestBody: { username, password } }),
	generateDefaultKey: () => UserService.generateDefaultKeyDefaultApiKeyGeneratePost(),
	listApis: (
		page = 1,
		limit = 20,
		source?: string,
		q?: string,
		opts?: { includeImported?: boolean },
	) => {
		const params = new URLSearchParams({ page: String(page), limit: String(limit) });
		if (source) params.set('source', source);
		if (q) params.set('q', q);
		// `include_imported=true` is meaningful only when source=catalog.
		// /discover sets it so the user keeps seeing already-imported
		// catalog APIs (rendered with `source=local`); /workspace's
		// "From the catalog" section omits it because that section is
		// "things you don't have yet".
		if (opts?.includeImported) params.set('include_imported', 'true');
		return fetchJson<any>(`/apis?${params}`);
	},
	getApi: (apiId: string) => fetchJson<any>(`/apis/${apiId}`),
	getCatalogEntry: (apiId: string) => fetchJson<any>(`/catalog/${apiId}`),
	// Detail-Sheet preview for directory (catalog) APIs: server fetches the
	// spec from GitHub and returns a flat operations list. Mirrors the
	// `{data, total}` envelope of `listOperations` so the same renderer works
	// for both workspace and directory APIs. See `src/routers/catalog.py`.
	previewCatalogOperations: (
		apiId: string,
		opts?: { offset?: number; limit?: number; tag?: string },
	) => {
		const params = new URLSearchParams();
		if (opts?.offset != null) params.set('offset', String(opts.offset));
		if (opts?.limit != null) params.set('limit', String(opts.limit));
		if (opts?.tag) params.set('tag', opts.tag);
		const qs = params.toString();
		return fetchJson<{
			data: Array<{
				method: string;
				path: string;
				summary?: string;
				description?: string;
				operation_id?: string | null;
				/**
				 * Path-level + op-level parameters merged per OpenAPI rules.
				 * Slimmed to the fields the Sheet renders to keep payloads
				 * cheap for large specs (Stripe-style 200+ ops).
				 */
				parameters?: Array<{
					name: string;
					in: string;
					required: boolean;
					description?: string;
				}>;
				/**
				 * Flat, deduplicated list of security scheme names that apply
				 * to this op (OAS array-of-{scheme: scopes} flattened — the
				 * Sheet doesn't render the AND/OR structure).
				 */
				security?: string[];
				/**
				 * OpenAPI tags projected per op. Powers the `tag` filter and
				 * the per-row tag chips. Empty array when the op has no tags.
				 */
				tags?: string[];
			}>;
			total: number;
			truncated: boolean;
			/** Echoed pagination cursor — same value the caller sent in `?offset`. */
			offset: number;
			/** Echoed page size — same value the caller sent in `?limit`. */
			limit: number;
			spec_url: string;
			info: { title?: string | null; version?: string | null; description?: string | null };
			/**
			 * `components.securitySchemes` projected to the fields the Sheet
			 * renders. Keys match the names referenced by per-op `security`.
			 */
			security_schemes?: Record<
				string,
				{
					type?: string;
					description?: string;
					in?: string;
					name?: string;
					scheme?: string;
					bearerFormat?: string;
					flows?: string[];
					openIdConnectUrl?: string;
				}
			>;
		}>(`/catalog/${apiId}/operations${qs ? `?${qs}` : ''}`);
	},
	// Detail-Sheet preview for directory (catalog) workflows: server fetches the
	// vendor's `workflows.arazzo.json` from GitHub on demand and projects each
	// workflow into a slim row. Mirrors `previewCatalogOperations` and lives at
	// the same `/catalog/{api_id}/...` namespace. Returns an empty envelope
	// (rather than 404) when the vendor ships no workflows so the sheet's
	// rendering path is uniform — sometimes "Workflows" is just an empty
	// section, never a missing one.
	previewCatalogWorkflows: (apiId: string) =>
		fetchJson<{
			data: Array<{
				/** Original Arazzo `workflowId` — display name and stable identity. */
				workflow_id: string;
				/**
				 * The slug `lazy_import_catalog_workflows` would assign at import
				 * time. Lets the UI render a deep link to
				 * `/workspace/workflows/<slug>` that resolves correctly *after*
				 * the user adds a credential and triggers the lazy import.
				 */
				slug: string;
				summary?: string | null;
				description?: string | null;
				steps_count: number;
			}>;
			total: number;
			api_id: string;
			/** Raw GitHub URL for the Arazzo file — `null` when no manifest entry. */
			arazzo_url: string | null;
			/** GitHub tree URL for the workflow folder — `null` when no manifest entry. */
			github_url: string | null;
		}>(`/catalog/${apiId}/workflows`),
	listOperations: (
		apiId: string,
		page = 1,
		limit = 50,
		opts?: { offset?: number; tag?: string },
	) => {
		// Hand-rolled to expose the new `offset`/`tag` query params without
		// regenerating the OpenAPI client. Keeps the legacy `page`-based
		// callers working — backend treats `offset` as the override when
		// supplied, otherwise falls back to `(page - 1) * limit`.
		const params = new URLSearchParams({ page: String(page), limit: String(limit) });
		if (opts?.offset != null) params.set('offset', String(opts.offset));
		if (opts?.tag) params.set('tag', opts.tag);
		return fetchJson<{
			data: Array<{
				id: string;
				summary?: string;
				description?: string;
				/** OpenAPI tags projected per op (always present, may be empty). */
				tags: string[];
			}>;
			page: number;
			limit: number;
			offset: number;
			total: number;
			total_pages: number;
			has_more: boolean;
			truncated: boolean;
			_links?: Record<string, string>;
		}>(`/apis/${apiId}/operations?${params}`);
	},
	listOverlays: (apiId: string) => CatalogService.listOverlaysApisApiIdOverlaysGet({ apiId }),
	submitOverlay: (apiId: string, overlay: any, contributedBy?: string) =>
		CatalogService.submitOverlayApisApiIdOverlaysPost({
			apiId,
			requestBody: { overlay, contributed_by: contributedBy },
		}),
	importSpec: (sources: any[]) =>
		CatalogService.importSourcesImportPost({ requestBody: { sources } }),
	listCatalog: (q?: string, limit = 50, registeredOnly = false, unregisteredOnly = false) =>
		CatalogService.listCatalogCatalogGet({
			q: q ?? null,
			limit,
			registeredOnly,
			unregisteredOnly,
		}),
	refreshCatalog: () => CatalogService.refreshCatalogCatalogRefreshPost(),
	importFromCatalog: (apiId: string) => CatalogService.getCatalogEntryCatalogApiIdGet({ apiId }),
	listWorkflows: (q?: string, source?: string) =>
		CatalogService.listWorkflowsWorkflowsGet({ q: q ?? null, source: source ?? null }),
	// Paginated variant — backend returns `{data, total, page, limit, total_pages}`
	// when *either* `page` or `limit` is present, otherwise it falls back to
	// the historical bare-array shape consumed by `listWorkflows()`. Exposed
	// separately so the workspace grid can fan out across pages without
	// breaking the half-dozen surfaces that still expect a flat list.
	listWorkflowsPaged: (page = 1, limit = 20, source?: string, q?: string) => {
		const params = new URLSearchParams({ page: String(page), limit: String(limit) });
		if (source) params.set('source', source);
		if (q) params.set('q', q);
		return fetchJson<{
			data: Array<Record<string, unknown>>;
			total: number;
			page: number;
			limit: number;
			total_pages: number;
		}>(`/workflows?${params}`);
	},
	getWorkflow: (slug: string) => CatalogService.getWorkflowWorkflowsSlugGet({ slug }),
	addNote: (resource: string, note: string, type?: string) =>
		CatalogService.createNoteNotesPost({ requestBody: { resource, note, type: type ?? null } }),
	listNotes: (resource?: string, type?: string, limit = 50) =>
		CatalogService.listNotesNotesGet({ resource: resource ?? null, type: type ?? null, limit }),
	listToolkits: () => ToolkitsService.listToolkitsToolkitsGet(),
	getToolkit: (toolkitId: string) =>
		ToolkitsService.getToolkitToolkitsToolkitIdGet({ toolkitId }),
	createToolkit: (body: any) => ToolkitsService.createToolkitToolkitsPost({ requestBody: body }),
	updateToolkit: (toolkitId: string, body: any) =>
		ToolkitsService.patchToolkitToolkitsToolkitIdPatch({ toolkitId, requestBody: body }),
	deleteToolkit: (toolkitId: string) =>
		ToolkitsService.deleteToolkitToolkitsToolkitIdDelete({ toolkitId }),
	listKeys: (toolkitId: string) =>
		ToolkitsService.listToolkitKeysToolkitsToolkitIdKeysGet({ toolkitId }),
	createKey: (toolkitId: string, body: any) =>
		ToolkitsService.createToolkitKeyToolkitsToolkitIdKeysPost({ toolkitId, requestBody: body }),
	revokeKey: (toolkitId: string, keyId: string) =>
		ToolkitsService.revokeToolkitKeyToolkitsToolkitIdKeysKeyIdDelete({ toolkitId, keyId }),
	patchKey: (toolkitId: string, keyId: string, body: any) =>
		ToolkitsService.patchToolkitKeyToolkitsToolkitIdKeysKeyIdPatch({
			toolkitId,
			keyId,
			requestBody: body,
		}),
	listToolkitCredentials: (toolkitId: string) =>
		ToolkitsService.listToolkitCredentialsToolkitsToolkitIdCredentialsGet({ toolkitId }),
	bindCredential: (toolkitId: string, credentialId: string) =>
		ToolkitsService.addCredentialToToolkitToolkitsToolkitIdCredentialsPost({
			toolkitId,
			requestBody: { credential_id: credentialId },
		}),
	unbindCredential: (toolkitId: string, credentialId: string) =>
		ToolkitsService.removeCredentialFromToolkitToolkitsToolkitIdCredentialsCredentialIdDelete({
			toolkitId,
			credentialId,
		}),
	getPermissions: (toolkitId: string, credId: string) =>
		ToolkitsService.getCredentialPermissionsToolkitsToolkitIdCredentialsCredIdPermissionsGet({
			toolkitId,
			credId,
		}),
	setPermissions: (toolkitId: string, credId: string, rules: any[]) =>
		ToolkitsService.setCredentialPermissionsToolkitsToolkitIdCredentialsCredIdPermissionsPut({
			toolkitId,
			credId,
			requestBody: rules,
		}),
	patchPermissions: (toolkitId: string, credId: string, add: any[], remove: any[]) =>
		ToolkitsService.patchCredentialPermissionsToolkitsToolkitIdCredentialsCredIdPermissionsPatch(
			{ toolkitId, credId, requestBody: { add, remove } },
		),
	listAccessRequests: (toolkitId: string, status?: string) =>
		ToolkitsService.listAccessRequestsToolkitsToolkitIdAccessRequestsGet({
			toolkitId,
			status: status ?? null,
		}),
	getAccessRequest: (toolkitId: string, reqId: string) =>
		ToolkitsService.getAccessRequestToolkitsToolkitIdAccessRequestsReqIdGet({
			toolkitId,
			reqId,
		}),
	createAccessRequest: (toolkitId: string, body: any) =>
		ToolkitsService.createAccessRequestToolkitsToolkitIdAccessRequestsPost({
			toolkitId,
			requestBody: body,
		}),
	approveAccessRequest: (toolkitId: string, reqId: string) =>
		ToolkitsService.approveAccessRequestToolkitsToolkitIdAccessRequestsReqIdApprovePost({
			toolkitId,
			reqId,
		}),
	denyAccessRequest: (toolkitId: string, reqId: string) =>
		ToolkitsService.denyAccessRequestToolkitsToolkitIdAccessRequestsReqIdDenyPost({
			toolkitId,
			reqId,
		}),
	listCredentials: (apiId?: string) =>
		fetchJson<any>(`/credentials${apiId ? `?api_id=${encodeURIComponent(apiId)}` : ''}`),
	createCredential: (body: any) =>
		fetchJson<any>('/credentials', {
			method: 'POST',
			body: JSON.stringify(body),
			headers: { 'Content-Type': 'application/json' },
		}),
	getCredential: (cid: string) => fetchJson<any>(`/credentials/${cid}`),
	updateCredential: (cid: string, body: any) =>
		fetchJson<any>(`/credentials/${cid}`, {
			method: 'PATCH',
			body: JSON.stringify(body),
			headers: { 'Content-Type': 'application/json' },
		}),
	deleteCredential: (cid: string) => fetchJson<any>(`/credentials/${cid}`, { method: 'DELETE' }),
	deleteApi: (apiId: string, opts?: { cascade?: boolean }) =>
		fetchJson<void>(`/apis/${apiId}${opts?.cascade ? '?cascade=true' : ''}`, {
			method: 'DELETE',
		}),
	deleteWorkflow: (slug: string) => fetchJson<void>(`/workflows/${slug}`, { method: 'DELETE' }),
	search: (
		q: string,
		n = 10,
		opts?: {
			source?: 'workspace' | 'directory' | 'all' | 'local' | 'catalog';
			type?: 'all' | 'endpoint' | 'workflow' | 'api';
		},
	) => {
		// Hand-rolled (instead of `SearchService.searchSearchGet`) so the
		// new `source` / `type` params and `matched_on` / `match_snippet`
		// response fields work without regenerating the OpenAPI client.
		// Mirrors the `listApis` pattern at the top of this file.
		const params = new URLSearchParams({ q, n: String(n) });
		if (opts?.source) params.set('source', opts.source);
		if (opts?.type) params.set('type', opts.type);
		return fetchJson<
			Array<{
				type: string;
				id: string;
				slug?: string;
				summary?: string | null;
				description?: string | null;
				score: number;
				involved_apis?: string[];
				source?: string;
				/** Which fields the query matched against (post-rank substring scan). */
				matched_on?: string[];
				/**
				 * ~80-char window around the matched span from the highest-priority
				 * matched field. The matched span is wrapped in `\u0001` sentinel
				 * chars so the client can render its own highlight without HTML.
				 * `null` when the row was a BM25 hit without an exact substring match.
				 */
				match_snippet?: string | null;
				api_id?: string;
				_links?: Record<string, string>;
			}>
		>(`/search?${params}`);
	},
	inspectCapability: (capabilityId: string, toolkitId?: string) =>
		InspectService.getCapabilityInspectCapabilityIdGet({ capabilityId, toolkitId }),
	listTraces: ({
		limit = 20,
		offset = 0,
		page,
		toolkit: _toolkit,
		workflow: _workflow,
	}: {
		limit?: number;
		offset?: number;
		page?: number;
		toolkit?: string;
		workflow?: string;
	} = {}) => {
		const effectiveOffset = page != null ? (page - 1) * (limit ?? 20) : (offset ?? 0);
		return ObserveService.listTracesTracesGet({ limit, offset: effectiveOffset });
	},
	getTrace: (traceId: string) => ObserveService.getTraceTracesTraceIdGet({ traceId }),
	listJobs: ({
		status,
		page = 1,
		limit = 20,
	}: { status?: string; page?: number; limit?: number } = {}) =>
		ObserveService.listJobsJobsGet({ status: status ?? null, page, limit }),
	getJob: (jobId: string) => ObserveService.getJobRouteJobsJobIdGet({ jobId }),
	cancelJob: (jobId: string) => ObserveService.cancelJobJobsJobIdDelete({ jobId }),
};

// --- OAuth Brokers (not in generated client — direct fetch) ---

/** Structured API error — carries the parsed JSON body when available. */
export class ApiError extends Error {
	constructor(
		public readonly status: number,
		public readonly statusText: string,
		/** Raw response body text */
		public readonly body: string,
		/** Parsed JSON body, if the response was JSON */
		public readonly data: Record<string, any> | null = null,
	) {
		super(data?.message ?? `${status} ${statusText}`);
		this.name = 'ApiError';
	}
}

async function fetchJson<T>(url: string, init?: RequestInit): Promise<T> {
	const res = await fetch(apiUrl(url), { credentials: 'include', ...init });
	if (!res.ok) {
		const body = await res.text().catch(() => '');
		let data: Record<string, any> | null = null;
		try {
			data = JSON.parse(body);
		} catch {
			/* not JSON */
		}
		throw new ApiError(res.status, res.statusText, body, data);
	}
	const text = await res.text();
	return text ? JSON.parse(text) : (undefined as unknown as T);
}

export interface OAuthBroker {
	id: string;
	type: string;
	config: Record<string, any>;
	created_at?: string;
}

export interface OAuthAccount {
	id: string;
	broker_id: string;
	external_user_id: string;
	api_host: string;
	app_slug: string;
	account_id: string;
	label: string;
	healthy: boolean;
	synced_at: string;
}

export interface ConnectLinkResponse {
	connect_link_url: string;
	expires_at: number;
	broker_id: string;
	app: string;
}

export interface SyncResponse {
	accounts_synced: number;
}

export const oauthBrokers = {
	list: () => fetchJson<OAuthBroker[]>('/oauth-brokers'),
	get: (id: string) => fetchJson<OAuthBroker>(`/oauth-brokers/${encodeURIComponent(id)}`),
	create: (body: { id: string; type: string; config: Record<string, any> }) =>
		fetchJson<OAuthBroker>('/oauth-brokers', {
			method: 'POST',
			headers: { 'Content-Type': 'application/json' },
			body: JSON.stringify(body),
		}),
	delete: (id: string) =>
		fetch(apiUrl(`/oauth-brokers/${encodeURIComponent(id)}`), {
			method: 'DELETE',
			credentials: 'include',
		}).then((r) => {
			if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
		}),
	accounts: (id: string, externalUserId = 'default') =>
		fetchJson<OAuthAccount[]>(
			`/oauth-brokers/${encodeURIComponent(id)}/accounts?external_user_id=${encodeURIComponent(externalUserId)}`,
		),
	deleteAccount: (id: string, accountId: string) =>
		fetch(
			apiUrl(
				`/oauth-brokers/${encodeURIComponent(id)}/accounts/${encodeURIComponent(accountId)}`,
			),
			{
				method: 'DELETE',
				credentials: 'include',
			},
		).then(async (r) => {
			if (!r.ok) {
				const e = await r.json().catch(() => ({}));
				throw new Error(e.detail || 'Failed to delete account');
			}
			return r.json();
		}),
	sync: (id: string, externalUserId = 'default') =>
		fetchJson<SyncResponse>(`/oauth-brokers/${encodeURIComponent(id)}/sync`, {
			method: 'POST',
			headers: { 'Content-Type': 'application/json' },
			body: JSON.stringify({ external_user_id: externalUserId }),
		}),
	connectLink: (
		id: string,
		body: { app: string; external_user_id?: string; label: string; api_id?: string },
	) =>
		fetchJson<ConnectLinkResponse>(`/oauth-brokers/${encodeURIComponent(id)}/connect-link`, {
			method: 'POST',
			headers: { 'Content-Type': 'application/json' },
			body: JSON.stringify(body),
		}),
	renameAccount: (brokerId: string, accountId: string, label: string) =>
		fetchJson<{ account_id: string; label: string }>(
			`/oauth-brokers/${encodeURIComponent(brokerId)}/accounts/${encodeURIComponent(accountId)}`,
			{
				method: 'PATCH',
				headers: { 'Content-Type': 'application/json' },
				body: JSON.stringify({ label }),
			},
		),
	reconnectLink: (brokerId: string, accountId: string) =>
		fetchJson<ConnectLinkResponse>(
			`/oauth-brokers/${encodeURIComponent(brokerId)}/accounts/${encodeURIComponent(accountId)}/reconnect-link`,
			{ method: 'POST', credentials: 'include' },
		),
	update: (id: string, config: Record<string, any>) =>
		fetchJson<OAuthBroker>(`/oauth-brokers/${encodeURIComponent(id)}`, {
			method: 'PATCH',
			headers: { 'Content-Type': 'application/json' },
			body: JSON.stringify({ config }),
		}),
};

export * from './generated';
