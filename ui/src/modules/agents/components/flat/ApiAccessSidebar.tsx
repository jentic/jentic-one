/**
 * ApiAccessSidebar — Manage access for one API tile, in one scrolling panel:
 *
 * - Credential: the credential behind the API, its id, and who else its rules
 *   govern.
 * - Access: what the agent can call through this API, read first ("Edit
 *   access" turns it into the setup queue's access step on a draft); an
 *   attached shared rule set shows here, read-only, with Detach.
 * - Test a request: the broker's dry run against the SAVED rules.
 * - Danger zone: unbind (this agent only) and delete the credential
 *   (org-wide), never conflated.
 *
 * Rules belong to the binding (agent × credential), so a credential that
 * reaches several APIs shares one rule list across them; the panel names
 * those APIs and keeps their actions intact on save. This component owns the
 * draft and every confirm; the sections are presentational.
 */
import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import {
	ConfirmDialog,
	DangerZone,
	ErrorAlert,
	SheetPrimitive,
	Skeleton,
	UserText,
	toast,
	type DangerZoneAction,
} from '@/shared/ui';
import { isolateText } from '@/shared/lib/utils';
import {
	AGENTS_WRITE,
	CREDENTIALS_WRITE,
	useCanAccess,
	useOptionalCurrentUser,
} from '@/shared/auth';
import { useCredentialAgents, useDeleteCredential } from '@/shared/credentials/api';
import {
	useConnectAfterCreate,
	CredentialDeleteDialog,
	EditCredentialSheet,
	useActionCatalog,
	useUnappliedChoices,
} from '@/shared/credentials/components';
import { credentialEditableBy } from '@/shared/credentials/lib/credentialAuthority';
import { apiScopeReach } from '@/shared/credentials/lib/apiIdentity';
import {
	summarizeBindingRules,
	useAgentBindingEffectiveRules,
	useAgentBindingPermissions,
	useInvalidateCredentialBindingSurfaces,
	useResumeAgentCredentialBinding,
	useUnbindAgentCredential,
	type AgentEntity,
} from '@/modules/agents/api';
import { AgentBindingRuleTester } from '@/modules/agents/components/detail/AgentBindingRuleTester';
import { toEditorRule } from '@/modules/agents/components/detail/shared';
import { BindingRuleSetPanel } from '@/modules/agents/components/detail/BindingRuleSetPanel';
import type { ApiTileModel } from '@/modules/agents/lib/apiTiles';
import { deriveTileStatus } from '@/modules/agents/lib/tileStatus';
import { AgentNameText } from '@/modules/agents/components/AgentNameText';
import { clipName } from '@/modules/agents/lib/agentName';
import { plural } from '@/modules/agents/lib/stripSummary';
import { otherApisPhrase, siblingApiReferences } from '@/modules/agents/lib/sharedRules';
import { AccessHeader } from '@/modules/agents/components/flat/access/AccessHeader';
import { AccessReadView } from '@/modules/agents/components/flat/access/AccessReadView';
import { AccessEditor } from '@/modules/agents/components/flat/access/AccessEditor';
import { CredentialSection } from '@/modules/agents/components/flat/access/CredentialSection';
import { useAccessDraft } from '@/modules/agents/components/flat/access/useAccessDraft';

/** How long "Saved — these rules are live" stays on the read view. */
const SAVED_NOTICE_MS = 4000;

/** The sheet's scrolling body, with a bottom fade shown only while content
 * continues below it — the panel's most consequential section is its last. The
 * fade must vanish at the end, or it implies content that isn't there. */
