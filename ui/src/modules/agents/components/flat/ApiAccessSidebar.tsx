/**
 * ApiAccessSidebar — everything about one API tile's access in one panel: the
 * resolved credential (read view + Edit), its permission rules, and the rule
 * tester, disabled while the editor holds an unsaved draft.
 *
 * Unbind (this agent only) and Delete credential (org-wide) are never conflated.
 * Suspend/resume is the reversible cut-off, so it sits in the header.
 */
import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { ExternalLink, Info, LogIn, PauseCircle, Pencil, PlayCircle, X } from 'lucide-react';
import {
	Button,
	DangerZone,
	ErrorAlert,
	SheetHeader,
	SheetPrimitive,
	Skeleton,
	VendorIcon,
	toast,
	ConfirmDialog,
} from '@/shared/ui';
import { vendorIconPropsFor } from '@/shared/lib';
import { formatTimestamp, timeAgo } from '@/shared/lib/utils';
import { useCredentialAgents, useDeleteCredential } from '@/shared/credentials/api';
import { useConnectAfterCreate } from '@/shared/credentials/components/useConnectAfterCreate';
import {
	OperationImpactPreview,
	type OpsApiReference,
} from '@/shared/credentials/components/OperationImpactPreview';
import { CredentialDeleteDialog } from '@/shared/credentials/components/CredentialDeleteDialog';
import { EditCredentialSheet } from '@/shared/credentials/components/EditCredentialSheet';
import {
	summarizeBindingRules,
	useAgentBindingPermissions,
	useInvalidateCredentialBindingSurfaces,
	useResumeAgentCredentialBinding,
	useUnbindAgentCredential,
	type AgentEntity,
} from '@/modules/agents/api';
import { AgentBindingPermissionsEditor } from '@/modules/agents/components/detail/AgentBindingPermissionsEditor';
import { AgentBindingRuleTester } from '@/modules/agents/components/detail/AgentBindingRuleTester';
import { toEditorRule } from '@/modules/agents/components/detail/shared';
import type { ApiTileModel } from '@/modules/agents/lib/apiTiles';
import { deriveTileStatus } from '@/modules/agents/lib/tileStatus';
import { TileStatusChip } from '@/modules/agents/components/flat/TileStatusMarker';

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
			<div ref={scroller} className="h-full overflow-y-auto px-5 pt-1 pb-5">
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
	/** Titles of the OTHER tiles sharing this binding — the blast radius. Rules are
	 * keyed by (agent, credential), so editing them here affects all of them. */
	siblingApiTitles: string[];
	open: boolean;
	onClose: () => void;
	/** DOM id for the panel content — the tile's `aria-controls` target. */
	sidebarId: string;
	/** Whether the agent serves traffic — one input to the header's status. */
	agentServing?: boolean;
	/** Open on the rules editor: scroll to it and focus "Add rule" (a Blocked
	 * tile's fix). `onRulesFocused` spends the request once it lands. */
	focusRules?: boolean;
	onRulesFocused?: () => void;
}

