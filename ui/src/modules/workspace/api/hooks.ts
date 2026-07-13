/**
 * Workspace service tier — TanStack Query hooks.
 *
 * The ONLY backend access path for Workspace views: components/pages call these
 * hooks, which call the repository (`./client`), which calls `@/shared/api`.
 * Views never reach past this layer (ESLint-enforced). Mirrors the backend's
 * Service layer.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
	keepPreviousData,
	useInfiniteQuery,
	useMutation,
	useQuery,
	useQueryClient,
	type UseQueryResult,
} from '@tanstack/react-query';
import { toast } from '@/shared/ui';
import {
	archiveRevision,
	deleteApi,
	getApi,
	getApiSpec,
	getRevisionSpec,
	getJob,
	importSources,
	listApis,
	listOperations,
	listRevisions,
	promoteRevision,
} from '@/modules/workspace/api/client';
import type { ApiKey } from '@/modules/workspace/api/apiId';
import { formatApiKey } from '@/modules/workspace/api/apiId';
import type {
	ApiOperation,
	ApiRevision,
	CursorPage,
	ImportSource,
	JobStatus,
	WorkspaceApi,
} from '@/modules/workspace/api/types';
import { sharedQueryKeys } from '@/shared/api';

/** Stable query-key roots so callers/tests can target invalidation precisely. */
export const workspaceKeys = {
	all: ['workspace'] as const,
	// Cross-module root: Discover invalidates this after a catalog import. Defined
	// once in the shared registry (`sharedQueryKeys.workspaceApis`) so the two
	// can't drift; this factory re-uses it as its own list key (#511).
	apis: () => [...sharedQueryKeys.workspaceApis] as const,
	api: (key: ApiKey) => [...workspaceKeys.all, 'api', formatApiKey(key)] as const,
	operations: (key: ApiKey) => [...workspaceKeys.all, 'operations', formatApiKey(key)] as const,
	revisions: (key: ApiKey) => [...workspaceKeys.all, 'revisions', formatApiKey(key)] as const,
	spec: (key: ApiKey) => [...workspaceKeys.all, 'spec', formatApiKey(key)] as const,
	revisionSpec: (key: ApiKey, revisionId: string) =>
		[...workspaceKeys.all, 'spec', formatApiKey(key), revisionId] as const,
};

/** The workspace API list. */
export function useWorkspaceApis(): UseQueryResult<CursorPage<WorkspaceApi>> {
	return useQuery({
		queryKey: workspaceKeys.apis(),
		queryFn: () => listApis(),
		placeholderData: keepPreviousData,
	});
}

/** A single API's detail. Disabled until a valid key is decoded from the route. */
export function useWorkspaceApi(key: ApiKey | null): UseQueryResult<WorkspaceApi> {
	return useQuery({
		queryKey: key ? workspaceKeys.api(key) : workspaceKeys.all,
		queryFn: () => getApi(key as ApiKey),
		enabled: key != null,
	});
}

/**
 * Operations for an API's current revision.
 *
 * The endpoint is cursor-paginated (25/page) with no server-side search, so to
 * let the UI filter across *every* operation we walk `next_cursor` to the end
 * in the background after the first page paints. `useInfiniteQuery` holds the
 * pages and {@link UseApiOperations.operations} flattens them; the section
 * shows load progress against the API's known `operation_count` while the
 * remaining pages stream in.
 *
 * The background walk is defensively bounded: it stops on error, once the
 * loaded count reaches the known `operation_count`, and if the server ever
 * repeats a cursor — so a misbehaving backend can't drive an unbounded loop.
 */
export interface UseApiOperations {
	/** All operations loaded so far (flattened across pages). */
	operations: ApiOperation[];
	/** First-page load in flight (nothing to show yet). */
	isLoading: boolean;
	/**
	 * The background walk hasn't reached the last page yet — more operations are
	 * still streaming in, so a client-side filter isn't yet exhaustive. False
	 * once every page is loaded, the walk errors, or the bound is hit.
	 */
	isLoadingAll: boolean;
	isError: boolean;
	error: unknown;
	/** Re-run the walk from the first page (used to recover from a mid-walk error). */
	retry: () => void;
}

