/**
 * Discover service tier — TanStack Query hooks.
 *
 * The ONLY backend access path for Discover views: components/pages call these
 * hooks, which call the repository (`./client`), which calls `@/shared/api`.
 * Views must never reach past this layer (ESLint-enforced). Mirrors the
 * backend's Service layer.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
	useInfiniteQuery,
	useQueries,
	useQuery,
	keepPreviousData,
	useMutation,
	useQueryClient,
	type InfiniteData,
} from '@tanstack/react-query';
import { toast } from '@/shared/ui';
import {
	catalogCursorAfter,
	importCatalogEntry,
	listCatalog as listCatalogOnce,
	previewOperations,
	refreshCatalog,
	type CatalogPage,
} from '@/modules/discover/api/client';
import type { CatalogFilter, DiscoveryEntity } from '@/modules/discover/api/types';
import type { OperationPreviewListResponse, PreviewOperationResponse } from '@/shared/api';
import { invalidateApiLists } from '@/shared/credentials/api';

/** Stable query-key roots so invalidation can target the catalog precisely. */
const discoverKeys = {
	all: ['discover'] as const,
	/**
	 * Root for every catalog page (all q/filter combos). Invalidating this
	 * refetches the browse feed without disturbing open operation previews.
	 */
	catalogAll: ['discover', 'catalog'] as const,
	catalog: (q: string, filter: CatalogFilter) => [...discoverKeys.catalogAll, q, filter] as const,
	operations: (apiId: string) => [...discoverKeys.all, 'operations', apiId] as const,
};

/**
 * How often to re-poll the catalog while an import job is settling.
 *
 * Overridable via {@link setImportPollIntervalForTests} so tests can poll on a
 * fast, deterministic cadence instead of racing the real 3s tick against an
 * assertion budget (the source of browser-mode flakiness).
 */
let importPollIntervalMs = 3_000;

/** Test-only: override the import poll cadence. Returns a restore function. */
export function setImportPollIntervalForTests(ms: number): () => void {
	const prev = importPollIntervalMs;
	importPollIntervalMs = ms;
	return () => {
		importPollIntervalMs = prev;
	};
}
/**
 * How long to keep a row in the pending state before giving up on the poll.
 * The catalog only exposes `registered` (not job status), so a failed/stuck
 * import never flips — without this cap the row would spin forever and the
 * poll would hammer the backend. On timeout we drop the pending state and tell
 * the user to refresh; a true failure toast would need a backend job-status
 * read the agent token can reach.
 */
const IMPORT_PENDING_TIMEOUT_MS = 60_000;

/** Pause before the one retry of a catalog read that hit a 409. */
const CATALOG_CONFLICT_RETRY_MS = 400;

/**
 * Catalog reads, retried ONCE on a 409. On an instance with no catalog
 * snapshot yet, concurrent first reads race to write it and the loser gets a
 * 409; by the retry the snapshot exists and the read succeeds. Every other
 * status (and a second 409) surfaces as-is — the shared QueryClient never
 * retries a 4xx, so without this the first visit would stay on an error.
 */
async function listCatalog(params: Parameters<typeof listCatalogOnce>[0]): Promise<CatalogPage> {
	try {
		return await listCatalogOnce(params);
	} catch (error) {
		if ((error as { status?: unknown }).status !== 409) throw error;
		await new Promise((resolve) => setTimeout(resolve, CATALOG_CONFLICT_RETRY_MS));
		return listCatalogOnce(params);
	}
}

/** The jump cursor couldn't be built — the ledger falls back to paging forward. */
function jumpCursorUnavailable(): Error {
	return new Error('This position in the catalog cannot be jumped to.');
}

/** Browse page size (the infinite scroll's step). */
const CATALOG_PAGE_SIZE = 50;
/** A seek's page size — the backend's max `limit`. */
const CATALOG_BULK_PAGE_SIZE = 200;
interface CatalogPageParam {
	cursor: string | null;
	limit: number;
}

export interface UseDiscoverCatalogResult {
	/** Flattened entities across all loaded keyset pages. */
	entities: DiscoveryEntity[];
	/** Whole-manifest size (stable while scrolling — no status-row flicker). */
	catalogTotal: number;
	/** How many of the whole manifest are imported locally. */
	registeredCount: number;
	/** How many imported entries have an upstream update available. */
	outdatedCount: number;
	/** Manifest freshness from the first page; null = never fetched. */
	manifestAgeSeconds: number | null;
	isPending: boolean;
	isFetching: boolean;
	error: Error | null;
	hasNextPage: boolean;
	isFetchingNextPage: boolean;
	/**
	 * Load the next page. `bulk` asks for a big page — for a seek (jump to a
	 * letter / `#` that hasn't loaded yet), so it takes a few requests, not dozens.
	 */
	fetchNextPage: (options?: { bulk?: boolean }) => void;
	/** Re-run the feed from its first page (the error state's Retry). */
	refetch: () => void;
}