export function ApiAccessSidebar({
	agent,
	tile,
	siblingApiTitles,
	open,
	onClose,
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

	// The editor's live draft-vs-saved dirtiness — gates the tester.
	const [rulesDirty, setRulesDirty] = useState(false);

	// The edit sheet is a SECOND SheetPrimitive: its Escape must not tear down this
	// sidebar. The confirms are native `<dialog>`s, which SheetPrimitive yields to.
	const [editOpen, setEditOpen] = useState(false);
	const [deleteOpen, setDeleteOpen] = useState(false);
	const [unbindOpen, setUnbindOpen] = useState(false);
	const guardedClose = (): void => {
		if (editOpen) return;
		onClose();
	};

	const credentialId = shown?.credentialId ?? null;
	const permissions = useAgentBindingPermissions(open ? agent.id : null, credentialId);

	// The header's status: the SAME derivation as the tile, off the same rules read.
	const savedRuleSummary = useMemo(
		() => (permissions.data ? summarizeBindingRules(permissions.data) : undefined),
		[permissions.data],
	);
	// The saved operator rules in the credentials kit's shape, for the preview.
	const savedEditorRules = useMemo(
		() => (permissions.data ?? []).filter((r) => !r._system).map(toEditorRule),
		[permissions.data],
	);
	const status = shown
		? deriveTileStatus({
				suspended: shown.suspended,
				agentServing,
				awaitingConsent: shown.awaitingConsent,
				rules: savedRuleSummary,
			})
		: 'ready';

	// Blocked → land on the fix: once the editor has rendered, bring the rules
	// section into view and put focus on "Add rule" (after the sheet's own
	// initial focus, hence the short delay).
	const rulesSectionRef = useRef<HTMLElement | null>(null);
	const rulesReady = open && !permissions.isPending && !permissions.isError;
	// Held in a ref so a host re-render (a fresh inline callback) can't restart
	// the timer below before it fires.
	const onRulesFocusedRef = useRef(onRulesFocused);
	useEffect(() => {
		onRulesFocusedRef.current = onRulesFocused;
	});
	useEffect(() => {
		if (!focusRules || !rulesReady) return undefined;
		const id = window.setTimeout(() => {
			const section = rulesSectionRef.current;
			if (!section) return;
			section.scrollIntoView?.({ block: 'start', behavior: 'smooth' });
			section
				.querySelector<HTMLButtonElement>('[data-rule-add]')
				?.focus({ preventScroll: true });
			onRulesFocusedRef.current?.();
		}, 120);
		return () => window.clearTimeout(id);
	}, [focusRules, rulesReady]);

	// The API this tile resolves to, for the rule editor's operation suggestions
	// and the effective-access preview. Only a concrete (imported) API has a
	// version to read operations from; a wildcard tile has none to preview.
	const apiReference = useMemo<OpsApiReference | null>(
		() =>
			shown?.version
				? { vendor: shown.vendor, name: shown.apiName, version: shown.version }
				: null,
		[shown?.vendor, shown?.apiName, shown?.version],
	);

	const unbind = useUnbindAgentCredential(agent.id);
	const resume = useResumeAgentCredentialBinding(agent.id);
	const deleteCredential = useDeleteCredential();
	const invalidateBindingSurfaces = useInvalidateCredentialBindingSurfaces(agent.id);
	const { connectExisting, deviceDialog } = useConnectAfterCreate();
	const [connecting, setConnecting] = useState(false);

	// Every agent bound to this credential — the "Used by" line and the delete
	// confirm's blast radius, off one cached read. Only while the sidebar is open.
	const credentialAgents = useCredentialAgents(credentialId ?? undefined, { enabled: open });
	const boundAgentRows = credentialAgents.data?.data ?? [];

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

	// "1 agent" is this agent alone; anything more is the shared-secret warning
	// the operator needs before editing or deleting.
	const usedByLabel =
		boundAgentRows.length === 1 ? 'this agent only' : `${boundAgentRows.length} agents`;
	const credentialAge = shown?.credentialUpdatedAt
		? `updated ${timeAgo(shown.credentialUpdatedAt)}`
		: shown?.credentialCreatedAt
			? `added ${timeAgo(shown.credentialCreatedAt)}`
			: null;

	const suspendPending = unbind.isPending && unbind.variables?.purge !== true;
	const unbindPending = unbind.isPending && unbind.variables?.purge === true;

	return (
		<>
			<SheetPrimitive
				open={open}
				onClose={guardedClose}
				ariaLabelledBy={headingId}
				className="sm:w-[640px] sm:max-w-[90vw] xl:w-[720px]"
			>
				{shown && (
					<div id={sidebarId} className="flex h-full flex-col">
						<SheetHeader className="justify-between">
							<div className="flex min-w-0 items-start gap-3">
								<VendorIcon {...vendorIconPropsFor(shown)} size="sm" />
								<div className="min-w-0">
									<div className="flex min-w-0 items-center gap-2">
										<h2
											id={headingId}
											className="font-heading text-foreground-name truncate text-base font-semibold"
										>
											{shown.title}
										</h2>
										<TileStatusChip status={status} className="shrink-0" />
									</div>
									<p className="text-muted-foreground truncate text-xs">
										{shown.host}
										{shown.authLabel ? ` · ${shown.authLabel}` : ''} — access
										for {agent.name}
										{shown.suspended ? ' · not serving calls' : ''}
										{shown.suspended && shown.suspendedReason === 'api_deleted'
											? ' because its API was deleted — review the rules before resuming'
											: ''}
									</p>
								</div>
							</div>
							{/* Suspend/resume lives HERE, not in the danger zone: pausing is safe (rules
							    survive, resume restores access), so it sits with the status line. */}
							<div className="flex shrink-0 items-center gap-1.5">
								{shown.suspended ? (
									<Button
										size="sm"
										variant="secondary"
										loading={resume.isPending}
										onClick={() => credentialId && resume.mutate(credentialId)}
										aria-label={`Resume binding for ${shown.credentialName}`}
										title="Resume this binding — rules survived; access is restored."
									>
										<PlayCircle className="h-4 w-4" /> Resume
									</Button>
								) : (
									<Button
										size="sm"
										variant="secondary"
										loading={suspendPending}
										onClick={() =>
											credentialId && unbind.mutate({ credentialId })
										}
										aria-label={`Suspend binding for ${shown.credentialName}`}
										title="Pause this binding — reversible; rules survive and resume restores access."
									>
										<PauseCircle className="h-4 w-4" /> Pause
									</Button>
								)}
								<Button
									variant="ghost"
									size="icon-xs"
									aria-label="Close"
									onClick={onClose}
									className="shrink-0"
								>
									<X className="h-4 w-4" />
								</Button>
							</div>
						</SheetHeader>

						<ScrollFadeBody>
							{/* Awaiting consent: the one not-usable state the redacted
							    credential can prove. The connect flow lives HERE. */}
							{shown.awaitingConsent && (
								<div
									data-testid="sidebar-connect-affordance"
									className="bg-surface-inset space-y-2 rounded-lg px-4 py-3"
								>
									<p className="text-foreground flex items-start gap-2 text-sm">
										<LogIn
											className="text-warning mt-0.5 h-4 w-4 shrink-0"
											aria-hidden="true"
										/>
										<span>
											Waiting for a sign-in at {shown.vendor}. Calls through
											this API fail until the connection completes.
										</span>
									</p>
									<Button
										size="sm"
										loading={connecting}
										onClick={() => void handleConnect()}
									>
										<ExternalLink className="h-4 w-4" />
										Finish connecting
									</Button>
								</div>
							)}

							{/* 1 — The credential. Read view; edits stack the shared
							    edit sheet (secrets stay write-only there). */}
							<section aria-label="Credential" className="space-y-3">
								<h3 className="text-foreground text-sm font-semibold">
									Credential
								</h3>
								<div className="bg-surface-inset flex flex-wrap items-center gap-3 rounded-lg px-4 py-3">
									<div className="min-w-0 flex-1">
										<p className="text-foreground-name truncate text-sm font-semibold">
											{shown.credentialName}
										</p>
										<p className="text-muted-foreground text-xs">
											{shown.authLabel ?? 'Credential'} · bound{' '}
											<span title={formatTimestamp(shown.boundAt)}>
												{timeAgo(shown.boundAt)}
											</span>
											{/* The credential's last update when one happened, else
											    its creation. Omitted when the org row is unreachable. */}
											{credentialAge && <> · {credentialAge}</>}
											{/* The blast radius of editing or revoking this secret,
											    from the read the cascade confirm already makes. */}
											{credentialAgents.isSuccess && (
												<> · used by {usedByLabel}</>
											)}
										</p>
									</div>
									<Button
										size="xs"
										variant="tonal"
										onClick={() => setEditOpen(true)}
									>
										<Pencil className="h-4 w-4" /> Edit credential
									</Button>
								</div>
							</section>

							{/* Blast radius: rules are keyed by (credential, agent) —
							    one credential can serve several APIs. */}
							{siblingApiTitles.length > 0 && (
								<p
									data-testid="blast-radius-note"
									className="bg-surface-inset text-foreground-lighter flex items-start gap-2 rounded-lg px-3.5 py-2.5 text-xs leading-relaxed"
								>
									<Info
										className="text-caution mt-0.5 h-4 w-4 shrink-0"
										aria-hidden="true"
									/>
									<span>
										This credential also serves{' '}
										<strong>{siblingApiTitles.join(', ')}</strong> for{' '}
										{agent.name}. The rules below are shared — changing them
										changes access to{' '}
										{siblingApiTitles.length === 1 ? 'that API' : 'those APIs'}{' '}
										too.
									</span>
								</p>
							)}

							{/* 2 — The permission rules, keyed by agent+credential so a
							    different tile never inherits a stale draft. */}
							<section
								ref={rulesSectionRef}
								aria-label="Permission rules"
								className="scroll-mt-2 space-y-3"
							>
								{permissions.isPending ? (
									<div role="status" aria-live="polite" aria-busy="true">
										<span className="sr-only">Loading rules…</span>
										<Skeleton className="h-32 rounded-lg" />
									</div>
								) : permissions.isError ? (
									<ErrorAlert
										message="Failed to load this binding's rules."
										onRetry={() => void permissions.refetch()}
									/>
								) : (
									<AgentBindingPermissionsEditor
										key={`${agent.id}:${shown.credentialId}`}
										agentId={agent.id}
										credentialId={shown.credentialId}
										credentialLabel={shown.credentialName}
										initialRules={permissions.data ?? []}
										onDirtyChange={setRulesDirty}
										apiReference={apiReference}
									/>
								)}
								{/* What the SAVED rules let this agent reach, against the
								    API's real operations — always visible, not gated on
								    editing, so the binding's surface reads at a glance. */}
								{apiReference && !permissions.isPending && !permissions.isError && (
									<OperationImpactPreview
										api={apiReference}
										rules={savedEditorRules}
										label="Effective access for this binding"
									/>
								)}
							</section>

							{/* 3 — The rule tester (rehosted; disabled while the
							    editor above holds an unsaved draft). */}
							<section aria-label="Test a request" className="space-y-3">
								<h3 className="text-foreground text-sm font-semibold">
									Test a request
								</h3>
								<AgentBindingRuleTester
									agentId={agent.id}
									credentialId={shown.credentialId}
									savedRules={permissions.data ?? []}
									disabled={rulesDirty}
									apiReference={apiReference}
								/>
							</section>

							{/* The two destructive verbs only — unbind (this agent) vs delete (org-wide).
							    The shared card carries the danger styling; this wrapper names the region. */}
							<section aria-label="Danger zone" data-testid="sidebar-danger-zone">
								<DangerZone
									pending={unbindPending || deleteCredential.isPending}
									onAction={(key) => {
										if (key === 'unbind') setUnbindOpen(true);
										if (key === 'delete') setDeleteOpen(true);
									}}
									actions={[
										{
											key: 'unbind',
											title: 'Unbind from this agent',
											description: `The binding and its rules are deleted for ${agent.name} only — the credential survives for every other agent.`,
											buttonLabel: 'Unbind from this agent',
											ariaLabel: `Unbind ${shown.credentialName} from ${agent.name}`,
											emphasis: 'outline',
										},
										{
											key: 'delete',
											title: 'Delete credential everywhere',
											description:
												'Removes the credential org-wide — every agent bound to it loses access.',
											buttonLabel: 'Delete credential',
											ariaLabel: `Delete credential ${shown.credentialName} org-wide`,
											emphasis: 'solid',
										},
									]}
								/>
							</section>
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

			{/* A stateless confirm, so conditional mounting is the sanctioned lifecycle.
			    States both blast radii: the binding and its rules go, the credential stays. */}
			{unbindOpen && shown && (
				<ConfirmDialog
					open
					title={`Unbind from ${agent.name}`}
					body={
						<>
							Permanently unbind <strong>{shown.credentialName}</strong> from{' '}
							<strong>{agent.name}</strong>? The binding and its rules are deleted —
							the credential survives for every other agent.
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
