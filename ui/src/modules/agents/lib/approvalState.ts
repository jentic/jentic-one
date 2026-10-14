/**
 * Held-execution vocabulary for the Approvals module — the single place the
 * list and detail pages read a decision state's or a call status's label and
 * badge variant from. `ApprovalStateValue` mirrors the backend
 * `HeldExecutionState` enum (the decision); `CallStatusValue` mirrors a
 * held execution's `execution_status` (where the call stands).
 */
export type ApprovalStateValue = 'pending' | 'approved' | 'denied' | 'expired' | 'withdrawn';

export const APPROVAL_STATE_LABELS: Record<ApprovalStateValue, string> = {
	pending: 'Pending',
	approved: 'Approved',
	denied: 'Denied',
	expired: 'Expired',
	withdrawn: 'Withdrawn',
};

export const APPROVAL_STATE_VARIANT: Record<
	ApprovalStateValue,
	'warning' | 'success' | 'danger' | 'default'
> = {
	pending: 'warning',
	approved: 'success',
	denied: 'danger',
	expired: 'danger',
	withdrawn: 'default',
};

export const APPROVAL_STATE_OPTIONS: { value: '' | ApprovalStateValue; label: string }[] = [
	{ value: '', label: 'All states' },
	...(Object.keys(APPROVAL_STATE_LABELS) as ApprovalStateValue[]).map((value) => ({
		value,
		label: APPROVAL_STATE_LABELS[value],
	})),
];

/**
 * A pending approval past its expiry is effectively expired: the reviewer can
 * no longer decide it, even before the expiry sweep records it.
 */
export function isDecidable(state: string, expiresAt: string, now: Date = new Date()): boolean {
	return state === 'pending' && new Date(expiresAt).getTime() > now.getTime();
}

/** Where a held call stands: held until decided, then queued, running and its outcome. */
export type CallStatusValue = 'held' | 'queued' | 'running' | 'completed' | 'failed' | 'cancelled';

export const CALL_STATUS_LABELS: Record<CallStatusValue, string> = {
	held: 'Held',
	queued: 'Queued',
	running: 'Running',
	completed: 'Completed',
	failed: 'Failed',
	cancelled: 'Cancelled',
};

export const CALL_STATUS_VARIANT: Record<
	CallStatusValue,
	'warning' | 'success' | 'danger' | 'pending' | 'default'
> = {
	held: 'warning',
	queued: 'default',
	running: 'pending',
	completed: 'success',
	failed: 'danger',
	cancelled: 'warning',
};

/** The call's steps in order; the last one is the outcome. */
export const CALL_STEPS = ['held', 'queued', 'running', 'outcome'] as const;
export type CallStep = (typeof CALL_STEPS)[number];

export function toCallStatus(status: string | null | undefined): CallStatusValue | null {
	return status != null && status in CALL_STATUS_LABELS ? (status as CallStatusValue) : null;
}

/** The step a call status sits on: completed, failed and cancelled are all the outcome. */
export function callStep(status: CallStatusValue): CallStep {
	return status === 'held' || status === 'queued' || status === 'running' ? status : 'outcome';
}

/**
 * One line on where the call stands, read with the decision: an approved call
 * that has not finished is on its way, a denied, expired or withdrawn one never
 * runs.
 */
export function describeCallStatus(state: string, status: CallStatusValue | null): string {
	if (state === 'pending') return 'The call is held until someone approves or denies it.';
	if (state === 'denied') return 'The call was denied and does not run.';
	if (state === 'expired') return 'Nobody decided in time, so the call does not run.';
	if (state === 'withdrawn') return 'The agent withdrew the call, so it does not run.';
	switch (status) {
		case 'held':
		case 'queued':
			return 'Approved. The call is queued to run.';
		case 'running':
			return 'Approved. The call is running.';
		case 'completed':
			return 'Approved. The call ran and completed.';
		case 'failed':
			return 'Approved. The call ran and failed.';
		case 'cancelled':
			return 'Approved, but the call was cancelled before it finished.';
		default:
			return 'Approved.';
	}
}

/** An approved call that has not reached its outcome yet: still worth re-reading. */
export function isCallUnderWay(
	state: string | undefined,
	status: string | null | undefined,
): boolean {
	return (
		state === 'approved' && (status === 'held' || status === 'queued' || status === 'running')
	);
}
