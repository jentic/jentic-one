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
 *                       binds an existing agent in place, with its access
 *                       rules (`BindAgentDialog`). Each bound agent shows
 *                       whether its rules let any call through (Blocked when
 *                       not). Whether a credential is needed at all comes
 *                       from the live spec's required security, not just the
 *                       declared schemes (`useApiAuthRequirement`); an API
 *                       with no schemes offers no Add credential. A live API
 *                       that needs none offers "Give an agent access": the
 *                       broker still resolves every call through a bound
 *                       credential, so the dialog binds through the API's
 *                       `no_auth` credential (created on confirm when there
 *                       is none). A draft API (no live revision) has no
 *                       declared security yet, so it claims nothing about
 *                       credentials and offers no access until promoted
 *   - Calls, 7 days   — `GET /monitoring/usage?group_by=api` (org:admin only;
 *                       hidden otherwise)
 *   - Notes           — `GET /notes?api=vendor:name:version`
 *   - Recent activity — this API's events off the shell's live stream
 *                       (events that carry its vendor/name/version tokens)
 *
 * Each block renders only what its read returned; nothing is defaulted.
 */
import { useCallback, useMemo, useState, type ReactNode } from 'react';
import { useSearchParams } from 'react-router';
import {
	Activity,
	Bot,
	ChevronRight,
	KeyRound,
	Link2,
	LockOpen,
	NotebookPen,
	PauseCircle,
	Plus,
	ShieldOff,
} from 'lucide-react';
import {
	AppLink,
	Button,
	Card,
	CardBody,
	CardHeader,
	CardTitle,
	ErrorAlert,
	Skeleton,
	ApiUsageSummary,
	StatusText,
	StreamEventRow,
	Tag,
} from '@/shared/ui';
import { ROUTE_PATHS } from '@/shared/app/routes';
import { useAgentStreamOptional } from '@/shared/lib';
import { timeAgo } from '@/shared/lib/utils';
import { CredentialType, useCreateCredential, type Credential } from '@/shared/credentials/api';
import { initialApiFor } from '@/shared/credentials/lib/initialApiFor';
import { schemeTypeLabel } from '@/shared/credentials/lib/schemes';
import { credentialSiblingHint } from '@/shared/credentials/lib/credentialIdentity';
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
	useApiAuthRequirement,
	useApiNotes,
	useApiUsageWeek,
} from '@/modules/workspace/api';
import type { ApiKey, WorkspaceApi } from '@/modules/workspace/api';
import { useOptionalCurrentUser } from '@/shared/auth';
import {
	credentialsBindableBy,
	useCanBindAgents,
	useCanCreateCredentials,
} from '@/shared/credentials/lib/bindAuthority';
import { BindAgentDialog } from '@/modules/workspace/components/BindAgentDialog';
import {
	useBindingAccessStates,
	type BindingAccessState,
} from '@/shared/credentials/api/vendors-hooks';

/** "Bearer Token", "Bearer Token and API Key" — or "security schemes" when none are listed. */
function schemeList(schemes: readonly string[]): string {
	const labels = schemes.map(schemeTypeLabel);
	if (labels.length === 0) return 'security schemes';
	if (labels.length === 1) return labels[0];
	return `${labels.slice(0, -1).join(', ')} and ${labels[labels.length - 1]}`;
}

