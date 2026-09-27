/**
 * The Activity log's open record — which row's detail the pane is showing.
 *
 * Lives in the URL, so a link can land on an open record and Back closes it:
 *
 *   trace_id      a trace (every call sharing it)       Everything, API calls, Jobs
 *   execution_id  one call with no usable trace          Everything, API calls, Jobs
 *   job_id        a job                                  Everything, API calls, Jobs
 *   audit_id      an audit entry                         Audit log
 *
 * On the Audit log `trace_id` is a FILTER (the trace sheet's "View in audit"
 * narrows the log with it), so there only `audit_id` opens a record.
 *
 * At xl the record docks beside the list; opening one from nothing is a view
 * transition there (the pane slides in, the list makes room), while stepping
 * between records (j/k) swaps in place. Narrower, it's a modal sheet.
 */
import { useCallback, useMemo } from 'react';
import { useSearchParams } from 'react-router';
import { useMediaQuery } from '@/shared/hooks';
import { withViewTransition } from '@/shared/app/viewTransitions';
import type { ActivitySource } from '@/modules/monitor/api';

export type LogDetail =
	| { kind: 'trace'; id: string }
	| { kind: 'execution'; id: string }
	| { kind: 'job'; id: string }
	| { kind: 'audit'; id: string };

/** From this width the detail docks beside the list; below it it's a sheet. */
export const DOCKED_DETAIL_QUERY = '(min-width: 1280px)';

/** Every param that can hold an open record. */
export const DETAIL_PARAMS = ['trace_id', 'execution_id', 'job_id', 'audit_id'] as const;

const PARAM_FOR: Record<LogDetail['kind'], (typeof DETAIL_PARAMS)[number]> = {
	trace: 'trace_id',
	execution: 'execution_id',
	job: 'job_id',
	audit: 'audit_id',
};

/** Stable identity for a record — rows compare against it to mark themselves open. */
export function detailKey(detail: LogDetail | null): string | null {
	return detail ? `${detail.kind}:${detail.id}` : null;
}

export function useLogDetail(source: ActivitySource) {
	const [searchParams, setSearchParams] = useSearchParams();
	const docked = useMediaQuery(DOCKED_DETAIL_QUERY);
	const traceId = searchParams.get('trace_id');
	const executionId = searchParams.get('execution_id');
	const jobId = searchParams.get('job_id');
	const auditId = searchParams.get('audit_id');

	const detail = useMemo<LogDetail | null>(() => {
		if (source === 'audit') return auditId ? { kind: 'audit', id: auditId } : null;
		if (traceId) return { kind: 'trace', id: traceId };
		if (executionId) return { kind: 'execution', id: executionId };
		if (jobId) return { kind: 'job', id: jobId };
		return null;
	}, [source, traceId, executionId, jobId, auditId]);

	const write = useCallback(
		(next: LogDetail | null) =>
			setSearchParams(
				(prev) => {
					const p = new URLSearchParams(prev);
					// On the Audit log trace_id is a filter — leave it alone.
					for (const k of DETAIL_PARAMS) {
						if (source === 'audit' && k === 'trace_id') continue;
						p.delete(k);
					}
					if (next) p.set(PARAM_FOR[next.kind], next.id);
					return p;
				},
				{ replace: false },
			),
		[setSearchParams, source],
	);

	// Only the docked pane changes the page's layout; the sheet animates itself.
	const isOpen = detail != null;
	const open = useCallback(
		(next: LogDetail) => {
			if (isOpen || !docked) write(next);
			else withViewTransition(() => write(next));
		},
		[isOpen, docked, write],
	);
	const close = useCallback(() => {
		if (docked) withViewTransition(() => write(null));
		else write(null);
	}, [docked, write]);

	return { detail, open, close, docked };
}
