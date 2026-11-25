/**
 * RenewBindingControl — renew one agent's binding to a credential: choose a
 * new duration (the shared duration control, nothing preselected) and save it
 * through `PATCH /agents/{id}/credentials/{cid}`. The end date runs from the
 * moment of saving. "No expiry" (in Custom…) clears the end date, behind the
 * "No expiry" warning (unless an idle limit still ends it). The idle row
 * below it starts on the binding's current idle limit and is sent when it
 * changes, and so do "End after one use" below that and, at the bottom, the
 * warning before access ends and what happens after it. Every renewal restarts
 * the idle clock and restores the uses. A renewal that chooses no end date runs
 * the binding again for as long as it was granted for, capped at the org's
 * maximum (the server works it out), so the confirmation names the new end.
 * Renewing restores an expired binding; it never resumes a suspended one.
 *
 * Shown only to a viewer who may renew: `agents:write` and the credential's
 * owner, or an `org:admin` (the same checks as resuming a binding). The host
 * decides; the server refuses anyone else with a 404.
 */
import { type KeyboardEvent, useId, useRef, useState } from 'react';
import { RefreshCw } from 'lucide-react';
import { Button, ErrorAlert, UserText, toast } from '@/shared/ui';
import { isolateText } from '@/shared/lib/utils';
import {
	useGrantPolicyLimits,
	useRenewCredentialBinding,
} from '@/shared/credentials/api/vendors-hooks';
import {
	bindingDurationError,
	expiresAtFromDuration,
	expiresAtIso,
	type BindingDuration,
} from '@/shared/credentials/lib/bindingDuration';
import {
	idleLimitError,
	idleLimitFromSeconds,
	idleSeconds,
	type IdleLimit,
} from '@/shared/credentials/lib/idleLimit';
import {
	afterExpiryFrom,
	expiryWarnable,
	expiryWarning,
	type AfterExpiry,
} from '@/shared/credentials/lib/accessEnd';
import { AccessEndFields } from '@/shared/credentials/components/AccessEndFields';
import { IdleLimitField } from '@/shared/credentials/components/IdleLimitField';
import { SingleUseField } from '@/shared/credentials/components/SingleUseField';
import { choosesNoExpiry, durationChosen } from '@/shared/credentials/lib/bindingExpiry';
import {
	BindingDurationField,
	formatDurationEnd,
} from '@/shared/credentials/components/BindingDurationField';
import {
	NoExpiryConfirmDialog,
	useNoExpiryConfirm,
} from '@/shared/credentials/components/NoExpiryConfirmDialog';

export interface RenewBindingControlProps {
	agentId: string;
	agentName: string;
	credentialId: string;
	/** The binding's current end date; null when it never expires. */
	expiresAt: string | null | undefined;
	expired: boolean;
	/** The binding's current idle limit in seconds; null without one. */
	maxIdlePeriod?: number | null;
	/** Whether its owner is warned before it ends (its end date or idle limit). */
	idleWarning?: boolean;
	/** What a call through it gets once it has ended (`hold` or `refuse`). */
	afterExpiry?: string | null;
	/** Its use count (1 is single use); null without one. Renewing restores the uses left. */
	maxQuota?: number | null;
	/** Fit into a list row: no visible label. */
	compact?: boolean;
	/** After a renewal lands. */
	onRenewed?: () => void;
	/** Prefix for test ids (`<prefix>-duration-*`, `<prefix>-save`). */
	testIdPrefix?: string;
}

/** Said under the warning box while the renewal's end date is the server's to choose. */
const WARNING_CAVEAT = 'Only sent if the renewed access lasts 4 days or more.';

/** ", until <end>" for a renewal's new end date, or nothing when it has none. */
function renewedUntil(end: string | null | undefined): string {
	return end ? `, until ${formatDurationEnd(new Date(end))}` : '';
}