export function useApiOperations(key: ApiKey | null, totalCount?: number): UseApiOperations {
	const query = useInfiniteQuery({
		queryKey: key ? workspaceKeys.operations(key) : workspaceKeys.all,
		queryFn: ({ pageParam }) =>
			listOperations({ key: key as ApiKey, cursor: pageParam as string | null }),
		initialPageParam: null as string | null,
		getNextPageParam: (lastPage) => (lastPage.hasMore ? lastPage.nextCursor : undefined),
		enabled: key != null,
		// A draft-only API legitimately 404s (`no_current_revision`); don't retry
		// that into a slow spinner — the component renders a promote CTA instead.
		retry: false,
		// Keep the prior list on refetch (e.g. after promote/archive invalidation)
		// so the section doesn't collapse to skeletons and restart the walk visibly.
		placeholderData: keepPreviousData,
	});

	const operations = useMemo(
		() => query.data?.pages.flatMap((page) => page.items) ?? [],
		[query.data],
	);

	// Walk to the end in the background so the client-side filter can cover every
	// operation (the backend offers no search param). One page is in flight at a
	// time; TanStack Query dedupes/serializes the `fetchNextPage` calls.
	//
	// Guards against an unbounded loop on a misbehaving backend: stop on error,
	// once we've loaded the known total, and if the next cursor repeats the one
	// we just used (a stable cursor would otherwise fetch forever).
	const { hasNextPage, isFetchingNextPage, isError, fetchNextPage } = query;
	const lastCursorRef = useRef<string | null | undefined>(undefined);
	useEffect(() => {
		if (!hasNextPage || isFetchingNextPage || isError) return;
		if (totalCount != null && operations.length >= totalCount) return;

		const pages = query.data?.pages;
		const nextCursor = pages?.[pages.length - 1]?.nextCursor ?? null;
		if (nextCursor != null && nextCursor === lastCursorRef.current) return;
		lastCursorRef.current = nextCursor;
		void fetchNextPage();
	}, [
		hasNextPage,
		isFetchingNextPage,
		isError,
		totalCount,
		operations.length,
		query.data,
		fetchNextPage,
	]);

	// "Loading the rest" is only true while the walk is actually progressing —
	// an errored walk has stopped, so the section can surface a retry instead of
	// a perpetual spinner. The bound (loaded >= total) also ends the progress.
	const reachedTotal = totalCount != null && operations.length >= totalCount;
	const isLoadingAll =
		!isError && !reachedTotal && (query.hasNextPage || query.isFetchingNextPage);

	return {
		operations,
		isLoading: query.isLoading,
		isLoadingAll,
		isError: query.isError,
		error: query.error,
		retry: () => {
			lastCursorRef.current = undefined;
			void query.refetch();
		},
	};
}

/** Revisions for an API. */
export function useApiRevisions(key: ApiKey | null): UseQueryResult<CursorPage<ApiRevision>> {
	return useQuery({
		queryKey: key ? workspaceKeys.revisions(key) : workspaceKeys.all,
		queryFn: () => listRevisions({ key: key as ApiKey }),
		enabled: key != null,
	});
}

/**
 * The resolved OpenAPI document for an API revision. Disabled until `enabled`
 * flips true (the spec viewer only fetches when opened) and a valid key is
 * present, so the (potentially large) document isn't loaded eagerly.
 *
 * Pass a `revisionId` to view a *specific* revision (old/archived or
 * draft/pending) instead of the live one — the live revision is the default
 * when `revisionId` is omitted/null.
 */
