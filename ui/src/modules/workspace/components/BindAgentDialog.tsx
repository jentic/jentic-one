/**
 * BindAgentDialog — "Bind to an agent", in place on the API hub's "Who can use
 * it" card: pick one of this API's credentials (when there are several), tick
 * active agents, say what they may call, confirm.
 *
 * A binding is default-deny — with no rules every call is refused — so the
 * dialog asks for the rules up front ("What can this agent call?"): Allow all
 * operations, Read-only (GET only), or Custom rules (the shared rules editor).
 * Nothing is preselected and Bind stays disabled until a choice is made (or,
 * for custom, at least one rule exists). It also asks how long the access
 * lasts (the shared duration control, nothing preselected): Bind waits for a
 * duration too, and "No expiry" (in Custom…) asks for confirmation first. The
 * end date runs from the moment Bind is confirmed. The same rules and end date go to
 * every ticked agent: each is bound (`POST /agents/{id}/credentials`), then its binding's
 * rules are replaced (`PUT /credentials/{cid}/agents/{aid}/permissions`). If a
 * rules save fails the binding stays — that agent reads Blocked — and the
 * dialog stays open on a Retry for just those rules.
 *
 * An API that needs no credential passes `createOnBind`: the dialog then binds
 * through a new no-auth credential that is created only when Bind is
 * confirmed, so cancelling leaves nothing behind.
 *
 * Offered only to a viewer with `agents:write` (the bind endpoint's
 * permission). The credential list is narrowed to the ones the viewer may bind
 * — the server lets a non-admin bind only credentials they created (else 404).
 * Only ACTIVE agents are offered (a pending, disabled, archived or rejected
 * agent serves nothing). Agents already bound to the chosen credential are
 * shown, disabled, with what their binding says — suspended, expired (renewed
 * elsewhere, never bound again), or when it ends — read in full (every page,
 * `useAllCredentialAgents`), so an agent past the first page can't pass for
 * unbound and be bound twice. Until that read is whole, nothing can be ticked
 * or bound.
 *
 * State lifecycle (dialog-state rule): the draft (credential, ticks, rules,
 * duration) persists across dismissals and resets on a successful bind; transient errors
 * clear on reopen.
 */
import { isoDurationSeconds } from '@/shared/lib';
import { useEffect, useId, useMemo, useRef, useState } from 'react';
import { Bot } from 'lucide-react';
import {
	AppLink,
	Button,
	Checkbox,
	Dialog,
	EmptyState,
	ErrorAlert,
	Label,
	Select,
	Skeleton,
	toast,
	type PermissionRuleInput,
} from '@/shared/ui';
import { ROUTES } from '@/shared/app/routes';
import { cn } from '@/shared/lib/utils';
import { useOptionalCurrentUser } from '@/shared/auth';
import { credentialsBindableBy } from '@/shared/credentials/lib/bindAuthority';
import { apiScopeReach } from '@/shared/credentials/lib/apiIdentity';
import { credentialSiblingHint } from '@/shared/credentials/lib/credentialIdentity';
import {
	useAllCredentialAgents,
	useGrantPolicyLimits,
	type Credential,
	type CredentialAgentResponse,
} from '@/shared/credentials/api';
import { grantDefaults, usePreselectGrantDefaults } from '@/shared/credentials/lib/grantDefaults';
import {
	useAgentsForPicker,
	useApplyBindingRules,
	useBindCredentialToAgents,
} from '@/shared/credentials/api/vendors-hooks';
import type { PermissionRule } from '@/shared/credentials/api/vendors-types';
import {
	AccessRulesStep,
	BindingDurationField,
	AccessEndFields,
	IdleLimitField,
	SingleUseField,
	NoExpiryConfirmDialog,
	formatDurationEnd,
	useNoExpiryConfirm,
	useUnappliedChoices,
} from '@/shared/credentials/components';
import {
	expiresAtFromDuration,
	expiresAtIso,
	type BindingDuration,
} from '@/shared/credentials/lib/bindingDuration';
import { idleLimitError, idleSeconds, type IdleLimit } from '@/shared/credentials/lib/idleLimit';
import {
	DEFAULT_AFTER_EXPIRY,
	expiryWarnable,
	expiryWarning,
	type AfterExpiry,
} from '@/shared/credentials/lib/accessEnd';
import {
	NO_DURATION_HINT,
	bindingExpiresSoon,
	bindingExpiryLabel,
	bindingExpiryShortLabel,
	bindingIsExpired,
	choosesNoExpiry,
	durationChosen,
	type BindingExpiryFields,
} from '@/shared/credentials/lib/bindingExpiry';
import {
	rulesForPreset,
	type RulesPreset,
	type ScopeReach,
} from '@/shared/credentials/lib/accessPresets';