function ScrollFadeBody({ children }: { children: ReactNode }) {
	const scroller = useRef<HTMLDivElement | null>(null);
	const content = useRef<HTMLDivElement | null>(null);
	const [moreBelow, setMoreBelow] = useState(false);

	useEffect(() => {
		const el = scroller.current;
		if (!el) return;
		// A couple of pixels of slack: sub-pixel layout rounding otherwise
		// leaves the fade painted at the very bottom of the scroll.
		const check = () => setMoreBelow(el.scrollTop + el.clientHeight < el.scrollHeight - 4);
		check();
		el.addEventListener('scroll', check, { passive: true });
		// The body grows as its async sections resolve (rules, agent usage), so
		// the CONTENT is watched too — not just the viewport.
		const ro = new ResizeObserver(check);
		ro.observe(el);
		if (content.current) ro.observe(content.current);
		return () => {
			el.removeEventListener('scroll', check);
			ro.disconnect();
		};
	}, []);

	return (
		<div className="relative min-h-0 flex-1">
			<div ref={scroller} className="h-full overflow-y-auto px-5 pt-4 pb-5">
				<div ref={content} className="space-y-6">
					{children}
				</div>
			</div>
			{moreBelow && (
				<div
					aria-hidden="true"
					data-testid="sidebar-scroll-fade"
					className="from-surface-sheet pointer-events-none absolute inset-x-0 bottom-0 h-10 bg-gradient-to-t to-transparent"
				/>
			)}
		</div>
	);
}

export interface ApiAccessSidebarProps {
	agent: AgentEntity;
	/** The clicked tile, resolved live by the host — null once the tile is gone. The
	 * sidebar keeps a sticky copy so the exit animation isn't an empty shell. */
	tile: ApiTileModel | null;
	/** The OTHER tiles this tile's binding fans out to. Rules are keyed by
	 * (agent, credential), so they govern all of them. */
	siblingTiles: ApiTileModel[];
	/** How many of the agent's bindings serve this tile's API — above 1, the panel
	 * says how a call picks between the credentials. */
	accountCount?: number;
	open: boolean;
	onClose: () => void;
	/** Open another tile's panel (a sibling API named in this one). */
	onOpenTile?: (key: string) => void;
	/** DOM id for the panel content — the tile's `aria-controls` target. */
	sidebarId: string;
	/** Whether the agent serves traffic — one input to the header's status. */
	agentServing?: boolean;
	/** Open on the rules editor with its first choice focused (a Blocked
	 * tile's fix). `onRulesFocused` spends the request once it lands. */
	focusRules?: boolean;
	onRulesFocused?: () => void;
}