export function useApiSpec(
	key: ApiKey | null,
	enabled: boolean,
	revisionId?: string | null,
): UseQueryResult<unknown> {
	return useQuery({
		queryKey: key
			? revisionId
				? workspaceKeys.revisionSpec(key, revisionId)
				: workspaceKeys.spec(key)
			: workspaceKeys.all,
		queryFn: () =>
			revisionId ? getRevisionSpec(key as ApiKey, revisionId) : getApiSpec(key as ApiKey),
		enabled: key != null && enabled,
		staleTime: 5 * 60_000,
	});
}

/** Promote / archive a revision, invalidating the API + its revision/op/spec lists. */
export function useRevisionActions(key: ApiKey) {
	const queryClient = useQueryClient();

	const invalidate = useCallback(() => {
		queryClient.invalidateQueries({ queryKey: workspaceKeys.api(key) });
		queryClient.invalidateQueries({ queryKey: workspaceKeys.revisions(key) });
		queryClient.invalidateQueries({ queryKey: workspaceKeys.operations(key) });
		// Promote changes which revision is live, so the cached live spec (and any
		// per-revision specs) are stale. `spec(key)` is the prefix of both the live
		// key and the `revisionSpec` keys, so this one call covers them all.
		queryClient.invalidateQueries({ queryKey: workspaceKeys.spec(key) });
	}, [queryClient, key]);

	const promote = useMutation({
		mutationFn: (revisionId: string) => promoteRevision(key, revisionId),
		onSuccess: () => {
			toast({
				variant: 'success',
				title: 'Revision promoted',
				description: 'It is now the live revision.',
			});
			invalidate();
		},
		onError: (error: unknown) => {
			toast({
				variant: 'error',
				title: 'Promote failed',
				description:
					error instanceof Error ? error.message : 'Could not promote the revision.',
			});
		},
	});

	const archive = useMutation({
		mutationFn: (revisionId: string) => archiveRevision(key, revisionId),
		onSuccess: () => {
			toast({ variant: 'success', title: 'Revision archived' });
			invalidate();
		},
		onError: (error: unknown) => {
			toast({
				variant: 'error',
				title: 'Archive failed',
				description:
					error instanceof Error ? error.message : 'Could not archive the revision.',
			});
		},
	});

	const pendingRevisionId =
		(promote.isPending && (promote.variables as string)) ||
		(archive.isPending && (archive.variables as string)) ||
		null;

	return {
		promote: (revisionId: string) => promote.mutate(revisionId),
		archive: (revisionId: string) => archive.mutate(revisionId),
		pendingRevisionId,
		/** Which action is in flight, so a row spins only the button it triggered. */
		pendingAction: (promote.isPending ? 'promote' : archive.isPending ? 'archive' : null) as
			'promote' | 'archive' | null,
	};
}

/**
 * Hard-delete an API (and every revision under it). Cascades server-side to
 * operations and release pointers — irreversible. UI must gate this behind
 * `CascadeDeleteDialog`. The kill switch / archive flows remain available for
 * reversible takedowns.
 */
export function useDeleteApi() {
	const queryClient = useQueryClient();
	return useMutation<void, Error, ApiKey>({
		mutationFn: (key) => deleteApi(key),
		onSuccess: () => {
			queryClient.invalidateQueries({ queryKey: workspaceKeys.apis() });
			toast({ variant: 'success', title: 'API removed' });
		},
		onError: (error) => {
			toast({
				variant: 'error',
				title: 'Remove failed',
				description: error instanceof Error ? error.message : 'Could not remove the API.',
			});
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
 * `succeeded`/`failed`. On success we invalidate the workspace list so the new
 * API materializes; on failure we surface the job's `error` (e.g. the backend
 * embeddings-extra gap verified against the live backend). The dialog awaits
 * the returned `JobStatus` so it can keep the form open + show the error on a
 * failed job, per the dialog state-lifecycle convention.
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
					queryClient.invalidateQueries({ queryKey: workspaceKeys.apis() });
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
