/**
 * ApiRowReveal — what a "Can call" row adds while it is revealed: on hover
 * intent, on keyboard focus, or while pinned (a click or tap pins the row open
 * until a second one unpins it). Three equal columns on the resting row's own
 * grid (`apiRowGrid`), so their edges are the row's edges:
 *
 *   under name/host     Activity · 7d — the week's area chart, stats beneath
 *   under auth/cred     Access — the rules in brief and the operations they allow
 *   under metric/status Recent calls — the newest three
 *
 * then a slim footer across the width with the row's verbs at its right edge.
 * The credential's own facts stay in the access sheet; the row already names it.
 *
 * The three headings share one baseline and each column keeps its shape when
 * empty, so a quiet or blocked row grows by exactly what a busy one does.
 *
 * Mounted only while revealed, so the rule and operation reads it makes run
 * for the row in view, not for every row on the page. Every figure is a read
 * the app already makes; a missing read leaves its figure out.
 */
import { useMemo, type ReactNode } from 'react';
import { ArrowUpRight, PauseCircle, PlayCircle, Settings2 } from 'lucide-react';
import {
	AppLink,
	AreaSparkline,
	Button,
	MethodBadge,
	SectionLabel,
	Skeleton,
	Tooltip,
} from '@/shared/ui';
import { ROUTE_PATHS } from '@/shared/app/routes';
import { hasTrace } from '@/shared/lib';
import { cn, timeAgo } from '@/shared/lib/utils';
import { ago } from '@/modules/agents/lib/ago';
import {
	useAllowedOperationCount,
	type OpsApiReference,
} from '@/shared/credentials/components/OperationImpactPreview';
import {
	useAgentBindingEffectiveRules,
	type ActorExecutionEntity,
	type BindingPermissionRule,
} from '@/modules/agents/api';
import { successShare, toEditorRule } from '@/modules/agents/components/detail/shared';
import { API_ROW_COLUMNS_LG, API_ROW_GAP } from '@/modules/agents/components/flat/apiRowGrid';
import type { ApiTileModel } from '@/modules/agents/lib/apiTiles';
import {
	callsBeyondScan,
	formatDurationMs,
	type ApiRowActivity,
} from '@/modules/agents/lib/apiRowActivity';
import { isBlockedStatus, type TileStatus } from '@/modules/agents/lib/tileStatus';

/** How many of the row's newest calls the list shows. */
const RECENT_CALLS_SHOWN = 3;

/** How many rule chips the summary names before "+N more". */
const RULE_CHIPS_SHOWN = 2;

/** A recent call: age | method | path | HTTP status | duration | trace. The
 * duration drops out first when the column is too narrow to hold it cleanly. */
const CALL_COLUMNS =
	'grid-cols-[26px_46px_minmax(0,1fr)_28px_24px] @[22rem]:grid-cols-[26px_46px_minmax(0,1fr)_28px_46px_24px]';

/** Every column's body height: three 28px call lines, 2px apart. The chart
 * column and the access column fill the same, so all three end on one line. */
const BODY_HEIGHT = 'h-[88px]';

interface ApiRowRevealProps {
	agentId: string;
	tile: ApiTileModel;
	status: TileStatus;
	activity: ApiRowActivity;
	/** How many of the agent's bindings serve this row's API. */
	accountCount: number;
	bindingPending: boolean;
	onOpen: () => void;
	onOpenRules: () => void;
	/** Pause this binding. Omitted when the viewer may not manage the agent's
	 * bindings (`agents:write`): the footer then offers no pause. */
	onSuspend?: () => void;
	/** Lift a suspension on this binding. Omitted like `onSuspend`. */
	onResume?: () => void;
}

