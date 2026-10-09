/**
 * The "Your APIs" list filter in the Library's workspace panel — a text filter
 * and a serving-state filter (All / Live / Draft / Update available), both
 * client-side over the already-loaded digest rows.
 *
 * The state lives in the URL on `/library` (`?q=`, `?status=`) — the params
 * the `/library/workspace` redirect carries over — so a redirected
 * `/library/workspace?status=draft` link, a reload, or an in-app link
 * (`/library?q=<catalog id>`) lands on the filtered list. Only one panel
 * surface (docked card or mobile sheet) is mounted at a time, so the URL is
 * the single owner.
 */
import { useCallback } from 'react';
import { useSearchParams } from 'react-router';
import { apiServingState } from '@/shared/ui';
import type { WorkspaceDigestRow } from '@/modules/discover/api';

export type WorkspaceStatusFilter = 'all' | 'live' | 'draft' | 'update';

const STATUS_FILTERS: readonly WorkspaceStatusFilter[] = ['all', 'live', 'draft', 'update'];

/** URL params the filter owns on `/library`. */
const WORKSPACE_FILTER_PARAMS = { q: 'q', status: 'status' } as const;

function isStatusFilter(value: string | null): value is WorkspaceStatusFilter {
	return STATUS_FILTERS.includes(value as WorkspaceStatusFilter);
}

export function matchesStatus(row: WorkspaceDigestRow, status: WorkspaceStatusFilter): boolean {
	return status === 'all' || apiServingState(row).states.includes(status);
}

/** In-memory match over name, description, vendor/name, host and catalog id. */
export function matchesText(row: WorkspaceDigestRow, needle: string): boolean {
	const n = needle.trim().toLowerCase();
	if (!n) return true;
	return [
		row.title,
		row.description ?? '',
		row.ref.vendor,
		row.ref.name,
		row.host ?? '',
		row.catalogApiId ?? '',
	]
		.join(' ')
		.toLowerCase()
		.includes(n);
}

export interface WorkspaceListFilter {
	q: string;
	status: WorkspaceStatusFilter;
	setQ: (next: string) => void;
	setStatus: (next: WorkspaceStatusFilter) => void;
	clear: () => void;
	/** A text or status filter is applied. */
	active: boolean;
}

export function useWorkspaceListFilter(): WorkspaceListFilter {
	const [searchParams, setSearchParams] = useSearchParams();
	const q = searchParams.get(WORKSPACE_FILTER_PARAMS.q) ?? '';
	const statusParam = searchParams.get(WORKSPACE_FILTER_PARAMS.status);
	const status: WorkspaceStatusFilter = isStatusFilter(statusParam) ? statusParam : 'all';

	const update = useCallback(
		(patch: Partial<Record<'q' | 'status', string | null>>) =>
			setSearchParams(
				(prev) => {
					const params = new URLSearchParams(prev);
					for (const [key, value] of Object.entries(patch)) {
						const name = WORKSPACE_FILTER_PARAMS[key as 'q' | 'status'];
						if (value) params.set(name, value);
						else params.delete(name);
					}
					return params;
				},
				// A filter keystroke isn't a navigation — don't stack history.
				{ replace: true },
			),
		[setSearchParams],
	);

	const setQ = useCallback((next: string) => update({ q: next }), [update]);
	const setStatus = useCallback(
		(next: WorkspaceStatusFilter) => update({ status: next === 'all' ? null : next }),
		[update],
	);
	const clear = useCallback(() => update({ q: null, status: null }), [update]);

	return { q, status, setQ, setStatus, clear, active: q.trim().length > 0 || status !== 'all' };
}