export interface BindAgentDialogProps {
	open: boolean;
	onClose: () => void;
	/** Every credential whose scope covers this API. */
	credentials: Credential[];
	/** Shown in the subtitle, e.g. "NewsAPI". */
	apiLabel: string;
	/**
	 * The API (with a concrete version) whose operations seed the custom rules
	 * editor's path suggestions. Omitted → the editor works without them.
	 */
	apiReference?: { vendor: string; name: string; version: string } | null;
	/**
	 * Preselect this credential. Seeds the pick when it changes (a caller that
	 * just created a credential opens the dialog on it); the user can still
	 * switch.
	 */
	initialCredentialId?: string | null;
	/**
	 * No usable credential yet, but one can be made: shown as the credential
	 * to use, and created only when Bind is confirmed (`create` resolves to it).
	 */
	createOnBind?: { name: string; create: () => Promise<Credential> } | null;
}

/** "a", "a and b", "a, b and c"; past three, just the count. */
function agentList(names: string[]): string {
	if (names.length > 3) return `${names.length} agents`;
	if (names.length <= 1) return names[0] ?? '';
	return `${names.slice(0, -1).join(', ')} and ${names[names.length - 1]}`;
}

export function BindAgentDialog({
	open,
	onClose,
	credentials,
	apiLabel,
	apiReference,
	initialCredentialId,
	createOnBind,
}: BindAgentDialogProps) {
	const accessSummaryId = useId();
	const viewer = useOptionalCurrentUser();
	// A credential this dialog created on Bind, held until the caller's list
	// carries it — so a retry never creates a second one.
	const [created, setCreated] = useState<Credential | null>(null);
	const known = useMemo(
		() =>
			created && !credentials.some((c) => c.credential_id === created.credential_id)
				? [...credentials, created]
				: credentials,
		[credentials, created],
	);
	const usable = useMemo(() => credentialsBindableBy(known, viewer), [known, viewer]);
	const agents = useAgentsForPicker();
	const bind = useBindCredentialToAgents();
	const applyRules = useApplyBindingRules();

	const [credentialId, setCredentialId] = useState<string | null>(initialCredentialId ?? null);
	// Seed-from-props syncs only when the seed itself changes (dialog-state rule).
	const lastSeedRef = useRef(initialCredentialId);
	useEffect(() => {
		if (lastSeedRef.current !== initialCredentialId) {
			lastSeedRef.current = initialCredentialId;
			if (initialCredentialId) setCredentialId(initialCredentialId);
		}
	}, [initialCredentialId]);
	const [selected, setSelected] = useState<Set<string>>(new Set());
	const [preset, setPreset] = useState<RulesPreset | null>(null);
	const [customRules, setCustomRules] = useState<PermissionRule[]>([]);
	// How long the access lasts; null = nothing chosen yet, and Bind waits for a
	// choice ("No expiry" is a choice, made in Custom… and confirmed).
	const [duration, setDuration] = useState<BindingDuration | null>(null);
	// The optional idle limit (null = off).
	const [idle, setIdle] = useState<IdleLimit | null>(null);
	// The optional use count: 1 for "End after one use", null = off.
	const [uses, setUses] = useState<number | null>(null);
	// Warn before the access ends (off to start), and what happens after it.
	const [warn, setWarn] = useState(false);
	const [afterExpiry, setAfterExpiry] = useState<AfterExpiry>(DEFAULT_AFTER_EXPIRY);
	// The org's defaults preselect the controls; its maximums bound them.
	const limits = useGrantPolicyLimits();
	usePreselectGrantDefaults(limits, (d) => {
		setDuration((cur) => cur ?? d.duration);
		setIdle((cur) => cur ?? d.idle);
		setUses((cur) => cur ?? d.uses);
	});
	const durationRef = useRef<HTMLSelectElement>(null);
	const noExpiry = useNoExpiryConfirm();
	// A bind that landed but whose rules didn't, for some agents: kept so the
	// Retry saves exactly those rules on exactly those bindings.
	const [rulesFailure, setRulesFailure] = useState<{
		credentialId: string;
		agents: { id: string; name: string }[];
		rules: PermissionRuleInput[];
	} | null>(null);
	const [createError, setCreateError] = useState<Error | null>(null);
	const [creating, setCreating] = useState(false);

	// Transient: clear the last errors — and a rules-save failure's Retry banner,
	// which belongs to the bind that raised it — on every (re)open. (`reset`
	// is stable.)
	const resetBind = bind.reset;
	const resetApply = applyRules.reset;
	useEffect(() => {
		if (open) {
			resetBind();
			resetApply();
			setCreateError(null);
			setRulesFailure(null);
		}
	}, [open, resetBind, resetApply]);

	// A stale / unusable pick falls back to the first usable credential.
	const credential = usable.find((c) => c.credential_id === credentialId) ?? usable[0] ?? null;
	// Bind through a credential made on confirm — only while none exists yet.
	const pendingCreate = credential == null ? (createOnBind ?? null) : null;
	// How far the chosen credential reaches; a credential made on Bind is
	// unpinned (no version — the backend's wildcard).
	const reach: ScopeReach = credential
		? apiScopeReach(credential.api)
		: pendingCreate
			? 'any-version'
			: 'pinned';
	// Every page: a partial list would offer already-bound agents as unbound.
	const boundHere = useAllCredentialAgents(credential?.credential_id, {
		enabled: open && credential != null,
	});
	// A credential that doesn't exist yet has no agents bound to it.
	const boundKnown = pendingCreate != null || boundHere.complete;
	// Each bound agent's binding, so its row can say whether it still works: a
	// suspended or expired binding is still a binding (binding again is a 409),
	// and must never read as plain "Already bound".
	const alreadyBound = useMemo(
		() =>
			new Map<string, CredentialAgentResponse>(
				pendingCreate ? [] : boundHere.items.map((a) => [a.agent_id, a]),
			),
		[boundHere.items, pendingCreate],
	);

	const allAgents = useMemo(() => agents.data?.data ?? [], [agents.data]);
	// Only an active agent serves traffic — the others can't use a grant.
	const candidates = useMemo(() => allAgents.filter((a) => a.status === 'active'), [allAgents]);
	// Ticks only count for agents still bindable to the chosen credential.
	const picked = boundKnown
		? candidates.filter((a) => selected.has(a.id) && !alreadyBound.has(a.id))
		: [];
	const everyAgentBound =
		boundKnown && candidates.length > 0 && candidates.every((a) => alreadyBound.has(a.id));
	// Offered agents whose binding has ended (and isn't also paused): binding
	// them again won't restore access, renewing will.
	const expiredBound = candidates.filter((a) => {
		const row = alreadyBound.get(a.id);
		return row != null && !row.suspended && bindingIsExpired(expiryOf(row));
	}).length;

	// The rules every picked agent gets, or null while the choice is incomplete.
	const rules = useMemo<PermissionRuleInput[] | null>(
		() => rulesForPreset(preset, customRules),
		[preset, customRules],
	);

	const busy = bind.isPending || applyRules.isPending || creating;

	const toggle = (id: string): void =>
		setSelected((prev) => {
			const next = new Set(prev);
			if (next.has(id)) next.delete(id);
			else next.add(id);
			return next;
		});

	/** The credential as the picker names it: a same-named sibling gets its hint. */
	const pickerLabel = (cred: Credential): string => {
		const hint = credentialSiblingHint(cred, usable);
		return hint ? `${cred.name} · ${hint}` : cred.name;
	};

	const finish = (credentialLabel: string, names: string[], expiresAt?: string | null): void => {
		const until = expiresAt ? `, until ${formatDurationEnd(new Date(expiresAt))}` : '';
		toast({
			title: 'Credential bound',
			description: `Bound “${credentialLabel}” to ${agentList(names)}, with access rules${until}.`,
			variant: 'success',
		});
		// Reset the draft only on a successful commit.
		setSelected(new Set());
		setPreset(null);
		setCustomRules([]);
		const fresh = grantDefaults(limits);
		setDuration(fresh.duration);
		setIdle(fresh.idle);
		setUses(fresh.uses);
		setWarn(false);
		setAfterExpiry(DEFAULT_AFTER_EXPIRY);
		setRulesFailure(null);
		onClose();
	};

	// By action's staged choices are settled before a bind — never dropped silently.
	const unapplied = useUnappliedChoices();
	const requestBind = (): void =>
		unapplied.settle('before binding', (applied) => {
			void handleBind(applied ? rulesForPreset(preset, applied) : rules);
		});

	const handleBind = async (rules: PermissionRuleInput[] | null): Promise<void> => {
		if (picked.length === 0 || rules == null || busy) return;
		setCreateError(null);
		let target = credential;
		if (!target && pendingCreate) {
			setCreating(true);
			try {
				target = await pendingCreate.create();
			} catch (error) {
				setCreateError(error instanceof Error ? error : new Error(String(error)));
				return;
			} finally {
				setCreating(false);
			}
			setCreated(target);
			setCredentialId(target.credential_id);
		}
		if (!target) return;
		const bound = target;
		const agentsToBind = picked.map((a) => ({ id: a.id, name: a.name }));
		// Worked out now, as the operator confirms: the duration runs from here.
		const expiresAt = expiresAtIso(duration);
		bind.mutate(
			{
				credentialId: bound.credential_id,
				agentIds: agentsToBind.map((a) => a.id),
				rules,
				expiresAt,
				maxIdlePeriod: idleSeconds(idle),
				idleWarning: expiryWarning(
					warn,
					expiresAtFromDuration(duration),
					idleSeconds(idle),
				),
				maxQuota: uses,
				afterExpiry,
			},
			{
				onSuccess: ({ rulesFailed }) => {
					if (rulesFailed.length === 0) {
						finish(
							pickerLabel(bound),
							agentsToBind.map((a) => a.name),
							expiresAt,
						);
						return;
					}
					setSelected(new Set());
					setRulesFailure({
						credentialId: bound.credential_id,
						agents: agentsToBind.filter((a) => rulesFailed.includes(a.id)),
						rules,
					});
				},
			},
		);
	};

	const retryRules = (): void => {
		if (!rulesFailure) return;
		const failure = rulesFailure;
		applyRules.mutate(
			{
				credentialId: failure.credentialId,
				agentIds: failure.agents.map((a) => a.id),
				rules: failure.rules,
			},
			{
				onSuccess: ({ rulesFailed }) => {
					const left = failure.agents.filter((a) => rulesFailed.includes(a.id));
					if (left.length > 0) {
						setRulesFailure({ ...failure, agents: left });
						return;
					}
					const cred = usable.find((c) => c.credential_id === failure.credentialId);
					finish(
						cred ? pickerLabel(cred) : 'the credential',
						failure.agents.map((a) => a.name),
					);
				},
			},
		);
	};

	const noActiveAgents = !agents.isPending && !agents.error && candidates.length === 0;
	const noCredential = usable.length === 0 && pendingCreate == null;

	let body: React.ReactNode;
	if (noCredential) {
		body = (
			<p className="text-muted-foreground text-sm" data-testid="bind-agent-no-credential">
				You can only bind credentials you created, and none of this API&apos;s credentials
				are yours. Add your own credential, or ask an admin to bind one.
			</p>
		);
	} else if (agents.isPending) {
		body = <Skeleton className="h-24 w-full" />;
	} else if (agents.error) {
		body = <ErrorAlert message={agents.error} />;
	} else if (noActiveAgents) {
		body = (
			<EmptyState
				icon={<Bot className="h-6 w-6" aria-hidden="true" />}
				title={allAgents.length === 0 ? 'No agents yet' : 'No active agents'}
				description={
					allAgents.length === 0
						? "Create or register an agent first, then bind this API's credential to it."
						: 'Only active agents can be given access. Approve or re-enable an agent first.'
				}
				action={
					<AppLink
						href={ROUTES.agents}
						variant="outline"
						size="sm"
						onClick={onClose}
						data-testid="bind-agent-go-create"
					>
						Go to Agents
					</AppLink>
				}
			/>
		);
	} else {
		body = (
			<div className="space-y-4">
				{usable.length > 1 ? (
					<div className="space-y-1.5">
						<Label htmlFor="bind-agent-credential">Credential</Label>
						<Select
							id="bind-agent-credential"
							value={credential?.credential_id ?? ''}
							onChange={(e): void => setCredentialId(e.target.value)}
							data-testid="bind-agent-credential"
						>
							{usable.map((c) => {
								return (
									<option key={c.credential_id} value={c.credential_id}>
										{pickerLabel(c)}
									</option>
								);
							})}
						</Select>
					</div>
				) : pendingCreate ? (
					<p
						className="text-muted-foreground text-sm"
						data-testid="bind-agent-create-note"
					>
						Uses a new no-auth credential,{' '}
						<strong className="text-foreground">{pendingCreate.name}</strong> — created
						when you bind (there&apos;s no secret to set up).
					</p>
				) : (
					<p className="text-muted-foreground text-sm">
						Using credential{' '}
						<strong className="text-foreground">{credential?.name}</strong>.
					</p>
				)}
				<fieldset className="min-w-0 space-y-1.5">
					<legend className="text-foreground mb-1.5 text-sm font-medium">Agents</legend>
					{!boundKnown &&
						(boundHere.error ? (
							<ErrorAlert
								message="Couldn’t check which agents already use this credential."
								onRetry={boundHere.retry}
								retrying={boundHere.isFetching}
							/>
						) : (
							<p
								className="text-muted-foreground text-xs"
								data-testid="bind-agent-checking"
							>
								Checking which agents already use this credential…
							</p>
						))}
					{everyAgentBound && !rulesFailure ? (
						<p
							className="text-muted-foreground text-sm"
							data-testid="bind-agent-all-bound"
						>
							Every active agent already uses this credential.
						</p>
					) : (
						<ul
							className="bg-field max-h-64 space-y-0.5 overflow-y-auto rounded-lg p-1.5"
							data-testid="bind-agent-list"
						>
							{candidates.map((a) => {
								const boundRow = alreadyBound.get(a.id);
								const bound = boundRow != null;
								return (
									<li
										key={a.id}
										className="hover:bg-tint-2 flex items-center justify-between gap-2 rounded-md px-2 py-1.5"
										data-testid="bind-agent-option"
									>
										<Checkbox
											size="sm"
											checked={bound || selected.has(a.id)}
											disabled={bound || !boundKnown || busy}
											onChange={(): void => toggle(a.id)}
										>
											<span className="text-foreground-name text-sm">
												{a.name}
											</span>
										</Checkbox>
										{boundRow && <BoundAgentNote row={boundRow} />}
									</li>
								);
							})}
						</ul>
					)}
					{expiredBound > 0 && (
						<p
							className="text-muted-foreground text-xs"
							data-testid="bind-agent-expired-note"
						>
							{expiredBound === 1
								? "One agent's access through this credential has expired."
								: `${expiredBound} agents' access through this credential has expired.`}{' '}
							Binding again won&apos;t restore it: renew it from the agent&apos;s page
							or from the credential.
						</p>
					)}
				</fieldset>
				{!everyAgentBound && (
					<AccessRulesStep
						reach={reach}
						preset={preset}
						onPresetChange={setPreset}
						customRules={customRules}
						onCustomRulesChange={setCustomRules}
						apiReference={apiReference}
						active={open}
						disabled={busy}
						stagedRef={unapplied.stagedRef}
						onStagedChange={unapplied.onStagedChange}
						testIdPrefix="bind-agent"
					/>
				)}
				{!everyAgentBound && (
					<BindingDurationField
						maxSeconds={limits.expires.maxSeconds}
						value={duration}
						onChange={setDuration}
						disabled={busy}
						label="Access expires after"
						selectRef={durationRef}
						testIdPrefix="bind-agent"
						describedBy={accessSummaryId}
					/>
				)}
				{!everyAgentBound && (
					<IdleLimitField
						maxSeconds={limits.idle.maxSeconds}
						value={idle}
						onChange={setIdle}
						disabled={busy}
						testIdPrefix="bind-agent"
						describedBy={accessSummaryId}
					/>
				)}
				{!everyAgentBound && (
					<SingleUseField
						value={uses}
						onChange={setUses}
						end={expiresAtFromDuration(duration)}
						idleSeconds={idleSeconds(idle)}
						disabled={busy}
						testIdPrefix="bind-agent"
						summaryId={accessSummaryId}
						emptyHint={NO_DURATION_HINT}
					/>
				)}
				{!everyAgentBound && (
					<AccessEndFields
						warn={warn}
						onWarnChange={setWarn}
						warnable={expiryWarnable(
							expiresAtFromDuration(duration),
							idleSeconds(idle),
						)}
						afterExpiry={afterExpiry}
						onAfterExpiryChange={setAfterExpiry}
						disabled={busy}
						testIdPrefix="bind-agent"
					/>
				)}
				{createError && (
					<ErrorAlert
						title="Couldn't create the no-auth credential"
						message={createError}
					/>
				)}
				{bind.error && <ErrorAlert message={bind.error} />}
				{rulesFailure && (
					<div data-testid="bind-agent-rules-failed">
						<ErrorAlert
							title="Bound, but the access rules weren't saved"
							message={`${agentList(rulesFailure.agents.map((a) => a.name))} ${
								rulesFailure.agents.length === 1 ? 'is' : 'are'
							} bound but blocked — every call is denied until the rules are saved.`}
							onRetry={retryRules}
							retrying={applyRules.isPending}
						/>
					</div>
				)}
			</div>
		);
	}

	const footer = (
		<>
			<Button variant="ghost" size="sm" onClick={onClose}>
				{rulesFailure ? 'Close' : 'Cancel'}
			</Button>
			<Button
				size="sm"
				onClick={(): void =>
					noExpiry.request(
						// An idle limit or a use count still ends the access, so only
						// none of them warns.
						choosesNoExpiry(duration) && idleSeconds(idle) === null && uses === null,
						requestBind,
					)
				}
				disabled={
					picked.length === 0 ||
					(rules == null && unapplied.staged === 0) ||
					!durationChosen(duration, limits.expires.maxSeconds) ||
					idleLimitError(idle, limits.idle.maxSeconds) !== null ||
					busy ||
					(!credential && !pendingCreate) ||
					!boundKnown
				}
				loading={bind.isPending || creating}
				data-testid="bind-agent-confirm"
			>
				{picked.length > 1 ? `Bind to ${picked.length} agents` : 'Bind to agent'}
			</Button>
		</>
	);

	return (
		<>
			<Dialog
				open={open}
				onClose={onClose}
				title="Bind to an agent"
				subtitle={`Give an existing agent access to ${apiLabel} through one of its credentials.`}
				footer={noActiveAgents || noCredential ? undefined : footer}
			>
				{body}
				{unapplied.dialog}
			</Dialog>
			<NoExpiryConfirmDialog
				{...noExpiry.dialogProps}
				subject={picked.length === 1 ? picked[0].name : 'These agents'}
				confirmLabel="Bind without expiry"
				fieldRef={durationRef}
			/>
		</>
	);
}