export function ApiRowReveal({
	agentId,
	tile,
	status,
	activity,
	accountCount,
	bindingPending,
	onOpen,
	onOpenRules,
	onSuspend,
	onResume,
}: ApiRowRevealProps) {
	return (
		<div data-testid="api-row-reveal" className="shadow-[inset_0_1px_0_var(--color-hairline)]">
			<div
				className={cn(
					'grid grid-cols-1 gap-y-5 pt-3 pb-3',
					API_ROW_GAP,
					API_ROW_COLUMNS_LG,
				)}
			>
				<ActivitySection activity={activity} className="lg:col-start-2" />
				<AccessSection
					agentId={agentId}
					tile={tile}
					onOpenRules={onOpenRules}
					className="lg:col-start-3"
				/>
				<RecentCallsSection
					agentId={agentId}
					tile={tile}
					status={status}
					activity={activity}
					className="lg:col-start-4"
				/>
			</div>
			<footer className="flex min-h-10 flex-wrap items-center justify-between gap-x-4 gap-y-2 pt-2.5 shadow-[inset_0_1px_0_var(--color-hairline)] lg:pl-[52px]">
				<p className="text-foreground-faint min-w-0 text-xs sm:truncate">
					{accountCount > 1 &&
						`One of ${accountCount} credentials for ${tile.title} — its rules apply only when a call uses it.`}
				</p>
				<div className="ml-auto flex shrink-0 items-center gap-1.5">
					<Button variant="tonal" size="xs" onClick={onOpen}>
						<Settings2 aria-hidden="true" className="h-3.5 w-3.5" />
						Manage access
					</Button>
					{tile.suspended
						? onResume && (
								<Tooltip
									content="Resume this binding — its rules are intact."
									interactiveChild
								>
									<Button
										variant="tonal"
										size="xs"
										loading={bindingPending}
										onClick={onResume}
										aria-label={`Resume ${tile.title} access`}
									>
										<PlayCircle aria-hidden="true" className="h-3.5 w-3.5" />
										Resume
									</Button>
								</Tooltip>
							)
						: onSuspend && (
								<Tooltip
									content="Pause this binding — reversible; rules survive and resume restores access."
									interactiveChild
								>
									<Button
										variant="tonal"
										size="xs"
										loading={bindingPending}
										onClick={onSuspend}
										aria-label={`Pause ${tile.title} access`}
									>
										<PauseCircle aria-hidden="true" className="h-3.5 w-3.5" />
										Pause
									</Button>
								</Tooltip>
							)}
				</div>
			</footer>
		</div>
	);
}

/** A column: its small-caps heading (one height in all three, so they share a
 * baseline) over a body of the shared height. */
function Section({
	label,
	aside,
	className,
	children,
	...props
}: {
	label: string;
	aside?: string | null;
	className?: string;
	children: ReactNode;
	'aria-label': string;
}) {
	return (
		<section className={cn('flex min-w-0 flex-col gap-2', className)} {...props}>
			<SectionLabel as="h4" className="flex h-4 min-w-0 items-baseline gap-2 leading-4">
				{label}
				{aside && (
					<span className="text-foreground-faint truncate text-[11px] font-semibold tracking-normal normal-case">
						{aside}
					</span>
				)}
			</SectionLabel>
			<div className={cn('flex min-w-0 flex-col', BODY_HEIGHT)}>{children}</div>
		</section>
	);
}

function ActivitySection({
	activity,
	className,
}: {
	activity: ApiRowActivity;
	className?: string;
}) {
	const { usage, percentiles } = activity;
	const lastAt = activity.calls?.[0]?.startedAt ?? null;
	const trend = usage?.trend.length ? usage.trend : [0, 0];
	return (
		<Section
			aria-label="Activity, last 7 days"
			label="Activity · 7d"
			aside={lastAt ? `last ${ago(lastAt)}` : null}
			className={className}
		>
			{usage === undefined ? (
				<Skeleton className="h-full w-full" />
			) : (
				<>
					<AreaSparkline
						data={trend}
						className={cn(
							'h-[52px] w-full',
							usage && usage.total > 0 ? 'text-primary' : 'text-foreground-faint',
						)}
					/>
					<p
						data-testid="row-usage-line"
						className="text-foreground-sub mt-auto min-w-0 overflow-hidden text-xs leading-[18px] tabular-nums"
					>
						{usage === null ? (
							<span className="text-foreground-faint">
								No usage figures to show here.
							</span>
						) : usage.total === 0 ? (
							<span className="text-foreground-faint">
								0 calls in the last 7 days
							</span>
						) : (
							// Two unbreakable groups — volume, then latency — so a narrow
							// column wraps between them, never inside one. Each opens with
							// its separator in an 18px gutter the row is pulled left over,
							// so a line never starts on a stray "·".
							<span className="-ml-[18px] flex flex-wrap">
								<span className="whitespace-nowrap">
									<StatSep />
									<b className="text-foreground-lighter">
										{usage.total.toLocaleString()}
									</b>{' '}
									calls ·{' '}
									<b className="text-foreground-lighter">
										{successShare(usage.success, usage.total)}
									</b>{' '}
									success
								</span>
								<span className="whitespace-nowrap">
									<StatSep />
									avg{' '}
									<b className="text-foreground-lighter">
										{formatDurationMs(usage.avgMs)}
									</b>
									{percentiles?.p95Ms != null && (
										<>
											{' '}
											· p95{' '}
											<b className="text-foreground-lighter">
												{formatDurationMs(percentiles.p95Ms)}
											</b>
										</>
									)}
								</span>
							</span>
						)}
					</p>
				</>
			)}
		</Section>
	);
}

