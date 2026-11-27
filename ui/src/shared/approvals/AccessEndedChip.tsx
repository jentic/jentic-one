import { cn } from '@/shared/lib/utils';
import { ATTENTION_CHIP_CLASS } from '@/shared/approvals/attentionTone';

/**
 * "Access ended" — marks a waiting call that is a re-grant of ended access, not
 * a call to approve, wherever the waiting calls are listed (the bell, the
 * rail's Inbox).
 */
export function AccessEndedChip({ className }: { className?: string }) {
	return (
		<span
			data-testid="access-ended-chip"
			className={cn(
				'inline-block rounded px-1.5 py-px align-middle text-[10.5px] leading-4 font-semibold',
				ATTENTION_CHIP_CLASS,
				className,
			)}
		>
			Access ended
		</span>
	);
}
