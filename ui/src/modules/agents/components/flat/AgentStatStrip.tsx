/**
 * AgentStatStrip — the selected agent's vitals as the agent card's KPI row:
 * APIs, operations reachable, credentials, calls in 7d (with a daily
 * sparkline), success rate and last used. Per figure: `undefined` renders a
 * skeleton, `null` (gated or failed) OMITS the cell, and nothing provable
 * renders no row at all.
 */
import { ShieldOff } from 'lucide-react';
import type { ReactNode } from 'react';
import { AreaSparkline, SectionLabel, Skeleton, Tooltip } from '@/shared/ui';
import { cn } from '@/shared/lib/utils';
import { ago } from '@/modules/agents/lib/ago';
import type { ActorUsageDetail } from '@/modules/agents/api';
import { successShare } from '@/modules/agents/components/detail/shared';
import type { ApiTileStats } from '@/modules/agents/lib/apiTiles';
import { dailyCallSeries } from '@/modules/agents/lib/usageSeries';

interface AgentStatStripProps {
	/** The agent named in the row's accessible label. */
	agentName: string;
	/** Distinct APIs the agent reaches (one per API, however many credentials). */
	apiCount: number | null | undefined;
	/** Tile-composition stats (same math as the rows): `undefined` while the join
	 * loads or drains, `null` when it failed — the figure drops rather than print a
	 * wrong number. */
	access: ApiTileStats | null | undefined;
	/** Bound-credential count off the already-fetched bindings list. */
	credentialCount: number | null | undefined;
	/** 7-day usage rollup; `null` = admin-gated (403) or failed → omitted. */
	usage: ActorUsageDetail | null | undefined;
	/** Most-recent-execution timestamp: `{ at: null }` means the feed loaded empty
	 * (em-dash); `null` means the feed is gated or failed (omit). */
	lastActivity: { at: string | null } | null | undefined;
}

/** One KPI cell. `value: undefined` renders the skeleton. */
interface Kpi {
	key: string;
	label: string;
	/** The label's full form, when the cell prints a shorter one. */
	title?: string;
	value: string | undefined;
	/** A quiet note beside the figure ("1 blocked"). */
	note?: ReactNode;
	/** A decorative chart right of the figure (the 7-day sparkline). */
	chart?: ReactNode;
	tone?: 'danger';
}

/** The blocked sub-count: a ShieldOff and the words, in the note's quiet tone.
 * Focusable, so the explanation reaches keyboard users too. */
function BlockedNote({ testId, label, hint }: { testId: string; label: string; hint: string }) {
	return (
		<Tooltip content={hint} placement="bottom">
			<span data-testid={testId} className="inline-flex items-center gap-1">
				<ShieldOff aria-hidden="true" className="h-3 w-3" />
				{label}
			</span>
		</Tooltip>
	);
}

