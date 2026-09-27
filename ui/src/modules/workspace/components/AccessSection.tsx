/**
 * AccessSection — the API detail page's "Access" tab: who can call this API
 * and who is calling it.
 *
 * In a shared workspace an API is one entry many people and agents depend on,
 * so before changing or removing it you want to see its blast radius:
 *
 *   - Credentials — the secrets the gateway injects for it, who added each,
 *     and which agents each one is bound to.
 *   - Calling agents — who actually called it over the last week, and how
 *     those calls went.
 *
 * Both are joins onto other resources (credentials, monitoring), read through
 * this module's hooks. Unknown stays unknown: a list still paging in shows a
 * spinner, never "none".
 */
import { ArrowUpRight, Bot, KeyRound } from 'lucide-react';
import {
	ActorLabel,
	AgentBadge,
	AppLink,
	Badge,
	Card,
	EmptyState,
	ErrorAlert,
	Skeleton,
} from '@/shared/ui';
import { ROUTE_PATHS } from '@/shared/app';
import { CredentialTypeBadge } from '@/shared/credentials/components/CredentialTypeBadge';
import {
	formatAgo,
	parseUsageCaller,
	useApiAgentTraffic,
	useCredentialBindings,
	useWorkspaceCredentials,
	USAGE_WINDOW_DAYS,
} from '@/modules/workspace/api';
import type { ApiKey, Credential, UsageRow, WorkspaceApi } from '@/modules/workspace/api';

function SectionHeader({
	icon,
	title,
	description,
	link,
}: {
	icon: React.ReactNode;
	title: string;
	description: string;
	link?: { href: string; label: string };
}) {
	return (
		<div className="border-border/60 flex items-start justify-between gap-3 border-b px-4 py-3">
			<div className="flex min-w-0 items-start gap-2.5">
				<span className="text-muted-foreground mt-0.5 shrink-0">{icon}</span>
				<div className="min-w-0">
					<h3 className="text-foreground text-sm font-semibold">{title}</h3>
					<p className="text-muted-foreground text-xs">{description}</p>
				</div>
			</div>
			{link ? (
				<AppLink
					href={link.href}
					className="text-primary inline-flex shrink-0 items-center gap-1 text-xs font-medium hover:underline"
				>
					{link.label}
					<ArrowUpRight size={12} aria-hidden="true" />
				</AppLink>
			) : null}
		</div>
	);
}

function ListSkeleton() {
	return (
		<div className="space-y-2 p-4" aria-busy="true">
			<Skeleton className="h-8 w-full rounded-md" />
			<Skeleton className="h-8 w-full rounded-md" />
		</div>
	);
}

/** The agents one credential is bound to, as a compact inline list. */
function CredentialBindings({ credentialId }: { credentialId: string }) {
	const bindings = useCredentialBindings(credentialId);
	if (bindings.error) return <span className="text-muted-foreground">Agents unavailable</span>;
	if (!bindings.complete) return <span className="text-muted-foreground">Loading agents…</span>;
	if (bindings.items.length === 0)
		return <span className="text-muted-foreground">Not bound to any agent</span>;
	return (
		<span className="flex flex-wrap items-center gap-x-3 gap-y-1">
			{bindings.items.map((binding) => (
				<AppLink
					key={binding.agent_id}
					href={ROUTE_PATHS.agentTab(binding.agent_id)}
					className="text-foreground inline-flex items-center gap-1.5 hover:underline"
				>
					<AgentBadge id={binding.agent_id} name={binding.agent_name} size="xs" />
					{binding.agent_name}
					{binding.suspended ? (
						<Badge variant="warning" className="px-1.5 py-0 text-[10px]">
							Suspended
						</Badge>
					) : null}
				</AppLink>
			))}
		</span>
	);
}

function CredentialItem({ credential }: { credential: Credential }) {
	const addedAgo = formatAgo(credential.created_at);
	return (
		<li className="space-y-1.5 px-4 py-3" data-testid="access-credential">
			<div className="flex flex-wrap items-center gap-2">
				<span className="text-foreground text-sm font-medium">{credential.name}</span>
				<CredentialTypeBadge credential={credential} />
				{credential.active ? null : <Badge variant="default">Disabled</Badge>}
			</div>
			<p className="text-muted-foreground text-xs">
				Added
				{credential.created_by ? (
					<>
						{' '}
						by{' '}
						<ActorLabel actorId={credential.created_by} className="text-foreground" />
					</>
				) : null}
				{addedAgo ? <> · {addedAgo}</> : null}
			</p>
			<div className="text-xs">
				<CredentialBindings credentialId={credential.credential_id} />
			</div>
		</li>
	);
}

