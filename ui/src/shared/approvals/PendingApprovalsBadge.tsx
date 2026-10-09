/**
 * The held-call count badge — how many calls an Ask rule holds for the
 * viewer's decision. Rides the Agents nav tab and the Agents page's Approvals
 * entry; renders nothing when nothing waits (or the viewer cannot decide).
 */
import { usePendingApprovalsCount } from '@/shared/approvals/api';
import { cn } from '@/shared/lib/utils';

export function PendingApprovalsBadge({ className }: { className?: string }) {
	const { count, atLeast } = usePendingApprovalsCount();
	if (count <= 0) return null;
	const label = atLeast ? `${count}+` : `${count}`;
	return (
		<span
			className={cn(
				'bg-warning/15 text-warning inline-flex h-4 min-w-4 items-center justify-center rounded-full px-1 text-[10px] font-semibold tabular-nums',
				className,
			)}
			aria-label={`${label} ${count === 1 && !atLeast ? 'call' : 'calls'} awaiting your approval`}
		>
			{label}
		</span>
	);
}
