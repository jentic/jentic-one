// React Query hooks for the guided add-credential flow. Lives alongside the
// other credentials hooks (`./index.ts`) but in its own file so the picker /
// scheme machinery doesn't bloat the credentials hook surface.
//
// Query keys are namespaced under `['credentials', 'apis', …]` and
// `['credentials', 'catalog', …]` so they live in the credentials cache slice
// and don't collide with any future apis/catalog modules.
import { useMemo } from 'react';
import { useMutation, useQuery, useQueryClient, type UseQueryResult } from '@tanstack/react-query';
import type { ApiImportResponse, ApiListResponse, CatalogListResponse } from '@/shared/api';
import { fetchPublicSpec, getApiSpec, importCatalogEntry, listApis, listCatalog } from './apis';
import {
	parseSchemeOptions,
	type RawSchemes,
	type SchemeOption,
} from '@/modules/credentials/lib/schemes';

/** Namespaced query keys for the credentials/apis cache slice. */
export const apiPickerKeys = {
	// List-root prefixes (match every param variant) — used for invalidation.
	apisList: () => ['credentials', 'apis', 'list'] as const,
	catalogList: () => ['credentials', 'catalog', 'list'] as const,
	apis: (vendor: string | null) => [...apiPickerKeys.apisList(), { vendor }] as const,
	apiSpec: (vendor: string, name: string, version: string) =>
		['credentials', 'apis', 'spec', vendor, name, version] as const,
	catalog: (q: string) => [...apiPickerKeys.catalogList(), { q }] as const,
	publicSpec: (url: string) => ['credentials', 'public-spec', url] as const,
};

/**
 * Normalised view of a picked API. The picker emits this; the form consumes it.
 * Carries enough to (a) fetch the spec on the right path and (b) build the
 * `APIReferenceRequest` for create without re-querying.
 */
export interface SelectedApi {
	source: 'local' | 'catalog';
	vendor: string;
	name: string;
	version: string;
	/** Catalog-only: the catalog `api_id` (for `:import` and the entry detail). */
	apiId?: string;
	/** Catalog-only: public URL for the spec (raw GitHub). */
	specUrl?: string;
	/** Catalog-only: true when the entry has already been imported. */
	registered?: boolean;
	/** Cheap auth-type hint from the local `/apis` row (string[]). */
	securitySchemeTypes?: string[];
	/** Human display name (falls back to vendor/name). */
	label: string;
}

/** List workspace APIs (cursor pagination policy owned here). */
export function useApis(params: { vendor?: string | null } = {}): UseQueryResult<ApiListResponse> {
	const vendor = params.vendor ?? null;
	return useQuery({
		queryKey: apiPickerKeys.apis(vendor),
		queryFn: () => listApis({ vendor }),
	});
}

/** Search the public catalog (search-driven; empty `q` returns the first page). */
export function useCatalog(q: string): UseQueryResult<CatalogListResponse> {
	return useQuery({
		queryKey: apiPickerKeys.catalog(q),
		queryFn: () => listCatalog({ q: q || undefined, limit: 30 }),
		// Catalog browse is heavy server-side; keep results fresh for a minute.
		staleTime: 60_000,
		placeholderData: (prev) => prev,
	});
}

/** Server-variable definition, normalised across local and catalog sources. */
export interface ServerVarDef {
	name: string;
	default?: string | null;
	description?: string | null;
	enum?: string[] | null;
	required: boolean;
}

/**
 * Resolve security schemes (+ server variables) for a selected API. Two paths:
 *
 *  - **local** — call `GET /apis/{vendor}/{name}/{version}/openapi`. The list
 *    row's `security_schemes` is a flat `string[]` (just type names) so it
 *    isn't enough for field-level shaping; the served spec is.
 *  - **catalog** — follow `selectedApi.specUrl` (raw GitHub) and parse
 *    `components.securitySchemes` and `servers[0].variables` off the YAML/JSON.
 *    Cached for 5 minutes so flipping between auth pills doesn't refetch.
 *
 * Returns the parsed scheme options plus the raw scheme map (callers that need
 * `name`/`in` detail consume the raw map; the rest use `options`).
 */
export function useApiSchemes(selectedApi: SelectedApi | null): {
	schemes: RawSchemes;
	options: SchemeOption[];
	serverVars: ServerVarDef[];
	spec: Record<string, unknown> | null;
	loading: boolean;
	error: Error | null;
} {
	const isLocal = selectedApi?.source === 'local';
	const isCatalog = selectedApi?.source === 'catalog';

	const localSpecQuery = useQuery({
		queryKey: apiPickerKeys.apiSpec(
			selectedApi?.vendor ?? '',
			selectedApi?.name ?? '',
			selectedApi?.version ?? '',
		),
		queryFn: () =>
			getApiSpec(selectedApi!.vendor, selectedApi!.name, selectedApi!.version) as Promise<
				Record<string, unknown>
			>,
		enabled: !!selectedApi && isLocal,
		staleTime: 5 * 60 * 1000,
	});

	const publicSpecQuery = useQuery({
		queryKey: apiPickerKeys.publicSpec(selectedApi?.specUrl ?? ''),
		queryFn: () => fetchPublicSpec(selectedApi!.specUrl as string),
		enabled: !!selectedApi && isCatalog && !!selectedApi.specUrl,
		staleTime: 5 * 60 * 1000,
		retry: false,
	});

	const spec = isLocal ? localSpecQuery.data : isCatalog ? publicSpecQuery.data : null;

	const { schemes, options, serverVars } = useMemo(() => {
		const components = (spec as { components?: { securitySchemes?: RawSchemes } } | null)
			?.components;
		const rawSchemes = components?.securitySchemes ?? null;
		const parsed = parseSchemeOptions(rawSchemes);
		const servers = (
			spec as { servers?: Array<{ variables?: Record<string, unknown> }> } | null
		)?.servers;
		const variables = (servers?.[0]?.variables ?? null) as Record<
			string,
			{ default?: string; description?: string; enum?: string[] }
		> | null;
		const vars: ServerVarDef[] = variables
			? Object.entries(variables).map(([name, def]) => ({
					name,
					default: def?.default ?? null,
					description: def?.description ?? null,
					enum: def?.enum ?? null,
					required: !def?.default,
				}))
			: [];
		return { schemes: rawSchemes, options: parsed, serverVars: vars };
	}, [spec]);

	return {
		schemes,
		options,
		serverVars,
		spec: (spec as Record<string, unknown> | undefined) ?? null,
		loading: isLocal ? localSpecQuery.isLoading : isCatalog ? publicSpecQuery.isLoading : false,
		error: (isLocal
			? localSpecQuery.error
			: isCatalog
				? publicSpecQuery.error
				: null) as Error | null,
	};
}

/**
 * Import a catalog API into the workspace. Used by the create flow when the
 * picked API is an un-registered catalog row — the credential create still
 * targets `{vendor,name,version}` directly (the import is async, but the
 * backend resolves the row by triple at create time).
 */
export function useImportCatalogEntry() {
	const queryClient = useQueryClient();
	return useMutation<ApiImportResponse, Error, string>({
		mutationFn: (apiId) => importCatalogEntry(apiId),
		onSuccess: () => {
			void queryClient.invalidateQueries({ queryKey: apiPickerKeys.apisList() });
			void queryClient.invalidateQueries({ queryKey: apiPickerKeys.catalogList() });
		},
	});
}