export function ApiAccessSidebar({
	agent,
	tile,
	siblingTiles,
	accountCount = 1,
	open,
	onClose,
	onOpenTile,
	sidebarId,
	agentServing = true,
	focusRules = false,
	onRulesFocused,
}: ApiAccessSidebarProps) {
	const headingId = `${sidebarId}-title`;

	// Sticky tile: keep the last real tile through the 300ms exit animation.
	const [stickyTile, setStickyTile] = useState<ApiTileModel | null>(null);
	useEffect(() => {
		if (tile) setStickyTile(tile);
	}, [tile]);
	const shown = tile ?? stickyTile;
	const credentialId = shown?.credentialId ?? null;

	// A credential shared with the viewer is theirs to bind and use, not to edit or
	// delete. Ownership that cannot be read is treated as NOT the viewer's — fail
	// closed. `credentialCreatedBy` is absent (not null) when no credential row
	// backed the tile at all, which is exactly what an owner-scoped
	// `GET /credentials` returns for someone else's credential bound to this
	// agent: offering Edit there would open a blank sheet, and both verbs 404 (#1543).
	const viewer = useOptionalCurrentUser();
	const credentialReadOnly =
		shown != null && !credentialEditableBy({ created_by: shown.credentialCreatedBy }, viewer);
	// Pause, resume and unbind write the agent's bindings (`agents:write`); editing,
	// deleting or connecting the credential and its binding rules need
	// `credentials:write`. An archived agent's access can't change at all.
	const archived = agent.status === 'archived';
	const canManageBinding = useCanAccess(AGENTS_WRITE) && !archived;
	const canWriteCredentials = useCanAccess(CREDENTIALS_WRITE);
	const credentialLocked = credentialReadOnly || !canWriteCredentials || archived;

	const permissions = useAgentBindingPermissions(open ? agent.id : null, credentialId);
	// What the broker evaluates: an attached rule set's rules, else the inline ones.
	const effective = useAgentBindingEffectiveRules(open ? agent.id : null, credentialId);

	// The header's status: the SAME derivation as the tile, off the same
	// effective rules — an attached rule set's, else the inline ones.
	const savedRuleSummary = useMemo(() => {
		if (!effective.rules) return undefined;
		const summary = summarizeBindingRules(effective.rules);
		return effective.ruleSet
			? {
					...summary,
					ruleSet: { name: effective.ruleSet.name, curated: effective.ruleSet.curated },
				}
			: summary;
	}, [effective.rules, effective.ruleSet]);
	const status = shown
		? deriveTileStatus({
				suspended: shown.suspended,
				agentServing,
				awaitingConsent: shown.awaitingConsent,
				rules: effective.isError ? 'error' : (savedRuleSummary ?? 'loading'),
			})
		: 'ready';

	// The rules as the broker applies them (read view), and the binding's own
	// inline rules (the draft's seed).
	const effectiveRules = useMemo(
		() => (effective.rules ?? []).filter((r) => !r._system).map(toEditorRule),
		[effective.rules],
	);
	const systemRuleCount = (effective.rules ?? []).filter((r) => r._system).length;
	const inlineRules = useMemo(
		() => permissions.data?.filter((r) => !r._system).map(toEditorRule),
		[permissions.data],
	);

	// Only a concrete (imported) API has a version to read operations from.
	const apiReference = useMemo(
		() =>
			shown?.version && shown.apiName
				? { vendor: shown.vendor, name: shown.apiName, version: shown.version }
				: null,
		[shown?.vendor, shown?.apiName, shown?.version],
	);
	const siblingRefs = useMemo(
		() => (shown ? siblingApiReferences(shown, siblingTiles) : []),
		[shown, siblingTiles],
	);
	const catalog = useActionCatalog(apiReference, { enabled: open, siblings: siblingRefs });
	const othersPhrase = shown ? otherApisPhrase(shown, siblingTiles) : null;
	const draft = useAccessDraft({
		agentId: agent.id,
		credentialId: credentialId ?? '',
		saved: inlineRules,
		catalog,
	});

	// Access's read/edit switch and the leave guard. "Saved" shows on the read
	// view for a few seconds after a save closes the editor.
	const [editing, setEditing] = useState(false);
	const [justSaved, setJustSaved] = useState(false);
	useEffect(() => {
		if (!justSaved) return undefined;
		const id = window.setTimeout(() => setJustSaved(false), SAVED_NOTICE_MS);
		return () => window.clearTimeout(id);
	}, [justSaved]);
	// By action's staged choices (not yet applied to the draft).
	const unapplied = useUnappliedChoices();
	const [guard, setGuard] = useState<null | { then: () => void }>(null);
	// Every open — and every other tile — starts on the read view.
	const lastOpened = useRef<string | null>(null);
	const openedKey = open && shown ? shown.key : null;
	useEffect(() => {
		if (openedKey === lastOpened.current) return;
		lastOpened.current = openedKey;
		if (openedKey == null) return;
		setEditing(false);
		setJustSaved(false);
	}, [openedKey]);
	const dirty = editing && (draft.dirty || unapplied.staged > 0);
	/** Run `then` now, or once the operator agrees to drop the draft (staged
	 * By action choices first, then the unsaved draft). */
	const leaving = (then: () => void): void =>
		unapplied.settle('before you leave', (applied) => {
			if (applied || (editing && draft.dirty)) setGuard({ then });
			else then();
		});
	const startEditing = (): void => {
		setJustSaved(false);
		setEditing(true);
	};
	const stopEditing = (): void => {
		unapplied.stagedRef.current?.discard();
		draft.discard();
		setEditing(false);
	};
	// Save rules is disabled while By action holds unapplied choices, so a save
	// is always of the applied draft. Success closes the editor onto the read
	// view; an error keeps it open with the draft intact.
	const save = (): void =>
		draft.save(() => {
			setEditing(false);
			setJustSaved(true);
		});
	/** Close editor: guarded while anything is pending. */
	const closeEditor = (): void => leaving(stopEditing);

	// The edit sheet is a SECOND SheetPrimitive: its Escape must not tear down this
	// sidebar. The confirms are native `<dialog>`s, which SheetPrimitive yields to.
	const [editOpen, setEditOpen] = useState(false);
	const [deleteOpen, setDeleteOpen] = useState(false);
	const [unbindOpen, setUnbindOpen] = useState(false);
	const close = (): void =>
		leaving(() => {
			stopEditing();
			onClose();
		});
	const guardedClose = (): void => {
		if (editOpen || guard || unapplied.settling) return;
		close();
	};

	// Blocked → land on the fix: the editor, its first choice focused (after the
	// sheet's own initial focus, hence the short delay).
	const bodyRef = useRef<HTMLDivElement | null>(null);
	const rulesReady = open && !permissions.isPending && !permissions.isError;
	// Held in a ref so a host re-render (a fresh inline callback) can't restart
	// the timer below before it fires.
	const onRulesFocusedRef = useRef(onRulesFocused);
	useEffect(() => {
		onRulesFocusedRef.current = onRulesFocused;
	});
	const canEditRules = !credentialLocked && !effective.ruleSetId;
	useEffect(() => {
		if (!focusRules || !rulesReady) return undefined;
		if (canEditRules) setEditing(true);
		const id = window.setTimeout(() => {
			const target = bodyRef.current?.querySelector<HTMLElement>(
				'[data-testid="manage-rules-preset"] [role="radio"]',
			);
			target?.scrollIntoView?.({ block: 'center', behavior: 'smooth' });
			target?.focus({ preventScroll: true });
			onRulesFocusedRef.current?.();
		}, 160);
		return () => window.clearTimeout(id);
	}, [focusRules, rulesReady, canEditRules]);

	const unbind = useUnbindAgentCredential(agent.id);
	const resume = useResumeAgentCredentialBinding(agent.id);
	const deleteCredential = useDeleteCredential();
	const invalidateBindingSurfaces = useInvalidateCredentialBindingSurfaces(agent.id);
	const { connectExisting, deviceDialog } = useConnectAfterCreate();
	const [connecting, setConnecting] = useState(false);

	// Every agent bound to this credential — the "used by" line and the delete
	// confirm's blast radius, off one cached read. Only while the sidebar is open.
	const credentialAgents = useCredentialAgents(credentialId ?? undefined, { enabled: open });
	const boundAgentRows = credentialAgents.data?.data ?? [];
	const usedBy = !credentialAgents.isSuccess
		? null
		: credentialReadOnly
			? `${boundAgentRows.length} of your agents`
			: boundAgentRows.length === 1
				? 'this agent only'
				: `${boundAgentRows.length} agents`;

	// The standalone connect keeps the credential whatever the outcome.
	const handleConnect = async (): Promise<void> => {
		if (!credentialId || !shown) return;
		setConnecting(true);
		try {
			await connectExisting(credentialId, shown.credentialName);
		} finally {
			setConnecting(false);
		}
	};

	const handleUnbind = (): void => {
		if (!credentialId) return;
		unbind.mutate(
			{ credentialId, purge: true },
			// The tile is gone with the binding — close the confirm AND the
			// sidebar over it.
			{
				onSuccess: () => {
					setUnbindOpen(false);
					onClose();
				},
			},
		);
	};

	const confirmDeleteCredential = (): void => {
		if (!credentialId) return;
		deleteCredential.mutate(credentialId, {
			onSuccess: () => {
				// The delete hook refreshes the credentials slice; the agents-side binding caches
				// must too. Org-wide scope: the delete removes the binding from EVERY bound agent.
				invalidateBindingSurfaces(credentialId, 'credential');
				toast({ title: 'Credential deleted', variant: 'success' });
				setDeleteOpen(false);
				onClose();
			},
		});
	};

	const dangerActions: DangerZoneAction[] = shown
		? [
				...(canManageBinding
					? [
							{
								key: 'unbind',
								title: 'Unbind from this agent',
								description: `The binding and its rules are deleted for ${isolateText(clipName(agent.name))} only — the credential survives for every other agent.`,
								buttonLabel: 'Unbind from this agent',
								ariaLabel: `Unbind ${shown.credentialName} from ${agent.name}`,
								emphasis: 'outline' as const,
							},
						]
					: []),
				// Only the owner or an admin, holding `credentials:write`, can delete
				// the credential itself.
				...(credentialLocked
					? []
					: [
							{
								key: 'delete',
								title: 'Delete credential everywhere',
								description:
									'Removes the credential org-wide — every agent bound to it loses access.',
								buttonLabel: 'Delete credential',
								ariaLabel: `Delete credential ${shown.credentialName} org-wide`,
								emphasis: 'solid' as const,
							},
						]),
			]
		: [];

	const suspendPending = unbind.isPending && unbind.variables?.purge !== true;
	const unbindPending = unbind.isPending && unbind.variables?.purge === true;

	const lockedReason = archived
		? `${agent.name} is archived, so its access can't change.`
		: credentialReadOnly
			? "Only the credential's owner or an org admin can change these rules."
			: !canWriteCredentials
				? 'Changing these rules needs permission to manage credentials.'
				: null;
	const changeCount = draft.changes.count + unapplied.staged;

	const accessBody = !shown ? null : effective.ruleSetId != null && effective.isError ? (
		// Never read a failed rule-set read as "no rules".
		<ErrorAlert
			message="Failed to load the attached rule set."
			onRetry={() => void effective.refetch()}
		/>
	) : (effective.ruleSetId == null ? permissions.isPending : effective.isPending) ? (
		<div role="status" aria-live="polite" aria-busy="true">
			<span className="sr-only">Loading rules…</span>
			<Skeleton className="h-32 rounded-lg" />
		</div>
	) : effective.ruleSetId == null && permissions.isError ? (
		<ErrorAlert
			message="Failed to load this binding's rules."
			onRetry={() => void permissions.refetch()}
		/>
	) : editing && canEditRules ? (
		<AccessEditor
			// One editor per binding, so a half-made draft never lands on another.
			key={`${agent.id}:${shown.credentialId}`}
			credentialName={shown.credentialName}
			reach={apiScopeReach(shown.scope)}
			apiReference={apiReference}
			siblings={siblingRefs}
			onOpenSibling={(key) => leaving(() => (stopEditing(), onOpenTile?.(key)))}
			systemRuleCount={systemRuleCount}
			catalog={catalog}
			draft={draft}
			unapplied={unapplied}
			onSave={save}
			onClose={closeEditor}
		/>
	) : (
		<AccessReadView
			credentialName={shown.credentialName}
			rules={effectiveRules}
			catalog={catalog}
			apiReference={apiReference}
			onEdit={canEditRules ? startEditing : undefined}
			lockedReason={
				lockedReason ??
				(effective.ruleSetId
					? 'This binding uses a shared rule set. Detach it below to edit its own rules.'
					: null)
			}
			ruleSetName={effective.ruleSet?.name ?? null}
			ruleSetPanel={
				effective.ruleSetId ? (
					<BindingRuleSetPanel
						agentId={agent.id}
						credentialId={shown.credentialId}
						credentialLabel={shown.credentialName}
						ruleSet={effective.ruleSet}
						isPending={effective.isPending}
						isError={effective.isError}
						onRetry={effective.refetch}
						inlineRules={permissions.data}
						canDetach={!credentialLocked}
					/>
				) : null
			}
			systemRuleCount={systemRuleCount}
			justSaved={justSaved}
		/>
	);

	return (
		<>
			<SheetPrimitive
				open={open}
				onClose={guardedClose}
				ariaLabelledBy={headingId}
				className="sm:w-[640px] sm:max-w-[90vw] xl:w-[720px]"
			>
				{shown && (
					<div id={sidebarId} ref={bodyRef} className="flex h-full flex-col">
						<AccessHeader
							tile={shown}
							headingId={headingId}
							agentName={agent.name}
							status={status}
							canPause={canManageBinding}
							pausing={suspendPending}
							resuming={resume.isPending}
							onPause={() => credentialId && unbind.mutate({ credentialId })}
							onResume={() => credentialId && resume.mutate(credentialId)}
							onClose={close}
							canConnect={canWriteCredentials}
							connecting={connecting}
							onConnect={() => void handleConnect()}
						/>
						<ScrollFadeBody>
							<CredentialSection
								tile={shown}
								agentName={agent.name}
								usedBy={usedBy}
								sharedWithViewer={credentialReadOnly}
								canEdit={!credentialLocked}
								onEdit={() => setEditOpen(true)}
								accountCount={accountCount}
								otherApisPhrase={othersPhrase}
							/>
							<section aria-label="Access" className="space-y-4">
								{accessBody}
							</section>
							{/* The dry run of the SAVED rules: the broker's own decision. */}
							<section aria-label="Test a request" className="space-y-3">
								<h3 className="text-foreground text-sm font-semibold">
									Test a request
								</h3>
								<AgentBindingRuleTester
									agentId={agent.id}
									credentialId={shown.credentialId}
									savedRules={effective.rules ?? []}
									apiReference={apiReference}
									draftPending={dirty}
								/>
							</section>
							{/* The two destructive verbs only — unbind (this agent) vs delete (org-wide).
							    The shared card carries the danger styling; this wrapper names the region.
							    A viewer who may do neither gets no danger zone at all. */}
							{dangerActions.length > 0 && (
								<section aria-label="Danger zone" data-testid="sidebar-danger-zone">
									<DangerZone
										pending={unbindPending || deleteCredential.isPending}
										onAction={(key) => {
											if (key === 'unbind') setUnbindOpen(true);
											if (key === 'delete') setDeleteOpen(true);
										}}
										actions={dangerActions}
									/>
								</section>
							)}
						</ScrollFadeBody>
					</div>
				)}
			</SheetPrimitive>

			{/* Stacked edit sheet (shared machinery: write-only secrets, OAuth
			    read-only after creation). */}
			<EditCredentialSheet
				credentialId={credentialId}
				open={editOpen}
				onClose={() => setEditOpen(false)}
				// The bound-agent roster links to an agent's tab on the surface behind this
				// sidebar — possibly a DIFFERENT agent — so following one closes both.
				onNavigateAway={() => {
					setEditOpen(false);
					onClose();
				}}
			/>

			{/* Staged By action choices are settled first: Apply or Discard. */}
			{unapplied.dialog}
			{/* The leave guard: a draft is never dropped without saying so. */}
			<ConfirmDialog
				open={guard != null}
				title={`Leave with ${plural(changeCount, 'unsaved change')}?`}
				body="Test runs against the saved rules, so it won't see your draft."
				confirmLabel="Discard and leave"
				cancelLabel="Keep editing"
				onConfirm={() => {
					const then = guard?.then;
					setGuard(null);
					stopEditing();
					then?.();
				}}
				onClose={() => setGuard(null)}
			/>

			{/* A stateless confirm, so conditional mounting is the sanctioned lifecycle.
			    States both blast radii: the binding and its rules go, the credential stays. */}
			{unbindOpen && shown && (
				<ConfirmDialog
					open
					title={
						<>
							Unbind from <AgentNameText name={agent.name} className="max-w-[32ch]" />
						</>
					}
					body={
						<>
							Permanently unbind{' '}
							<strong>
								<UserText>{shown.credentialName}</UserText>
							</strong>{' '}
							from <AgentNameText name={agent.name} className="font-semibold" />? The
							binding and its rules are deleted — the credential survives for every
							other agent.
						</>
					}
					confirmLabel="Unbind"
					pending={unbindPending}
					onConfirm={handleUnbind}
					onClose={() => setUnbindOpen(false)}
				/>
			)}

			{/* Org-wide delete confirm — blast radius NAMES the bound agents. */}
			{deleteOpen && shown && (
				<CredentialDeleteDialog
					open
					credentialId={shown.credentialId}
					credentialName={shown.credentialName}
					currentAgentId={agent.id}
					onClose={() => setDeleteOpen(false)}
					onConfirm={confirmDeleteCredential}
					loading={deleteCredential.isPending}
					error={deleteCredential.error}
				/>
			)}
			{deviceDialog}
		</>
	);
}
