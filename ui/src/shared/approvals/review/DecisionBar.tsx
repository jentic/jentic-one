/**
 * The review pop-up's footer for a call that is still waiting: the deny
 * reason (always visible; sent to the agent only with a deny), then Deny and
 * "Approve and run".
 *
 * Mounted per call (keyed by the held call's id), so a reason typed for one
 * call never carries over to the next one the pop-up moves on to.
 */
import { useId, useState } from 'react';
import { Button, Label, Textarea } from '@/shared/ui';

/** The longest reason the decide endpoint accepts. */
export const DENY_REASON_MAX = 500;

export type Decision = 'approve' | 'deny';

export function DecisionBar({
	onDecide,
	pending,
	error,
}: {
	/** `reason` is the trimmed deny reason, or null; it is never sent with an approve. */
	onDecide: (decision: Decision, reason: string | null) => void;
	/** The decision in flight, if any. */
	pending: Decision | null;
	/** Why the last decision failed, in place above the buttons. */
	error: string | null;
}) {
	const [reason, setReason] = useState('');
	const reasonId = useId();
	const counterId = useId();
	const busy = pending !== null;

	return (
		<div className="w-full space-y-3">
			<div className="space-y-1.5">
				<Label htmlFor={reasonId} className="text-[12.5px]">
					Reason (optional), sent to the agent if you deny
				</Label>
				<Textarea
					id={reasonId}
					rows={2}
					maxLength={DENY_REASON_MAX}
					value={reason}
					onChange={(e) => setReason(e.target.value.slice(0, DENY_REASON_MAX))}
					placeholder="e.g. The refund was already issued by hand"
					aria-describedby={counterId}
				/>
				<p id={counterId} className="text-foreground-sub flex justify-end text-[11px]">
					<span className="tabular-nums">
						{reason.length}/{DENY_REASON_MAX}
					</span>
					<span className="sr-only"> characters</span>
				</p>
			</div>
			{error && (
				<p role="alert" className="text-danger text-xs">
					{error}
				</p>
			)}
			<div className="flex flex-wrap items-center justify-end gap-2">
				<Button
					variant="danger"
					size="md"
					disabled={busy}
					loading={pending === 'deny'}
					onClick={() => onDecide('deny', reason.trim() || null)}
				>
					Deny
				</Button>
				<Button
					variant="primary"
					size="md"
					disabled={busy}
					loading={pending === 'approve'}
					onClick={() => onDecide('approve', null)}
				>
					Approve and run
				</Button>
			</div>
		</div>
	);
}