/**
 * Browse/search the public catalog with keyset cursor pagination.
 *
 * `q` and `filter` are baked into the query key, so changing either starts a
 * fresh paged query from a null cursor (the contract requires keeping them
 * constant across a cursored scroll). `catalog_total`/`registered_count` are
 * read off the first page only — they describe the whole manifest and stay
 * constant while paging, so the Discover status row doesn't flicker.
 */
export function useDiscoverCatalog(params: {
	q: string;
	filter: CatalogFilter;
	/**
	 * Poll the feed every few seconds while an import is in flight, so a row
	 * flips Available → In your workspace on its own once the async job lands (the
	 * catalog's `registered` flag is the only completion signal the agent-scoped
	 * UI can observe — `/jobs` is admin-only). Off when nothing is pending.
	 */
	pollWhilePending?: boolean;
}): UseDiscoverCatalogResult {
	// The page size rides in the page param (not just the request) so a poll's
	// refetch of every loaded page replays each page at its original size —
	// a bulk page refetched small would drop rows before the next cursor.
	const bulkRef = useRef(false);
	const query = useInfiniteQuery<
		CatalogPage,
		Error,
		InfiniteData<CatalogPage>,
		readonly unknown[],
		CatalogPageParam
	>({
		queryKey: discoverKeys.catalog(params.q, params.filter),
		queryFn: ({ pageParam }) =>
			listCatalog({
				q: params.q,
				filter: params.filter,
				cursor: pageParam.cursor,
				limit: pageParam.limit,
			}),
		initialPageParam: { cursor: null, limit: CATALOG_PAGE_SIZE },
		getNextPageParam: (lastPage) =>
			lastPage.hasMore && lastPage.nextCursor
				? {
						cursor: lastPage.nextCursor,
						limit: bulkRef.current ? CATALOG_BULK_PAGE_SIZE : CATALOG_PAGE_SIZE,
					}
				: undefined,
		// Keep the previous page visible while a new q/filter query loads, so the
		// ledger doesn't flash to skeletons on every keystroke.
		placeholderData: keepPreviousData,
		refetchInterval: params.pollWhilePending ? importPollIntervalMs : false,
	});

	const entities = useMemo(
		() => query.data?.pages.flatMap((p) => p.entities) ?? [],
		[query.data],
	);
	const first = query.data?.pages[0];
	// Stable, so the ledger's seek / infinite-scroll effects don't re-run (and
	// re-arm their observers) on every render.
	const { fetchNextPage: fetchNext, refetch: refetchFeed } = query;
	const fetchNextPage = useCallback(
		(options?: { bulk?: boolean }) => {
			bulkRef.current = options?.bulk ?? false;
			void fetchNext();
		},
		[fetchNext],
	);
	const refetch = useCallback(() => void refetchFeed(), [refetchFeed]);

	return {
		entities,
		catalogTotal: first?.catalogTotal ?? 0,
		registeredCount: first?.registeredCount ?? 0,
		outdatedCount: first?.outdatedCount ?? 0,
		manifestAgeSeconds: first?.manifestAgeSeconds ?? null,
		isPending: query.isPending,
		isFetching: query.isFetching,
		error: query.error,
		hasNextPage: query.hasNextPage,
		isFetchingNextPage: query.isFetchingNextPage,
		fetchNextPage,
		refetch,
	};
}

export interface UseCatalogJumpResult {
	entities: DiscoveryEntity[];
	hasNextPage: boolean;
	isFetchingNextPage: boolean;
	/** The first page is in flight. */
	isPending: boolean;
	/** The first page has arrived (or failed). */
	isFetched: boolean;
	error: Error | null;
	fetchNextPage: () => void;
	/**
	 * Where the loaded range now starts: the jump's start, or lower once
	 * `loadEarlierFrom` has prepended whole letters before it.
	 */
	startKey: string | null;
	/** Prepend the range from `startKey` up to the current start (a letter). */
	loadEarlierFrom: (startKey: string) => void;
	isLoadingEarlier: boolean;
}

