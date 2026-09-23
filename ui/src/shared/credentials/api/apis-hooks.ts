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
import { apiRefDisplayName } from '@/shared/lib';
import { useEagerCursorDrain, type DrainedList } from '@/shared/hooks/useEagerCursorDrain';
import {
	fetchPublicSpec,
	getApi,
	getApiSpec,
	getImportedApiRefs,
	getJob,
	importCatalogEntry,
	importSources,
	listApis,
	listCatalog,
	type ImportedApiRef,
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
	/** Every page of {@link useAllApis} — its own key (an infinite query cannot
	 * share one with {@link useApis}) but under the `apisList()` prefix so import
	 * invalidation sweeps it. */
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

/** A workspace `/apis` row as a pick. */
export function apiRowToSelected(row: ApiResponse): SelectedApi {
	const ref = row.api;
	// Friendly primary line: explicit display_name, else the persisted catalog
	// slug (`nytimes.com/article_search` → `Article Search`), else the legacy
	// vendor/name humanisation.
	const label = apiRefDisplayName({
		displayName: row.display_name,
		catalogApiId: row.catalog_api_id,
		vendor: ref.vendor,
		name: ref.name,
	});
	return {
		source: 'local',
		vendor: ref.vendor,
		name: ref.name,
		version: ref.version,
		apiId: row.catalog_api_id ?? undefined,
		securitySchemeTypes: row.security_schemes ?? [],
		label,
	};
}

/** A pick built from the bare identity, for when the full row can't be read. */
function importedRefToSelected(ref: ImportedApiRef): SelectedApi {
	return {
		source: 'local',
		vendor: ref.vendor,
		name: ref.name,
		version: ref.version,
		label: apiRefDisplayName({ vendor: ref.vendor, name: ref.name }),
	};
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
 * EVERY workspace API — the cursor pages drained eagerly, for consumers that
 * join against the registry rather than list it. `complete` is true only when
 * every page loaded; until then a join may not assert registry-derived states,
 * or an imported API past page 1 lands in the "not imported" fallback tile.
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
	const refresh = useCallback(() => void refetch(), [refetch]);

	return {
		items,
		isPending: query.isPending,
		error: query.error,
		complete: query.isSuccess && !query.hasNextPage,
		retry,
		refresh,
		isFetching: query.isFetching,
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

/** Job states the backend never advances past, spelled as its `JobStatus` enum
 * serialises them — any other spelling polls a finished job until the timeout. */
const TERMINAL_JOB_STATUSES = new Set(['completed', 'failed', 'cancelled', 'dead_letter']);

/** The one terminal state that means the job did the work it was queued for. */
export function jobSucceeded(status: JobStatus): boolean {
	return status.status === 'completed';
}

/**
 * Poll `/jobs/{id}` to a terminal state or the deadline, returning the last status
 * read. A failed *read* is not a failed job — a transient 5xx, or `apis:write`
 * without `jobs:read` — so a rejected poll runs on to the deadline.
 */
export async function pollJobToTerminal(initial: JobStatus): Promise<JobStatus> {
	const deadline = Date.now() + JOB_POLL_TIMEOUT_MS;
	let status = initial;
	let readError: string | null = null;

	while (!TERMINAL_JOB_STATUSES.has(status.status) && Date.now() < deadline) {
		await new Promise((resolve) => setTimeout(resolve, JOB_POLL_INTERVAL_MS));
		try {
			status = await getJob(status.jobId);
			readError = null;
		} catch (error: unknown) {
			readError = error instanceof Error ? error.message : String(error);
		}
	}

	// Timed out short of a terminal state: annotate it so the caller can't render
	// the stale `queued`/`running` as a verdict on the import. Covers both a poll
	// that never read the job (a lingering `readError`) and one that read fine but
	// ran out the deadline before the backend finished.
	if (!TERMINAL_JOB_STATUSES.has(status.status)) {
		const detail = readError
			? `Couldn't check the import job (${readError}).`
			: `The import job didn't finish in time.`;
		return { ...status, error: `${detail} The import may still be running.` };
	}
	return status;
}

/** The terminal job state, plus the APIs a successful import registered. */
export interface ImportSpecResult extends JobStatus {
	/** Empty unless the job succeeded AND its result could be read. */
	imported: SelectedApi[];
}

export interface UseImportSpec {
	importSpec: (sources: ImportSource[]) => Promise<ImportSpecResult>;
	isImporting: boolean;
}

/**
 * Resolve what a completed import registered, as picks. Best-effort: the import
 * already succeeded, so an unreadable result (`jobs:read` missing, the result
 * expired) yields no picks rather than a failure, and an unreadable row falls
 * back to the bare identity.
 */
async function resolveImported(jobId: string): Promise<SelectedApi[]> {
	let refs: ImportedApiRef[];
	try {
		refs = await getImportedApiRefs(jobId);
	} catch {
		return [];
	}
	const unique = refs.filter(
		(ref, i) =>
			refs.findIndex(
				(other) =>
					other.vendor === ref.vendor &&
					other.name === ref.name &&
					other.version === ref.version,
			) === i,
	);
	// `.catch` after `.then`, not an onRejected: a row too malformed to map falls
	// back to the bare ref like a failed read, rather than failing the import.
	return Promise.all(
		unique.map((ref) =>
			getApi(ref.vendor, ref.name, ref.version)
				.then(apiRowToSelected)
				.catch(() => importedRefToSelected(ref)),
		),
	);
}

/**
 * Enqueue a spec import and poll the job to a terminal state. Returns the terminal
 * `JobStatus` rather than throwing, so the dialog can show the job's own `error`
 * instead of losing a pasted spec to a toast. On success it also returns the APIs
 * the import registered, so the surface that uploaded can select them. Invalidates
 * both the Workspace list and the picker's slice, so the new API is findable where
 * it was uploaded.
 */
export function useImportSpec(): UseImportSpec {
	const queryClient = useQueryClient();
	const [isImporting, setIsImporting] = useState(false);
	const activeRef = useRef(true);

	// Flip the guard on unmount so only the `isImporting` write below is skipped —
	// the poll and the invalidations must still finish, or the API lists go stale.
	useEffect(() => {
		activeRef.current = true;
		return () => {
			activeRef.current = false;
		};
	}, []);

	const importSpec = useCallback(
		async (sources: ImportSource[]): Promise<ImportSpecResult> => {
			setIsImporting(true);
			try {
				const job = await importSources(sources);
				const status = await pollJobToTerminal({
					jobId: job.jobId,
					status: job.status,
					error: null,
				});
				if (!jobSucceeded(status)) return { ...status, imported: [] };

				void queryClient.invalidateQueries({ queryKey: sharedQueryKeys.workspaceApis });
				void queryClient.invalidateQueries({ queryKey: apiPickerKeys.apisList() });
				void queryClient.invalidateQueries({ queryKey: apiPickerKeys.catalogList() });

				const imported = await resolveImported(status.jobId);
				toast({
					variant: 'success',
					title: 'API imported',
					description:
						imported.length === 1
							? `${imported[0].label} is in your Workspace.`
							: imported.length > 1
								? `${imported.length} APIs are in your Workspace.`
								: 'The API is in your Workspace.',
				});
				return { ...status, imported };
			} finally {
				if (activeRef.current) setIsImporting(false);
			}
		},
		[queryClient],
	);

	return { importSpec, isImporting };
}