/** The "·" between the stat groups, in a fixed gutter (see the usage line). */
function StatSep() {
	return (
		<span aria-hidden="true" className="inline-block w-[18px] text-center">
			{' · '}
		</span>
	);
}

function RecentCallsSection({
	agentId,
	tile,
	status,
	activity,
	className,
}: {
	agentId: string;
	tile: ApiTileModel;
	status: TileStatus;
	activity: ApiRowActivity;
	className?: string;
}) {
	const { calls } = activity;
	const shown = calls?.slice(0, RECENT_CALLS_SHOWN) ?? [];
	// The feed is the agent's newest calls across every API: an empty pick
	// says "none yet" only when the scan reached back to the start.
	const beyond = callsBeyondScan(activity);
	const quiet = beyond ? (
		<>
			None in the newest {activity.scanned?.count.toLocaleString()} calls.{' '}
			<AppLink
				href={ROUTE_PATHS.monitorExecutions({ actorId: agentId, actorType: 'agent' })}
				className="text-primary hover:underline"
			>
				See all in Monitor
			</AppLink>
		</>
	) : isBlockedStatus(status) ? (
		'No calls — default-deny refuses every call until a rule allows it.'
	) : tile.suspended ? (
		'No calls while paused.'
	) : (
		'No calls yet.'
	);
	return (
		<Section
			aria-label="Recent calls"
			label="Recent calls"
			className={cn('@container', className)}
		>
			{calls === undefined ? (
				<Skeleton className="h-full w-full" />
			) : calls === null ? (
				<p className="text-foreground-faint text-xs leading-[18px]">
					No call records to show here.
				</p>
			) : shown.length === 0 ? (
				<p className="text-foreground-faint text-xs leading-[18px]">{quiet}</p>
			) : (
				<ol aria-label="Recent calls" className="flex flex-col gap-0.5">
					{shown.map((call) => (
						<RecentCall key={call.id} agentId={agentId} call={call} />
					))}
				</ol>
			)}
		</Section>
	);
}

function RecentCall({ agentId, call }: { agentId: string; call: ActorExecutionEntity }) {
	const failed = call.status === 'failed';
	const traceId = hasTrace(call.traceId) ? call.traceId : null;
	return (
		<li
			data-testid="row-recent-call"
			className={cn(
				'text-foreground-sub grid h-7 items-center gap-x-2 text-xs',
				CALL_COLUMNS,
			)}
		>
			<span className="text-foreground-faint text-right tabular-nums">
				{timeAgo(call.startedAt)}
			</span>
			<MethodBadge method={call.operationMethod} className="w-[46px]" />
			<span className="text-foreground-lighter min-w-0 truncate font-mono text-[11.5px]">
				{call.operationPath ?? '—'}
			</span>
			{/* The code as a plain figure: only a failure takes a hue. */}
			{(() => {
				const code = (
					<span
						className={cn(
							'text-right font-mono text-[11px] font-bold tabular-nums',
							failed ? 'text-danger' : 'text-foreground-sub',
						)}
					>
						{call.httpStatus ?? (failed ? 'failed' : 'ok')}
					</span>
				);
				return call.error ? (
					<Tooltip content={call.error} className="justify-end">
						{code}
					</Tooltip>
				) : (
					code
				);
			})()}
			<span className="hidden text-right tabular-nums @[22rem]:inline">
				{formatDurationMs(call.durationMs) ?? '—'}
			</span>
			{traceId ? (
				<Tooltip content="Open trace" interactiveChild className="justify-self-end">
					<AppLink
						href={ROUTE_PATHS.monitorExecutions({
							actorId: agentId,
							traceId,
						})}
						variant="ghost"
						size="icon-xs"
						className="h-6 w-6"
						aria-label={`Open trace ${traceId}`}
					>
						<ArrowUpRight aria-hidden="true" className="h-3.5 w-3.5" />
					</AppLink>
				</Tooltip>
			) : (
				<span aria-hidden="true" />
			)}
		</li>
	);
}

/** "Allow GET, POST /repos/*" — one rule as a chip's words. */
function ruleWords(rule: BindingPermissionRule): { effect: string; scope: string } {
	const effect = String(rule.effect) === 'deny' ? 'Deny' : 'Allow';
	const bits: string[] = [];
	bits.push(rule.methods?.length ? rule.methods.join(', ') : 'Any');
	if (rule.operations?.length) {
		bits.push(
			`${rule.operations.length} ${rule.operations.length === 1 ? 'operation' : 'operations'}`,
		);
	}
	if (rule.path) bits.push(rule.path);
	return { effect, scope: bits.join(' ') };
}

