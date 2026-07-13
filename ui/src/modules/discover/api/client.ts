/**
 * Discover repository tier.
 *
 * The ONLY place in the Discover module that talks to `@/shared/api` (the HTTP
 * facade). Views and hooks never import the facade directly — ESLint enforces
 * this (see ui/eslint.config.js "Layering"). Mirrors the backend's Repository
 * layer: thin wrappers that turn typed service calls into UI-shaped data and
 * normalize errors into a single sentinel type the service tier can branch on.
 *
 * Discover reads exactly one backend feed — the public catalog (`GET /catalog`).
 * There is no blended `/apis` feed under D-005a; `/apis` is the local registry,
 * a separate surface this module does not consume.
 */
import {
	ApiError,
	CatalogService,
	type ApiImportResponse,
	type CatalogRefreshResponse,
	type OperationPreviewListResponse,
} from '@/shared/api';
import { catalogEntryToEntity } from '@/modules/discover/api/adapters';
import type { CatalogFilter, DiscoveryEntity } from '@/modules/discover/api/types';

/**
 * Sentinel error for Discover repository calls. Hooks/components branch on
 * `error instanceof DiscoverApiError` without importing the generated
 * `ApiError` (which lives behind the facade). `status` is null for
 * network/parse failures that never reached the server.
 */
export class DiscoverApiError extends Error {
	readonly status: number | null;
	readonly cause?: unknown;

	constructor(message: string, status: number | null, cause?: unknown) {
		super(message);
		this.name = 'DiscoverApiError';
		this.status = status;
		this.cause = cause;
	}
}

function toDiscoverError(error: unknown, fallback: string): DiscoverApiError {
	if (error instanceof ApiError) {
		const detail = (error.body as { detail?: string } | undefined)?.detail ?? error.message;
		return new DiscoverApiError(detail || fallback, error.status, error);
	}
	if (error instanceof Error) {
		return new DiscoverApiError(error.message || fallback, null, error);
	}
	return new DiscoverApiError(fallback, null, error);
}

export interface CatalogPage {
	/** This page's entries, already adapted to the UI shape. */
	entities: DiscoveryEntity[];
	/** Whole-manifest size (stable across pages — drives the status row). */
	catalogTotal: number;
	/** How many of the whole manifest are imported locally (stable across pages). */
	registeredCount: number;
	/** Manifest freshness; null = never fetched / no snapshot yet. */
	manifestAgeSeconds: number | null;
	/** Whether another keyset page follows. */
	hasMore: boolean;
	/** Opaque cursor to fetch the next page; null when `hasMore` is false. */
	nextCursor: string | null;
}

/**
 * One keyset page of the public catalog (`GET /catalog`).
 *
 * The cursor is OPAQUE — we never parse or build it, just echo back the last
 * `next_cursor`. `q` and the registration filter must stay constant across a
 * cursored scroll (changing them invalidates the cursor), so callers reset to a
 * null cursor whenever the query/filter changes.
 */
export async function listCatalog(params: {
	q?: string;
	filter?: CatalogFilter;
	cursor?: string | null;
	limit?: number;
}): Promise<CatalogPage> {
	try {
		const res = await CatalogService.listCatalog({
			q: params.q || null,
			registeredOnly: params.filter === 'registered',
			unregisteredOnly: params.filter === 'unregistered',
			cursor: params.cursor ?? null,
			limit: params.limit ?? 50,
		});
		return {
			entities: res.data.map(catalogEntryToEntity),
			catalogTotal: res.catalog_total,
			registeredCount: res.registered_count,
			manifestAgeSeconds: res.manifest_age_seconds ?? null,
			hasMore: res.has_more ?? false,
			nextCursor: res.next_cursor ?? null,
		};
	} catch (error) {
		throw toDiscoverError(error, 'Failed to load the public catalog.');
	}
}

/**
 * Preview a catalog entry's operations (capped, offset-paginated). Powers the
 * detail sheet's operations list without importing the API.
 */
export async function previewOperations(params: {
	apiId: string;
	offset?: number;
	limit?: number;
	tag?: string | null;
	q?: string | null;
}): Promise<OperationPreviewListResponse> {
	try {
		return await CatalogService.previewCatalogOperations({
			apiId: params.apiId,
			offset: params.offset,
			limit: params.limit,
			tag: params.tag ?? null,
			q: params.q ?? null,
		});
	} catch (error) {
		throw toDiscoverError(error, 'Failed to load operations.');
	}
}

/**
 * Enqueue an import of a catalog entry into the local registry. The backend
 * resolves the spec server-side and returns 202 with a queued job — we surface
 * the job id so the caller can toast/track it. After the job lands, the entry's
 * `registered` flips to true on the next catalog refetch.
 */
export async function importCatalogEntry(apiId: string): Promise<ApiImportResponse> {
	try {
		return await CatalogService.importCatalogEntry({ apiId });
	} catch (error) {
		throw toDiscoverError(error, 'Failed to queue the import.');
	}
}

/**
 * Force the backend to rebuild its catalog snapshot from the upstream manifest
 * (`POST /catalog:refresh`, org:admin). Unlike a client refetch this resets the
 * manifest's freshness — `manifest_age_seconds` returns to ~0 on the next
 * `GET /catalog`, which is what makes the "updated …" line move.
 */
export async function refreshCatalog(): Promise<CatalogRefreshResponse> {
	try {
		return await CatalogService.refreshCatalog();
	} catch (error) {
		throw toDiscoverError(error, 'Failed to refresh the catalog.');
	}
}
