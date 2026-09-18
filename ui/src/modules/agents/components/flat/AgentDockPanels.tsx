/**
 * AgentDockPanels — the dock's rehosted sheet surfaces (library-first: a
 * sheet keeps the flat surface's context; a dialog is for blocking
 * decisions).
 *
 *  - `AgentKeysSheet` rehosts the console's `AgentKeysPanel` verbatim, so the
 *    API-key contract stays exactly what the console ships: metadata always,
 *    plaintext exactly once via `ApiKeyDialog`, generation only while active.
 *  - `AgentActivitySheet` rehosts `ActivityPanel` (charts + recent
 *    executions), which carries its own Monitor deep link, plus the
 *    console Overview's "Recent changes" audit slice (`ActorAuditPanel`) as
 *    a section of the same sheet (D21 — changes ARE activity; one section,
 *    not another surface).
 *  - `AgentPermissionsSheet` rehosts the retired Access tab's non-credential
 *    cards as-is (plan §4.7, D4): `ScopesCard`, `ActorAccessRequestsCard`,
 *    `ConnectedClientsCard`. Its intro copy names the two permission models
 *    users conflate (platform scopes ≠ upstream API access — §4.7/P13).
 *  - `AgentMcpSheet` rehosts the console MCP tab's `McpPanel` (config card +
 *    session history) as-is. Archived is the exception: an archived agent can
 *    never authenticate, so a copy-paste connect invitation would be a lie —
 *    the sheet keeps only the session history, with copy naming the state
 *    (the Permissions sheet's archived-history precedent).
 *  - `AgentSettingsSheet` rehosts the console Settings tab's
 *    `AgentSettingsPanel` (identity form via PATCH /agents/{id} + danger
 *    zone; the panel itself renders read-only for archived) and fills its
 *    `afterIdentity` slot with `AgentProvenance` — registration and approval
 *    attribution, the last console-only block, so the flat surface needs no
 *    jump-off to read an agent in full.
 *
 * Both key/permissions panels open nested native `<dialog>` confirms
 * (regenerate/revoke, edit scopes). `SheetPrimitive` itself yields Escape to
 * an open native modal `<dialog>` (the top layer sits above every sheet), so
 * Escape closes the dialog first and a second Escape closes the sheet — no
 * per-sheet guard needed here.
 */
import { Fingerprint, X } from 'lucide-react';
import { Button, DetailSection, SheetPrimitive } from '@/shared/ui';
import type { AgentEntity } from '@/modules/agents/api';
import { AgentKeysPanel } from '@/modules/agents/components/detail/AgentKeysPanel';
import { ActivityPanel } from '@/modules/agents/components/detail/ActivityPanel';
import { ActorAuditPanel } from '@/modules/agents/components/detail/ActorAuditPanel';
import { AgentSettingsPanel } from '@/modules/agents/components/detail/AgentSettingsPanel';
import { AgentProvenance } from '@/modules/agents/components/detail/AgentProvenance';
import { McpPanel, McpSessionsCard } from '@/modules/agents/components/detail/McpPanel';
import { ScopesCard } from '@/modules/agents/components/ScopesCard';
import { ActorAccessRequestsCard } from '@/modules/agents/components/ActorAccessRequestsCard';
import { ConnectedClientsCard } from '@/modules/agents/components/detail/ConnectedClientsCard';

/** Shared chrome: header with title/subtitle + close, scrollable body. */
function DockSheetFrame({
	title,
	subtitle,
	headingId,
	onClose,
	children,
}: {
	title: string;
	subtitle: string;
	headingId: string;
	onClose: () => void;
	children: React.ReactNode;
}) {
	return (
		<div className="flex h-full flex-col">
			<header className="border-border flex items-start justify-between gap-3 border-b px-5 py-4">
				<div className="min-w-0">
					<h2 id={headingId} className="text-foreground text-base font-semibold">
						{title}
					</h2>
					<p className="text-muted-foreground truncate text-xs">{subtitle}</p>
				</div>
				<Button
					variant="ghost"
					size="sm"
					aria-label="Close"
					onClick={onClose}
					className="text-muted-foreground hover:text-foreground"
				>
					<X className="h-4 w-4" />
				</Button>
			</header>
			<div className="flex-1 overflow-y-auto px-5 py-4">{children}</div>
		</div>
	);
}

export function AgentKeysSheet({
	agent,
	open,
	onClose,
}: {
	agent: AgentEntity;
	open: boolean;
	onClose: () => void;
}) {
	const headingId = 'agent-keys-sheet-title';
	return (
		<SheetPrimitive open={open} onClose={onClose} ariaLabelledBy={headingId}>
			<DockSheetFrame
				title="API key"
				subtitle={agent.name}
				headingId={headingId}
				onClose={onClose}
			>
				<AgentKeysPanel agent={agent} />
			</DockSheetFrame>
		</SheetPrimitive>
	);
}

export function AgentActivitySheet({
	agent,
	open,
	onClose,
}: {
	agent: AgentEntity;
	open: boolean;
	onClose: () => void;
}) {
	const headingId = 'agent-activity-sheet-title';
	return (
		<SheetPrimitive
			open={open}
			onClose={onClose}
			ariaLabelledBy={headingId}
			className="sm:w-[560px]"
		>
			<DockSheetFrame
				title="Activity"
				subtitle={agent.name}
				headingId={headingId}
				onClose={onClose}
			>
				<div className="space-y-4">
					<ActivityPanel actorId={agent.id} actorType="agent" />
					{/* D21: the console Overview's "Recent changes" feed —
					    lifecycle changes are activity too, so the actor-scoped
					    audit slice lives here as a section (admin-gated; the
					    card renders a quiet empty state for non-admins). */}
					<ActorAuditPanel actorKind="agent" actorId={agent.id} />
				</div>
			</DockSheetFrame>
		</SheetPrimitive>
	);
}

