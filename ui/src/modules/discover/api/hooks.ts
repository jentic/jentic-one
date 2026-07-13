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
	keepPreviousData,
	useMutation,
	useQueryClient,
} from '@tanstack/react-query';
import { toast } from '@/shared/ui';
import {
	importCatalogEntry,
	listCatalog,
	previewOperations,
	refreshCatalog,
	type CatalogPage,
} from '@/modules/discover/api/client';
import type { CatalogFilter, DiscoveryEntity } from '@/modules/discover/api/types';
import type { OperationPreviewListResponse, PreviewOperationResponse } from '@/shared/api';
import { sharedQueryKeys } from '@/shared/api';

/** Stable query-key roots so callers/tests can target invalidation precisely. */
export const discoverKeys = {
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
 * How long to keep a card in the pending state before giving up on the poll.
 * The catalog only exposes `registered` (not job status), so a failed/stuck
 * import never flips — without this cap the card would spin forever and the
 * poll would hammer the backend. On timeout we drop the pending state and tell
 * the user to refresh; a true failure toast would need a backend job-status
 * read the agent token can reach.
 */
const IMPORT_PENDING_TIMEOUT_MS = 60_000;

export interface UseDiscoverCatalogResult {
	/** Flattened entities across all loaded keyset pages. */
	entities: DiscoveryEntity[];
	/** Whole-manifest size (stable while scrolling — no status-row flicker). */
	catalogTotal: number;
	/** How many of the whole manifest are imported locally. */
	registeredCount: number;
	/** Manifest freshness from the first page; null = never fetched. */
	manifestAgeSeconds: number | null;
	isPending: boolean;
	isFetching: boolean;
	error: Error | null;
	hasNextPage: boolean;
	isFetchingNextPage: boolean;
	fetchNextPage: () => void;
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
	 * Poll the feed every few seconds while an import is in flight, so a card
	 * flips Available → Imported on its own once the async job lands (the
	 * catalog's `registered` flag is the only completion signal the agent-scoped
	 * UI can observe — `/jobs` is admin-only). Off when nothing is pending.
	 */
	pollWhilePending?: boolean;
}): UseDiscoverCatalogResult {
	const query = useInfiniteQuery<CatalogPage>({
		queryKey: discoverKeys.catalog(params.q, params.filter),
		queryFn: ({ pageParam }) =>
			listCatalog({ q: params.q, filter: params.filter, cursor: pageParam as string | null }),
		initialPageParam: null as string | null,
		getNextPageParam: (lastPage) => (lastPage.hasMore ? lastPage.nextCursor : undefined),
		// Keep the previous page visible while a new q/filter query loads, so the
		// grid doesn't flash to skeletons on every keystroke.
		placeholderData: keepPreviousData,
		refetchInterval: params.pollWhilePending ? importPollIntervalMs : false,
	});

	const entities = useMemo(
		() => query.data?.pages.flatMap((p) => p.entities) ?? [],
		[query.data],
	);
	const first = query.data?.pages[0];

	return {
		entities,
		catalogTotal: first?.catalogTotal ?? 0,
		registeredCount: first?.registeredCount ?? 0,
		manifestAgeSeconds: first?.manifestAgeSeconds ?? null,
		isPending: query.isPending,
		isFetching: query.isFetching,
		error: query.error as Error | null,
		hasNextPage: query.hasNextPage,
		isFetchingNextPage: query.isFetchingNextPage,
		fetchNextPage: () => {
			void query.fetchNextPage();
		},
		refetch: () => {
			void query.refetch();
		},
	};
}

/** Operations are paged 25 at a time behind a "Load more" button. */
export const OPERATION_PREVIEW_PAGE_SIZE = 25;

export interface OperationPreviewPage {
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

	return {
		operations,
		total: first?.total ?? 0,
		info: first?.info,
		securitySchemes: first?.securitySchemes ?? {},
		isPending: query.isPending,
		error: query.error as Error | null,
		hasNextPage: query.hasNextPage,
		isFetchingNextPage: query.isFetchingNextPage,
		fetchNextPage: () => {
			void query.fetchNextPage();
		},
	};
}

interface UseImportResult {
	/** Enqueue an import for a catalog entity. Resolves once queued (202). */
	importEntity: (entity: DiscoveryEntity) => Promise<void>;
	isImporting: boolean;
	/**
	 * Catalog api_ids with an import job still settling — covers the whole
	 * window from the 202 until the catalog reports `registered: true` (or the
	 * safety timeout fires), NOT just the in-flight HTTP request. The grid + sheet
	 * read this to keep the card in a "Importing…" pending state.
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
 * So a card has three honest states — Available → Pending → Imported — and the
 * pending state must outlive the (millisecond) 202 request. We track pending
 * api_ids in a set, ask the page to poll the feed while it's non-empty, and
 * clear an id (with a success toast) when `reconcileImported` sees it turn
 * registered. Each pending id also has a safety timeout so a failed/stuck job
 * can't pin a card in "Importing…" forever.
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
		onSuccess: (res, entity) => {
			toast({
				title: 'Import started',
				description: `${entity.summary} is importing into your workspace (job ${res.job_id}). This can take a moment.`,
				variant: 'success',
			});
			// Enter the pending state and arm a safety timeout for this id.
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
					title: 'Still importing',
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
				title: 'Import failed',
				description:
					error instanceof Error
						? error.message
						: `Couldn't import ${entity.summary} from the public catalog.`,
				variant: 'error',
			});
		},
		onSettled: (_data, _error, entity) => {
			inFlightRef.current.delete(entity.apiId);
		},
	});

	const importEntity = useCallback(
		async (entity: DiscoveryEntity) => {
			// Ignore re-clicks while an import for this id is already settling (the
			// 202 round-trip leaves a brief window where the button is still enabled
			// before pendingApiIds updates). Without this, a double-click fires two
			// POST :import calls, two toasts, and two safety timers for one id. Keyed
			// per-id so importing a *different* API concurrently still works.
			if (pendingApiIds.has(entity.apiId) || inFlightRef.current.has(entity.apiId)) return;
			await mutation.mutateAsync(entity);
		},
		[mutation, pendingApiIds],
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
						title: 'Import complete',
						description: `${label} is now in your workspace.`,
						variant: 'success',
					});
				}
			}
			// At least one import landed: a new API now exists in the Workspace
			// registry, so drop the stale `GET /apis` cache. The cross-module key
			// comes from the shared registry (#511) so it can't drift from
			// `workspaceKeys.apis()`. Hoisted out of the loop — one invalidation
			// covers the whole batch.
			if (landed) {
				queryClient.invalidateQueries({ queryKey: sharedQueryKeys.workspaceApis });
			}
		},
		[clearPending, queryClient],
	);

	return {
		importEntity,
		isImporting: mutation.isPending,
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
 * This differs from `useDiscoverCatalog().refetch` (a client refetch of the
 * current page): it asks the backend to pull the upstream manifest again, which
 * is what actually resets `manifest_age_seconds`. On success we invalidate the
 * whole Discover feed so the grid + status row re-read the fresh snapshot, and
 * toast the new entry count.
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
