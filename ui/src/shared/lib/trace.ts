/**
 * The backend stores `trace_id="unknown"` for executions/jobs that ran without
 * a `traceparent`/`x-request-id` header (see the broker's executor). Such a
 * value can't open a trace sheet or filter the audit log, so it — and
 * empty/nullish ids — count as "no usable trace" everywhere a cross-link is
 * offered.
 */
export function hasTrace(traceId: string | null | undefined): traceId is string {
	return traceId != null && traceId !== '' && traceId !== 'unknown';
}
