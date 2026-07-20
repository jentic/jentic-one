/**
 * Client-side filter state for the Sessions table, persisted to the URL search
 * params so a filtered view is deep-linkable and survives reloads.
 *
 *   q       free-text query over session title + actor id
 *   status  session status (absent = "all")
 *   api     one of the union of `apis_touched` (absent = "all")
 *
 * The filtering itself is applied in the view; this hook only owns the
 * read/write of the three params (mirrors the Monitor module's pattern).
 */
import { useCallback, useMemo } from 'react';
import { useSearchParams } from 'react-router-dom';

export interface SessionFilters {
	q: string;
	status: string;
	api: string;
	setQuery: (value: string) => void;
	setStatus: (value: string) => void;
	setApi: (value: string) => void;
	reset: () => void;
	active: boolean;
}

const ALL = 'all';

export function useSessionFilters(): SessionFilters {
	const [searchParams, setSearchParams] = useSearchParams();

	const q = searchParams.get('q') ?? '';
	const status = searchParams.get('status') ?? ALL;
	const api = searchParams.get('api') ?? ALL;

	const update = useCallback(
		(key: string, value: string, emptyWhen: string) => {
			setSearchParams(
				(prev) => {
					const next = new URLSearchParams(prev);
					if (!value || value === emptyWhen) next.delete(key);
					else next.set(key, value);
					return next;
				},
				{ replace: true },
			);
		},
		[setSearchParams],
	);

	const setQuery = useCallback((value: string) => update('q', value, ''), [update]);
	const setStatus = useCallback((value: string) => update('status', value, ALL), [update]);
	const setApi = useCallback((value: string) => update('api', value, ALL), [update]);

	const reset = useCallback(() => {
		setSearchParams(
			(prev) => {
				const next = new URLSearchParams(prev);
				next.delete('q');
				next.delete('status');
				next.delete('api');
				return next;
			},
			{ replace: true },
		);
	}, [setSearchParams]);

	const active = useMemo(
		() => q.trim() !== '' || status !== ALL || api !== ALL,
		[q, status, api],
	);

	return { q, status, api, setQuery, setStatus, setApi, reset, active };
}
