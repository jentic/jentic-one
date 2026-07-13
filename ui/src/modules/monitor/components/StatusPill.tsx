/**
 * Status pills for Monitor lifecycle/severity vocabularies.
 *
 * The shared `<StatusBadge>` is HTTP-numeric; these map Monitor's own typed
 * unions (execution/job lifecycle, event severity) to the shared `<Badge>`
 * variant palette. Rendering off the typed union (not the raw wire string)
 * keeps unknown server values from leaking a broken colour.
 */
import { Badge, type BadgeVariant } from '@/shared/ui';
import { type ExecutionStatusUi, type JobStatusUi } from '@/modules/monitor/api';

const EXECUTION_VARIANT: Record<ExecutionStatusUi, BadgeVariant> = {
	running: 'pending',
	completed: 'success',
	failed: 'danger',
	cancelled: 'warning',
	unknown: 'default',
};

const EXECUTION_LABEL: Record<ExecutionStatusUi, string> = {
	running: 'Running',
	completed: 'Completed',
	failed: 'Failed',
	cancelled: 'Cancelled',
	unknown: 'Unknown',
};

export function ExecutionStatusPill({ status }: { status: ExecutionStatusUi }) {
	return <Badge variant={EXECUTION_VARIANT[status]}>{EXECUTION_LABEL[status]}</Badge>;
}

const JOB_VARIANT: Record<JobStatusUi, BadgeVariant> = {
	queued: 'default',
	running: 'pending',
	completed: 'success',
	failed: 'danger',
	cancelled: 'warning',
	dead_letter: 'danger',
	unknown: 'default',
};

const JOB_LABEL: Record<JobStatusUi, string> = {
	queued: 'Queued',
	running: 'Running',
	completed: 'Completed',
	failed: 'Failed',
	cancelled: 'Cancelled',
	dead_letter: 'Dead letter',
	unknown: 'Unknown',
};

export function JobStatusPill({ status }: { status: JobStatusUi }) {
	return <Badge variant={JOB_VARIANT[status]}>{JOB_LABEL[status]}</Badge>;
}
