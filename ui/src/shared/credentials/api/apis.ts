// Data-layer wrappers for the guided add-credential flow. The credentials
// module reaches into the apis/catalog services here ONLY so the picker hook
// and the spec hook keep a stable internal contract — components/pages should
// import from `./apis-hooks`, never these wrappers directly.
//
// Spec import (`POST /apis` + the job poll) lives here rather than in a feature
// module because uploading a spec is an action on every surface that shows a
// selected API, and a module cannot import a sibling's dialog.
import {
	ApiError,
	ApIsService,
	ApiSpecService,
	CatalogService,
	JobsService,
	type ApiImportResponse,
	type ApiListResponse,
	type ApiResponse,
	type CatalogListResponse,
} from '@/shared/api';

export interface ListApisParams {
	vendor?: string | null;
	cursor?: string | null;
	limit?: number;
}

/** GET /apis — cursor-paginated workspace APIs. */
export function listApis(params: ListApisParams = {}): Promise<ApiListResponse> {
	return ApIsService.listApis({
		vendor: params.vendor ?? undefined,
		cursor: params.cursor ?? undefined,
		limit: params.limit,
	}) as unknown as Promise<ApiListResponse>;
}

/**
 * GET /apis/{vendor}/{name}/{version}/openapi — full OpenAPI doc for the live
 * revision (overlays applied by default). Typed as `unknown` because the
 * generated schema is intentionally open.
 */
export function getApiSpec(
	vendor: string,
	name: string,
	version: string,
): Promise<Record<string, unknown>> {
	return ApiSpecService.getApiSpec({
		vendor,
		name,
		version,
	}) as unknown as Promise<Record<string, unknown>>;
}

export interface ListCatalogParams {
	q?: string | null;
	registeredOnly?: boolean;
	unregisteredOnly?: boolean;
	cursor?: string | null;
	limit?: number;
}

/** GET /catalog — search/filter aware catalog browse. */
export function listCatalog(params: ListCatalogParams = {}): Promise<CatalogListResponse> {
	return CatalogService.listCatalog({
		q: params.q ?? undefined,
		registeredOnly: params.registeredOnly,
		unregisteredOnly: params.unregisteredOnly,
		cursor: params.cursor ?? undefined,
		limit: params.limit,
	});
}

/** POST /catalog/{entry_id}:import — enqueue an async import into the workspace. */
export function importCatalogEntry(entryId: string): Promise<ApiImportResponse> {
	return CatalogService.importCatalogEntry({ entryId });
}

/** Result of enqueuing a spec import (`POST /apis` → 202). */
export interface ImportJob {
	jobId: string;
	status: string;
}

/** Intermediate or terminal job state when polling `/jobs/{id}`. */
export interface JobStatus {
	jobId: string;
	status: string;
	error: string | null;
}

/** One spec source for an import — a URL the server fetches, or inline text. */
export type ImportSource =
	| { type: 'url'; url: string; vendor?: string; apiName?: string; version?: string }
	| { type: 'inline'; content: string; filename: string };

/**
 * Surface the server's `detail` rather than the transport's status text: an import
 * rejection ("unsupported OpenAPI version") is the only thing the operator can act
 * on, and the dialog renders the thrown message verbatim.
 */
function toImportError(error: unknown, fallback: string): Error {
	if (error instanceof ApiError) {
		const body = error.body as { detail?: string } | undefined;
		return new Error(body?.detail || error.message || fallback);
	}
	if (error instanceof Error) return new Error(error.message || fallback);
	return new Error(fallback);
}

/**
 * Enqueue an import of one or more spec sources via `POST /apis`. Async: the
 * backend ingests server-side and returns 202 with a job id, which the caller
 * polls via {@link getJob} until terminal.
 */
export async function importSources(sources: ImportSource[]): Promise<ImportJob> {
	try {
		const res = await ApIsService.importApis({
			requestBody: {
				sources: sources.map((s) =>
					s.type === 'url'
						? {
								type: 'url',
								url: s.url,
								vendor: s.vendor ?? null,
								api_name: s.apiName ?? null,
								version: s.version ?? null,
							}
						: { type: 'inline', content: s.content, filename: s.filename },
				),
			},
		});
		const body = (res ?? {}) as { job_id?: string; status?: string };
		return { jobId: String(body.job_id ?? ''), status: String(body.status ?? 'queued') };
	} catch (error) {
		throw toImportError(error, 'Failed to start the import.');
	}
}

/** Poll an import job's status via `GET /jobs/{id}` (tagged `admin`). */
export async function getJob(jobId: string): Promise<JobStatus> {
	try {
		const res = await JobsService.getJob({ jobId });
		return { jobId: res.job_id, status: res.status, error: res.error ?? null };
	} catch (error) {
		throw toImportError(error, 'Failed to read the import job.');
	}
}

/** The identity of one API an import job registered. */
export interface ImportedApiRef {
	vendor: string;
	name: string;
	version: string;
}

/**
 * Read which APIs a completed import registered, via `GET /jobs/{id}/result`.
 * The result lists one revision per ingested source; a revision the job reused
 * or superseded still names the API it landed on, so every entry counts.
 */
export async function getImportedApiRefs(jobId: string): Promise<ImportedApiRef[]> {
	const body = (await JobsService.getJobResult({ jobId })) as {
		revisions?: Array<{ api?: Partial<ImportedApiRef> | null }> | null;
	} | null;
	const refs: ImportedApiRef[] = [];
	for (const revision of body?.revisions ?? []) {
		const api = revision.api;
		if (api?.vendor && api.name && api.version) {
			refs.push({ vendor: api.vendor, name: api.name, version: api.version });
		}
	}
	return refs;
}

/** GET /apis/{vendor}/{name}/{version} — one workspace API row. */
export function getApi(vendor: string, name: string, version: string): Promise<ApiResponse> {
	return ApIsService.getApi({ vendor, name, version });
}

/**
 * Security schemes and server variables of a catalog entry that isn't imported
 * yet, read from `GET /catalog/{entry_id}/operations` (the server fetches the
 * spec, so the browser never reaches the source host). Returned as a minimal
 * OpenAPI-shaped document so the scheme parsers read it like a served spec:
 * `components.securitySchemes` (OAuth2 flows rebuilt from `flow_details`, so
 * authorization/token URLs and scopes prefill), `servers[0].variables` (so
 * per-tenant hosts prompt as before) and a top-level `security` listing every
 * scheme the spec's operations require (`required_schemes`, computed over the
 * whole spec, not one page of operations).
 */
export async function fetchCatalogSchemeSpec(entryId: string): Promise<Record<string, unknown>> {
	const res = await CatalogService.previewCatalogOperations({ entryId, limit: 1 });
	const securitySchemes = Object.fromEntries(
		Object.entries(res.security_schemes ?? {}).map(([name, scheme]) => {
			const { flow_details: details, flows, ...rest } = scheme as Record<string, unknown>;
			if (!Array.isArray(flows)) return [name, scheme];
			const detailMap = (details ?? {}) as Record<string, Record<string, unknown>>;
			return [
				name,
				{
					...rest,
					flows: Object.fromEntries(
						flows.map((f) => [String(f), detailMap[String(f)] ?? {}]),
					),
				},
			];
		}),
	);
	const variables = res.server_variables ?? {};
	return {
		info: res.info,
		components: { securitySchemes },
		...(Object.keys(variables).length > 0 ? { servers: [{ url: '', variables }] } : {}),
		security: (res.required_schemes ?? []).map((name) => ({ [name]: [] })),
	};
}
