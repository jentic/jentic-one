// Data-layer wrappers for the guided add-credential flow. The credentials
// module reaches into the apis/catalog services here ONLY so the picker hook
// and the spec hook keep a stable internal contract — components/pages should
// import from `./apis-hooks`, never these wrappers directly.
//
// All four endpoints already exist in jentic-one (`/apis`, `/catalog`, the
// per-API `/openapi`, and the catalog `:import` action) and are re-exported by
// the `@/shared/api` facade with Bearer-JWT applied.
import {
	ApIsService,
	ApiSpecService,
	CatalogService,
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
