import { useEffect } from 'react';

/**
 * The slice of a TanStack `useInfiniteQuery` result the drain effect needs.
 * Structural (not the full query object) so callers can pass the query result
 * directly or a hand-built shim in tests.
 */
export interface EagerCursorDrainSource {
	hasNextPage: boolean;
	isFetchingNextPage: boolean;
	/** The query is in error state — a page fetch failed (after retries). */
	isError: boolean;
	fetchNextPage: () => Promise<unknown>;
}

/**
 * Eagerly drain a cursor-paginated infinite query: whenever another page
 * exists and no fetch is in flight, request it — until the roster is
 * complete. For surfaces with no "Load more" affordance (a pill strip, a
 * join source) that need the full list.
 *
 * Guarded against the TanStack v5 failure mode: a failed `fetchNextPage`
 * (after retries) puts the query in error state while `hasNextPage` stays
 * true and `isFetchingNextPage` returns false — an unguarded effect would
 * re-fire forever, hammering the endpoint. The drain stops while `isError`
 * is set; the caller renders a retry affordance whose `fetchNextPage()`
 * clears the error state on success, which resumes the drain naturally.
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
 * Return contract for a hooks-layer "all pages" list read built on
 * {@link useEagerCursorDrain}. Deliberately NOT the raw infinite-query
 * result: consumers get the flattened rows plus exactly the drain facts an
 * honest surface needs — `complete` (every page loaded successfully; until
 * then the list may be missing rows, so derived states must not be
 * asserted) and `retry` (resume after a failed page).
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
}