/** A quiet in-line link that opens the rules editor. */
function RulesLink({ onClick, children }: { onClick: () => void; children: ReactNode }) {
	return (
		<Button
			variant="ghost"
			size="xs"
			className="text-primary hover:text-primary h-auto shrink-0 px-0 py-0 text-xs hover:bg-transparent hover:underline"
			onClick={onClick}
		>
			{children}
		</Button>
	);
}

/**
 * The binding's rules in brief: how many there are, the first two as chips
 * (+N more opens the editor), then how many of the API's operations they let
 * through, with a slim meter.
 */
function AccessSection({
	agentId,
	tile,
	onOpenRules,
	className,
}: {
	agentId: string;
	tile: ApiTileModel;
	onOpenRules: () => void;
	className?: string;
}) {
	const effective = useAgentBindingEffectiveRules(agentId, tile.credentialId);
	const operatorRules = useMemo(
		() => (effective.rules ? effective.rules.filter((r) => !r._system) : undefined),
		[effective.rules],
	);
	const editorRules = useMemo(() => operatorRules?.map(toEditorRule), [operatorRules]);
	const apiReference = useMemo<OpsApiReference | null>(
		() =>
			tile.version
				? { vendor: tile.vendor, name: tile.apiName, version: tile.version }
				: null,
		[tile.vendor, tile.apiName, tile.version],
	);
	const coverage = useAllowedOperationCount(apiReference, editorRules);
	const more = (operatorRules?.length ?? 0) - RULE_CHIPS_SHOWN;

	return (
		<Section aria-label="Access rules" label="Access" className={className}>
			<p className="text-foreground-sub flex h-[18px] min-w-0 items-center gap-2 text-xs">
				{effective.isError ? (
					<span className="text-foreground-faint">Couldn&rsquo;t read the rules.</span>
				) : operatorRules === undefined ? (
					<Skeleton className="h-4 w-28" />
				) : operatorRules.length === 0 ? (
					<>
						<span className="truncate">No rules — every call is blocked.</span>
						<RulesLink onClick={onOpenRules}>Add rule</RulesLink>
					</>
				) : (
					<span className="truncate">
						<b className="text-foreground-lighter tabular-nums">
							{operatorRules.length}
						</b>{' '}
						{operatorRules.length === 1 ? 'access rule' : 'access rules'}
						{effective.ruleSet && ` · rule set ${effective.ruleSet.name}`}
					</span>
				)}
			</p>
			{operatorRules && operatorRules.length > 0 && (
				<div
					className="mt-2 flex h-6 min-w-0 items-center gap-1.5 overflow-hidden whitespace-nowrap"
					data-testid="row-rule-chips"
				>
					{operatorRules.slice(0, RULE_CHIPS_SHOWN).map((rule, index) => {
						const { effect, scope } = ruleWords(rule);
						return (
							<Tooltip
								key={index}
								content={rule._comment ?? `${effect} ${scope}`}
								className={cn(
									'min-w-0 rounded-md',
									// The first, usually longer chip gives way first, so the
									// second keeps its words.
									index === 0 ? 'shrink-[4]' : 'shrink',
								)}
							>
								<span
									data-effect={effect.toLowerCase()}
									className="bg-surface-chip text-foreground-sub inline-flex h-6 min-w-0 items-center gap-1.5 rounded-md px-2 text-[11.5px]"
								>
									<b className="text-foreground-lighter shrink-0 font-semibold">
										{effect}
									</b>
									<span className="min-w-0 truncate font-mono">{scope}</span>
								</span>
							</Tooltip>
						);
					})}
					{more > 0 && <RulesLink onClick={onOpenRules}>+{more} more</RulesLink>}
				</div>
			)}
			<div className="mt-auto flex min-w-0 flex-col gap-1.5">
				{coverage === undefined && operatorRules !== undefined && apiReference ? (
					<Skeleton className="h-4 w-36" />
				) : coverage ? (
					<>
						<p
							data-testid="row-ops-allowed"
							className="text-foreground-sub text-xs leading-[18px] tabular-nums"
						>
							<Tooltip content="Each operation in the API's list run through this binding's rules (first match wins, default deny).">
								<span>
									<b className="text-foreground-lighter">
										{coverage.allowed.toLocaleString()}
									</b>{' '}
									of {coverage.total.toLocaleString()} operations allowed
								</span>
							</Tooltip>
						</p>
						<span
							aria-hidden="true"
							className="bg-surface-chip block h-0.5 w-full overflow-hidden rounded-full"
						>
							<span
								className="bg-primary block h-full rounded-full"
								style={{
									width: `${coverage.total ? (coverage.allowed / coverage.total) * 100 : 0}%`,
								}}
							/>
						</span>
					</>
				) : null}
			</div>
		</Section>
	);
}
