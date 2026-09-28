/**
 * Global Monitor filters — the shared time-window + actor selection that the
 * filter bar writes and every list tab reads.
 *
 * State lives in the URL search params so it's deep-linkable and survives tab
 * switches (MonitorPage's `setTab` preserves these keys):
 *
 *   days        trailing window: 1 | 7 | 30 | all (absent = the 7-day default,
 *               the same on every tab — Overview included)
 *   actor_id    selected actor id (absent = "All actors")
 *   actor_type  the selected actor's type (carried alongside actor_id so the
 *               Events/audit endpoints can filter by both)
 *   origin      request origin surface (cli | dashboard | api | agent |
 *               system | mcp — the backend `Origin` enum; absent = all).
 *               Executions-only (the executions endpoint is the one list with
 *               an `origin` query param), so it's dropped on lens switches.
 *
 *   from, to    a custom range (ISO instants) — set by brushing the log's
 *               timeline. Overrides `days` while present; picking a window
 *               (or "Reset") drops it. Log-only: the Overview ignores it.
 *
 * `from` is derived from `days` as an ISO timestamp `days` before now; "All"
 * (`days=all`, list tabs only) omits it. A custom range supplies both ends.
 * Tabs fold `{ from, to, actorId, actorType }` into their list params.
 */
import { useCallback, useMemo } from 'react';
import { useSearchParams } from 'react-router';

export type WindowValue = 'all' | '1' | '7' | '30';

/** The window every tab shows when the URL carries no `days`. */
export const DEFAULT_WINDOW: WindowValue = '7';

export const WINDOW_OPTIONS: { value: WindowValue; label: string }[] = [
	{ value: '1', label: '24h' },
	{ value: '7', label: '7d' },
	{ value: '30', label: '30d' },
	{ value: 'all', label: 'All' },
];

function isWindowValue(value: string | null): value is WindowValue {
	return value === '1' || value === '7' || value === '30' || value === 'all';
}

/**
 * The backend `Origin` enum's wire values (shared/models/actors.py) — how a
 * request reached the platform. `mcp` landed with the local-MCP telemetry
 * work (#1178); an unknown value in the URL is preserved and sent as-is (the
 * backend validates), but the picker only offers the known set.
 */
export const ORIGIN_OPTIONS: { value: string; label: string }[] = [
	{ value: 'cli', label: 'CLI' },
	{ value: 'dashboard', label: 'Dashboard' },
	{ value: 'api', label: 'API' },
	{ value: 'agent', label: 'Agent' },
	{ value: 'system', label: 'System' },
	{ value: 'mcp', label: 'MCP' },
];

/** A brushed time range, epoch ms. */
export interface TimeRange {
	fromMs: number;
	toMs: number;
}

function parseRange(from: string | null, to: string | null): TimeRange | null {
	if (!from || !to) return null;
	const fromMs = Date.parse(from);
	const toMs = Date.parse(to);
	if (Number.isNaN(fromMs) || Number.isNaN(toMs) || toMs <= fromMs) return null;
	return { fromMs, toMs };
}

export interface MonitorFilters {
	/** Raw window selection (defaults to {@link DEFAULT_WINDOW}). */
	window: WindowValue;
	/** Lower bound (ISO): the custom range's start, else `days` before now; null for "All". */
	from: string | null;
	/** Upper bound (ISO) — only a custom range has one; null means "up to now". */
	to: string | null;
	/** The custom range, when one is set. */
	range: TimeRange | null;
	/** `from` expressed in days, or null for "All" (Overview reads this). */
	days: number | null;
	actorId: string | null;
	actorType: string | null;
	/** Origin scope for the executions lens (null = all origins). */
	origin: string | null;
	setWindow: (value: WindowValue) => void;
	setActor: (actorId: string | null, actorType: string | null) => void;
	setOrigin: (origin: string | null) => void;
	setRange: (range: TimeRange | null) => void;
}

export function useMonitorFilters(): MonitorFilters {
	const [searchParams, setSearchParams] = useSearchParams();

	const daysParam = searchParams.get('days');
	const windowValue: WindowValue = isWindowValue(daysParam) ? daysParam : DEFAULT_WINDOW;
	const actorId = searchParams.get('actor_id');
	const actorType = searchParams.get('actor_type');
	const origin = searchParams.get('origin');
	const fromParam = searchParams.get('from');
	const toParam = searchParams.get('to');
	const range = useMemo(() => parseRange(fromParam, toParam), [fromParam, toParam]);

	const { windowFrom, days } = useMemo(() => {
		if (windowValue === 'all') return { windowFrom: null, days: null };
		const d = Number(windowValue);
		const ms = Date.now() - d * 24 * 60 * 60 * 1000;
		return { windowFrom: new Date(ms).toISOString(), days: d };
	}, [windowValue]);
	const from = range ? new Date(range.fromMs).toISOString() : windowFrom;
	const to = range ? new Date(range.toMs).toISOString() : null;

	const setWindow = useCallback(
		(value: WindowValue) => {
			setSearchParams(
				(prev) => {
					const next = new URLSearchParams(prev);
					if (value === DEFAULT_WINDOW) next.delete('days');
					else next.set('days', value);
					next.delete('from');
					next.delete('to');
					next.delete('cursor');
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

	const setOrigin = useCallback(
		(nextOrigin: string | null) => {
			setSearchParams(
				(prev) => {
					const next = new URLSearchParams(prev);
					if (nextOrigin) next.set('origin', nextOrigin);
					else next.delete('origin');
					return next;
				},
				{ replace: true },
			);
		},
		[setSearchParams],
	);

	const setRange = useCallback(
		(nextRange: TimeRange | null) => {
			setSearchParams(
				(prev) => {
					const next = new URLSearchParams(prev);
					if (nextRange) {
						next.set('from', new Date(nextRange.fromMs).toISOString());
						next.set('to', new Date(nextRange.toMs).toISOString());
					} else {
						next.delete('from');
						next.delete('to');
					}
					next.delete('cursor');
					return next;
				},
				{ replace: false },
			);
		},
		[setSearchParams],
	);

	return {
		window: windowValue,
		from,
		to,
		range,
		days,
		actorId,
		actorType,
		origin,
		setWindow,
		setActor,
		setOrigin,
		setRange,
	};
}
