/**
 * The Activity log's one status vocabulary.
 *
 * Every source maps its own lifecycle onto the same five tones (see
 * StatusGlyph) and the same words, so "Failed" looks and reads the same on a
 * call, a job and an event.
 */
import type { ExecutionStatusUi, JobStatusUi } from '@/modules/monitor/api';
import { ORIGIN_OPTIONS } from '@/modules/monitor/lib/useMonitorFilters';

export type LogTone = 'ok' | 'fail' | 'warn' | 'running' | 'neutral';

export const EXECUTION_TONE: Record<ExecutionStatusUi, LogTone> = {
	running: 'running',
	completed: 'ok',
	failed: 'fail',
	cancelled: 'warn',
	unknown: 'neutral',
};

export const EXECUTION_LABEL: Record<ExecutionStatusUi, string> = {
	running: 'Running',
	completed: 'Completed',
	failed: 'Failed',
	cancelled: 'Cancelled',
	unknown: 'Unknown',
};

export const JOB_TONE: Record<JobStatusUi, LogTone> = {
	queued: 'running',
	running: 'running',
	completed: 'ok',
	failed: 'fail',
	cancelled: 'warn',
	dead_letter: 'fail',
	unknown: 'neutral',
};

export const JOB_LABEL: Record<JobStatusUi, string> = {
	queued: 'Queued',
	running: 'Running',
	completed: 'Completed',
	failed: 'Failed',
	cancelled: 'Cancelled',
	dead_letter: 'Gave up',
	unknown: 'Unknown',
};

/** How a job's state reads after its kind: "Import running", "Execution gave up". */
const JOB_PHRASE: Record<JobStatusUi, string> = {
	queued: 'queued',
	running: 'running',
	completed: 'completed',
	failed: 'failed',
	cancelled: 'cancelled',
	dead_letter: 'gave up after retries',
	unknown: 'in an unknown state',
};

/** A job kind as a noun: `import` → "Import", `spec_refresh` → "Spec refresh". */
export function jobKindLabel(kind: string): string {
	const words = kind.replace(/[_.-]+/g, ' ').trim();
	return words ? words[0].toUpperCase() + words.slice(1) : 'Job';
}

export function jobSentence(kind: string, status: JobStatusUi): string {
	return `${jobKindLabel(kind)} ${JOB_PHRASE[status]}`;
}

/** Wall-clock span between two instants, e.g. "4m 12s", "850ms". */
export function formatSpan(fromIso: string | null | undefined, toIso: string | null | undefined) {
	if (!fromIso || !toIso) return null;
	const ms = Date.parse(toIso) - Date.parse(fromIso);
	if (!Number.isFinite(ms) || ms < 0) return null;
	if (ms < 1000) return `${ms}ms`;
	const s = Math.round(ms / 1000);
	if (s < 60) return `${s}s`;
	const m = Math.floor(s / 60);
	if (m < 60) return s % 60 ? `${m}m ${s % 60}s` : `${m}m`;
	const h = Math.floor(m / 60);
	return m % 60 ? `${h}h ${m % 60}m` : `${h}h`;
}

/** Where a call or action came from, as the Origin picker names it ("CLI", "MCP"). */
export function originLabel(origin: string): string {
	const known = ORIGIN_OPTIONS.find((o) => o.value === origin);
	return known ? known.label : origin.charAt(0).toUpperCase() + origin.slice(1);
}
