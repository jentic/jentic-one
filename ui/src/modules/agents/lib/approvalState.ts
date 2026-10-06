/**
 * Approval-state vocabulary for the Approvals module — the single place the
 * list and detail pages read a state's label and badge variant from. Mirrors
 * the backend `ExecutionApprovalState` enum.
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
