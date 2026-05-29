/**
 * useInfiniteScroll
 *
 * Tiny `IntersectionObserver` wrapper that fires `onLoadMore` whenever the
 * sentinel element becomes visible — provided more pages exist and a
 * load isn't already in flight.
 *
 * Ported from the same pattern used in `jentic-webapp` (see
 * `client/src/components/collections/add-api/ApiSearchStep.tsx`). The
 * hook keeps the observer alive for the component's lifetime and reads
 * the live `hasMore` / `isLoading` flags through refs so the callback
 * is stable and we don't tear down/rebuild the observer on every render.
 *
 * Returns a ref-callback. Pass it to the sentinel element rendered at
 * the bottom of the list:
 *
 * ```tsx
 * const sentinelRef = useInfiniteScroll({ hasMore, isLoading, onLoadMore });
 * <div ref={sentinelRef} />
 * ```
 *
 * Notes:
 *  - `rootMargin: '300px'` so the next page starts loading slightly before
 *    the sentinel actually scrolls into view — gives perceived instant
 *    pagination on fast networks.
 *  - When `hasMore` flips false, the next-page callback simply no-ops; we
 *    leave the observer attached so re-enabling later (filter change with
 *    a fresh result set) immediately resumes auto-loading without a
 *    re-mount round-trip.
 *  - After a load completes the hook re-observes the sentinel — important
 *    when the freshly-loaded rows didn't push the sentinel out of the
 *    viewport (very tall viewports / very short pages).
 */

import { useCallback, useEffect, useRef } from 'react';

export interface UseInfiniteScrollOptions {
	/** Whether more pages are available from the server. */
	hasMore: boolean;
	/** Whether a load is currently in flight — prevents duplicate fetches. */
	isLoading: boolean;
	/** Called when the sentinel intersects the viewport. */
	onLoadMore: () => void;
	/**
	 * IntersectionObserver `rootMargin`. Defaults to `'300px'` so loading
	 * starts a screen-ish before the sentinel scrolls into view.
	 */
	rootMargin?: string;
	/**
	 * Hard cap on the number of items the hook is willing to keep
	 * auto-loading past. Past this point the user has to click an
	 * explicit "load more" button — a guardrail against runaway scroll
	 * eating the device's memory budget. Set to `Infinity` to disable.
	 */
	maxAutoLoad?: number;
	/** Current accumulated count, only used together with `maxAutoLoad`. */
	currentCount?: number;
}

export function useInfiniteScroll({
	hasMore,
	isLoading,
	onLoadMore,
	rootMargin = '300px',
	maxAutoLoad = Infinity,
	currentCount = 0,
}: UseInfiniteScrollOptions): (node: HTMLElement | null) => void {
	const observerRef = useRef<IntersectionObserver | null>(null);
	const sentinelNodeRef = useRef<HTMLElement | null>(null);

	const onLoadMoreRef = useRef(onLoadMore);
	onLoadMoreRef.current = onLoadMore;
	const isLoadingRef = useRef(isLoading);
	isLoadingRef.current = isLoading;
	const hasMoreRef = useRef(hasMore);
	hasMoreRef.current = hasMore;
	const currentCountRef = useRef(currentCount);
	currentCountRef.current = currentCount;
	const maxAutoLoadRef = useRef(maxAutoLoad);
	maxAutoLoadRef.current = maxAutoLoad;

	useEffect(() => {
		if (typeof IntersectionObserver === 'undefined') return;

		const observer = new IntersectionObserver(
			(entries) => {
				const inView = entries[0]?.isIntersecting ?? false;
				if (
					inView &&
					!isLoadingRef.current &&
					hasMoreRef.current &&
					currentCountRef.current < maxAutoLoadRef.current
				) {
					onLoadMoreRef.current();
				}
			},
			{ rootMargin, threshold: 0 },
		);

		observerRef.current = observer;
		if (sentinelNodeRef.current) {
			observer.observe(sentinelNodeRef.current);
		}

		return () => {
			observer.disconnect();
			observerRef.current = null;
		};
	}, [rootMargin]);

	useEffect(() => {
		if (isLoading || !hasMore || currentCount >= maxAutoLoad) return;

		const timer = setTimeout(() => {
			const sentinel = sentinelNodeRef.current;
			const observer = observerRef.current;
			if (sentinel && observer) {
				observer.unobserve(sentinel);
				observer.observe(sentinel);
			}
		}, 100);

		return () => clearTimeout(timer);
	}, [isLoading, hasMore, currentCount, maxAutoLoad]);

	const sentinelRef = useCallback((node: HTMLElement | null) => {
		const observer = observerRef.current;
		if (observer && sentinelNodeRef.current) {
			observer.unobserve(sentinelNodeRef.current);
		}
		sentinelNodeRef.current = node;
		if (observer && node) {
			observer.observe(node);
		}
	}, []);

	return sentinelRef;
}
