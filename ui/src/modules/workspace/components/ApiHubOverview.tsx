/**
 * ApiHubOverview — the extra, real-data blocks on the API hub's Overview tab,
 * beside the existing OverviewStrip (update banner, host, stats, description):
 *
 *   - Who can use it  — active credentials covering this API (`GET /credentials`,
 *                       the shared `apiAccess` rule) and the agents bound to
 *                       them (`GET /credentials/{id}/agents`); each credential
 *                       row opens the shared `EditCredentialSheet` in place,
 *                       and its Add credential opens the shared flow in place,
 *                       already on this API's form (`?credential=new`);
 *                       "Bind to an agent" (one card-level action)
 *                       binds an existing agent in place (`BindAgentDialog`)
 *   - Calls, 7 days   — `GET /monitoring/usage?group_by=api` (org:admin only;
 *                       hidden otherwise)
 *   - Notes           — `GET /notes?api=vendor:name:version`
 *   - Recent activity — this API's events off the shell's live stream
 *                       (events that carry its vendor/name/version tokens)
 *
 * Each block renders only what its read returned; nothing is defaulted.
 */
import { useCallback, useMemo, useState } from 'react';
import { useSearchParams } from 'react-router';
import {
	Activity,
	Bot,
	ChevronRight,
	KeyRound,
	Link2,
	NotebookPen,
	PauseCircle,
	Plus,
} from 'lucide-react';
import {
	AppLink,
	Badge,
	Button,
	Card,
	CardBody,
	CardHeader,
	CardTitle,
	ErrorAlert,
	Skeleton,
	ApiUsageSummary,
	StreamEventRow,
	Tag,
} from '@/shared/ui';
import { ROUTE_PATHS } from '@/shared/app/routes';
import { useAgentStreamOptional } from '@/shared/lib';
import { timeAgo } from '@/shared/lib/utils';
import type { SelectedApi } from '@/shared/credentials/api';
import {
	CreateCredentialFlow,
	type CreatedCredentialInfo,
} from '@/shared/credentials/components/CreateCredentialFlow';
import { useConnectAfterCreate } from '@/shared/credentials/components/useConnectAfterCreate';
import { EditCredentialSheet } from '@/shared/credentials/components/EditCredentialSheet';
import { CredentialTypeBadge } from '@/shared/credentials/components/CredentialTypeBadge';
import {
	credentialIsConnected,
	credentialIsPendingSignIn,
} from '@/shared/credentials/components/CredentialCard';
import {
	agentsExhaustive,
	apiUsageKeyFor,
	callsInWeek,
	workspaceApiDisplayTitle,
	useAgentAccess,
	useApiAccessIndex,
	useApiNotes,
	useApiUsageWeek,
} from '@/modules/workspace/api';
import type { ApiKey, WorkspaceApi } from '@/modules/workspace/api';
import { useCanBindAgents } from '@/shared/credentials/lib/bindAuthority';
import { BindAgentDialog } from '@/modules/workspace/components/BindAgentDialog';

function HubCard({
	title,
	icon,
	action,
	children,
	testId,
}: {
	title: string;
	icon: React.ReactNode;
	action?: React.ReactNode;
	children: React.ReactNode;
	testId?: string;
}) {
	return (
		<Card data-testid={testId}>
			<CardHeader className="flex items-center justify-between gap-2 py-3">
				<CardTitle as="h2" className="flex items-center gap-2 text-base">
					<span className="text-muted-foreground" aria-hidden="true">
						{icon}
					</span>
					{title}
				</CardTitle>
				{action}
			</CardHeader>
			<CardBody className="py-3">{children}</CardBody>
		</Card>
	);
}