/**
 * Every browse row from `startKey` (exclusive keyset position) up to `endKey`
 * (exclusive api_id bound) — one rail letter, in a few bulk pages.
 */
async function listCatalogRange(
	startKey: string,
	endKey: string,
	filter: CatalogFilter,
): Promise<DiscoveryEntity[]> {
	const rows: DiscoveryEntity[] = [];
	let cursor: string | null = catalogCursorAfter(startKey);
	if (cursor == null) throw jumpCursorUnavailable();
	for (;;) {
		const page = await listCatalog({ filter, cursor, limit: CATALOG_BULK_PAGE_SIZE });
		rows.push(...page.entities.filter((e) => e.apiId < endKey));
		const last = page.entities[page.entities.length - 1];
		if (!page.hasMore || !page.nextCursor || !last || last.apiId >= endKey) return rows;
		cursor = page.nextCursor;
	}
}

/**
 * An A–Z rail jump: the browse feed started at a letter instead of the top
 * (`startKey`, e.g. `y` — see `jumpStartKey`), so the jump costs one page
 * rather than paging through everything before it. The ledger shows it as a
 * second range after the head feed, and the two merge when they meet.
 * Shares the catalog root key, so refreshes / import landings refetch it.
 */
export function useCatalogJump(params: {
	startKey: string | null;
	filter: CatalogFilter;
	enabled?: boolean;
	pollWhilePending?: boolean;
}): UseCatalogJumpResult {
	const enabled = (params.enabled ?? true) && params.startKey != null;
	const jumpCursor = params.startKey != null ? catalogCursorAfter(params.startKey) : null;
	const query = useInfiniteQuery<
		CatalogPage,
		Error,
		InfiniteData<CatalogPage>,
		readonly unknown[],
		CatalogPageParam
	>({
		queryKey: [...discoverKeys.catalogAll, 'jump', params.startKey, params.filter] as const,
		queryFn: ({ pageParam }) => {
			// Every page of a jump is cursored; a null cursor means the start
			// position couldn't be encoded, so fail and let the ledger page forward.
			if (pageParam.cursor == null) throw jumpCursorUnavailable();
			return listCatalog({
				filter: params.filter,
				cursor: pageParam.cursor,
				limit: pageParam.limit,
			});
		},
		initialPageParam: { cursor: jumpCursor, limit: CATALOG_PAGE_SIZE },
		getNextPageParam: (lastPage) =>
			lastPage.hasMore && lastPage.nextCursor
				? { cursor: lastPage.nextCursor, limit: CATALOG_PAGE_SIZE }
				: undefined,
		enabled,
		retry: false,
		refetchInterval: params.pollWhilePending ? importPollIntervalMs : false,
	});
	const { fetchNextPage: fetchNext } = query;
	const fetchNextPage = useCallback(() => void fetchNext(), [fetchNext]);

	// Growing the range backward, a letter at a time (the server only pages
	// forward, but a jump cursor can start anywhere). Reset with the jump.
	const scope = `${params.startKey}\u0000${params.filter}`;
	const [earlier, setEarlier] = useState<{ scope: string; keys: string[] }>({
		scope,
		keys: [],
	});
	const earlierKeys = enabled && earlier.scope === scope ? earlier.keys : EMPTY_KEYS;
	const ranges = useQueries({
		queries: earlierKeys.map((key, i) => {
			const end = i === 0 ? params.startKey! : earlierKeys[i - 1];
			return {
				queryKey: [...discoverKeys.catalogAll, 'range', key, end, params.filter] as const,
				queryFn: () => listCatalogRange(key, end, params.filter),
				retry: false,
			};
		}),
		combine: combineRanges,
	});
	const startKey = !enabled
		? null
		: ranges.loaded.length > 0
			? earlierKeys[ranges.loaded.length - 1]
			: params.startKey;
	const isLoadingEarlier = ranges.pending;
	const entities = useMemo(
		() =>
			enabled
				? [...ranges.loaded.flat(), ...(query.data?.pages.flatMap((p) => p.entities) ?? [])]
				: EMPTY_ENTITIES,
		[enabled, query.data, ranges.loaded],
	);
	const loadEarlierFrom = useCallback(
		(key: string) =>
			setEarlier((prev) => {
				const keys = prev.scope === scope ? prev.keys : [];
				return keys.includes(key) ? prev : { scope, keys: [...keys, key] };
			}),
		[scope],
	);
	return {
		entities,
		hasNextPage: enabled && query.hasNextPage,
		isFetchingNextPage: query.isFetchingNextPage,
		isPending: enabled && query.isPending,
		isFetched: enabled && query.isFetched,
		error: enabled ? query.error : null,
		fetchNextPage,
		startKey,
		loadEarlierFrom,
		isLoadingEarlier,
	};
}

