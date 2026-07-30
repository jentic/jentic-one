/**
 * Global Monitor filters — the shared time-window + actor selection that the
 * filter bar writes and every list tab reads.
 *
 * State lives in the URL search params so it's deep-linkable and survives tab
 * switches (MonitorPage's `setTab` preserves these keys):
 *
 *   days        trailing window in days: 1 | 7 | 30 (absent = "All")
 *   actor_id    selected actor id (absent = "All actors")
 *   actor_type  the selected actor's type (carried alongside actor_id so the
 *               Events/audit endpoints can filter by both)
 *   toolkit_id  scope executions to one toolkit (absent = all). Only the
 *               executions endpoint supports it, so it's an executions-tab
 *               deep-link param (the toolkit detail's "Open in Monitor" link
 *               writes it) and is dropped on lens switches, unlike the global
 *               filters above.
 *
 * `from` is derived from `days` as an ISO timestamp `days` before now; "All"
 * omits it. Tabs fold `{ from, actorId, actorType }` into their list params.
 */
import { useCallback, useMemo } from 'react';
import { useSearchParams } from 'react-router';

export type WindowValue = 'all' | '1' | '7' | '30';

export const WINDOW_OPTIONS: { value: WindowValue; label: string }[] = [
	{ value: '1', label: '24h' },
	{ value: '7', label: '7d' },
	{ value: '30', label: '30d' },
	{ value: 'all', label: 'All' },
];

function isWindowValue(value: string | null): value is WindowValue {
	return value === '1' || value === '7' || value === '30' || value === 'all';
}

export interface MonitorFilters {
	/** Raw window selection (defaults to "All"). */
	window: WindowValue;
	/** ISO timestamp `days` before now, or null for "All". */
	from: string | null;
	/** `from` expressed in days, or null for "All" (Overview reads this). */
	days: number | null;
	actorId: string | null;
	actorType: string | null;
	/** Toolkit scope for the executions lens (deep-linked from toolkit detail). */
	toolkitId: string | null;
	setWindow: (value: WindowValue) => void;
	setActor: (actorId: string | null, actorType: string | null) => void;
	setToolkit: (toolkitId: string | null) => void;
}

export function useMonitorFilters(): MonitorFilters {
	const [searchParams, setSearchParams] = useSearchParams();

	const daysParam = searchParams.get('days');
	const windowValue: WindowValue = isWindowValue(daysParam) ? daysParam : 'all';
	const actorId = searchParams.get('actor_id');
	const actorType = searchParams.get('actor_type');
	const toolkitId = searchParams.get('toolkit_id');

	const { from, days } = useMemo(() => {
		if (windowValue === 'all') return { from: null, days: null };
		const d = Number(windowValue);
		const ms = Date.now() - d * 24 * 60 * 60 * 1000;
		return { from: new Date(ms).toISOString(), days: d };
	}, [windowValue]);

	const setWindow = useCallback(
		(value: WindowValue) => {
			setSearchParams(
				(prev) => {
					const next = new URLSearchParams(prev);
					if (value === 'all') next.delete('days');
					else next.set('days', value);
					return next;
				},
				{ replace: true },
			);
		},
		[setSearchParams],
	);

	const setActor = useCallback(
		(nextActorId: string | null, nextActorType: string | null) => {
			setSearchParams(
				(prev) => {
					const next = new URLSearchParams(prev);
					if (nextActorId) next.set('actor_id', nextActorId);
					else next.delete('actor_id');
					if (nextActorId && nextActorType) next.set('actor_type', nextActorType);
					else next.delete('actor_type');
					return next;
				},
				{ replace: true },
			);
		},
		[setSearchParams],
	);

	const setToolkit = useCallback(
		(nextToolkitId: string | null) => {
			setSearchParams(
				(prev) => {
					const next = new URLSearchParams(prev);
					if (nextToolkitId) next.set('toolkit_id', nextToolkitId);
					else next.delete('toolkit_id');
					return next;
				},
				{ replace: true },
			);
		},
		[setSearchParams],
	);

	return {
		window: windowValue,
		from,
		days,
		actorId,
		actorType,
		toolkitId,
		setWindow,
		setActor,
		setToolkit,
	};
}
