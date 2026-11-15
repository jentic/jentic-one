import { useCallback, useEffect, useId, useMemo, useRef, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { ArrowLeft, Loader2, ShieldAlert } from 'lucide-react';
import {
	ActorLabel,
	Badge,
	Button,
	Checkbox,
	ConfirmDialog,
	ErrorAlert,
	Input,
	Label,
	Skeleton,
	toast,
} from '@/shared/ui';
import { cn, timeAgo } from '@/shared/lib/utils';
import {
	connectRequestsKey,
	invalidateEditSurfaces,
	useAmendConnectSession,
	useBindingAccess,
	useCancelConnectSession,
	useConfirmConnectSession,
	useConnectSessionRevisions,
	usePollConnectSessionStatus,
	useRebaseConnectSession,
	useRejectConnectSession,
} from '@/shared/credentials/api/vendors-hooks';
import { cancelConnectSession } from '@/shared/credentials/api/vendors-client';
import { ConfirmBlockedNotice } from '@/shared/credentials/components/ConnectReviewParts';
import { EffectBadge } from '@/shared/credentials/components/EffectBadge';
import { confirmBlockedReason, describeConfirmError } from '@/shared/credentials/lib/connectReview';
import {
	addedScopes,
	describeConflict,
	describeRuleMatch,
	removedScopes,
	resumes,
	revisionAddingRule,
	revisionChanges,
	revisionReason,
	revisionsAddingRules,
	revisionSummary,
	ruleChanges,
	rulesToConfirm,
	sameRules,
	scopeChanges,
	type RevisionChange,
	type RuleChange,
	type ScopeChange,
} from '@/shared/credentials/lib/connectEdit';
import { isHttpsVendorUrl, openVendorUrl } from '@/shared/credentials/lib/safe-navigation';
import type {
	AmendedSession,
	AmendEditBody,
	ConfirmResponse,
	EditConfirmRequest,
	PermissionRule,
	ReviewEdit,
	ReviewSession,
} from '@/shared/credentials/api/vendors-types';
import {
	AgentRequestCard,
	AwaitingStep,
	PreviouslyRejected,
	RulesStep,
	ScopeChooseField,
	TerminalStep,
	VendorHeader,
	signInFailure,
} from './VendorConnectSteps';
import type { VendorDisplay } from './vendorFlowTypes';

/** The edit review's steps. */
type EditPhase = 'review' | 'rules' | 'scopes' | 'awaiting' | 'terminal';

/**
 * The approve dialog for an edit session: the combined diff between the
 * binding's access today and what approving the current revision leaves in
 * place. A new revision (the agent amended its ask) pauses Approve behind a
 * banner until the reviewer opens it; the reviewer's own rules are what get
 * applied. Added OAuth scopes run a vendor consent before anything lands.
 */
export function EditApproveFlow({
	sessionId,
	pollToken,
	session,
	onReload,
	onBack,
	onDone,
}: {
	sessionId: string;
	pollToken?: string;
	session: ReviewSession & { edit: ReviewEdit };
	onReload: () => void;
	onBack: () => void;
	onDone: () => void;
}) {
	const latest = session.edit;
	const queryClient = useQueryClient();
	const confirmMutation = useConfirmConnectSession(sessionId, pollToken);
	const rejectMutation = useRejectConnectSession();
	const cancelMutation = useCancelConnectSession();
	const [shown, setShown] = useState<ReviewEdit>(latest);
	const [phase, setPhase] = useState<EditPhase>('review');
	const phaseRef = useRef<EditPhase>('review');
	const [resume, setResume] = useState<boolean>(() => resumes(latest));
	// The reviewer's own edit, saved as a revision: shown once it's read back.
	const amendMutation = useAmendConnectSession(sessionId);
	const rebaseMutation = useRebaseConnectSession(sessionId, pollToken);
	const [ownRevision, setOwnRevision] = useState<string | null>(null);
	// The rules while the reviewer edits them, before they're saved.
	const [draftRules, setDraftRules] = useState<PermissionRule[]>([]);
	const [challenge, setChallenge] = useState<ConfirmResponse | null>(null);
	const [error, setError] = useState<string | null>(null);
	const [failure, setFailure] = useState<string | null>(null);
	const [historyOpen, setHistoryOpen] = useState(false);
	const [rejectOpen, setRejectOpen] = useState(false);
	// Approve was refused because the request had changed: the banner says so.
	const [staleApprove, setStaleApprove] = useState(false);
	const revisions = useConnectSessionRevisions(sessionId, pollToken);
	const history = useMemo(
		() => revisionChanges(shown, revisions.data ?? []),
		[shown, revisions.data],
	);
	// Which revision put each added rule in the request, up to the one shown.
	const introducedIn = useMemo(
		() =>
			revisionsAddingRules(history.filter((c) => c.revision.number <= shown.revision.number)),
		[history, shown.revision.number],
	);
	const latestChange = history.find((c) => c.revision.id === latest.revision.id);
	// Everything that happened since the revision on screen, so a rebase is
	// never the only change named for a reviewer who also has an amend to read.
	const since = useMemo(
		() =>
			history
				.filter((c) => c.revision.number > shown.revision.number)
				.sort((a, b) => a.revision.number - b.revision.number),
		[history, shown.revision.number],
	);
	// A new revision the review poll found: read the history again to describe it.
	const refetchedFor = useRef<string | null>(null);
	useEffect(() => {
		if (!revisions.data || latestChange || refetchedFor.current === latest.revision.id) return;
		refetchedFor.current = latest.revision.id;
		void revisions.refetch();
	}, [latest.revision.id, latestChange, revisions]);

	const goTo = (next: EditPhase): void => {
		phaseRef.current = next;
		setPhase(next);
	};
	useEffect(() => {
		if (ownRevision !== null && latest.revision.id === ownRevision) {
			setShown(latest);
			setOwnRevision(null);
		}
	}, [latest, ownRevision]);
	// The binding moved since the request was opened. A read says so and
	// writes nothing, so bring it up to date before the reviewer studies the
	// diff; once they are reading, the changed-request banner offers it
	// instead, so the diff never moves under them unasked.
	// What the reviewer has in front of them, read inside the callback so the
	// mount effect below keeps one identity. Written in an effect, not in
	// render: a discarded concurrent render would otherwise leave it pointing
	// at a revision that never committed.
	const shownRevision = useRef(shown.revision);
	useEffect(() => {
		shownRevision.current = shown.revision;
	}, [shown.revision]);
	const rebase = useCallback(async (): Promise<void> => {
		let rebased: AmendedSession;
		try {
			rebased = await rebaseMutation.mutateAsync();
		} catch (err) {
			// A refusal (the request closed under them, the chain full, the
			// bucket spent) leaves the diff as it is and says so: the banner
			// goes on offering the action, so saying nothing reads as a dead
			// button.
			setError(err instanceof Error ? err.message : 'The request couldn’t be updated.');
			return;
		}
		setError(null);
		// Read after the write, not before it: a reviewer who acknowledges an
		// amend while the rebase is in flight has read that revision by the
		// time it lands, and comparing against the one they had on screen when
		// they clicked would re-disable Approve over their own rebase.
		const from = shownRevision.current;
		// Adopted as the revision under review only when the rebase appended
		// onto the one being read, or returned that one unchanged: they asked
		// for it, so it is theirs. Rebased over an amend they have not seen,
		// it stays bannered as someone else's change — adopting it would clear
		// that acknowledgement and re-enable Approve for an ask never shown.
		if (rebased.revision_id === from.id || rebased.revision_number === from.number + 1) {
			setOwnRevision(rebased.revision_id);
		}
	}, [rebaseMutation]);
	// Only what the first read said: going stale later belongs to the banner,
	// so the diff never moves while they are reading it. Only for a reviewer
	// who can approve, too: for anyone else the write would charge their page
	// open to the connect bucket for an action they cannot take.
	const openedStale = useRef(session.stale === true && session.can_confirm);
	const askedToRebase = useRef(false);
	useEffect(() => {
		if (!openedStale.current || askedToRebase.current) return;
		askedToRebase.current = true;
		void rebase();
	}, [rebase]);

	const changed = latest.revision.id !== shown.revision.id && latest.revision.id !== ownRevision;
	const conflicts = shown.conflicts ?? [];
	const changes = useMemo(() => ruleChanges(shown), [shown]);
	const newScopes = useMemo(() => addedScopes(shown), [shown]);
	const droppedScopes = useMemo(() => removedScopes(shown), [shown]);
	const scopes = useMemo(() => scopeChanges(shown), [shown]);
	const credentialName = shown.binding.credential_name ?? session.vendor_display_name;
	const display: VendorDisplay = { displayName: credentialName, iconKey: session.vendor_key };
	// A person's own change (the human Edit access flow): no agent asked, so
	// there is nothing to reject. Discarding it cancels the request.
	const byAgent = session.requested_by_actor_id === shown.binding.agent_id;

	const showLatest = (): void => {
		setShown(latest);
		setResume(resumes(latest));
		setError(null);
		setStaleApprove(false);
	};

	// Everything the applied change shows on: the request lists, the
	// credential, and the agent's binding surfaces (rules, tiles, rule sets).
	const afterApplied = useCallback((): void => {
		void queryClient.invalidateQueries({ queryKey: connectRequestsKey });
		void queryClient.invalidateQueries({ queryKey: ['credentials'] });
		void invalidateEditSurfaces(queryClient, shown);
		toast({ title: 'Access updated', variant: 'success' });
	}, [queryClient, shown]);

	const polling = usePollConnectSessionStatus(sessionId, pollToken, {
		enabled: phase === 'awaiting',
		intervalMs:
			challenge?.kind === 'device_authorization'
				? (challenge.poll_interval_seconds ?? 5) * 1000
				: undefined,
	});

	useEffect(() => {
		if (phase !== 'awaiting') return;
		const err = polling.error as { status?: number } | undefined;
		const status = polling.data?.status;
		if (err?.status === 403 || status === 'failed' || status === 'expired') {
			setFailure(signInFailure(polling.data));
			goTo('terminal');
			void queryClient.invalidateQueries({ queryKey: connectRequestsKey });
			return;
		}
		if (status === 'applied') {
			goTo('terminal');
			afterApplied();
		}
	}, [phase, polling.data, polling.error, queryClient, afterApplied]);

	// Closing the dialog while the vendor consent runs cancels it; before
	// approving, closing leaves the request open.
	useEffect(() => {
		return () => {
			if (phaseRef.current !== 'awaiting') return;
			void cancelConnectSession(sessionId, pollToken).catch(() => {});
		};
	}, [sessionId, pollToken]);

	const approve = async (): Promise<void> => {
		setError(null);
		const body: EditConfirmRequest = {
			kind: 'edit',
			revision_id: shown.revision.id,
			permission_rules: rulesToConfirm(shown.result.rules),
			resume,
			confirmed_scopes: shown.needs_vendor_consent ? shown.result.scopes : null,
			resolve_conflicts: conflicts.length > 0,
		};
		try {
			const result = await confirmMutation.mutateAsync(body);
			if (result.kind === 'connected') {
				goTo('terminal');
				afterApplied();
				return;
			}
			setChallenge(result);
			goTo('awaiting');
			if (result.kind === 'authorization_code' && isHttpsVendorUrl(result.authorize_url)) {
				openVendorUrl(result.authorize_url, '_blank', 'noopener,noreferrer');
			}
		} catch (err) {
			const copy = describeConfirmError(err);
			if (copy.action === 'ended') {
				setFailure(copy.message);
				goTo('terminal');
				void queryClient.invalidateQueries({ queryKey: connectRequestsKey });
				return;
			}
			if (copy.action === 'reload') {
				// The changed-request banner explains it once the new version is read.
				onReload();
				setStaleApprove(true);
			} else {
				setError(copy.message);
			}
			goTo('review');
		}
	};

	const handleDiscard = (): void => {
		cancelMutation.mutate({ sessionId, pollToken });
		phaseRef.current = 'terminal';
		onBack();
	};

	const handleReject = async (): Promise<void> => {
		try {
			await rejectMutation.mutateAsync({ sessionId });
			setRejectOpen(false);
			phaseRef.current = 'terminal';
			toast({ title: 'Request rejected', variant: 'success' });
			onDone();
		} catch {
			// surfaced in the confirm dialog below.
		}
	};

	if (phase === 'terminal') {
		return (
			<TerminalStep
				display={display}
				status={failure ? 'failed' : 'applied'}
				connectedAs={null}
				errorCode={null}
				failureMessage={failure ?? undefined}
				credentialId={null}
				boundAgentId={shown.binding.agent_id}
				onDone={onDone}
			>
				{!failure && polling.data?.error_code === 'scopes_not_removed' && (
					<p role="status" className="text-warning text-sm">
						The rest of the change applied, but the vendor kept scopes it removes, so
						the scopes didn’t change. Revoke the app’s access at the vendor to drop
						them, then edit the scopes again.
					</p>
				)}
			</TerminalStep>
		);
	}
	if (phase === 'awaiting' && challenge) {
		return (
			<AwaitingStep
				display={display}
				challenge={challenge}
				status={polling.data?.status ?? 'pending'}
				onCancel={(): void => {
					cancelMutation.mutate({ sessionId, pollToken });
					phaseRef.current = 'terminal';
					onBack();
				}}
			/>
		);
	}
	// A reviewer's change becomes a revision of the request, recorded as theirs.
	// Saving what is already there changes nothing, so it sends nothing.
	const saveRevision = async (body: AmendEditBody): Promise<void> => {
		setError(null);
		const sameScopes = (next: string[]): boolean =>
			JSON.stringify([...next].sort()) === JSON.stringify([...shown.result.scopes].sort());
		if (
			(body.rules && sameRules(body.rules.set, rulesToConfirm(shown.result.rules))) ||
			(body.scopes && sameScopes(body.scopes.set))
		) {
			goTo('review');
			return;
		}
		try {
			const amended = await amendMutation.mutateAsync(body);
			setOwnRevision(amended.revision_id);
			onReload();
			goTo('review');
		} catch (err) {
			setError(err instanceof Error ? err.message : 'Your change couldn’t be saved.');
		}
	};

	if (phase === 'rules') {
		return (
			<RulesStep
				display={display}
				requestedRules={rulesToConfirm(
					shown.result.rules.filter((r) => r.added_by === shown.binding.agent_id),
				)}
				currentRules={draftRules}
				onChange={setDraftRules}
				onBack={(): void => goTo('review')}
				onContinue={(finalRules: PermissionRule[]): void =>
					void saveRevision({ rules: { set: finalRules } })
				}
				submitting={amendMutation.isPending}
				error={error}
				apiReference={session.api_reference}
				requireRule
			/>
		);
	}
	if (phase === 'scopes') {
		return (
			<EditReviewScopesStep
				display={display}
				edit={shown}
				saving={amendMutation.isPending}
				error={error}
				onBack={(): void => goTo('review')}
				onSave={(next: string[]): void => void saveRevision({ scopes: { set: next } })}
			/>
		);
	}

	const canConfirm = session.can_confirm;
	const ruleSet = shown.current.rule_set;
	const otherAgents = shown.other_bound_agents;
	return (
		<div className="space-y-5">
			<VendorHeader
				display={display}
				subtitle={
					byAgent
						? `An agent is asking to change what it can do with ${credentialName}.`
						: `Review your change to what the agent can do with ${credentialName}.`
				}
			/>

			{(changed || staleApprove || session.stale) && (
				<RequestChangedBanner
					// Remounted when which thing happened changes, so a reader
					// is told again rather than the live region mutating under
					// them unannounced.
					key={changed ? 'changed' : 'stale'}
					changes={changed ? since : undefined}
					changed={changed}
					agentId={byAgent ? shown.binding.agent_id : undefined}
					afterApprove={staleApprove}
					stale={session.stale === true}
					onUpdate={session.stale && canConfirm ? (): void => void rebase() : undefined}
					updating={rebaseMutation.isPending}
					onReview={changed ? showLatest : undefined}
					onHistory={(): void => setHistoryOpen(true)}
				/>
			)}

			{error && <ErrorAlert message={error} />}

			{conflicts.length > 0 && (
				<section
					aria-label="Conflicts"
					className="border-warning/40 bg-warning/10 space-y-1 rounded-lg border p-3 text-sm"
				>
					<p className="font-medium">
						This request no longer fits the access as it is now.
					</p>
					<ul className="list-disc space-y-0.5 pl-5 text-xs">
						{conflicts.map((conflict, i) => (
							<li key={i}>{describeConflict(conflict)}</li>
						))}
					</ul>
					<p className="text-muted-foreground text-xs">
						Check the rules below and edit them if needed: approving applies them as
						shown. Or leave it for the agent to update its request.
					</p>
				</section>
			)}

			{byAgent && (
				<AgentRequestCard
					agent={
						session.agent?.name
							? { id: session.agent.agent_id, name: session.agent.name }
							: undefined
					}
					actorId={shown.binding.agent_id}
					ownerId={session.agent?.owner_id ?? null}
					reason={session.reason}
					loading={false}
				/>
			)}
			{byAgent && <PreviouslyRejected at={session.last_rejected_at} />}

			<div className="text-muted-foreground flex items-center justify-between text-xs">
				<span>Revision {shown.revision.number}</span>
				<Button
					type="button"
					variant="ghost"
					size="sm"
					onClick={(): void => setHistoryOpen((open) => !open)}
				>
					{historyOpen ? 'Hide history' : 'History'}
				</Button>
			</div>
			{historyOpen && (
				<EditHistory
					changes={history}
					loading={revisions.isLoading}
					failed={revisions.error != null}
					agentId={byAgent ? shown.binding.agent_id : undefined}
				/>
			)}

			{shown.widens_access && (
				<p className="text-warning flex items-center gap-2 text-sm">
					<ShieldAlert className="h-4 w-4" />
					This change gives the agent more access than it has today.
				</p>
			)}

			{ruleSet && (
				<p className="text-muted-foreground text-sm">
					{ruleSet.curated
						? `These rules come from the curated set ${ruleSet.name}. Approving copies them onto this binding; the set itself doesn't change.`
						: `These rules belong to the shared rule set ${ruleSet.name}. Approving changes the set for every binding that uses it.`}
				</p>
			)}

			<section aria-label="Rule changes" className="space-y-2">
				<div className="flex items-center justify-between">
					<h3 className="text-sm font-medium">Rules</h3>
					{canConfirm && (
						<Button
							type="button"
							variant="ghost"
							size="sm"
							onClick={(): void => {
								setDraftRules(rulesToConfirm(shown.result.rules));
								setError(null);
								goTo('rules');
							}}
						>
							Edit rules
						</Button>
					)}
				</div>
				<RuleChangeList
					changes={changes}
					agentId={shown.binding.agent_id}
					introducedIn={introducedIn}
				/>
			</section>

			{shown.current.granted_scopes != null && (
				<section aria-label="Scope changes" className="space-y-2 text-sm">
					<div className="flex items-center justify-between">
						<h3 className="font-medium">Scopes</h3>
						{canConfirm && (
							<Button
								type="button"
								variant="ghost"
								size="sm"
								onClick={(): void => {
									setError(null);
									goTo('scopes');
								}}
							>
								Edit scopes
							</Button>
						)}
					</div>
					<ScopeChangeList changes={scopes} agentId={shown.binding.agent_id} />
					{(newScopes.length > 0 || droppedScopes.length > 0) && (
						<p className="text-muted-foreground text-xs">
							Approving asks you to sign in to the vendor again for the new set of
							scopes; nothing changes until you do.
						</p>
					)}
				</section>
			)}

			{otherAgents.length > 0 && (
				<section aria-label="Agents this change reaches" className="text-xs">
					<p className="text-muted-foreground">
						The changes also reach{' '}
						{otherAgents.length === 1
							? '1 other agent'
							: `${otherAgents.length} other agents`}
						, bound to this credential or to a rule set this change rewrites:{' '}
						{otherAgents.map((agent, i) => (
							<span key={agent.agent_id}>
								{i > 0 && ', '}
								<ActorLabel actorId={agent.agent_id} />
							</span>
						))}
						.
					</p>
				</section>
			)}

			{shown.current.suspended && (
				<Checkbox checked={resume} onChange={setResume} className="text-sm">
					Switch this binding back on (it is suspended)
				</Checkbox>
			)}

			{!canConfirm && <ConfirmBlockedNotice reason={confirmBlockedReason(session)} />}

			<div className="bg-surface-sheet-foot border-hairline-field sticky -bottom-4 z-10 -mx-5 mt-auto -mb-4 flex items-center justify-between gap-2 border-t px-5 py-3.5">
				<div className="flex items-center gap-1">
					<Button type="button" variant="ghost" size="sm" onClick={onBack}>
						{canConfirm ? 'Not now' : 'Close'}
					</Button>
					{canConfirm && byAgent && (
						<Button
							type="button"
							variant="ghost"
							size="sm"
							className="text-danger hover:text-danger"
							onClick={(): void => {
								rejectMutation.reset();
								setRejectOpen(true);
							}}
						>
							Reject
						</Button>
					)}
					{canConfirm && !byAgent && (
						<Button type="button" variant="ghost" size="sm" onClick={handleDiscard}>
							Discard
						</Button>
					)}
				</div>
				{canConfirm && (
					<Button
						type="button"
						variant="primary"
						disabled={changed || confirmMutation.isPending}
						onClick={(): void => void approve()}
					>
						{confirmMutation.isPending && <Loader2 className="h-4 w-4 animate-spin" />}
						{conflicts.length > 0
							? 'Resolve and approve'
							: shown.needs_vendor_consent
								? 'Approve and sign in'
								: 'Approve'}
					</Button>
				)}
			</div>

			<ConfirmDialog
				open={rejectOpen}
				title="Reject this request?"
				body={
					<div className="space-y-2">
						<p>The agent is told it was rejected. Its access stays as it is.</p>
						{rejectMutation.error && (
							<ErrorAlert message="The request couldn't be rejected. Try again." />
						)}
					</div>
				}
				confirmLabel="Reject"
				onConfirm={(): void => void handleReject()}
				onClose={(): void => setRejectOpen(false)}
				pending={rejectMutation.isPending}
			/>
		</div>
	);
}

/** Rule-by-rule change rows: added, removed and kept, with widening flagged. */
function RuleChangeList({
	changes,
	agentId,
	introducedIn,
}: {
	changes: readonly RuleChange[];
	/** The binding's agent: whose asks read "Requested by agent". */
	agentId?: string;
	/** The revision that added each rule (see ``revisionsAddingRules``). */
	introducedIn?: ReadonlyMap<string, number>;
}) {
	if (changes.length === 0) {
		return <p className="text-muted-foreground text-xs">No rule changes.</p>;
	}
	return (
		<ul className="space-y-1">
			{changes.map((change, i) => (
				<li
					key={`${change.kind}-${change.rule.id ?? i}`}
					className={cn(
						'rounded px-2 py-1 text-sm',
						change.kind === 'added' && 'bg-success/10',
						change.kind === 'removed' && 'bg-danger/10',
					)}
				>
					<div className="flex flex-wrap items-center gap-2">
						<EffectBadge effect={change.rule.effect}>{change.rule.effect}</EffectBadge>
						<span
							className={cn(
								'font-mono text-xs',
								change.kind === 'removed' && 'line-through',
							)}
						>
							{describeRuleMatch(change.rule)}
						</span>
						{change.kind !== 'kept' && (
							<Badge variant={change.kind === 'added' ? 'success' : 'danger'}>
								{change.kind === 'added' ? 'Added' : 'Removed'}
							</Badge>
						)}
						{change.widens && <Badge variant="warning">Widens</Badge>}
						{change.kind === 'added' && change.rule.added_by && (
							<AddedBy actorId={change.rule.added_by} agentId={agentId} />
						)}
						{change.kind === 'added' && introducedIn && (
							<IntroducedIn number={revisionAddingRule(introducedIn, change.rule)} />
						)}
					</div>
					{change.rule.comment && (
						<p className="text-foreground-sub mt-0.5 text-[11px] italic">
							{change.rule.comment}
						</p>
					)}
				</li>
			))}
		</ul>
	);
}

/** Scope-by-scope rows, like the rule rows: added, removed and kept, with who asked and why. */
function ScopeChangeList({
	changes,
	agentId,
}: {
	changes: readonly ScopeChange[];
	agentId?: string;
}) {
	if (changes.length === 0) {
		return <p className="text-muted-foreground text-xs">No scopes.</p>;
	}
	return (
		<ul className="space-y-1">
			{changes.map((change) => (
				<li
					key={change.scope}
					className={cn(
						'rounded px-2 py-1 text-sm',
						change.kind === 'added' && 'bg-success/10',
						change.kind === 'removed' && 'bg-danger/10',
					)}
				>
					<div className="flex flex-wrap items-center gap-2">
						<span
							className={cn(
								'font-mono text-xs',
								change.kind === 'removed' && 'line-through',
							)}
						>
							{change.scope}
						</span>
						{change.kind === 'kept' ? (
							<Badge variant="default">Granted</Badge>
						) : (
							<Badge variant={change.kind === 'added' ? 'success' : 'danger'}>
								{change.kind === 'added' ? 'Added' : 'Removed'}
							</Badge>
						)}
						{change.kind !== 'kept' && change.addedBy && (
							<AddedBy actorId={change.addedBy} agentId={agentId} />
						)}
					</div>
					{change.comment && (
						<p className="text-foreground-sub mt-0.5 text-[11px] italic">
							{change.comment}
						</p>
					)}
				</li>
			))}
		</ul>
	);
}

/** Who put a change in the request: the agent asking, or a reviewer by name. */
function AddedBy({ actorId, agentId }: { actorId: string; agentId?: string }) {
	if (actorId === agentId) return <Badge variant="default">Requested by agent</Badge>;
	return (
		<span className="text-muted-foreground text-[11px]">
			Added by <ActorLabel actorId={actorId} />
		</span>
	);
}

/**
 * The reviewer's view of the scopes: what the agent has today and what the
 * request asks for, ticked, beside the rest the vendor offers. Saving makes
 * the new set a revision of the request.
 */
function EditReviewScopesStep({
	display,
	edit,
	saving,
	error,
	onBack,
	onSave,
}: {
	display: VendorDisplay;
	edit: ReviewEdit;
	saving: boolean;
	error: string | null;
	onBack: () => void;
	onSave: (scopes: string[]) => void;
}) {
	const access = useBindingAccess(edit.binding.agent_id, edit.binding.credential_id);
	const [picked, setPicked] = useState<Set<string>>(() => new Set(edit.result.scopes));
	const [typed, setTyped] = useState('');
	const typedId = useId();
	const granted = edit.current.granted_scopes ?? [];
	const catalog = access.data?.scope_catalog ?? null;
	const listed = new Set(catalog?.map((scope) => scope.name) ?? []);
	const options = [
		...(catalog ?? []).map((scope) => ({ ...scope, default: false })),
		...[...new Set([...granted, ...edit.result.scopes])]
			.filter((name) => !listed.has(name))
			.map((name) => ({
				name,
				classification: 'read' as const,
				default: false,
				description: '',
			})),
	];
	const next = [
		...new Set([...picked, ...(catalog ? [] : typed.split(/[\s,]+/).filter(Boolean))]),
	].sort();
	const unchanged = JSON.stringify(next) === JSON.stringify([...edit.result.scopes].sort());

	return (
		<div className="space-y-5">
			<VendorHeader
				display={display}
				subtitle="Change the scopes this request asks for. Saving makes it a new revision."
			/>
			<ScopeChooseField
				label="Scopes"
				loading={access.isPending}
				error={null}
				scopes={options}
				selected={picked}
				onToggle={(name: string): void =>
					setPicked((current) => {
						const updated = new Set(current);
						if (updated.has(name)) updated.delete(name);
						else updated.add(name);
						return updated;
					})
				}
			/>
			{!access.isPending && !catalog && (
				<div className="space-y-1">
					<Label htmlFor={typedId}>Scopes to add</Label>
					<Input
						id={typedId}
						value={typed}
						onChange={(e) => setTyped(e.target.value)}
						placeholder="Separate scopes with spaces or commas"
					/>
				</div>
			)}
			<p className="text-muted-foreground text-xs">
				Granted today: {granted.length > 0 ? granted.join(', ') : 'none'}.
			</p>
			{error && <ErrorAlert message={error} />}
			<div className="bg-surface-sheet-foot border-hairline-field sticky -bottom-4 z-10 -mx-5 mt-auto -mb-4 flex items-center justify-between border-t px-5 py-3.5">
				<Button type="button" variant="ghost" size="sm" onClick={onBack} disabled={saving}>
					<ArrowLeft className="h-4 w-4" />
					Back
				</Button>
				<Button
					type="button"
					variant="primary"
					loading={saving}
					disabled={unchanged}
					onClick={(): void => onSave(next)}
				>
					Save scopes
				</Button>
			</div>
		</div>
	);
}

function IntroducedIn({ number }: { number: number | undefined }) {
	if (number === undefined) return null;
	return <span className="text-muted-foreground text-[11px]">Added in revision {number}</span>;
}

/** Who made a revision: the agent, a reviewer, or no one (a rebase). */
function RevisionAuthor({ createdBy, agentId }: { createdBy: string | null; agentId?: string }) {
	if (createdBy === null) return <span>Rebased</span>;
	return (
		<span className="inline-flex items-center gap-1">
			{agentId !== undefined && (
				<Badge variant={createdBy === agentId ? 'default' : 'neutral'}>
					{createdBy === agentId ? 'Agent' : 'Reviewer'}
				</Badge>
			)}
			<ActorLabel actorId={createdBy} />
		</span>
	);
}

function capitalise(text: string): string {
	return text.charAt(0).toUpperCase() + text.slice(1);
}

/**
 * The request changed under the reviewer: who changed it, how long ago,
 * what they did and why. Also shown when Approve was refused for it.
 */
function RequestChangedBanner({
	changes,
	changed = false,
	agentId,
	afterApprove,
	stale = false,
	onUpdate,
	updating = false,
	onReview,
	onHistory,
}: {
	/**
	 * Every revision since the one under review, oldest first: a rebase that
	 * lands over an amend must not be the only change the reviewer is shown,
	 * or the ask they are acknowledging reads as their own.
	 */
	changes?: RevisionChange[];
	/** The request itself moved, which outranks the access moving under it. */
	changed?: boolean;
	agentId?: string;
	afterApprove: boolean;
	/** The access moved since the request was opened, so the diff is behind it. */
	stale?: boolean;
	onUpdate?: () => void;
	updating?: boolean;
	/** Absent until the new version is read. */
	onReview?: () => void;
	onHistory: () => void;
}) {
	// Newest last, so the reviewer reads them in the order they happened.
	const named = changes ?? [];
	const reason = named.length === 1 ? revisionReason(named[0].revision) : null;
	return (
		<div
			role="alert"
			className="border-warning/40 bg-warning/10 flex items-start justify-between gap-3 rounded-lg border p-3 text-sm"
		>
			<div className="space-y-1">
				<p className="font-medium">
					{afterApprove
						? 'Not approved: this request changed while you were reviewing it.'
						: changed
							? 'This request changed while you were reviewing it.'
							: 'The access this request was opened against has changed since.'}
				</p>
				{stale && (
					<p className="text-xs">
						The changes below are against the access as it was.
						{onUpdate
							? ' Bring the request up to date to review it against the access as it is now.'
							: ' Whoever approves it can bring it up to date first.'}
					</p>
				)}
				{named.map((entry) => (
					<p className="text-xs" key={entry.revision.id}>
						Revision {entry.revision.number}:{' '}
						{entry.revision.created_by === null ? (
							'the request was rebased onto the access as it is now'
						) : (
							<>
								<RevisionAuthor
									createdBy={entry.revision.created_by}
									agentId={agentId}
								/>{' '}
								{revisionSummary(entry)}
							</>
						)}
						, {timeAgo(entry.revision.created_at)} ago.
					</p>
				))}
				{reason && <p className="text-foreground-sub text-[11px] italic">“{reason}”</p>}
			</div>
			<div className="flex shrink-0 gap-2">
				{onUpdate && (
					<Button
						type="button"
						size="sm"
						variant="secondary"
						onClick={onUpdate}
						disabled={updating}
					>
						{updating ? 'Updating…' : 'Update to current access'}
					</Button>
				)}
				{onReview && (
					<Button type="button" size="sm" variant="secondary" onClick={onReview}>
						Review the new version
					</Button>
				)}
				<Button type="button" size="sm" variant="ghost" onClick={onHistory}>
					See history
				</Button>
			</div>
		</div>
	);
}

/** Each revision: who made it and when, what it did and why, and its rule and scope diff. */
function EditHistory({
	changes,
	loading,
	failed,
	agentId,
}: {
	changes: readonly RevisionChange[];
	loading: boolean;
	failed: boolean;
	agentId?: string;
}) {
	if (loading) return <Skeleton className="h-12 w-full" />;
	if (failed) return <ErrorAlert message="The history couldn't be loaded." />;
	return (
		<ol aria-label="Request history" className="space-y-3">
			{changes.map((change) => {
				const { revision, rules, addedScopes: added, removedScopes } = change;
				const reason = revisionReason(revision);
				return (
					<li key={revision.id} className="space-y-1">
						<p className="text-xs">
							<span className="font-medium">Revision {revision.number}</span>{' '}
							<span className="text-muted-foreground">
								<RevisionAuthor createdBy={revision.created_by} agentId={agentId} />
								{' · '}
								{new Date(revision.created_at).toLocaleString()}
							</span>
						</p>
						<p className="text-xs">{capitalise(revisionSummary(change))}.</p>
						{reason && (
							<p className="text-foreground-sub text-[11px] italic">“{reason}”</p>
						)}
						<RuleChangeList changes={rules} />
						{added.length > 0 && (
							<p className="text-xs">
								Scopes added: <span className="font-mono">{added.join(', ')}</span>
							</p>
						)}
						{removedScopes.length > 0 && (
							<p className="text-xs">
								Scopes dropped:{' '}
								<span className="font-mono">{removedScopes.join(', ')}</span>
							</p>
						)}
					</li>
				);
			})}
		</ol>
	);
}
