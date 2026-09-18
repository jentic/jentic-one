/**
 * ApiAccessSidebar — everything about one API tile's access, in one panel
 * (plan §4.5). Opens when a tile on the flat Agents surface is clicked and
 * holds, for that API's resolved credential+binding:
 *
 *   1. The credential — a read view plus an "Edit credential" affordance
 *      that stacks the existing EditCredentialSheet (OAuth stays read-only
 *      after creation; write-only secrets are never revealed — both already
 *      enforced by the shared machinery this rehosts).
 *   2. The permission rules — the existing AgentBindingPermissionsEditor,
 *      keyed by (agent, credential). When the binding serves MORE than one
 *      API, a blast-radius note names the other affected APIs: rules are
 *      per-credential, not per-tile.
 *   3. The rule tester — the existing AgentBindingRuleTester, DISABLED while
 *      the editor holds an unsaved draft (the editor reports its own diff
 *      via `onDirtyChange`; nothing is recomputed here). The dry-run
 *      evaluates saved broker policy only; nothing goes upstream.
 *
 * Two distinct destructive verbs (never conflated):
 *   - UNBIND — `DELETE /agents/{id}/credentials/{cid}` (purge): this agent
 *     only; the credential survives for everyone else.
 *   - DELETE CREDENTIAL — `DELETE /credentials/{id}`: org-wide; the confirm
 *     names every agent bound to it (`GET /credentials/{id}/agents`).
 *
 * Suspend/resume (the reversible cut-off) sits in the HEADER beside the
 * status line — it is safe (rules survive, resume restores access), so it
 * never shares a section with the destructive verbs. Dashed
 * (awaiting-consent) tiles open this same
 * sidebar — the "Finish connecting" flow lives here (D13: every attached API
 * has a real binding; there is no empty-rules branch).
 *
 * No invented health (risk O7): the panel states only what the binding and
 * the credential's redacted state can prove.
 */
import { useEffect, useRef, useState, type ReactNode } from 'react';
import { AlertTriangle, ExternalLink, PauseCircle, Pencil, PlayCircle, X } from 'lucide-react';
import {
	Badge,
	Button,
	CascadeDeleteDialog,
	DangerZone,
	ErrorAlert,
	SheetPrimitive,
	Skeleton,
	VendorIcon,
	toast,
	type CascadeDependentGroup,
} from '@/shared/ui';
import { formatTimestamp, timeAgo } from '@/shared/lib/utils';
import {
	useCredentialAgents,
	useDeleteCredential,
	useRunConnectFlow,
} from '@/shared/credentials/api';
import { EditCredentialSheet } from '@/shared/credentials/components/EditCredentialSheet';
import {
	useAgentBindingPermissions,
	useInvalidateCredentialBindingSurfaces,
	useResumeAgentCredentialBinding,
	useUnbindAgentCredential,
	type AgentEntity,
} from '@/modules/agents/api';
import { AgentBindingPermissionsEditor } from '@/modules/agents/components/detail/AgentBindingPermissionsEditor';
import { AgentBindingRuleTester } from '@/modules/agents/components/detail/AgentBindingRuleTester';
import { ConfirmDialog } from '@/modules/agents/components/confirm/ConfirmDialog';
import type { ApiTileModel } from '@/modules/agents/lib/apiTiles';

/**
 * The sheet's scrolling body, with a bottom fade shown only while content
 * continues below it. The panel's most consequential section (the danger zone)
 * is its last, so "there is more down here" has to be visible rather than
 * assumed — and the fade must disappear at the end, or it would imply content
 * that isn't there.
 */
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
			<div ref={scroller} className="h-full overflow-y-auto px-5 py-5">
				<div ref={content} className="space-y-6">
					{children}
				</div>
			</div>
			{moreBelow && (
				<div
					aria-hidden="true"
					data-testid="sidebar-scroll-fade"
					className="from-card pointer-events-none absolute inset-x-0 bottom-0 h-10 bg-gradient-to-t to-transparent"
				/>
			)}
		</div>
	);
}

