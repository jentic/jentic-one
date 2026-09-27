/**
 * Monitor module — UI-facing types.
 *
 * The backend's OpenAPI contract types `ExecutionResponse.status` and
 * `JobResponse.status` as bare `string` (the server models them as
 * `ExecutionStatus`/job-status `StrEnum`s, but FastAPI serialises them to plain
 * strings on the wire — see STATUS.md decision log [ui-monitor 2026-06-19]). So
 * the UI owns its own typed status vocabulary here and maps unknown wire values
 * to a safe `unknown` bucket rather than trusting the string blindly.
 *
 * Severity, by contrast, IS a real generated enum (`EventSeverity`) so we reuse
 * it directly from the facade and don't redeclare it.
 */

/**
 * Monitor's two layouts. Drives the `?view=` deep-link:
 *
 *   overview  (default) the usage overview beside a docked live activity
 *             panel — org:admin; members only ever get `activity`
 *   activity  the activity log expanded to the full page, with its sources
 *             (implied by any `?show=`)
 */
export type MonitorView = 'overview' | 'activity';

/**
 * What the Activity view is showing. Drives the `?show=` deep-link:
 *
 *   all    (default) the live platform event feed (`/events` + SSE)
 *   calls  the execution log (`/executions`) — dense table
 *   jobs   the async job queue (`/jobs`)
 *   audit  the org:admin audit log (`/audit`) — who changed what
 */
export type ActivitySource = 'all' | 'calls' | 'jobs' | 'audit';

export const ACTIVITY_SOURCES: ActivitySource[] = ['all', 'calls', 'jobs', 'audit'];

/**
 * Execution lifecycle, in UI vocabulary. The live backend's `ExecutionStatus`
 * StrEnum is terminal-only (`completed` | `failed`) — executions are recorded
 * after they finish, so there's no running/queued execution to display. The
 * extra members below are defensive only: if the backend ever widens the enum,
 * the table degrades gracefully instead of crashing, and `unknown` catches
 * anything we haven't taught the UI about.
 */
export type ExecutionStatusUi = 'running' | 'completed' | 'failed' | 'cancelled' | 'unknown';

/** Known wire values → UI status. Anything else collapses to `unknown`. */
const EXECUTION_STATUS_MAP: Record<string, ExecutionStatusUi> = {
	completed: 'completed',
	failed: 'failed',
	// Defensive only — not emitted by the current backend (terminal-only enum).
	running: 'running',
	in_progress: 'running',
	cancelled: 'cancelled',
	canceled: 'cancelled',
};

export function toExecutionStatus(wire: string): ExecutionStatusUi {
	return EXECUTION_STATUS_MAP[wire.toLowerCase()] ?? 'unknown';
}

/**
 * Job lifecycle, in UI vocabulary. Mirrors the backend `JobStatus` StrEnum:
 * queued → running → {completed | failed | cancelled | dead_letter}. `unknown`
 * is the safe fallback for any value the server adds later.
 */
export type JobStatusUi =
	'queued' | 'running' | 'completed' | 'failed' | 'cancelled' | 'dead_letter' | 'unknown';

const JOB_STATUS_MAP: Record<string, JobStatusUi> = {
	queued: 'queued',
	running: 'running',
	completed: 'completed',
	failed: 'failed',
	cancelled: 'cancelled',
	canceled: 'cancelled',
	dead_letter: 'dead_letter',
};

export function toJobStatus(wire: string): JobStatusUi {
	return JOB_STATUS_MAP[wire.toLowerCase()] ?? 'unknown';
}

/** Whether a job is in a terminal state (so the Cancel action is hidden). */
export function isTerminalJobStatus(status: JobStatusUi): boolean {
	return (
		status === 'completed' ||
		status === 'failed' ||
		status === 'cancelled' ||
		status === 'dead_letter'
	);
}

/**
 * An actor that performed an audited action, resolved from `AuditResponse`.
 * Jobs/executions carry no actor on the wire (STATUS.md decision: actor
 * attribution lives only in the audit log), so trace/job detail views resolve
 * the actor by cross-referencing audit entries on `trace_id` / `job_id`.
 */
export interface AuditActor {
	actorId: string | null;
	actorType: string;
}