/** A roster row's end date, in the shape the expiry helpers read. */
function expiryOf(row: CredentialAgentResponse): BindingExpiryFields {
	return {
		expiresAt: row.expires_at ?? null,
		expired: row.expired ?? false,
		expiredReason: row.expired_reason ?? null,
		maxIdlePeriod: isoDurationSeconds(row.max_idle_period),
		idleExpiresAt: row.idle_expires_at ?? null,
		maxQuota: row.max_quota ?? null,
		quotaRemaining: row.quota_remaining ?? null,
	};
}

/**
 * What an already-bound agent's row says about its binding: paused, ended, or
 * bound (with when it ends, if it does). Suspended first, as the broker reports
 * them — renewing alone would not restore a paused binding.
 */
function BoundAgentNote({ row }: { row: CredentialAgentResponse }) {
	const binding = expiryOf(row);
	if (row.suspended) {
		return (
			<span
				className="text-muted-foreground shrink-0 text-[11px]"
				data-testid="bind-agent-bound-note"
			>
				Bound · suspended
			</span>
		);
	}
	if (bindingIsExpired(binding)) {
		return (
			<span
				className="text-caution shrink-0 text-[11px]"
				data-testid="bind-agent-bound-note"
				title={bindingExpiryLabel(binding) ?? undefined}
			>
				Bound · expired
			</span>
		);
	}
	const ends = bindingExpiryShortLabel(binding);
	return (
		<span
			className={cn(
				'shrink-0 text-[11px]',
				bindingExpiresSoon(binding) ? 'text-caution' : 'text-muted-foreground',
			)}
			data-testid="bind-agent-bound-note"
			title={bindingExpiryLabel(binding) ?? undefined}
		>
			Already bound{ends ? ` · ${ends.charAt(0).toLowerCase()}${ends.slice(1)}` : ''}
		</span>
	);
}
