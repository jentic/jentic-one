/**
 * RenewalDecision — the decision on a held call whose agent's access ended
 * (`kind: grant_renewal`). A reviewer who may renew the binding directly
 * chooses its fresh policy with the same controls as granting access (an end
 * date, nothing preselected; an idle limit; single use; the warning before
 * it ends and what happens after it, starting on the binding's own), then either
 * re-grants access and runs the call, re-grants it without running a stale call, or
 * denies. Re-granting replaces the binding's old policy; its rules stay.
 *
 * Anyone else who may review sees only Deny, with who can re-grant access.
 * Where the deployment can't tell who may renew from here (`renewElsewhere`),
 * everyone sees the choices, and the renewal itself says if they may not.
 *
 * `AccessEndedSection` says why the access ended and when it was last used,
 * above the request in the review pop-up.
 */
import { useId, useRef, useState } from 'react';
import { Hourglass } from 'lucide-react';
import {
	AccessEndFields,
	BindingDurationField,
	IdleLimitField,
	NoExpiryConfirmDialog,
	SingleUseField,
	useNoExpiryConfirm,
} from '@/shared/credentials/components';
import { useGrantPolicyLimits } from '@/shared/credentials/api';
import { isRenewalCall } from '@/shared/approvals/heldCall';
import { usePreselectGrantDefaults } from '@/shared/credentials/lib/grantDefaults';
import {
	expiresAtFromDuration,
	expiresAtIso,
	type BindingDuration,
} from '@/shared/credentials/lib/bindingDuration';
import {
	choosesNoExpiry,
	durationChosen,
	NO_DURATION_HINT,
} from '@/shared/credentials/lib/bindingExpiry';
import { idleLimitError, idleSeconds, type IdleLimit } from '@/shared/credentials/lib/idleLimit';
import {
	afterExpiryFrom,
	expiryWarnable,
	expiryWarning,
	type AfterExpiry,
} from '@/shared/credentials/lib/accessEnd';
import { idleLabel } from '@/shared/credentials/lib/idleLimit';
import { isoDurationSeconds } from '@/shared/lib';
import { formatTimestamp } from '@/shared/lib/utils';
import { Button, Label, Textarea, Tooltip, UserText } from '@/shared/ui';
import type { HeldCallDetail } from '@/shared/approvals/heldCall';
import { waitingSince } from '@/shared/approvals/heldCallCopy';

/** The fresh policy a renewal sends, as `:decide` takes it. */
export interface RenewalPolicyBody {
	expires_at: string | null;
	max_idle_period: number | null;
	idle_warning: boolean;
	max_quota: number | null;
	after_expiry: AfterExpiry;
}

export interface RenewalDecisionProps {
	agentName: string;
	/** Whether the viewer may re-grant access (the server's `renewal.can_renew`). */
	canRenew: boolean;
	/**
	 * The renewal goes through the binding's own renew endpoint before the
	 * approval (`renewal.renew_on_decide` is false), so who may renew is only
	 * known once it is tried.
	 */
	renewElsewhere?: boolean;
	/** What a call through the binding gets once it ends now (`hold` or `refuse`). */
	afterExpiry?: string | null;
	pending: boolean;
	onDeny: (reason: string) => void;
	onRenew: (policy: RenewalPolicyBody, run: boolean, reason: string) => void;
}