function HubCard({
	title,
	icon,
	action,
	children,
	testId,
	className,
}: {
	title: string;
	icon: React.ReactNode;
	action?: React.ReactNode;
	children: React.ReactNode;
	testId?: string;
	className?: string;
}) {
	return (
		<Card data-testid={testId} className={className}>
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

/**
 * A bound agent's access at a glance: nothing while its rules let some call
 * through, "Blocked" (with the reason) when none do, a neutral
 * "checking" / "status unknown" while that isn't known.
 */
function AgentAccessMarker({ state }: { state: BindingAccessState }): ReactNode {
	if (state === 'open') return null;
	if (state === 'blocked') {
		return (
			<span
				className="text-foreground-sub inline-flex items-center gap-0.5 text-[11px]"
				data-testid="hub-access-agent-blocked"
				title="No allow rule — every call is denied. Add access rules on the agent's page."
			>
				<ShieldOff className="text-caution h-3 w-3" aria-hidden="true" />
				Blocked
			</span>
		);
	}
	return (
		<span className="text-muted-foreground text-[11px]" data-testid="hub-access-agent-checking">
			{state === 'loading' ? '· checking…' : '· status unknown'}
		</span>
	);
}

/** The card's one "Bind to an agent" action (beside the agent chips, or in their empty state). */
function BindAgentButton({ onClick }: { onClick: () => void }) {
	return (
		<Button variant="tonal" size="xs" onClick={onClick} data-testid="hub-access-bind-agent">
			<Link2 size={12} aria-hidden="true" />
			Bind to an agent
		</Button>
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
	// Required-vs-declared comes from the resolved spec (see
	// `useApiAuthRequirement`); tiles and the docked panel only see declared.
	const auth = useApiAuthRequirement(api);
	const needsAuth = auth.requirement === 'required';
	// Nothing to configure: a credential form for an API with no schemes has
	// no scheme to fill in.
	const canAddCredential = auth.requirement !== 'none';

	// The Add credential flow opens here, on this API's form — the hub already
	// knows the API, so asking for it again would be redundant. `?credential=new`
	// is the open state, so the docked panel's "no credential" links land
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
	const initialApi = useMemo(
		() =>
			initialApiFor({
				ref: api.api,
				catalogApiId: api.catalogApiId,
				securitySchemes: api.securitySchemes,
				label: workspaceApiDisplayTitle(api),
				hasLiveRevision: api.currentRevisionId != null,
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
	// The credential the dialog opens on (set by "Give an agent access").
	const [bindCredentialId, setBindCredentialId] = useState<string | null>(null);

	// "Give an agent access" — for a live API that needs no credential. The
	// broker resolves every call through a bound credential regardless, so the
	// bind dialog uses a `no_auth` credential the viewer can bind — or, when
	// there is none, creates one (no secret, for any version) only once Bind
	// is confirmed, so cancelling leaves nothing behind.
	const viewer = useOptionalCurrentUser();
	const canCreate = useCanCreateCredentials();
	const createCredential = useCreateCredential();
	const reusableNoAuth = useMemo(
		() =>
			credentialsBindableBy(entry?.credentials ?? [], viewer).find(
				(c) => c.type === CredentialType.NO_AUTH,
			) ?? null,
		[entry, viewer],
	);
	// A draft (no live revision) declares no security yet, so "needs none"
	// can't be told from "not promoted yet": no claim, no access offered.
	const isDraft = api.currentRevisionId == null;
	const noAuthRequirement =
		!isDraft && (auth.requirement === 'none' || auth.requirement === 'optional');
	const offerNoAuthAccess = noAuthRequirement && canBind && (reusableNoAuth != null || canCreate);
	const [bindVia, setBindVia] = useState<'any' | 'no-auth'>('any');
	const openBind = (): void => {
		// On an API that needs no credential, open on its no-auth credential.
		setBindVia('any');
		setBindCredentialId(noAuthRequirement ? (reusableNoAuth?.credential_id ?? null) : null);
		setBindOpen(true);
	};
	const giveAgentAccess = (): void => {
		setBindVia('no-auth');
		setBindCredentialId(reusableNoAuth?.credential_id ?? null);
		setBindOpen(true);
	};
	const noAuthName = `${workspaceApiDisplayTitle(api)} (no auth)`;
	const createNoAuth = useMemo(
		() =>
			canCreate
				? {
						name: noAuthName,
						create: async (): Promise<Credential> => {
							const created = await createCredential.mutateAsync({
								type: CredentialType.NO_AUTH,
								name: noAuthName,
								provider: 'static',
								api: {
									vendor: api.api.vendor,
									name: api.api.name,
									// No version: the backend's wildcard (covers every revision).
									catalog_api_id: api.catalogApiId ?? undefined,
								},
							});
							return created.credential;
						},
					}
				: null,
		[canCreate, noAuthName, createCredential, api],
	);
	// What the dialog binds through: the no-auth credential (or the promise of
	// one) for "Give an agent access"; otherwise every covering credential.
	const bindCredentials =
		bindVia === 'no-auth'
			? reusableNoAuth
				? [reusableNoAuth]
				: []
			: (entry?.credentials ?? []);
	const apiReference = api.api.version
		? { vendor: api.api.vendor, name: api.api.name, version: api.api.version }
		: null;

	// Whether each bound agent can actually call anything: a binding with no
	// allow rule is default-deny, so it reads Blocked, not just "bound". The
	// binding's `ruleSetId` rides along: a governed binding is judged by its
	// SET's rules, which is what the broker evaluates.
	const bindingPairs = useMemo(
		() =>
			(agentAccess?.bindings ?? []).map((b) => ({
				agentId: b.agentId,
				credentialId: b.credentialId,
				ruleSetId: b.ruleSetId,
			})),
		// eslint-disable-next-line react-hooks/exhaustive-deps -- keyed on content
		[JSON.stringify(agentAccess?.bindings ?? [])],
	);
	const accessStates = useBindingAccessStates(bindingPairs);
	const agentState = (agentId: string): BindingAccessState => {
		let state: BindingAccessState | null = null;
		for (const b of agentAccess?.bindings ?? []) {
			if (b.agentId !== agentId || b.suspended) continue;
			const s = accessStates.get(`${b.agentId}\n${b.credentialId}`) ?? 'loading';
			// Any open binding is enough; otherwise the least-known state wins.
			if (s === 'open') return 'open';
			if (state == null || s === 'loading' || (s === 'unknown' && state === 'blocked'))
				state = s;
		}
		return state ?? 'open';
	};

	return (
		<>
			<HubCard
				title="Who can use it"
				icon={<Bot className="h-4 w-4" />}
				testId="hub-access"
				action={
					canAddCredential ? (
						<Button
							variant="tonal"
							size="xs"
							onClick={(): void => setCreateOpen(true)}
							data-testid="hub-access-add-credential"
						>
							<Plus size={12} aria-hidden="true" />
							Add credential
						</Button>
					) : undefined
				}
			>
				{access.error && !access.credentialsComplete ? (
					<ErrorAlert message={access.error} onRetry={access.retry} />
				) : access.isPending ? (
					<Skeleton className="h-12 w-full" />
				) : !entry ? (
					!access.credentialsComplete || auth.pending ? (
						<Skeleton className="h-12 w-full" />
					) : isDraft ? (
						<p className="text-muted-foreground text-sm" data-testid="hub-access-draft">
							This API is a draft — nothing is live yet. Promote a revision to see
							what it needs to be called, then give agents access.
						</p>
					) : needsAuth ? (
						<p className="text-muted-foreground text-sm" data-testid="hub-access-none">
							No active credential covers this API yet, so no agent can call it. Add
							one, then bind it to an agent.
						</p>
					) : (
						<div
							className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between md:gap-6"
							data-testid="hub-access-no-auth"
						>
							<div className="min-w-0 space-y-1">
								<p className="text-foreground flex items-center gap-1.5 text-sm font-medium">
									<LockOpen
										className="text-muted-foreground h-3.5 w-3.5 shrink-0"
										aria-hidden="true"
									/>
									{auth.requirement === 'optional'
										? 'Credential optional'
										: 'No credential needed'}
								</p>
								<p className="text-muted-foreground text-sm">
									{auth.requirement === 'optional'
										? `It declares ${schemeList(api.securitySchemes)}, but no operation requires it. No credential is bound yet, so no agent can call it.`
										: 'It doesn’t use authentication.'}{' '}
									{offerNoAuthAccess
										? 'Give an agent access to let it call this API — no secret to set up.'
										: 'An agent still needs access bound to it before it can call this API.'}
								</p>
							</div>
							{offerNoAuthAccess && (
								<div className="shrink-0">
									<Button
										variant="tonal"
										size="xs"
										onClick={giveAgentAccess}
										data-testid="hub-access-give-agent-access"
									>
										<Link2 size={14} aria-hidden="true" />
										Give an agent access
									</Button>
								</div>
							)}
						</div>
					)
				) : (
					<div
						className="grid grid-cols-1 gap-4 md:grid-cols-2 md:gap-6"
						data-testid="hub-access-columns"
					>
						<div className="min-w-0">
							<div className="mb-1 flex items-center justify-between gap-2">
								<h3 className="font-heading text-foreground text-sm font-semibold">
									Agents
								</h3>
								{canBind && agents.length > 0 && (
									<BindAgentButton onClick={openBind} />
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
										<BindAgentButton onClick={openBind} />
									)}
								</div>
							) : (
								<ul className="flex flex-wrap gap-1.5">
									{agents.map((a) => (
										<li key={a.agent_id}>
											<AppLink
												href={ROUTE_PATHS.agentTab(a.agent_id)}
												className="bg-surface-field hover:bg-surface-chip-active text-foreground-name focus-visible:ring-ring inline-flex items-center gap-1 rounded-md px-2 py-1 text-xs font-medium focus-visible:ring-2 focus-visible:outline-none"
												data-testid="hub-access-agent"
											>
												<Bot className="h-3 w-3" aria-hidden="true" />
												{a.agent_name}
												{a.suspended ? (
													<>
														<PauseCircle
															className="text-caution h-3 w-3"
															aria-hidden="true"
														/>
														<span className="sr-only">(suspended)</span>
													</>
												) : (
													<AgentAccessMarker
														state={agentState(a.agent_id)}
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
									{agentAccess?.agentsError
										? 'Some credentials’ agents couldn’t be read — this list may be incomplete.'
										: 'More agents are bound than shown — this list is the first page of each credential’s agents.'}
								</p>
							)}
						</div>
						<div className="min-w-0">
							<h3 className="font-heading text-foreground mb-1 text-sm font-semibold">
								Credentials
							</h3>
							<ul className="-mx-1.5 space-y-0.5">
								{entry.credentials.map((c) => {
									const hint = credentialSiblingHint(c, entry.credentials);
									return (
										<li key={c.credential_id}>
											{/* The whole row opens the credential's details — the shared
										    edit sheet every other credential surface uses — in place. */}
											<Button
												variant="ghost"
												fullWidth
												onClick={(): void =>
													openCredential(c.credential_id)
												}
												data-testid="hub-access-credential"
												className="group justify-between gap-2 rounded-md px-1.5 py-1 text-left font-normal active:scale-100"
											>
												<span className="text-foreground flex min-w-0 items-center gap-1.5">
													<span className="sr-only">View </span>
													<KeyRound
														className="text-muted-foreground h-3.5 w-3.5 shrink-0"
														aria-hidden="true"
													/>
													<span className="truncate">{c.name}</span>
													{hint && (
														<span
															className="text-muted-foreground shrink-0 font-mono text-[11px]"
															data-testid="hub-access-credential-hint"
														>
															{hint}
														</span>
													)}
												</span>
												<span className="flex shrink-0 items-center gap-1.5">
													{credentialIsConnected(c) && (
														<StatusText tone="success">
															Connected
														</StatusText>
													)}
													{credentialIsPendingSignIn(c) && (
														<StatusText tone="warning">
															Pending sign-in
														</StatusText>
													)}
													<CredentialTypeBadge credential={c} />
													<span
														className="text-muted-foreground group-hover:text-foreground inline-flex items-center text-xs"
														aria-hidden="true"
													>
														View
														<ChevronRight
															className="h-3.5 w-3.5"
															aria-hidden="true"
														/>
													</span>
												</span>
											</Button>
										</li>
									);
								})}
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
			{canBind && (bindCredentials.length > 0 || bindVia === 'no-auth') && (
				<BindAgentDialog
					open={bindOpen}
					onClose={(): void => setBindOpen(false)}
					credentials={bindCredentials}
					apiLabel={workspaceApiDisplayTitle(api)}
					apiReference={apiReference}
					initialCredentialId={bindCredentialId}
					createOnBind={bindVia === 'no-auth' ? createNoAuth : null}
				/>
			)}
		</>
	);
}

function UsageCard({ api, className }: { api: WorkspaceApi; className?: string }) {
	const usage = useApiUsageWeek();
	if (!usage.available && !usage.isLoading) return null;
	const row = usage.byApi.get(apiUsageKeyFor(api.api)) ?? null;
	const total = callsInWeek(row, usage.exhaustive);

	return (
		<HubCard
			title="Calls, last 7 days"
			icon={<Activity className="h-4 w-4" />}
			testId="hub-usage"
			className={className}
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
					Outside the top APIs by volume in the last 7 days — see Monitor for the
					breakdown.
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

function NotesCard({ apiKey, className }: { apiKey: ApiKey; className?: string }) {
	const notes = useApiNotes(apiKey);
	if (notes.isError) return null;
	const rows = notes.data?.items ?? [];
	return (
		<HubCard
			title="Notes"
			icon={<NotebookPen className="h-4 w-4" />}
			testId="hub-notes"
			className={className}
		>
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

/**
 * "Who can use it" spans the Overview (its Agents and Credentials side by side
 * on wide screens), then Notes | Calls as an equal-height pair, then Recent
 * activity. Narrow screens stack one column: Who can use it, Calls, Notes —
 * DOM order is the narrow reading order; Notes moves left on wide screens. A
 * pair that loses a card (Calls are admin-only; Notes hide on a failed read)
 * lets the survivor take the row.
 */
export function ApiHubOverview({ api }: { api: WorkspaceApi }) {
	return (
		<div className="space-y-4" data-testid="hub-overview-blocks">
			<AccessCard api={api} />
			<div
				className="grid grid-cols-1 items-stretch gap-4 lg:grid-cols-2"
				data-testid="hub-overview-pair"
			>
				<UsageCard api={api} className="lg:only:col-span-2" />
				<NotesCard apiKey={api.api} className="lg:order-first lg:only:col-span-2" />
			</div>
			<RecentActivityCard api={api} />
		</div>
	);
}
