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
 * The Monitor tabs. Drives the `?tab=` deep-link + the segmented toggle.
 *
 * `overview` leads (it's the natural landing lens) and is fully wired: its
 * charts read the enriched usage-aggregation endpoint `GET /monitoring/usage`
 * (jentic-one-internal#561) and per-agent attribution reads
 * `actor_id`/`actor_type` off executions (#375). All five tabs are functional.
 */
export type MonitorTab = 'overview' | 'executions' | 'jobs' | 'events' | 'audit';

export const MONITOR_TABS: MonitorTab[] = ['overview', 'executions', 'jobs', 'events', 'audit'];

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