function AccessCard({ api }: { api: WorkspaceApi }) {
	const access = useApiAccessIndex();
	const entry = access.entryFor(api.api);
	const accessFor = useAgentAccess(entry ? [entry.credentials] : []);
	const agentAccess = entry ? accessFor(entry.credentials) : null;
	const agents = agentAccess?.agents ?? [];
	const agentsSettled = agentAccess?.agentsSettled ?? false;
	const agentsWhole = agentAccess != null && agentsExhaustive(agentAccess);
	const needsAuth = api.securitySchemes.length > 0;

	// The Add credential flow opens here, on this API's form — the hub already
	// knows the API, so asking for it again would be redundant. `?credential=new`
	// is the open state, so the docked panel's "no credential yet" links land
	// straight on the form too. Mounted once; the flow owns its reset.
	const [searchParams, setSearchParams] = useSearchParams();
	const createOpen = searchParams.get('credential') === 'new';
	const setCreateOpen = useCallback(
		(open: boolean): void =>
			setSearchParams(
				(prev) => {
					const next = new URLSearchParams(prev);
					if (open) next.set('credential', 'new');
					else next.delete('credential');
					return next;
				},
				{ replace: true },
			),
		[setSearchParams],
	);
	const initialApi = useMemo<SelectedApi>(
		() => ({
			source: 'local',
			vendor: api.api.vendor,
			name: api.api.name,
			version: api.api.version,
			apiId: api.catalogApiId ?? undefined,
			registered: true,
			securitySchemeTypes: api.securitySchemes,
			label: workspaceApiDisplayTitle(api),
		}),
		[api],
	);
	const { afterCreate, deviceDialog } = useConnectAfterCreate();
	// The credential whose details sheet is open. `sticky…` outlives the close
	// so the sheet keeps its content while it animates out.
	const [viewCredentialId, setViewCredentialId] = useState<string | null>(null);
	const [stickyCredentialId, setStickyCredentialId] = useState<string | null>(null);
	const openCredential = (id: string): void => {
		setStickyCredentialId(id);
		setViewCredentialId(id);
	};
	// "Bind to an agent" — one card-level action; the dialog picks the
	// credential. Mounted once, toggled.
	const canBind = useCanBindAgents();
	const [bindOpen, setBindOpen] = useState(false);
	const openBind = (): void => setBindOpen(true);

	return (
		<>
			<HubCard
				title="Who can use it"
				icon={<Bot className="h-4 w-4" />}
				testId="hub-access"
				action={
					<Button
						variant="ghost"
						size="sm"
						onClick={(): void => setCreateOpen(true)}
						className="text-primary hover:text-primary h-7 gap-1 px-2 text-xs"
						data-testid="hub-access-add-credential"
					>
						<Plus size={12} aria-hidden="true" />
						Add credential
					</Button>
				}
			>
				{access.error && !access.credentialsComplete ? (
					<ErrorAlert message={access.error} onRetry={access.retry} />
				) : access.isPending ? (
					<Skeleton className="h-12 w-full" />
				) : !entry ? (
					access.credentialsComplete ? (
						<p className="text-muted-foreground text-sm" data-testid="hub-access-none">
							{needsAuth
								? 'No active credential covers this API yet, so no agent can call it. Add one, then bind it to an agent.'
								: 'No active credential covers this API. It declares no security schemes, so none may be needed.'}
						</p>
					) : (
						<Skeleton className="h-12 w-full" />
					)
				) : (
					<div className="space-y-3">
						<div>
							<div className="mb-1 flex items-center justify-between gap-2">
								<p className="text-muted-foreground text-xs tracking-wider uppercase">
									Agents
								</p>
								{canBind && agents.length > 0 && (
									<Button
										variant="ghost"
										size="sm"
										className="h-6 px-1.5 text-xs"
										onClick={openBind}
										data-testid="hub-access-bind-agent"
									>
										<Link2 size={12} aria-hidden="true" />
										Bind to an agent
									</Button>
								)}
							</div>
							{agents.length === 0 ? (
								<div className="flex flex-wrap items-center justify-between gap-2">
									<p className="text-muted-foreground text-sm">
										{!agentsSettled
											? 'Loading…'
											: agentsWhole
												? 'No agent is bound to these credentials yet.'
												: 'Couldn’t read the agents bound to these credentials.'}
									</p>
									{canBind && agentsSettled && (
										<Button
											variant="outline"
											size="sm"
											onClick={openBind}
											data-testid="hub-access-bind-agent"
										>
											<Link2 size={14} aria-hidden="true" />
											Bind to an agent
										</Button>
									)}
								</div>
							) : (
								<ul className="flex flex-wrap gap-1.5">
									{agents.map((a) => (
										<li key={a.agent_id}>
											<AppLink
												href={ROUTE_PATHS.agentTab(a.agent_id)}
												className="bg-muted/60 hover:bg-muted text-foreground inline-flex items-center gap-1 rounded-md px-2 py-1 text-xs font-medium"
											>
												<Bot className="h-3 w-3" aria-hidden="true" />
												{a.agent_name}
												{a.suspended && (
													<PauseCircle
														className="text-warning h-3 w-3"
														aria-label="suspended"
													/>
												)}
											</AppLink>
										</li>
									))}
								</ul>
							)}
							{agents.length > 0 && agentsSettled && !agentsWhole && (
								<p
									className="text-muted-foreground mt-1 text-xs"
									data-testid="hub-access-agents-partial"
								>
									Some credentials’ agents couldn’t be read — this list may be
									incomplete.
								</p>
							)}
						</div>
						<div>
							<p className="text-muted-foreground mb-1 text-xs tracking-wider uppercase">
								Credentials
							</p>
							<ul className="-mx-1.5 space-y-0.5">
								{entry.credentials.map((c) => (
									<li key={c.credential_id}>
										{/* The whole row opens the credential's details — the shared
										    edit sheet every other credential surface uses — in place. */}
										<Button
											variant="ghost"
											fullWidth
											onClick={(): void => openCredential(c.credential_id)}
											aria-label={`View ${c.name}`}
											data-testid="hub-access-credential"
											className="group justify-between gap-2 rounded-md px-1.5 py-1 text-left font-normal active:scale-100"
										>
											<span className="text-foreground flex min-w-0 items-center gap-1.5">
												<KeyRound
													className="text-muted-foreground h-3.5 w-3.5 shrink-0"
													aria-hidden="true"
												/>
												<span className="truncate">{c.name}</span>
											</span>
											<span className="flex shrink-0 items-center gap-1.5">
												{credentialIsConnected(c) && (
													<Badge variant="success">Connected</Badge>
												)}
												{credentialIsPendingSignIn(c) && (
													<Badge variant="pending">Pending sign-in</Badge>
												)}
												<CredentialTypeBadge credential={c} />
												<span className="text-muted-foreground group-hover:text-foreground inline-flex items-center text-xs">
													View
													<ChevronRight
														className="h-3.5 w-3.5"
														aria-hidden="true"
													/>
												</span>
											</span>
										</Button>
									</li>
								))}
							</ul>
						</div>
					</div>
				)}
			</HubCard>
			<EditCredentialSheet
				credentialId={stickyCredentialId}
				open={viewCredentialId != null}
				onClose={(): void => setViewCredentialId(null)}
				// Kept through the close animation so the sheet doesn't blank mid-slide.
				onAfterClose={(): void => setStickyCredentialId(null)}
				// A bound-agent link leaves the hub for the Agents page.
				onNavigateAway={(): void => setViewCredentialId(null)}
			/>
			<CreateCredentialFlow
				open={createOpen}
				onClose={(): void => setCreateOpen(false)}
				initialApi={initialApi}
				onCreated={(info: CreatedCredentialInfo): void => {
					// The create invalidated the credentials slice, so this card, the
					// docked panel and the workspace tiles all refetch from it.
					setCreateOpen(false);
					afterCreate(info);
				}}
			/>
			{deviceDialog}
			{entry && canBind && (
				<BindAgentDialog
					open={bindOpen}
					onClose={(): void => setBindOpen(false)}
					credentials={entry.credentials}
					apiLabel={workspaceApiDisplayTitle(api)}
				/>
			)}
		</>
	);
}