export function RenewBindingControl({
	agentId,
	agentName,
	credentialId,
	expiresAt,
	expired,
	maxIdlePeriod = null,
	idleWarning: warnedBefore = false,
	afterExpiry: afterExpiryBefore = null,
	maxQuota = null,
	compact = false,
	onRenewed,
	testIdPrefix = 'renew-binding',
}: RenewBindingControlProps) {
	const accessSummaryId = useId();
	const renew = useRenewCredentialBinding();
	// The org's maximums bound a renewal's choices (the server refuses more).
	const limits = useGrantPolicyLimits();
	const confirm = useNoExpiryConfirm();
	const fieldRef = useRef<HTMLSelectElement>(null);
	const [duration, setDuration] = useState<BindingDuration | null>(null);
	const [idle, setIdle] = useState<IdleLimit | null>(() => idleLimitFromSeconds(maxIdlePeriod));
	const [warn, setWarn] = useState(warnedBefore);
	const afterBefore = afterExpiryFrom(afterExpiryBefore);
	const [after, setAfter] = useState<AfterExpiry>(afterBefore);
	const [uses, setUses] = useState<number | null>(maxQuota);
	// The form starts from the binding as it is, and starts over when the
	// binding changes under it (renewed in another tab, or by the owner
	// elsewhere): left alone, it would write the old values back on save. Done
	// during render rather than by remounting, so a renewal in flight still
	// reports (a remount would drop its callbacks).
	const baseline = [
		expiresAt ?? '',
		maxIdlePeriod ?? '',
		warnedBefore,
		afterBefore,
		maxQuota ?? '',
	]
		.map(String)
		.join('|');
	const [seenBaseline, setSeenBaseline] = useState(baseline);
	if (seenBaseline !== baseline) {
		setSeenBaseline(baseline);
		setDuration(null);
		setIdle(idleLimitFromSeconds(maxIdlePeriod));
		setWarn(warnedBefore);
		setAfter(afterBefore);
		setUses(maxQuota);
	}
	const chosen = durationChosen(duration);
	// A renewal that chooses no end date runs the binding again for as long as it
	// was granted for (capped at the org's maximum), which only the server knows:
	// not the end it has now, which may have passed.
	const endUnknown = !chosen && (expiresAt != null || limits.expires.maxSeconds != null);
	// The end date the access would have if saved now.
	const end: Date | 'never' | null = chosen
		? expiresAtFromDuration(duration)
		: endUnknown
			? null
			: 'never';

	const idleNow = idleSeconds(idle);
	const warnEnd = endUnknown ? 'unknown' : end;
	const warnable = expiryWarnable(warnEnd, idleNow);
	const warnNow = expiryWarning(warn, warnEnd, idleNow);
	const limitChanged = idleNow !== (maxIdlePeriod ?? null);
	// With nothing to warn about, the box is disabled and its value left alone.
	const warnChanged = warnable && warnNow !== warnedBefore;
	const afterChanged = after !== afterBefore;
	const usesChanged = uses !== maxQuota;
	// Anything other than the end date changed.
	const limitsChanged = limitChanged || warnChanged || afterChanged || usesChanged;
	const idleInvalid = idleLimitError(idle, limits.idle.maxSeconds) !== null;
	// A choice the org's maximum refuses blocks saving rather than being dropped.
	const durationInvalid =
		duration !== null && bindingDurationError(duration, limits.expires.maxSeconds) !== null;
	const clearing = choosesNoExpiry(duration);
	// Clearing an end date the binding doesn't have changes nothing.
	const nothingToClear = clearing && !expiresAt && !limitsChanged;
	const blocked =
		renew.isPending ||
		idleInvalid ||
		durationInvalid ||
		(!durationChosen(duration) && !limitsChanged) ||
		nothingToClear;
	// With no end date, idle limit or use count left, the access never ends: warn first.
	const leavesOpenEnded = clearing && idleNow === null && uses === null;

	const done = (title: string, description: string): void => {
		toast({ title, description, variant: 'success' });
		setDuration(null);
		onRenewed?.();
	};

	const save = (): void => {
		// Worked out now, as the operator confirms: the duration runs from here.
		const at = durationChosen(duration) ? expiresAtIso(duration) : undefined;
		renew.mutate(
			{
				agentId,
				credentialId,
				...(at !== undefined ? { expiresAt: at } : {}),
				...(limitChanged ? { maxIdlePeriod: idleNow } : {}),
				...(warnChanged ? { idleWarning: warnNow } : {}),
				...(usesChanged ? { maxQuota: uses } : {}),
				...(afterChanged ? { afterExpiry: after } : {}),
			},
			{
				onSuccess: (end) => {
					if (at === null) {
						done(
							'Expiry removed',
							idleNow === null
								? `${isolateText(agentName)} keeps this access until someone removes it.`
								: `${isolateText(agentName)} keeps this access while it uses it.`,
						);
					} else if (at) {
						done(
							'Access renewed',
							`${isolateText(agentName)} has access until ${formatDurationEnd(new Date(at))}.`,
						);
					} else {
						done(
							limitChanged && !usesChanged
								? 'Idle limit saved'
								: 'Access limits saved',
							`${isolateText(agentName)}'s access was re-certified${renewedUntil(end)}.`,
						);
					}
				},
			},
		);
	};

	const label = expired ? 'Renew for' : expiresAt ? 'Change expiry to' : 'Set an expiry';
	const action =
		!durationChosen(duration) && limitsChanged
			? limitChanged && !usesChanged && !warnChanged && !afterChanged
				? 'Save idle limit'
				: 'Save limits'
			: clearing
				? 'Remove expiry'
				: expired
					? 'Renew'
					: expiresAt
						? 'Change expiry'
						: 'Set expiry';

	const submit = (): void => confirm.request(leavesOpenEnded, save);
	// Enter in the amount renews — and never submits a form the host wraps
	// around this control (the edit-credential sheet is one).
	const onKeyDown = (event: KeyboardEvent<HTMLDivElement>): void => {
		if (event.key !== 'Enter' || !(event.target instanceof HTMLInputElement)) return;
		event.preventDefault();
		if (!blocked) submit();
	};

	return (
		<div className="space-y-2" data-testid={testIdPrefix} onKeyDown={onKeyDown}>
			<BindingDurationField
				maxSeconds={limits.expires.maxSeconds}
				value={duration}
				onChange={setDuration}
				disabled={renew.isPending}
				label={label}
				compact={compact}
				selectRef={fieldRef}
				testIdPrefix={testIdPrefix}
				describedBy={accessSummaryId}
			/>
			<IdleLimitField
				maxSeconds={limits.idle.maxSeconds}
				value={idle}
				onChange={setIdle}
				disabled={renew.isPending}
				testIdPrefix={testIdPrefix}
				describedBy={accessSummaryId}
			/>
			<SingleUseField
				value={uses}
				onChange={setUses}
				end={end}
				idleSeconds={idleNow}
				disabled={renew.isPending}
				testIdPrefix={testIdPrefix}
				summaryId={accessSummaryId}
			/>
			<AccessEndFields
				warn={warn}
				onWarnChange={setWarn}
				warnable={warnable}
				warnCaveat={endUnknown ? WARNING_CAVEAT : null}
				afterExpiry={after}
				onAfterExpiryChange={setAfter}
				disabled={renew.isPending}
				testIdPrefix={testIdPrefix}
			/>
			<div className="flex flex-wrap items-center gap-2">
				<Button
					size="sm"
					variant={expired ? 'primary' : 'secondary'}
					disabled={blocked}
					loading={renew.isPending}
					onClick={submit}
					data-testid={`${testIdPrefix}-save`}
					aria-label={`${action} for ${isolateText(agentName)}`}
				>
					<RefreshCw className="h-3.5 w-3.5" aria-hidden="true" />
					{action}
				</Button>
			</div>
			{renew.error && <ErrorAlert message={renew.error} />}
			<NoExpiryConfirmDialog
				{...confirm.dialogProps}
				kind="renew"
				subject={<UserText>{agentName}</UserText>}
				fieldRef={fieldRef}
			/>
		</div>
	);
}