const EMPTY_KEYS: string[] = [];

/**
 * Only ranges that loaded, contiguously back from the jump, count. Module
 * scope so TanStack memoises the result (a stable `loaded` while unchanged).
 */
function combineRanges(results: { data?: DiscoveryEntity[]; isPending: boolean }[]) {
	const loaded: DiscoveryEntity[][] = [];
	for (const r of results) {
		if (!r.data) break;
		loaded.push(r.data);
	}
	return { loaded, pending: results.some((r) => r.isPending) };
}

/**
 * The catalog entries already in your workspace (`registered_only`), for the
 * ledger's "In your workspace" group — so it lists all of them up front, not
 * just the ones that happen to be on the pages scrolled so far. One page is
 * plenty for a workspace; it shares the catalog root key, so refreshes and
 * import landings refetch it too. Callers enable it only once the main feed
 * has settled, so the two never race for the first catalog snapshot.
 */
export function useCatalogInWorkspace(params: { enabled: boolean; pollWhilePending?: boolean }) {
	const query = useQuery({
		queryKey: [...discoverKeys.catalogAll, 'in-workspace'] as const,
		queryFn: () => listCatalog({ registeredOnly: true, limit: CATALOG_BULK_PAGE_SIZE }),
		enabled: params.enabled,
		refetchInterval: params.pollWhilePending ? importPollIntervalMs : false,
	});
	return query.data?.entities ?? EMPTY_ENTITIES;
}
const EMPTY_ENTITIES: DiscoveryEntity[] = [];

/** Operations are paged 25 at a time behind a "Load more" button. */
export const OPERATION_PREVIEW_PAGE_SIZE = 25;

interface OperationPreviewPage {
	operations: PreviewOperationResponse[];
	/** Full (filtered) operation count in the spec — drives "Load more". */
	total: number;
	offset: number;
	info: OperationPreviewListResponse['info'];
	securitySchemes: OperationPreviewListResponse['security_schemes'];
}

export interface UseOperationPreviewResult {
	/** Flattened operations across all loaded pages. */
	operations: PreviewOperationResponse[];
	/** Full filtered count in the spec (stable across pages). */
	total: number;
	info: OperationPreviewListResponse['info'] | undefined;
	securitySchemes: OperationPreviewListResponse['security_schemes'];
	isPending: boolean;
	error: Error | null;
	hasNextPage: boolean;
	isFetchingNextPage: boolean;
	fetchNextPage: () => void;
}

/**
 * Operation preview for a catalog entry, paged with a "Load more" button.
 *
 * Filtering (`q` free-text + `tag`) is server-side: it's baked into the query
 * key, so changing either restarts pagination from offset 0 and the result
 * covers EVERY operation in the spec — not just the loaded page. `total` is the
 * full filtered count, so "Load more" knows when to stop. Disabled until an
 * `apiId` is provided (`null` while the sheet is closed).
 */
export function useOperationPreview(
	apiId: string | null,
	opts: { q?: string; tag?: string | null } = {},
): UseOperationPreviewResult {
	const q = opts.q?.trim() || undefined;
	const tag = opts.tag ?? undefined;

	const query = useInfiniteQuery<OperationPreviewPage>({
		queryKey: [...discoverKeys.operations(apiId ?? ''), { q: q ?? '', tag: tag ?? '' }],
		queryFn: async ({ pageParam }) => {
			const res = await previewOperations({
				apiId: apiId as string,
				offset: pageParam as number,
				limit: OPERATION_PREVIEW_PAGE_SIZE,
				q,
				tag,
			});
			return {
				operations: res.data,
				total: res.total,
				offset: res.offset,
				info: res.info,
				securitySchemes: res.security_schemes,
			};
		},
		initialPageParam: 0,
		getNextPageParam: (lastPage) => {
			const loaded = lastPage.offset + lastPage.operations.length;
			return loaded < lastPage.total ? loaded : undefined;
		},
		// Keep the previous operations visible while a new q/tag query loads, so
		// the list doesn't flash to skeletons on every keystroke.
		placeholderData: keepPreviousData,
		enabled: apiId != null,
	});

	const operations = useMemo(
		() => query.data?.pages.flatMap((p) => p.operations) ?? [],
		[query.data],
	);
	const first = query.data?.pages[0];
	const { fetchNextPage: fetchNext } = query;
	const fetchNextPage = useCallback(() => void fetchNext(), [fetchNext]);

	return {
		operations,
		total: first?.total ?? 0,
		info: first?.info,
		securitySchemes: first?.securitySchemes ?? {},
		isPending: query.isPending,
		error: query.error,
		hasNextPage: query.hasNextPage,
		isFetchingNextPage: query.isFetchingNextPage,
		fetchNextPage,
	};
}

