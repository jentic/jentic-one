/**
 * ApiCard — one workspace API as a clickable tile.
 *
 * The whole card is a router link to the API's hub, with a subtle hover lift.
 * Hierarchy, top to bottom:
 *
 *   title row     name + Live/Draft (`current_revision_id`) + Update available
 *                 (`update_available`) via the shared `ApiStateBadge`, chevron
 *   identity row  vendor/name/version (mono)
 *   description   (when the spec has one)
 *   metrics row   operations · revisions · security schemes · agents with access
 *   usage row     7-day calls, failed count, sparkline (org:admin only)
 *   warning row   "Credential missing" when the API needs auth and — with every
 *                 credential page loaded — no credential targets it
 *
 * The health rows come from `healthIndex` (`useApiHealthIndex`, mounted once by
 * the grid over already-shared queries — no per-card fetch) and `agents` (the
 * grid's agent reads for the cards on screen). Values still
 * loading render same-size skeletons so tiles don't jump; the usage row is
 * absent for users who can't read usage. Without an index (e.g. isolated
 * renders) the card shows only its own registry fields.
 */
import { AlertTriangle, Bot, ChevronRight, GitBranch, ShieldCheck, Zap } from 'lucide-react';
import { ApiStateBadges, ApiUsageSummary, AppLink, Skeleton, VendorIcon } from '@/shared/ui';
import {
	callsInWeek,
	isCredentialMissing,
	workspaceApiDisplayTitle,
	type AgentFigure,
	type ApiHealthIndex,
	type WorkspaceApi,
} from '@/modules/workspace/api';
import { ROUTE_PATHS } from '@/shared/app/routes';
import { vendorIconPropsFor } from '@/shared/lib';

function plural(n: number, noun: string): string {
	return `${n.toLocaleString()} ${noun}${n === 1 ? '' : 's'}`;
}

function UsageRow({ api, healthIndex }: { api: WorkspaceApi; healthIndex: ApiHealthIndex }) {
	if (healthIndex.usageLoading) {
		return <Skeleton className="h-5 w-full" data-testid="workspace-api-card-usage-loading" />;
	}
	if (!healthIndex.usageAvailable) return null;
	const { usage } = healthIndex.healthFor(api.api);
	const calls = callsInWeek(usage, healthIndex.usageExhaustive);
	// Outside a capped top-N list: unknown, not zero — say nothing.
	if (calls == null) return null;
	return (
		<ApiUsageSummary
			size="row"
			calls={calls}
			failed={usage?.failed}
			trend={usage?.trend}
			testId="workspace-api-card-usage"
			failuresTestId="workspace-api-card-failures"
		/>
	);
}

export function ApiCard({
	api,
	healthIndex,
	agents,
}: {
	api: WorkspaceApi;
	/** Shared per-API health (usage, credentials); omitted ⇒ registry fields only. */
	healthIndex?: ApiHealthIndex;
	/** Agents with access (read for the cards on screen); omitted ⇒ not shown. */
	agents?: AgentFigure;
}) {
	const title = workspaceApiDisplayTitle(api);
	const health = healthIndex?.healthFor(api.api) ?? null;
	const credentialMissing =
		health != null &&
		isCredentialMissing(api.securitySchemes.length > 0, health.credentialCount);

	return (
		<AppLink
			href={ROUTE_PATHS.workspaceApiHub(api.api)}
			data-testid="workspace-api-card"
			aria-label={`Open ${title}`}
			className="group border-border/60 bg-card hover:border-border hover:bg-muted/30 focus-visible:ring-primary/40 flex h-full w-full min-w-0 flex-col gap-3 overflow-hidden rounded-xl border p-4 text-left transition-all hover:-translate-y-0.5 hover:shadow-sm focus-visible:ring-2 focus-visible:outline-none"
		>
			<div className="flex items-start gap-3">
				<VendorIcon
					{...vendorIconPropsFor({ title, ...api.api, iconUrl: api.iconUrl })}
					size="lg"
				/>
				<div className="min-w-0 flex-1">
					<div className="flex items-center justify-between gap-2">
						<h3 className="text-foreground min-w-0 flex-1 truncate text-sm font-semibold">
							{title}
						</h3>
						<div className="flex shrink-0 items-center gap-1.5">
							<ApiStateBadges
								currentRevisionId={api.currentRevisionId}
								updateAvailable={api.updateAvailable}
								className="px-1.5 py-0 text-[10px]"
							/>
							<ChevronRight
								size={16}
								aria-hidden="true"
								className="text-muted-foreground group-hover:text-foreground transition-colors"
							/>
						</div>
					</div>
					<p className="text-muted-foreground mt-0.5 truncate font-mono text-xs">
						{api.api.vendor}/{api.api.name}/{api.api.version}
					</p>
					{api.description ? (
						<p className="text-muted-foreground mt-1.5 line-clamp-2 text-xs leading-snug break-words">
							{api.description}
						</p>
					) : null}
				</div>
			</div>

			<div className="mt-auto flex flex-col gap-2">
				<div
					className="text-muted-foreground flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px]"
					data-testid="workspace-api-card-metrics"
				>
					<span className="inline-flex items-center gap-1">
						<Zap size={11} aria-hidden="true" />
						{plural(api.operationCount, 'op')}
					</span>
					<span className="inline-flex items-center gap-1">
						<GitBranch size={11} aria-hidden="true" />
						{plural(api.revisionCount, 'revision')}
					</span>
					{api.securitySchemes.length > 0 ? (
						<span className="inline-flex items-center gap-1">
							<ShieldCheck size={11} aria-hidden="true" />
							{plural(api.securitySchemes.length, 'scheme')}
						</span>
					) : null}
					{agents ? (
						agents.agentsLoading ? (
							<Skeleton
								className="h-3 w-14"
								data-testid="workspace-api-card-agents-loading"
							/>
						) : (
							<span
								className="inline-flex items-center gap-1"
								title={
									agents.agentCount == null
										? 'Couldn’t read every credential’s bound agents'
										: agents.agentsAtLeast
											? 'At least this many agents are bound to a credential for this API'
											: 'Agents bound to a credential for this API'
								}
								data-testid="workspace-api-card-agents"
							>
								<Bot size={11} aria-hidden="true" />
								{agents.agentCount == null
									? '— agents'
									: agents.agentsAtLeast
										? `${agents.agentCount.toLocaleString()}+ agents`
										: plural(agents.agentCount, 'agent')}
							</span>
						)
					) : null}
				</div>

				{healthIndex ? <UsageRow api={api} healthIndex={healthIndex} /> : null}

				{credentialMissing ? (
					<p
						className="border-warning/30 bg-warning/10 text-warning flex items-center gap-1.5 rounded-md border px-2 py-1 text-xs"
						data-testid="workspace-api-card-credential-missing"
					>
						<AlertTriangle size={12} className="shrink-0" aria-hidden="true" />
						No credential — agents can’t call it
					</p>
				) : null}
			</div>
		</AppLink>
	);
}
