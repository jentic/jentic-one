import { motion } from 'framer-motion';
import { CheckCircle2, MessageSquare, RotateCcw, XCircle } from 'lucide-react';
import { itemTargetLabel, type AccessRequestItem } from '@/shared/lib';

/**
 * AccessRequestChip — a drafted item in the dialog's Approved / Denied lanes.
 * Ported from jentic-webapp's `ApprovedChip` / `RejectedChip` (scale-fade in,
 * hover "undo"), rebuilt on jentic-one accent tokens:
 *   approved → accent-green
 *   denied   → muted + struck-through (the webapp's deliberately colourless
 *              "rejected" treatment — NOT red), with the deny reason exposed to
 *              both mouse (hover title) and assistive tech (`aria-describedby`).
 *
 * Undo moves the item back to the pending rail (the webapp "undo"). Since
 * jentic-one's `:decide` is terminal server-side, undo is purely client-side
 * draft state until the operator submits.
 */
interface AccessRequestChipProps {
	item: AccessRequestItem;
	verdict: 'approved' | 'denied';
	/**
	 * The deny reason (denied chips only). Surfaced as a hover `title` for mouse
	 * users AND as an SR-reachable description (`aria-describedby` → a visually
	 * hidden span) so touch/AT users — who never get a `title` tooltip — can
	 * still hear why the item was denied.
	 */
	reason?: string;
	onUndo: () => void;
}

export function AccessRequestChip({ item, verdict, reason, onUndo }: AccessRequestChipProps) {
	const label = itemTargetLabel(item);
	const approved = verdict === 'approved';
	const hasReason = !approved && (reason ?? '').trim().length > 0;
	const reasonId = `ar-chip-reason-${item.id}`;

	return (
		<motion.div
			layout
			initial={{ opacity: 0, scale: 0.9 }}
			animate={{ opacity: 1, scale: 1 }}
			exit={{ opacity: 0, scale: 0.9 }}
			transition={{ type: 'spring', stiffness: 420, damping: 32 }}
			className={`group flex max-w-full items-center gap-1.5 rounded-full py-1 pr-1 pl-2 text-xs font-medium ${
				approved ? 'bg-accent-green/10 text-accent-green' : 'bg-muted text-muted-foreground'
			}`}
			title={hasReason ? `Denied: ${reason}` : undefined}
			aria-describedby={hasReason ? reasonId : undefined}
		>
			{approved ? (
				<CheckCircle2 className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
			) : (
				<XCircle className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
			)}
			<span className={`max-w-[150px] truncate ${approved ? '' : 'line-through'}`}>
				{label}
			</span>
			{hasReason && (
				<>
					<MessageSquare
						className="h-3 w-3 shrink-0 opacity-60"
						aria-label="Has a deny reason"
					/>
					<span id={reasonId} className="sr-only">
						Deny reason: {reason}
					</span>
				</>
			)}
			<button
				type="button"
				onClick={onUndo}
				title="Move back to pending"
				aria-label={`Move ${label} back to pending`}
				className="hover:bg-foreground/10 flex shrink-0 items-center justify-center rounded-full p-2 opacity-50 transition-opacity hover:opacity-100 sm:p-0.5"
			>
				<RotateCcw className="h-3 w-3" aria-hidden="true" />
			</button>
		</motion.div>
	);
}
