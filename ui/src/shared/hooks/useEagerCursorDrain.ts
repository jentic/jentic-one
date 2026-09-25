import { useEffect } from 'react';

/** The slice of a TanStack `useInfiniteQuery` result the drain effect needs.
 * Structural, so tests can pass a hand-built shim. */
export interface EagerCursorDrainSource {
	hasNextPage: boolean;
	isFetchingNextPage: boolean;
	/** The query is in error state — a page fetch failed (after retries). */
	isError: boolean;
	fetchNextPage: () => Promise<unknown>;
}

/**
 * Eagerly drain a cursor-paginated infinite query: request the next page whenever
 * one exists and nothing is in flight, for surfaces with no "Load more". The drain
 * stops while `isError` is set — a failed `fetchNextPage` leaves `hasNextPage` true
 * and `isFetchingNextPage` false, so an unguarded effect would re-fire forever.
 */
export function useEagerCursorDrain({
	hasNextPage,
	isFetchingNextPage,
	isError,
	fetchNextPage,
}: EagerCursorDrainSource): void {
	useEffect(() => {
		if (isError) return;
		if (hasNextPage && !isFetchingNextPage) void fetchNextPage();
	}, [hasNextPage, isFetchingNextPage, isError, fetchNextPage]);
}

/**
 * Return contract for a hooks-layer "all pages" read: the flattened rows plus
 * `complete` (until then, derived states must not be asserted) and `retry`.
 */
export interface DrainedList<T> {
	/** Every row loaded so far, flattened across pages (memoised). */
	items: T[];
	/** No page has resolved yet (first-page skeleton gate). */
	isPending: boolean;
	/** The most recent page failure, if the query is in error state. */
	error: Error | null;
	/** True only when every page loaded successfully — the list is whole. */
	complete: boolean;
	/** Retry after a failure; a success resumes the eager drain. */
	retry: () => void;
	/** Re-read every page already loaded, for a surface with a refresh verb.
	 * Distinct from {@link retry}, which resumes a stalled drain. */
	refresh: () => void;
	/** A read is in flight — any page, first or subsequent. Drives a spinner. */
	isFetching: boolean;
}
