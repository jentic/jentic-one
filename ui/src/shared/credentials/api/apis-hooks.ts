// React Query hooks for the guided add-credential flow. Lives alongside the
// other credentials hooks (`./index.ts`) but in its own file so the picker /
// scheme machinery doesn't bloat the credentials hook surface.
//
// Query keys are namespaced under `['credentials', 'apis', …]` and
// `['credentials', 'catalog', …]` so they live in the credentials cache slice
// and don't collide with any future apis/catalog modules.
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
	useInfiniteQuery,
	useMutation,
	useQuery,
	useQueryClient,
	type UseQueryResult,
} from '@tanstack/react-query';
import { sharedQueryKeys } from '@/shared/api';
import type {
	ApiImportResponse,
	ApiListResponse,
	ApiResponse,
	CatalogListResponse,
} from '@/shared/api';
import { toast } from '@/shared/ui';
import { useEagerCursorDrain, type DrainedList } from '@/shared/hooks/useEagerCursorDrain';
import {
	fetchPublicSpec,
	getApiSpec,
	getJob,
	importCatalogEntry,
	importSources,
	listApis,
	listCatalog,
	type ImportSource,
	type JobStatus,
} from './apis';
import {
	parseSchemeOptions,
	type RawSchemes,
	type SchemeOption,
} from '@/shared/credentials/lib/schemes';

/** Namespaced query keys for the credentials/apis cache slice. */
export const apiPickerKeys = {
	// List-root prefixes (match every param variant) — used for invalidation.
	apisList: () => ['credentials', 'apis', 'list'] as const,
	catalogList: () => ['credentials', 'catalog', 'list'] as const,
	apis: (vendor: string | null) => [...apiPickerKeys.apisList(), { vendor }] as const,
	/**
	 * Every page of the workspace API list ({@link useAllApis}). Its own key —
	 * an infinite query cannot share a cache entry with the plain
	 * {@link useApis} query — but nested under the `apisList()` prefix so the
	 * existing import invalidation ({@link useImportCatalogEntry}) sweeps it
	 * without any call-site change.
	 */
	apisAll: () => [...apiPickerKeys.apisList(), 'all-pages'] as const,
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
	/** The catalog `api_id` slug (for `:import`, the entry detail, and the
	 * credential's stored `catalog_api_id`). Set for catalog rows and for
	 * workspace rows whose API recorded one at import. */
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

/**
 * EVERY workspace API — the cursor pages drained eagerly (pagination policy
 * owned here, like the first-page {@link useApis}).
 *
 * For client-side join consumers (the flat Agents surface resolves each
 * binding's served APIs against this registry): joining against a
 * first-page-only list drops any imported API past page 1 into the honest
 * but wrong "not imported" fallback tile. `complete` is true only when
 * every page loaded successfully — until then the join may not assert
 * registry-derived states. `retry` refetches the first page when nothing
 * loaded, else the failed next page; success resumes the drain.
 */
export function useAllApis(): DrainedList<ApiResponse> {
	const query = useInfiniteQuery({
		queryKey: apiPickerKeys.apisAll(),
		queryFn: ({ pageParam }): Promise<ApiListResponse> => listApis({ cursor: pageParam }),
		initialPageParam: null as string | null,
		getNextPageParam: (last) => (last.has_more ? (last.next_cursor ?? null) : null),
	});
	useEagerCursorDrain(query);

	const { data, isError, refetch, fetchNextPage } = query;
	const items = useMemo(() => data?.pages.flatMap((page) => page.data) ?? [], [data]);
	const retry = useCallback(() => {
		if (isError && !data) void refetch();
		else void fetchNextPage();
	}, [isError, data, refetch, fetchNextPage]);

	return {
		items,
		isPending: query.isPending,
		error: query.error,
		complete: query.isSuccess && !query.hasNextPage,
		retry,
	};
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

const JOB_POLL_INTERVAL_MS = 1500;
const JOB_POLL_TIMEOUT_MS = 60_000;
const TERMINAL_STATUSES = new Set(['succeeded', 'failed', 'cancelled', 'error']);

export interface UseImportSpec {
	importSpec: (sources: ImportSource[]) => Promise<JobStatus>;
	isImporting: boolean;
}

/**
 * Enqueue a spec import via `POST /apis` and poll the job to a terminal state.
 *
 * Import is async: 202 returns a job id, then we poll `/jobs/{id}` until
 * `succeeded`/`failed`. The caller gets the terminal `JobStatus` rather than a
 * thrown error on failure, so the dialog can stay open and show the job's own
 * `error` (per the dialog state-lifecycle convention) instead of losing a
 * pasted spec to a toast.
 *
 * A successful import materialises a new workspace API, so it invalidates both
 * the Workspace list (`sharedQueryKeys.workspaceApis` — owned by that module,
 * reachable from here) and the picker's own list slice. The second one is what
 * makes the uploaded API appear in the Add-APIs tray the operator uploaded it
 * from; without it they would upload a spec and still not find the API.
 */
export function useImportSpec(): UseImportSpec {
	const queryClient = useQueryClient();
	const [isImporting, setIsImporting] = useState(false);
	const activeRef = useRef(true);

	// Flip the guard on unmount so an in-flight poll loop stops touching state
	// (and breaks out at the next interval) instead of warning post-unmount.
	useEffect(() => {
		activeRef.current = true;
		return () => {
			activeRef.current = false;
		};
	}, []);

	const importSpec = useCallback(
		async (sources: ImportSource[]): Promise<JobStatus> => {
			setIsImporting(true);
			try {
				const job = await importSources(sources);
				const deadline = Date.now() + JOB_POLL_TIMEOUT_MS;
				let status: JobStatus = { jobId: job.jobId, status: job.status, error: null };

				while (!TERMINAL_STATUSES.has(status.status) && Date.now() < deadline) {
					await new Promise((resolve) => setTimeout(resolve, JOB_POLL_INTERVAL_MS));
					if (!activeRef.current) break;
					status = await getJob(job.jobId);
				}

				if (status.status === 'succeeded') {
					toast({
						variant: 'success',
						title: 'API imported',
						description: `Import job ${status.jobId} completed.`,
					});
					void queryClient.invalidateQueries({ queryKey: sharedQueryKeys.workspaceApis });
					void queryClient.invalidateQueries({ queryKey: apiPickerKeys.apisList() });
					void queryClient.invalidateQueries({ queryKey: apiPickerKeys.catalogList() });
				}
				return status;
			} finally {
				if (activeRef.current) setIsImporting(false);
			}
		},
		[queryClient],
	);

	return { importSpec, isImporting };
}