export function AgentPermissionsSheet({
	agent,
	open,
	onClose,
}: {
	agent: AgentEntity;
	open: boolean;
	onClose: () => void;
}) {
	const headingId = 'agent-permissions-sheet-title';
	// Archive sweeps this agent's scope grants and OAuth consents, so for an
	// archived agent the sheet is a historical record (requests it filed,
	// consents since revoked) — never a grant invite. The scope editor is
	// therefore gated off for archived; every other status keeps the retired
	// Access tab's behaviour exactly (edit always offered; the backend's 403
	// is still handled defensively inside the card).
	const isArchived = agent.status === 'archived';
	return (
		<SheetPrimitive
			open={open}
			onClose={onClose}
			ariaLabelledBy={headingId}
			className="sm:w-[560px]"
		>
			<DockSheetFrame
				title="Permissions"
				subtitle={agent.name}
				headingId={headingId}
				onClose={onClose}
			>
				<div className="space-y-4">
					{/* §4.7: platform scopes and upstream API access are different
					    permission models and users conflate them — the surface's
					    copy must name the difference. */}
					<p className="text-muted-foreground text-sm">
						Scopes govern what {agent.name} may do on the Jentic control plane itself —
						they have nothing to do with any upstream API. What it may call upstream is
						set by the API tiles on the main screen (each tile&rsquo;s credential and
						rules).
					</p>
					{isArchived && (
						<p className="text-muted-foreground text-sm">
							Archiving swept this agent&rsquo;s scope grants and OAuth consents —
							what remains below is history.
						</p>
					)}
					{/* Scopes — platform permissions granted to this agent (#615). */}
					<ScopesCard
						actorKind="agent"
						actorId={agent.id}
						actorName={agent.name}
						canEdit={!isArchived}
					/>
					{/* Pending access requests this agent has filed (#619). */}
					<ActorAccessRequestsCard actorId={agent.id} actorName={agent.name} />
					{/* OAuth clients holding a consent→agent grant. */}
					<ConnectedClientsCard agentId={agent.id} agentName={agent.name} />
				</div>
			</DockSheetFrame>
		</SheetPrimitive>
	);
}

export function AgentMcpSheet({
	agent,
	open,
	onClose,
}: {
	agent: AgentEntity;
	open: boolean;
	onClose: () => void;
}) {
	const headingId = 'agent-mcp-sheet-title';
	// An archived agent can never authenticate again, so the config card's
	// copy-paste connect invitation would advertise a dead end — the sheet
	// keeps only the session history, as history (the same archived-history
	// treatment as the Permissions sheet).
	const isArchived = agent.status === 'archived';
	return (
		<SheetPrimitive
			open={open}
			onClose={onClose}
			ariaLabelledBy={headingId}
			className="sm:w-[560px]"
		>
			<DockSheetFrame
				title="MCP"
				subtitle={agent.name}
				headingId={headingId}
				onClose={onClose}
			>
				{isArchived ? (
					<div className="space-y-4">
						<p className="text-muted-foreground text-sm">
							This agent is archived and can no longer authenticate, so there is
							nothing to connect — the sessions below are history.
						</p>
						<McpSessionsCard agentId={agent.id} />
					</div>
				) : (
					<McpPanel agentName={agent.name} agentId={agent.id} />
				)}
			</DockSheetFrame>
		</SheetPrimitive>
	);
}

export function AgentSettingsSheet({
	agent,
	open,
	onClose,
	onArchive,
	archivePending,
}: {
	agent: AgentEntity;
	open: boolean;
	onClose: () => void;
	/** Stage the archive confirm (routes through the existing LifecycleDialogs). */
	onArchive: () => void;
	/** True while the archive mutation is in flight. */
	archivePending: boolean;
}) {
	const headingId = 'agent-settings-sheet-title';
	return (
		<SheetPrimitive
			open={open}
			onClose={onClose}
			ariaLabelledBy={headingId}
			className="sm:w-[560px]"
		>
			<DockSheetFrame
				title="Settings"
				subtitle={agent.name}
				headingId={headingId}
				onClose={onClose}
			>
				{/* The console panel as-is: identity form (PATCH /agents/{id})
				    + danger zone. The panel renders read-only for archived and
				    an empty danger zone where no destructive verb applies; its
				    Archive defers to the page-level LifecycleDialogs confirm
				    (a native modal dialog in the top layer above this sheet). */}
				<AgentSettingsPanel
					agent={agent}
					lifecyclePending={archivePending}
					onLifecycle={() => onArchive()}
					afterIdentity={
						// Where this agent came from and who vouched for it —
						// read-only, and the one console block the flat surface
						// had no home for. Same component the console's Overview
						// tab renders, so the two can't drift.
						<DetailSection
							title="Provenance"
							icon={<Fingerprint className="h-4 w-4" />}
						>
							<AgentProvenance agent={agent} columns="sheet" />
						</DetailSection>
					}
				/>
			</DockSheetFrame>
		</SheetPrimitive>
	);
}