function UsageCard({ api }: { api: WorkspaceApi }) {
	const usage = useApiUsageWeek();
	if (!usage.available && !usage.isLoading) return null;
	const row = usage.byApi.get(apiUsageKeyFor(api.api)) ?? null;
	const total = callsInWeek(row, usage.exhaustive);

	return (
		<HubCard
			title="Calls, last 7 days"
			icon={<Activity className="h-4 w-4" />}
			testId="hub-usage"
			action={
				<AppLink
					href={ROUTE_PATHS.monitorExecutions()}
					className="text-primary text-xs font-medium hover:underline"
				>
					Open Monitor →
				</AppLink>
			}
		>
			{usage.isLoading ? (
				<Skeleton className="h-10 w-full" />
			) : total == null ? (
				<p className="text-muted-foreground text-sm">
					Outside the top APIs by volume this week — see Monitor for the breakdown.
				</p>
			) : (
				<ApiUsageSummary
					size="large"
					calls={total}
					failed={row?.failed}
					trend={row?.trend}
				/>
			)}
		</HubCard>
	);
}

function NotesCard({ apiKey }: { apiKey: ApiKey }) {
	const notes = useApiNotes(apiKey);
	if (notes.isError) return null;
	const rows = notes.data?.items ?? [];
	return (
		<HubCard title="Notes" icon={<NotebookPen className="h-4 w-4" />} testId="hub-notes">
			{notes.isPending ? (
				<Skeleton className="h-10 w-full" />
			) : rows.length === 0 ? (
				<p className="text-muted-foreground text-sm">
					No notes on this API yet. Agents and operators can attach hints (auth quirks,
					usage tips, corrections) that show up here.
				</p>
			) : (
				<ul className="space-y-2.5">
					{rows.map((n) => (
						<li key={n.id} className="text-sm">
							<div className="mb-0.5 flex flex-wrap items-center gap-1.5">
								{n.type && <Tag>{n.type.replace(/_/g, ' ')}</Tag>}
								{n.confidence && (
									<span className="text-muted-foreground text-[11px]">
										{n.confidence} confidence
									</span>
								)}
								<span className="text-muted-foreground ml-auto text-[11px]">
									{timeAgo(n.createdAt)}
								</span>
							</div>
							<p className="text-foreground leading-snug whitespace-pre-line">
								{n.body}
							</p>
						</li>
					))}
				</ul>
			)}
		</HubCard>
	);
}