export function RenewalDecision({
	agentName,
	canRenew: viewerMayRenew,
	renewElsewhere = false,
	afterExpiry: afterExpiryNow = null,
	pending,
	onDeny,
	onRenew,
}: RenewalDecisionProps) {
	const accessSummaryId = useId();
	const canRenew = viewerMayRenew || renewElsewhere;
	const [reason, setReason] = useState('');
	const [duration, setDuration] = useState<BindingDuration | null>(null);
	const [idle, setIdle] = useState<IdleLimit | null>(null);
	const [uses, setUses] = useState<number | null>(null);
	const [warn, setWarn] = useState(false);
	const [after, setAfter] = useState<AfterExpiry>(() => afterExpiryFrom(afterExpiryNow));
	// The org's defaults preselect the re-granted access (D14); its maximums bound it.
	const limits = useGrantPolicyLimits();
	usePreselectGrantDefaults(limits, (d) => {
		setDuration((cur) => cur ?? d.duration);
		setIdle((cur) => cur ?? d.idle);
		setUses((cur) => cur ?? d.uses);
	});
	const durationRef = useRef<HTMLSelectElement>(null);
	const noExpiry = useNoExpiryConfirm();

	const incomplete =
		!durationChosen(duration, limits.expires.maxSeconds) ||
		idleLimitError(idle, limits.idle.maxSeconds) !== null;
	const renew = (run: boolean): void =>
		noExpiry.request(
			// An idle limit or a use count still ends the access; only none warns.
			choosesNoExpiry(duration) && idleSeconds(idle) === null && uses === null,
			() =>
				onRenew(
					{
						// Worked out now, as the reviewer decides: the duration runs from here.
						expires_at: expiresAtIso(duration) ?? null,
						max_idle_period: idleSeconds(idle),
						idle_warning: expiryWarning(
							warn,
							expiresAtFromDuration(duration),
							idleSeconds(idle),
						),
						max_quota: uses,
						after_expiry: after,
					},
					run,
					reason,
				),
		);

	return (
		<div className="flex flex-col gap-4" data-testid="renewal-decision">
			{canRenew ? (
				<div className="space-y-3">
					{renewElsewhere && (
						<p
							className="text-muted-foreground text-sm"
							data-testid="renewal-elsewhere"
						>
							Re-granting access renews the agent&apos;s access first. Only someone
							who owns both the agent and the credential, or an org admin, can.
						</p>
					)}
					<BindingDurationField
						maxSeconds={limits.expires.maxSeconds}
						value={duration}
						onChange={setDuration}
						disabled={pending}
						label="Re-grant access for"
						selectRef={durationRef}
						testIdPrefix="renewal"
						describedBy={accessSummaryId}
					/>
					<IdleLimitField
						maxSeconds={limits.idle.maxSeconds}
						value={idle}
						onChange={setIdle}
						disabled={pending}
						testIdPrefix="renewal"
						describedBy={accessSummaryId}
					/>
					<SingleUseField
						value={uses}
						onChange={setUses}
						end={expiresAtFromDuration(duration)}
						idleSeconds={idleSeconds(idle)}
						disabled={pending}
						testIdPrefix="renewal"
						summaryId={accessSummaryId}
						emptyHint={NO_DURATION_HINT}
					/>
					<AccessEndFields
						warn={warn}
						onWarnChange={setWarn}
						warnable={expiryWarnable(
							expiresAtFromDuration(duration),
							idleSeconds(idle),
						)}
						afterExpiry={after}
						onAfterExpiryChange={setAfter}
						disabled={pending}
						testIdPrefix="renewal"
					/>
				</div>
			) : (
				<p className="text-muted-foreground text-sm" data-testid="renewal-cannot-renew">
					Only someone who owns both the agent and the credential, or an org admin, can
					re-grant access. You can deny the call.
				</p>
			)}
			<div>
				<Label htmlFor="renewal-reason">Reason (optional)</Label>
				<Textarea
					id="renewal-reason"
					placeholder="Recorded with your decision…"
					value={reason}
					onChange={(e) => setReason(e.target.value)}
					rows={2}
					maxLength={500}
					className="mt-1"
				/>
			</div>
			<div className="flex flex-wrap gap-3">
				<Button
					type="button"
					variant="danger"
					onClick={() => onDeny(reason)}
					disabled={pending}
					data-testid="renewal-deny"
				>
					Deny
				</Button>
				{canRenew && (
					<>
						<Button
							type="button"
							variant="secondary"
							onClick={() => renew(false)}
							disabled={pending || incomplete}
							data-testid="renewal-grant"
						>
							Re-grant access
						</Button>
						<Button
							type="button"
							variant="primary"
							onClick={() => renew(true)}
							disabled={pending || incomplete}
							data-testid="renewal-grant-and-run"
						>
							Re-grant access and run this call
						</Button>
					</>
				)}
			</div>
			<NoExpiryConfirmDialog
				{...noExpiry.dialogProps}
				kind="renew"
				subject={<UserText>{agentName}</UserText>}
				fieldRef={durationRef}
			/>
		</div>
	);
}

export { isRenewalCall };

type RenewalContext = NonNullable<HeldCallDetail['renewal']>;

/** Why an ended binding ended: "End date passed 3d ago", "Unused for 14 days", "Already used once". */
export function endedText(renewal: RenewalContext): string {
	const idle = isoDurationSeconds(renewal.max_idle_period);
	switch (renewal.expired_reason) {
		case 'end_date':
			return renewal.expires_at
				? `End date passed ${waitingSince(renewal.expires_at)}`
				: 'End date passed';
		case 'idle':
			return idle ? `Unused for ${idleLabel(idle)}` : 'Unused too long';
		case 'uses':
			return renewal.max_quota === 1
				? 'Already used once'
				: `All ${renewal.max_quota ?? ''} uses taken`.replace('  ', ' ');
		default:
			return 'It has been renewed since, and holds again';
	}
}

/** "Why access ended" and "Last used", for a call held because the agent's access ended. */
export function AccessEndedSection({ call }: { call: HeldCallDetail }) {
	const renewal = call.renewal;
	if (!isRenewalCall(call) || !renewal) return null;
	return (
		<section
			aria-label="Why access ended"
			className="border-hairline-field space-y-1 rounded-lg border px-3.5 py-3 text-[13px]"
			data-testid="renewal-context"
		>
			<p className="text-foreground-name flex items-center gap-1.5 font-semibold">
				<Hourglass className="text-caution h-3.5 w-3.5" aria-hidden="true" />
				<span>
					Why access ended: <span data-testid="renewal-ended">{endedText(renewal)}</span>
				</span>
			</p>
			<p className="text-foreground-sub" data-testid="renewal-last-used">
				Last used:{' '}
				{renewal.last_used_at ? (
					<Tooltip content={formatTimestamp(renewal.last_used_at)}>
						<span>{waitingSince(renewal.last_used_at)}</span>
					</Tooltip>
				) : (
					'Never'
				)}
			</p>
		</section>
	);
}
