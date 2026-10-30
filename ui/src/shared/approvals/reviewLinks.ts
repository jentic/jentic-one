/**
 * The URL vocabulary that opens the held-call review surfaces on any page:
 *
 *   - `?review=<heldId>` — the review pop-up on one held call. It walks that
 *     call's agent's waiting calls: after a decision it moves on to the agent's
 *     next, and the agent's last decision closes it. A held call's
 *     `review_url` lands here. With `&review_all=1` (the attention toast, the
 *     one surface that speaks for every agent at once) it walks every waiting
 *     call instead, the open call's agent first.
 *   - `?review_batch=<id>,<id>…` — the bulk-approve confirmation for those
 *     calls (deletes are never in it; each is approved on its own).
 *   - `?inbox=1` — the shell rail opened on its Inbox tab (the drawer below
 *     `xl`). Read once and stripped, like the Agents page's `?credentials`.
 *
 * Every surface (the waiting card, the inbox rail, the bell, the attention
 * toast) opens them through `useReviewLinks`, so the pop-up host in the shell
 * is the one place that renders them.
 */
import { useCallback, useMemo } from 'react';
import { useSearchParams } from 'react-router';

export const REVIEW_PARAM = 'review';
export const REVIEW_ALL_PARAM = 'review_all';
export const REVIEW_BATCH_PARAM = 'review_batch';
export const INBOX_PARAM = 'inbox';

/**
 * An id that can be one path segment: not empty, no whitespace, none of the
 * characters that would change the route (`/ ? # %`), not a dot segment. Only
 * the shape that would break the request is refused; the server's own 404 is
 * the guard on what the id names.
 */
const HELD_ID = /^(?!\.{1,2}$)[^\s/?#%]{1,256}$/;
const validId = (value: string | null): string | null =>
	value != null && HELD_ID.test(value) ? value : null;

/** The search string that opens the pop-up on `heldId`, keeping the rest of `search`. */
export function reviewSearch(search: URLSearchParams | string, heldId: string): string {
	const next = new URLSearchParams(search);
	next.delete(REVIEW_BATCH_PARAM);
	next.delete(REVIEW_ALL_PARAM);
	next.set(REVIEW_PARAM, heldId);
	return `?${next}`;
}

export interface ReviewLinks {
	/** The held call the pop-up is on, if it is open. */
	reviewId: string | null;
	/** The open pop-up walks every agent's waiting calls, not just the open call's agent's. */
	reviewAll: boolean;
	/** The calls the bulk-approve confirmation is on, if it is open; each once. */
	batchIds: string[];
	/** A review param is present but names nothing (empty, malformed): the host strips it. */
	dangling: boolean;
	/** Open the pop-up on `heldId`, over its agent's calls; `all` for every agent's. */
	openReview: (heldId: string, opts?: { all?: boolean }) => void;
	openBatch: (heldIds: readonly string[]) => void;
	/** Moves the open pop-up to another call. */
	advanceReview: (heldId: string) => void;
	/** Closes the pop-up and the bulk confirmation. */
	closeReview: () => void;
	openInbox: () => void;
}

export function useReviewLinks(): ReviewLinks {
	const [params, setParams] = useSearchParams();
	const reviewId = validId(params.get(REVIEW_PARAM));
	const reviewAll = params.get(REVIEW_ALL_PARAM) === '1';
	const batchParam = params.get(REVIEW_BATCH_PARAM);
	const hasReviewParam = params.has(REVIEW_PARAM);

	// Every change replaces the entry: the pop-up and the confirmation are no
	// history of their own, so Back leaves the page instead of walking through
	// each one opened and closed on it.
	const update = useCallback(
		(edit: (next: URLSearchParams) => void) =>
			setParams(
				(prev) => {
					const next = new URLSearchParams(prev);
					edit(next);
					return next;
				},
				{ replace: true },
			),
		[setParams],
	);

	return useMemo(() => {
		const batchIds = batchParam
			? [
					...new Set(
						batchParam
							.split(',')
							.map(validId)
							.filter((id): id is string => id != null),
					),
				]
			: [];
		return {
			reviewId,
			reviewAll,
			batchIds,
			dangling:
				(hasReviewParam && !reviewId) ||
				(!reviewId && batchParam != null && batchIds.length === 0),
			openReview: (heldId, opts) =>
				update((next) => {
					next.delete(REVIEW_BATCH_PARAM);
					if (opts?.all) next.set(REVIEW_ALL_PARAM, '1');
					else next.delete(REVIEW_ALL_PARAM);
					next.set(REVIEW_PARAM, heldId);
				}),
			openBatch: (heldIds) =>
				update((next) => {
					next.delete(REVIEW_PARAM);
					next.delete(REVIEW_ALL_PARAM);
					next.set(REVIEW_BATCH_PARAM, heldIds.join(','));
				}),
			// The scope rides along: the pop-up keeps walking the same queue.
			advanceReview: (heldId) => update((next) => next.set(REVIEW_PARAM, heldId)),
			closeReview: () =>
				update((next) => {
					next.delete(REVIEW_PARAM);
					next.delete(REVIEW_ALL_PARAM);
					next.delete(REVIEW_BATCH_PARAM);
				}),
			openInbox: () => update((next) => next.set(INBOX_PARAM, '1')),
		};
	}, [reviewId, reviewAll, batchParam, hasReviewParam, update]);
}