interface UseImportResult {
	/**
	 * Enqueue an import for a catalog entity. Fire-and-forget: success and
	 * failure are both reported by toast (and the pending set), so callers
	 * never see a rejected promise.
	 */
	importEntity: (entity: DiscoveryEntity) => void;
	/**
	 * Catalog api_ids with an import job still settling — covers the whole
	 * window from the 202 until the catalog reports `registered: true` (or the
	 * safety timeout fires), NOT just the in-flight HTTP request. The ledger +
	 * sheet read this to keep the row in an "Adding…" pending state.
	 */
	pendingApiIds: Set<string>;
	/** True while any import is settling — drives the catalog poll. */
	hasPendingImports: boolean;
	/**
	 * Reconcile pending imports against the freshest catalog entities: any
	 * pending id that now reports `registered: true` is resolved (cleared +
	 * success toast). Called by the page whenever the polled feed updates.
	 */
	reconcileImported: (entities: DiscoveryEntity[]) => void;
}

/**
 * Import a catalog API into the local registry via `POST /catalog/{id}:import`.
 *
 * Import is async: the backend resolves the spec and enqueues a job (202), and
 * the catalog entry only flips to `registered: true` once the worker lands it.
 * So a row has three honest states — Available → Pending → Imported — and the
 * pending state must outlive the (millisecond) 202 request. We track pending
 * api_ids in a set, ask the page to poll the feed while it's non-empty, and
 * clear an id (with a success toast) when `reconcileImported` sees it turn
 * registered. Each pending id also has a safety timeout so a failed/stuck job
 * can't pin a row in "Adding…" forever.
 */