function CredentialsCard({ api }: { api: WorkspaceApi }) {
	const credentials = useWorkspaceCredentials();
	const items = credentials.forApi(api.api);
	const needed = api.securitySchemes.length > 0;

	let body: React.ReactNode;
	if (credentials.error) {
		body = (
			<div className="p-4">
				<ErrorAlert message="Credentials could not be loaded." />
			</div>
		);
	} else if (!credentials.complete) {
		body = <ListSkeleton />;
	} else if (items.length === 0) {
		body = (
			<p className="text-muted-foreground px-4 py-6 text-center text-sm">
				{needed
					? 'No credential yet — agents calling this API will be refused until someone adds one.'
					: 'This API declares no authentication, so no credential is needed.'}
			</p>
		);
	} else {
		body = (
			<ul className="divide-border/60 divide-y">
				{items.map((credential) => (
					<CredentialItem key={credential.credential_id} credential={credential} />
				))}
			</ul>
		);
	}

	return (
		<Card className="overflow-hidden p-0" data-testid="access-credentials">
			<SectionHeader
				icon={<KeyRound size={16} aria-hidden="true" />}
				title="Credentials"
				description="Secrets the gateway injects when an agent calls this API. Shared by everyone in the workspace."
				link={{
					href: ROUTE_PATHS.credentialInventory({ create: needed && items.length === 0 }),
					label: needed && items.length === 0 ? 'Add a credential' : 'Manage credentials',
				}}
			/>
			{body}
		</Card>
	);
}

function CallCounts({ row }: { row: UsageRow }) {
	return (
		<span className="text-foreground shrink-0 font-mono text-xs">
			{row.total.toLocaleString()} call{row.total === 1 ? '' : 's'}
			{row.failed > 0 ? (
				<span className="text-danger"> · {row.failed.toLocaleString()} failed</span>
			) : null}
		</span>
	);
}

/**
 * One caller. Usage is grouped by actor, so a person testing from the
 * console shows up alongside agents; only agents link to the Agents page.
 */
function CallerItem({ row }: { row: UsageRow }) {
	const caller = parseUsageCaller(row.key);
	const rowClass = 'flex items-center gap-3 px-4 py-2.5';
	if (!caller) {
		return (
			<li className={rowClass} data-testid="access-agent">
				<span className="text-muted-foreground min-w-0 flex-1 text-sm">Unattributed</span>
				<CallCounts row={row} />
			</li>
		);
	}
	const body = (
		<>
			<AgentBadge
				id={caller.actorId}
				kind={caller.actorType === 'agent' ? 'Agent' : 'User'}
				size="sm"
			/>
			<span className="text-foreground min-w-0 flex-1 truncate text-sm">
				<ActorLabel actorId={caller.actorId} actorType={caller.actorType} />
			</span>
			<CallCounts row={row} />
		</>
	);
	return (
		<li data-testid="access-agent">
			{caller.actorType === 'agent' ? (
				<AppLink
					href={ROUTE_PATHS.agentTab(caller.actorId)}
					className={`${rowClass} hover:bg-muted/40 transition-colors`}
				>
					{body}
				</AppLink>
			) : (
				<div className={rowClass}>{body}</div>
			)}
		</li>
	);
}

function CallingAgentsCard({ apiKey }: { apiKey: ApiKey }) {
	const traffic = useApiAgentTraffic(apiKey);

	let body: React.ReactNode;
	if (traffic.isError) {
		body = (
			<div className="p-4">
				<ErrorAlert message="Usage could not be loaded." />
			</div>
		);
	} else if (traffic.isPending) {
		body = <ListSkeleton />;
	} else if (traffic.data.length === 0) {
		body = (
			<EmptyState
				icon={<Bot size={28} aria-hidden="true" />}
				title={`No calls in the last ${USAGE_WINDOW_DAYS} days`}
				description="Agents granted this API show up here once they call it."
			/>
		);
	} else {
		body = (
			<ul className="divide-border/60 divide-y">
				{traffic.data.map((row) => (
					<CallerItem key={row.key || '__unattributed__'} row={row} />
				))}
			</ul>
		);
	}

	return (
		<Card className="overflow-hidden p-0" data-testid="access-agents">
			<SectionHeader
				icon={<Bot size={16} aria-hidden="true" />}
				title={`Callers · last ${USAGE_WINDOW_DAYS} days`}
				description="Who depends on this API right now — check here before changing or removing it."
			/>
			{body}
		</Card>
	);
}

export function AccessSection({ api }: { api: WorkspaceApi }) {
	return (
		<div className="grid gap-4 lg:grid-cols-2" data-testid="access-section">
			<CredentialsCard api={api} />
			<CallingAgentsCard apiKey={api.api} />
		</div>
	);
}