function RecentActivityCard({ api }: { api: WorkspaceApi }) {
	const stream = useAgentStreamOptional();
	const events = useMemo(
		() =>
			(stream?.events ?? [])
				.filter(
					(ev) =>
						(ev.tokens.vendor === api.api.vendor &&
							ev.tokens.name === api.api.name &&
							(!ev.tokens.version || ev.tokens.version === api.api.version)) ||
						(api.catalogApiId != null && ev.tokens.api_id === api.catalogApiId),
				)
				.slice(0, 6),
		[stream?.events, api.api.vendor, api.api.name, api.api.version, api.catalogApiId],
	);
	if (events.length === 0) return null;
	return (
		<HubCard
			title="Recent activity"
			icon={<Activity className="h-4 w-4" />}
			testId="hub-recent"
		>
			<ul className="space-y-1">
				{events.map((ev) => (
					<StreamEventRow key={ev.id} ev={ev} size="md" className="-mx-1.5" />
				))}
			</ul>
		</HubCard>
	);
}

export function ApiHubOverview({ api }: { api: WorkspaceApi }) {
	return (
		<div className="grid grid-cols-1 items-start gap-4 lg:grid-cols-2">
			<div className="space-y-4">
				<AccessCard api={api} />
				<UsageCard api={api} />
			</div>
			<div className="space-y-4">
				<NotesCard apiKey={api.api} />
				<RecentActivityCard api={api} />
			</div>
		</div>
	);
}