export function AgentStatStrip({
	agentName,
	apiCount,
	access,
	credentialCount,
	usage,
	lastActivity,
}: AgentStatStripProps) {
	const kpis: Kpi[] = [];

	// Setup figures — the rows' own math. The notes appear only when the drained
	// join PROVES a gap; words stay neutral, set apart by weight, not hue.
	if (apiCount !== null && access !== null) {
		const notes: ReactNode[] = [];
		if (access && access.needsSetup > 0) {
			notes.push(
				<span key="setup" data-testid="stat-needs-setup">
					{access.needsSetup} to set up
				</span>,
			);
		}
		// Blocked is a credential fact (rules live on the binding) and sits under
		// Credentials; the APIs cell names only an API none of whose credentials
		// lets a call through.
		if (access && access.fullyBlockedApis > 0) {
			notes.push(
				<BlockedNote
					key="blocked"
					testId="stat-apis-blocked"
					label={`${access.fullyBlockedApis} fully blocked`}
					hint={`${access.fullyBlockedApis} ${access.fullyBlockedApis === 1 ? 'API has' : 'APIs have'} no credential with a rule that lets a call through`}
				/>,
			);
		}
		kpis.push({
			key: 'apis',
			label: 'APIs',
			value: apiCount === undefined || !access ? undefined : apiCount.toLocaleString(),
			note: notes.length > 0 ? notes : undefined,
		});
	}
	// "reachable" is load-bearing: the figure excludes paused and blocked
	// bindings. A partly provable count takes the `+` that says the sum is only
	// a floor; a row still checking its rules holds the cell on a skeleton, and
	// one that can't read them ("Status unavailable") claims nothing.
	if (access !== null && (!access || access.operationsChecking || access.operations !== null)) {
		kpis.push({
			key: 'operations',
			label: 'Operations',
			title: 'Operations reachable',
			value:
				access && !access.operationsChecking && access.operations !== null
					? `${access.operations.toLocaleString()}${access.operationsAtLeast ? '+' : ''}`
					: undefined,
		});
	}
	if (credentialCount !== null) {
		// Blocked credentials (no rules / all denied) reach nothing — named, so the
		// reachable figure beside them doesn't read as a contradiction.
		const blocked = access ? access.blockedBindings : 0;
		kpis.push({
			key: 'credentials',
			label: 'Credentials',
			value: credentialCount === undefined ? undefined : credentialCount.toLocaleString(),
			note:
				blocked > 0 ? (
					<BlockedNote
						testId="stat-credentials-blocked"
						label={`${blocked} blocked`}
						hint={`${blocked} ${blocked === 1 ? 'credential has' : 'credentials have'} no rule that lets a call through`}
					/>
				) : undefined,
		});
	}

	// Activity figures — the 7-day usage rollup and the newest execution.
	if (usage !== null) {
		const series = usage ? dailyCallSeries(usage.buckets ?? [], Date.now()) : null;
		kpis.push({
			key: 'executions',
			label: 'Calls · 7d',
			value: usage && usage.total.toLocaleString(),
			// No traffic draws the flat baseline, in the muted tone of a zero.
			chart: series && (
				<AreaSparkline
					data={series}
					className={cn(
						'hidden h-5 max-w-24 min-w-8 flex-1 self-center sm:block',
						usage && usage.total > 0 ? 'text-primary' : 'text-foreground-faint',
					)}
				/>
			),
		});
		const unhealthy = usage ? usage.total > 0 && usage.success / usage.total < 0.95 : false;
		kpis.push({
			key: 'success-rate',
			label: 'Success',
			title: 'Success rate',
			// A zero-traffic rate has nothing to judge: an em-dash.
			value: usage ? successShare(usage.success, usage.total) : undefined,
			tone: unhealthy ? 'danger' : undefined,
		});
	}
	if (lastActivity !== null) {
		kpis.push({
			key: 'last-activity',
			label: 'Last used',
			value:
				lastActivity === undefined
					? undefined
					: lastActivity.at
						? ago(lastActivity.at)
						: '—',
		});
	}

	if (kpis.length === 0) return null;

	return (
		<dl
			aria-label={`${agentName} stats`}
			data-testid="agent-stat-strip"
			className="border-hairline-field grid grid-cols-2 border-t sm:grid-cols-3 lg:auto-cols-fr lg:grid-flow-col lg:grid-cols-none"
		>
			{kpis.map((kpi) => {
				const empty = kpi.value === '—' || kpi.value === '0';
				return (
					<div
						key={kpi.key}
						data-testid={`stat-${kpi.key}`}
						className="min-w-0 px-5 pt-3 pb-3.5 not-first:shadow-[inset_1px_0_0_var(--color-hairline)]"
					>
						<SectionLabel as="dt" className="whitespace-nowrap">
							{kpi.title ? (
								<Tooltip content={kpi.title} placement="bottom">
									{kpi.label}
								</Tooltip>
							) : (
								kpi.label
							)}
						</SectionLabel>
						<dd
							className={cn(
								'font-heading mt-1 flex items-baseline gap-2 text-xl leading-6 font-semibold whitespace-nowrap tabular-nums',
								kpi.tone === 'danger'
									? 'text-danger'
									: empty
										? 'text-foreground-faint'
										: 'text-foreground-name',
							)}
						>
							{kpi.value === undefined ? (
								<Skeleton className="h-5 w-12" />
							) : (
								<span data-testid={`stat-${kpi.key}-value`}>{kpi.value}</span>
							)}
							{kpi.note && (
								<span className="text-foreground-sub flex min-w-0 items-center gap-2 font-sans text-[11.5px] font-semibold">
									{kpi.note}
								</span>
							)}
							{kpi.chart}
						</dd>
					</div>
				);
			})}
		</dl>
	);
}
