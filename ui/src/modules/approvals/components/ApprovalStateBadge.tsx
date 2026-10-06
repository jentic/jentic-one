import { Badge } from '@/shared/ui';
import {
	APPROVAL_STATE_LABELS,
	APPROVAL_STATE_VARIANT,
	type ApprovalStateValue,
} from '@/modules/approvals/lib/approvalState';

/** The approval state as a badge, from the module's single state vocabulary. */
export function ApprovalStateBadge({ state }: { state: string }) {
	const known = state in APPROVAL_STATE_LABELS ? (state as ApprovalStateValue) : null;
	return (
		<Badge variant={known ? APPROVAL_STATE_VARIANT[known] : 'default'}>
			{known ? APPROVAL_STATE_LABELS[known] : state}
		</Badge>
	);
}
