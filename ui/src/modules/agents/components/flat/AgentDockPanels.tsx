/**
 * AgentDockPanels — the dock's sheet surfaces. Each rehosts the console panel it
 * replaces verbatim, so the contracts stay what the console ships (notably the API
 * key's metadata-always, plaintext-once rule). Archived is the exception: an
 * archived agent can never authenticate, so the MCP sheet drops the connect
 * invitation and Permissions drops the scope editor — both keep the history.
 */
import { Fingerprint, X } from 'lucide-react';
import { Button, DetailSection, SheetPrimitive } from '@/shared/ui';
import { useIsGeneratingAgentApiKey, type AgentEntity } from '@/modules/agents/api';
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
	// A new key's plaintext is revealed once, by the panel inside this sheet —
	// closing mid-generate would unmount it before the key lands and lose it.
	const generating = useIsGeneratingAgentApiKey();
	const guardedClose = (): void => {
		if (!generating) onClose();
	};
	return (
		<SheetPrimitive open={open} onClose={guardedClose} ariaLabelledBy={headingId}>
			<DockSheetFrame
				title="API key"
				subtitle={agent.name}
				headingId={headingId}
				onClose={guardedClose}
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
					{/* Lifecycle changes are activity too — admin-gated; the card renders a
					    quiet empty state for non-admins. */}
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
	// archived agent the sheet is a record, never a grant invite.
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
					{/* Platform scopes and upstream API access are different permission models
					    and users conflate them — the copy must name the difference. */}
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
					<ScopesCard
						actorKind="agent"
						actorId={agent.id}
						actorName={agent.name}
						canEdit={!isArchived}
					/>
					<ActorAccessRequestsCard actorId={agent.id} actorName={agent.name} />
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
	// An archived agent can never authenticate, so the config card's connect
	// invitation would advertise a dead end — keep only the session history.
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
			// Escape or the X is a dismissal, not a discard: the identity draft
			// survives it, and only a successful save clears it.
			keepMounted
		>
			<DockSheetFrame
				title="Settings"
				subtitle={agent.name}
				headingId={headingId}
				onClose={onClose}
			>
				{/* The console panel as-is. Its Archive defers to the page-level
				    LifecycleDialogs confirm, a native dialog above this sheet. */}
				<AgentSettingsPanel
					agent={agent}
					lifecyclePending={archivePending}
					onLifecycle={() => onArchive()}
					afterIdentity={
						// Where this agent came from and who vouched for it. Same component
						// the console's Overview renders, so the two can't drift.
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
