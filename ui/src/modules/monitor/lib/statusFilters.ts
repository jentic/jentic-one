/**
 * Per-source status filters for the Activity view.
 *
 * Every source keeps its status narrowing in the same `?status=` param (the
 * toolbar writes it, the source body reads it) and speaks one vocabulary —
 * "Failed" means the same thing wherever you are — plus the extras a source
 * genuinely has. Switching source drops the param, so a value never leaks
 * into a source that can't read it.
 *
 *   all    failed | action                (Everything feed: "Needs you")
 *   calls  failed | completed             (API calls)
 *   jobs   failed | active | completed    (Jobs: "In progress")
 *   audit  —                              (no status axis)
 *
 * Links from before the shared vocabulary said `failures` on Everything;
 * {@link useStatusFilter} reads that as `failed`.
 */
import { useCallback } from 'react';
import { useSearchParams } from 'react-router';
import type { ActivitySource } from '@/modules/monitor/api';

export type FeedStatus = 'all' | 'failed' | 'action';
export type CallStatus = 'all' | 'failed' | 'completed';
export type JobStatusFilter = 'all' | 'failed' | 'active' | 'completed';

export const STATUS_OPTIONS: Record<ActivitySource, { value: string; label: string }[]> = {
	all: [
		{ value: 'all', label: 'All' },
		{ value: 'failed', label: 'Failed' },
		{ value: 'action', label: 'Needs you' },
	],
	calls: [
		{ value: 'all', label: 'All' },
		{ value: 'failed', label: 'Failed' },
		{ value: 'completed', label: 'Succeeded' },
	],
	jobs: [
		{ value: 'all', label: 'All' },
		{ value: 'failed', label: 'Failed' },
		{ value: 'active', label: 'In progress' },
		{ value: 'completed', label: 'Completed' },
	],
	audit: [],
};

/** Retired `?status=` spellings → today's value. */
const STATUS_ALIASES: Record<string, string> = { failures: 'failed' };

// The backend's ExecutionStatus enum is terminal-only — it accepts exactly
// `completed` and `failed`, and 422s on any other value (see the executions
// router's `_TERMINAL_STATUSES` guard). There is no "running" execution to
// filter on, so we send the exact wire value for the chosen terminal status.
export const CALL_STATUS_WIRE: Record<Exclude<CallStatus, 'all'>, string[]> = {
	completed: ['completed'],
	failed: ['failed'],
};

// The backend's JobStatus StrEnum: queued/running/completed/failed/cancelled/
// dead_letter. "In progress" = not yet terminal; "failed" includes the
// dead-letter (exhausted-retries) bucket so a poison job still surfaces.
export const JOB_STATUS_WIRE: Record<Exclude<JobStatusFilter, 'all'>, string[]> = {
	active: ['queued', 'running'],
	completed: ['completed'],
	failed: ['failed', 'dead_letter'],
};

/** Read + write `?status=` against one source's vocabulary ('all' = absent). */
export function useStatusFilter<T extends string>(source: ActivitySource) {
	const [searchParams, setSearchParams] = useSearchParams();
	const param = searchParams.get('status');
	const raw = param ? (STATUS_ALIASES[param] ?? param) : null;
	const allowed = STATUS_OPTIONS[source];
	const status = (allowed.some((o) => o.value === raw) ? raw : 'all') as T | 'all';

	const setStatus = useCallback(
		(value: string) => {
			setSearchParams(
				(prev) => {
					const next = new URLSearchParams(prev);
					if (value === 'all') next.delete('status');
					else next.set('status', value);
					return next;
				},
				{ replace: true },
			);
		},
		[setSearchParams],
	);

	return { status, setStatus };
}