export function useImportCatalogApi(): UseImportResult {
	const queryClient = useQueryClient();
	const [pendingApiIds, setPendingApiIds] = useState<Set<string>>(() => new Set());
	// Per-id timeout handles + entity labels, kept in refs so they survive renders
	// without widening the reactive surface.
	const timeoutsRef = useRef<Map<string, ReturnType<typeof setTimeout>>>(new Map());
	const labelsRef = useRef<Map<string, string>>(new Map());
	// api_ids with a POST :import HTTP request in flight (pre-202). Covers the
	// gap before `pendingApiIds` updates so a double-click can't double-submit,
	// without the global `mutation.isPending` blocking a *different* id.
	const inFlightRef = useRef<Set<string>>(new Set());

	const clearPending = useCallback((apiId: string) => {
		const handle = timeoutsRef.current.get(apiId);
		if (handle) clearTimeout(handle);
		timeoutsRef.current.delete(apiId);
		labelsRef.current.delete(apiId);
		setPendingApiIds((prev) => {
			if (!prev.has(apiId)) return prev;
			const next = new Set(prev);
			next.delete(apiId);
			return next;
		});
	}, []);

	// Clean up any outstanding timers on unmount.
	useEffect(() => {
		const timers = timeoutsRef.current;
		return () => {
			for (const handle of timers.values()) clearTimeout(handle);
			timers.clear();
		};
	}, []);

	const mutation = useMutation({
		mutationFn: (entity: DiscoveryEntity) => importCatalogEntry(entity.apiId),
		onMutate: (entity) => {
			inFlightRef.current.add(entity.apiId);
			labelsRef.current.set(entity.apiId, entity.summary);
		},
		onSuccess: (_res, entity) => {
			// Enter the pending state and arm a safety timeout for this id. The
			// row's own status says "Adding…" meanwhile; the one toast is the
			// final "Added to workspace" (or the timeout / failure notice).
			setPendingApiIds((prev) => {
				const next = new Set(prev);
				next.add(entity.apiId);
				return next;
			});
			// Cancel any stale timer for the same id before arming a new one, so a
			// re-import never leaks the previous timeout (which would later fire and
			// clear the fresh pending entry out from under us).
			const stale = timeoutsRef.current.get(entity.apiId);
			if (stale) clearTimeout(stale);
			const handle = setTimeout(() => {
				const label = labelsRef.current.get(entity.apiId) ?? entity.summary;
				clearPending(entity.apiId);
				toast({
					title: 'Still adding to workspace',
					description: `${label} is taking longer than expected. Refresh the catalog to check its status.`,
					variant: 'default',
				});
			}, IMPORT_PENDING_TIMEOUT_MS);
			timeoutsRef.current.set(entity.apiId, handle);
			// Kick an immediate refetch of the browse feed; the poll (driven by
			// hasPendingImports) takes over from here. Scoped to the catalog so an
			// open operation preview isn't needlessly refetched.
			queryClient.invalidateQueries({ queryKey: discoverKeys.catalogAll });
		},
		onError: (error: unknown, entity) => {
			labelsRef.current.delete(entity.apiId);
			toast({
				title: 'Couldn’t add to workspace',
				description:
					error instanceof Error
						? error.message
						: `Couldn't add ${entity.summary} to your workspace from the public catalog.`,
				variant: 'error',
			});
		},
		onSettled: (_data, _error, entity) => {
			inFlightRef.current.delete(entity.apiId);
		},
	});

	// `mutate` is stable across renders (the mutation result object isn't), so
	// memoised catalog rows don't re-render on every parent render. Unlike
	// `mutateAsync` it never rejects — `onError` already toasts the failure.
	const { mutate } = mutation;
	const importEntity = useCallback(
		(entity: DiscoveryEntity) => {
			// Ignore re-clicks while an import for this id is already settling (the
			// 202 round-trip leaves a brief window where the button is still enabled
			// before pendingApiIds updates). Without this, a double-click fires two
			// POST :import calls, two toasts, and two safety timers for one id. Keyed
			// per-id so importing a *different* API concurrently still works.
			if (pendingApiIds.has(entity.apiId) || inFlightRef.current.has(entity.apiId)) return;
			mutate(entity);
		},
		[mutate, pendingApiIds],
	);

	const reconcileImported = useCallback(
		(entities: DiscoveryEntity[]) => {
			if (timeoutsRef.current.size === 0) return;
			let landed = false;
			for (const entity of entities) {
				if (entity.registered && timeoutsRef.current.has(entity.apiId)) {
					const label = labelsRef.current.get(entity.apiId) ?? entity.summary;
					clearPending(entity.apiId);
					landed = true;
					toast({
						title: 'Added to workspace',
						description: `${label} is now in your workspace.`,
						variant: 'success',
					});
				}
			}
			// At least one import landed: a new API now exists in the registry, so
			// drop every stale `GET /apis` list (the landed import then moves from
			// "Adding…" into the panel's API list). Hoisted out of the loop — one
			// invalidation covers the whole batch.
			if (landed) invalidateApiLists(queryClient);
		},
		[clearPending, queryClient],
	);

	return {
		importEntity,
		pendingApiIds,
		hasPendingImports: pendingApiIds.size > 0,
		reconcileImported,
	};
}

interface UseRefreshCatalogResult {
	/** Force a backend manifest rebuild, then refetch the Discover feed. */
	refresh: () => void;
	isRefreshing: boolean;
}

/**
 * Force-refresh the catalog snapshot via `POST /catalog:refresh`.
 *
 * This differs from a client refetch of the loaded pages: it asks the backend
 * to pull the upstream manifest again, which is what actually resets
 * `manifest_age_seconds`. On success we invalidate the whole catalog feed so
 * the ledger + status row re-read the fresh snapshot, and toast the new entry
 * count.
 */
export function useRefreshCatalog(): UseRefreshCatalogResult {
	const queryClient = useQueryClient();

	const mutation = useMutation({
		mutationFn: () => refreshCatalog(),
		onSuccess: (res) => {
			toast({
				title: 'Catalog refreshed',
				description: `Pulled the latest manifest from the Jentic public catalog (${res.count.toLocaleString()} APIs).`,
				variant: 'success',
			});
			queryClient.invalidateQueries({ queryKey: discoverKeys.catalogAll });
		},
		onError: (error: unknown) => {
			toast({
				title: 'Refresh failed',
				description:
					error instanceof Error ? error.message : "Couldn't refresh the public catalog.",
				variant: 'error',
			});
		},
	});

	return {
		refresh: () => {
			mutation.mutate();
		},
		isRefreshing: mutation.isPending,
	};
}