export interface ApiAccessSidebarProps {
	agent: AgentEntity;
	/**
	 * The clicked tile, resolved live by the host — null once the tile is
	 * gone (unbound elsewhere / agent switched). The sidebar keeps a sticky
	 * copy so the exit animation doesn't render an empty shell.
	 */
	tile: ApiTileModel | null;
	/**
	 * Titles of the OTHER tiles sharing this tile's binding — the blast
	 * radius. Rules are keyed by (agent, credential): editing them here
	 * affects every API this credential serves.
	 */
	siblingApiTitles: string[];
	open: boolean;
	onClose: () => void;
	/** DOM id for the panel content — the tile's `aria-controls` target. */
	sidebarId: string;
}

export function ApiAccessSidebar({
	agent,
	tile,
	siblingApiTitles,
	open,
	onClose,
	sidebarId,
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

	// Nested overlays. The edit sheet is a SECOND SheetPrimitive: its Escape
	// must not also tear down this sidebar (same guard as
	// CredentialInventorySheet). The confirms are native <dialog>s —
	// SheetPrimitive itself yields Escape to them.
	const [editOpen, setEditOpen] = useState(false);
	const [deleteOpen, setDeleteOpen] = useState(false);
	const [unbindOpen, setUnbindOpen] = useState(false);
	const guardedClose = (): void => {
		if (editOpen) return;
		onClose();
	};

	const credentialId = shown?.credentialId ?? null;
	const permissions = useAgentBindingPermissions(open ? agent.id : null, credentialId);

	const unbind = useUnbindAgentCredential(agent.id);
	const resume = useResumeAgentCredentialBinding(agent.id);
	const deleteCredential = useDeleteCredential();
	const invalidateBindingSurfaces = useInvalidateCredentialBindingSurfaces(agent.id);
	const runConnect = useRunConnectFlow();
	const [connecting, setConnecting] = useState(false);

	// Every agent bound to this credential — the org-delete blast radius.
	// Fetched only while the sidebar is open.
	const credentialAgents = useCredentialAgents(credentialId ?? undefined, { enabled: open });
	const boundAgentRows = credentialAgents.data?.data ?? [];
	const deleteDependents: CascadeDependentGroup[] | undefined = credentialAgents.isSuccess
		? [
				{
					label: 'agent binding',
					count: boundAgentRows.length,
					names: boundAgentRows.map((row) =>
						row.agent_id === agent.id
							? `${row.agent_name} (this agent)`
							: row.agent_name,
					),
				},
			]
		: undefined;

	// Mirrors CredentialsPage/CredentialInventorySheet handleConnect: the
	// standalone connect keeps the credential whatever the outcome.
	const handleConnect = async (): Promise<void> => {
		if (!credentialId || !shown) return;
		setConnecting(true);
		toast({ title: `Opening sign-in for ${shown.credentialName}…` });
		try {
			const outcome = await runConnect(credentialId);
			switch (outcome.status) {
				case 'connected':
					toast({ title: 'Connected', variant: 'success' });
					break;
				case 'redirected':
					break;
				case 'cancelled':
					toast({ title: 'Connection cancelled' });
					break;
				case 'timeout':
					toast({
						title: 'Connection timed out',
						description: 'Finish the sign-in and refresh to see the result.',
						variant: 'error',
					});
					break;
			}
		} catch {
			toast({ title: 'Could not start the OAuth flow', variant: 'error' });
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
				// The delete hook refreshes the credentials slice; the agents-
				// side binding caches must refresh too. Org-wide scope: the
				// delete removes the binding from EVERY bound agent, and the
				// flat surface keeps every agent's binding query mounted (strip
				// gap hints) — a this-agent-only invalidation would leave the
				// other agents' tiles/hints stale until reload.
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
						<header className="border-border flex items-start justify-between gap-3 border-b px-5 py-4">
							<div className="flex min-w-0 items-start gap-3">
								<VendorIcon
									name={shown.title}
									vendor={shown.vendor}
									iconUrl={shown.iconUrl}
									size="sm"
								/>
								<div className="min-w-0">
									<div className="flex min-w-0 items-center gap-2">
										<h2
											id={headingId}
											className="text-foreground truncate text-base font-semibold"
										>
											{shown.title}
										</h2>
										{shown.suspended && (
											<Badge
												variant="warning"
												data-testid="sidebar-suspended-badge"
												className="shrink-0"
											>
												Suspended
											</Badge>
										)}
									</div>
									<p className="text-muted-foreground truncate text-xs">
										{shown.host}
										{shown.authLabel ? ` · ${shown.authLabel}` : ''} — access
										for {agent.name}
										{shown.suspended ? ' · not serving calls' : ''}
									</p>
								</div>
							</div>
							{/* Suspend/resume — the reversible cut-off. It lives HERE,
							    not in the danger zone: pausing is safe (rules survive,
							    resume restores access), so it sits with the status line
							    where it's visible without scrolling. */}
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
									size="sm"
									aria-label="Close"
									onClick={onClose}
									className="text-muted-foreground hover:text-foreground shrink-0"
								>
									<X className="h-4 w-4" />
								</Button>
							</div>
						</header>

						<ScrollFadeBody>
							{/* Awaiting consent: the one not-usable state the redacted
							    credential can prove. The connect flow lives HERE. */}
							{shown.awaitingConsent && (
								<div
									data-testid="sidebar-connect-affordance"
									className="border-warning/60 bg-warning/5 space-y-2 rounded-lg border border-dashed px-4 py-3"
								>
									<p className="text-warning flex items-start gap-1.5 text-sm">
										<AlertTriangle
											className="mt-0.5 h-4 w-4 shrink-0"
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
								<div className="border-border bg-muted/30 flex flex-wrap items-center gap-3 rounded-lg border px-4 py-3">
									<div className="min-w-0 flex-1">
										<p className="text-foreground truncate text-sm font-medium">
											{shown.credentialName}
										</p>
										<p className="text-muted-foreground text-xs">
											{shown.authLabel ?? 'Credential'} · bound{' '}
											<span title={formatTimestamp(shown.boundAt)}>
												{timeAgo(shown.boundAt)}
											</span>
											{/* The credential's own age: its last update
											    (a rotation or edit) when one happened,
											    else its creation. Omitted when the org
											    row is unreachable. */}
											{credentialAge && <> · {credentialAge}</>}
											{/* The blast radius of editing or revoking
											    this secret, from the read this sidebar
											    already makes for the cascade confirm. */}
											{credentialAgents.isSuccess && (
												<> · used by {usedByLabel}</>
											)}
										</p>
									</div>
									<Button
										size="sm"
										variant="secondary"
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
									className="border-warning/40 bg-warning/5 text-foreground flex items-start gap-2 rounded-lg border px-3.5 py-2.5 text-xs leading-relaxed"
								>
									<AlertTriangle
										className="text-warning mt-0.5 h-4 w-4 shrink-0"
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

							{/* 2 — The permission rules (rehosted editor, keyed by
							    agent+credential so a different tile never inherits a
							    stale draft). */}
							<section aria-label="Permission rules" className="space-y-3">
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
								/>
							</section>

							{/* Danger zone — the two destructive verbs only (suspend is
							    reversible and lives in the header), in the app's shared
							    danger-zone grammar: unbind (this agent only) vs delete
							    (org-wide), visually and textually distinct. The ring is
							    scoped to THIS surface: in a scrolling sheet the zone has
							    to announce itself, where a settings page has room to let
							    it sit quietly. The shared card itself is untouched. */}
							<section
								aria-label="Danger zone"
								data-testid="sidebar-danger-zone"
								className="ring-danger/30 ring-offset-card rounded-xl ring-1 ring-offset-4"
							>
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
			/>

			{/* Unbind confirm — the app's standard confirm-dialog pattern (a
			    stateless confirm, so conditional mounting is the sanctioned
			    lifecycle). Names the agent and states both blast radii: the
			    binding and its rules go, the credential survives elsewhere. */}
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
				<CascadeDeleteDialog
					open
					onClose={() => setDeleteOpen(false)}
					onConfirm={confirmDeleteCredential}
					entityType="credential"
					entityName={shown.credentialName}
					dependents={deleteDependents}
					loading={deleteCredential.isPending}
					error={deleteCredential.error}
				/>
			)}
		</>
	);
}
