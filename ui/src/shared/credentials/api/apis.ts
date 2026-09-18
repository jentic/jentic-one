// Data-layer wrappers for the guided add-credential flow. The credentials
// module reaches into the apis/catalog services here ONLY so the picker hook
// and the spec hook keep a stable internal contract — components/pages should
// import from `./apis-hooks`, never these wrappers directly.
//
// All of these endpoints already exist in jentic-one (`/apis`, `/catalog`, the
// per-API `/openapi`, the catalog `:import` action, and `/jobs/{id}`) and are
// re-exported by the `@/shared/api` facade with Bearer-JWT applied.
//
// Spec import (`POST /apis` + the job poll) lives here rather than in a feature
// module because uploading a spec is an action on every surface that shows a
// selected API — the Workspace page and the agents Add-APIs tray (D6) — and a
// module cannot import a sibling's dialog.
import {
	ApiError,
	ApIsService,
	ApiSpecService,
	CatalogService,
	JobsService,
	type ApiImportResponse,
	type ApiListResponse,
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

/** POST /catalog/{api_id}:import — enqueue an async import into the workspace. */
export function importCatalogEntry(apiId: string): Promise<ApiImportResponse> {
	return CatalogService.importCatalogEntry({ apiId });
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
 * Surface the server's `detail` rather than the transport's status text: an
 * import rejection ("unsupported OpenAPI version", "vendor already registered")
 * is the only thing the operator can act on, and the dialog renders the thrown
 * message verbatim.
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
 * Enqueue an import of one or more spec sources via `POST /apis`.
 *
 * Async: the backend resolves + ingests server-side and returns 202 with a job
 * id. The caller polls {@link getJob} until terminal. Maps the UI
 * {@link ImportSource} union onto the generated `ApiSourceUrl | ApiSourceInline`
 * wire shapes.
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

/**
 * Fetch an OpenAPI document from a public spec URL (raw.githubusercontent.com,
 * etc.) with a hard timeout. Used for catalog APIs where we don't have the
 * spec locally yet. Mirrors mini's catalog spec fetch.
 *
 * Caveats:
 *   - Subject to CORS on the public host. raw.githubusercontent.com serves
 *     `Access-Control-Allow-Origin: *` for the manifests we care about; if a
 *     given host doesn't, the picker still works but auto-shaping degrades to
 *     the manual fallback.
 *   - The 10s abort keeps the credential form from hanging on a slow host.
 */
export async function fetchPublicSpec(
	specUrl: string,
	timeoutMs = 10_000,
): Promise<Record<string, unknown>> {
	const controller = new AbortController();
	const timer = setTimeout(() => controller.abort(), timeoutMs);
	try {
		const res = await fetch(specUrl, { signal: controller.signal });
		if (!res.ok) throw new Error(`Failed to fetch spec (${res.status})`);
		return (await res.json()) as Record<string, unknown>;
	} finally {
		clearTimeout(timer);
	}
}
