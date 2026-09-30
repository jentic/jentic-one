/**
 * Monitor module — the observability surface for jentic-one.
 *
 * One page, two layouts:
 *
 *   Overview   (default, org:admin) a stat strip over a split: the usage
 *              charts on the left, the live activity stream docked on the
 *              right. "Expand" opens the stream into the full log.
 *   Activity   (`?view=activity`, or any `?show=`) the activity log at full
 *              width — Everything plus the API-calls / Jobs / Audit-log
 *              sources behind it. "Overview" folds it back into the panel.
 *              Members only ever get this layout (usage is org:admin).
 *
 * Expanding and collapsing are view transitions: the docked panel and the
 * full log share the activity stream's transition name, so the panel grows
 * into the page (and shrinks back) instead of cutting.
 *
 * Links from before the redesign carried a `?tab=` vocabulary; they're
 * rewritten on arrival (see LEGACY_TABS).
 */
import { useEffect } from 'react';
import { useSearchParams } from 'react-router';
import { ArrowLeft, Maximize2 } from 'lucide-react';
import { Button, CardFooter, PageShell, PageHeader, PageHelp } from '@/shared/ui';
import { ActivityStreamPanel } from '@/shared/app/rail/ActivityStreamPanel';
import { activityStreamVtStyle, withViewTransition } from '@/shared/app/viewTransitions';
import { ACTIVITY_SOURCES, type ActivitySource } from '@/modules/monitor/api';
import { ActivityView, SOURCE_SCOPED_PARAMS } from '@/modules/monitor/components/ActivityView';
import { MonitorFilterBar } from '@/modules/monitor/components/MonitorFilterBar';
import { MonitorOverview, type LinkBase } from '@/modules/monitor/components/MonitorOverview';
import { RefreshControl } from '@/modules/monitor/components/RefreshControl';
import { StatStrip, StatStripSkeleton } from '@/modules/monitor/components/StatStrip';
import { monitorHref } from '@/modules/monitor/lib/links';
import { DEFAULT_WINDOW, useMonitorFilters } from '@/modules/monitor/lib/useMonitorFilters';
import { AUTO_REFRESH_MS, useUsageOverview } from '@/modules/monitor/lib/useUsageOverview';
import { usePermission, ORG_ADMIN } from '@/modules/monitor/lib/usePermission';

/**
 * Pre-redesign `?tab=` values → what replaces them. `expand` opens the full
 * log; `show` picks its source. Anything else lands on the Overview.
 */
const LEGACY_TABS: Record<string, { expand?: boolean; show?: ActivitySource }> = {
	overview: {},
	usage: {},
	activity: { expand: true },
	events: { expand: true },
	executions: { show: 'calls' },
	jobs: { show: 'jobs' },
	audit: { show: 'audit' },
};

/** Params only the expanded log understands — dropped when folding it back. */
const LOG_SCOPED_PARAMS = [...SOURCE_SCOPED_PARAMS, 'show', 'view', 'severity', 'from', 'to'];

function isActivitySource(value: string | null): value is ActivitySource {
	return value != null && (ACTIVITY_SOURCES as string[]).includes(value);
}

function windowLabel(days: number): string {
	return days === 1 ? '24h' : `${days}d`;
}

export default function MonitorPage() {
	const [searchParams, setSearchParams] = useSearchParams();
	const isAdmin = usePermission(ORG_ADMIN);
	const filters = useMonitorFilters();

	const tabParam = searchParams.get('tab');
	const showParam = searchParams.get('show');
	// The Audit log is org:admin; anyone else asking for it lands on Everything.
	const sources = isAdmin ? ACTIVITY_SOURCES : ACTIVITY_SOURCES.filter((s) => s !== 'audit');
	const source: ActivitySource =
		isActivitySource(showParam) && sources.includes(showParam) ? showParam : 'all';
	const expanded = !isAdmin || searchParams.get('view') === 'activity' || source !== 'all';

	const usage = useUsageOverview({ enabled: isAdmin && !expanded });

	// Arrival rewrites (replace: no history entry). Legacy `?tab=` values map
	// onto the new vocabulary; the rest are retired params.
	//  - `toolkit_id`: deprecation-window scrub (theme-5 5d) — DELETE IN 6b.
	//    Pre-5b deep links could carry it, and `setSearchParams((prev) => …)`
	//    would otherwise copy it forward on every change.
	//  - `live`: the old Events tab's opt-in; the feed is always live now.
	//  - `lens`: the old Usage tab's breakdown lens; the charts own it now.
	useEffect(() => {
		const legacy = tabParam ? (LEGACY_TABS[tabParam] ?? {}) : undefined;
		const retired = ['toolkit_id', 'live', 'lens'].filter((k) => searchParams.has(k));
		if (!legacy && retired.length === 0) return;
		setSearchParams(
			(prev) => {
				const next = new URLSearchParams(prev);
				for (const k of retired) next.delete(k);
				if (legacy) {
					next.delete('tab');
					if (legacy.show) next.set('show', legacy.show);
					else if (legacy.expand) next.set('view', 'activity');
					// The old Events tab's filters don't carry over to the feed.
					if (tabParam === 'events') {
						next.delete('status');
						next.delete('severity');
					}
				}
				return next;
			},
			{ replace: true },
		);
	}, [tabParam, searchParams, setSearchParams]);

	const setExpanded = (open: boolean) =>
		withViewTransition(() =>
			setSearchParams(
				(prev) => {
					const next = new URLSearchParams(prev);
					// The GLOBAL filters (days / actor_id / actor_type) survive;
					// everything the log alone understands (source, status, open
					// sheets) is dropped so nothing stale reopens.
					for (const k of LOG_SCOPED_PARAMS) next.delete(k);
					if (open) next.set('view', 'activity');
					return next;
				},
				{ replace: false },
			),
		);

	// Carry the window + actor into every drill-down; the default window is
	// implicit (no `days` param means 7d everywhere).
	const linkBase: LinkBase = {
		days: String(usage.days) === DEFAULT_WINDOW ? undefined : usage.days,
		actorId: filters.actorId ?? undefined,
		actorType: filters.actorType ?? undefined,
	};
	const hasData = !!usage.overview && usage.overview.totalExecutions > 0;

	return (
		<PageShell>
			<PageHeader
				title="Monitor"
				subtitle={
					isAdmin
						? 'How much your agents are doing, how well — and what they’re doing right now.'
						: 'Everything your agents and the platform are doing, live.'
				}
				actions={
					<PageHelp
						title="About Monitor"
						intro="Monitor shows what your agents and the platform are doing. The time window and actor filters apply to whatever you're looking at."
						sections={[
							...(isAdmin
								? [
										{
											heading: 'Overview',
											body: 'The strip up top is the window at a glance — calls, success rate, latency, failures and how many APIs saw traffic. Below it, the volume, bubble and breakdown charts show where the traffic went; one API keeps one colour across all three. Click a number or a breakdown row to jump to the matching API calls.',
										},
									]
								: []),
							{
								heading: 'Live activity',
								body: 'Platform events — calls, jobs, approvals, alerts — newest first, as they happen. Acknowledge alerts right from the row. On the Overview it sits docked on the right; Expand opens the full log.',
							},
							{
								heading: 'The full log',
								body: 'Everything is the live feed with history, pause and filters. API calls is the trace log of every call (with origin filter); Jobs is the async work queue (admins can cancel from the detail sheet); the Audit log is the org-admin record of who changed what. Click any row for its detail.',
							},
						]}
					/>
				}
			/>

			{expanded ? (
				<div style={activityStreamVtStyle}>
					<ActivityView
						source={source}
						sources={sources}
						leading={
							isAdmin && (
								<Button
									variant="ghost"
									size="sm"
									className="text-muted-foreground hover:text-foreground -ml-2 h-[1.875rem] gap-1.5 px-2 text-xs"
									onClick={() => setExpanded(false)}
								>
									<ArrowLeft className="h-3.5 w-3.5" aria-hidden="true" />
									Overview
								</Button>
							)
						}
					/>
				</div>
			) : (
				<div className="space-y-4">
					<div
						role="toolbar"
						aria-label="Monitor filters"
						className="flex flex-wrap items-center gap-2"
					>
						<MonitorFilterBar view="usage" />
						<RefreshControl
							className="ml-auto"
							updatedAt={usage.updatedAt}
							onRefresh={usage.refresh}
							intervalMs={AUTO_REFRESH_MS}
						/>
					</div>

					{usage.isLoading && <StatStripSkeleton />}
					{hasData && usage.usage && usage.overview && (
						<StatStrip
							key={`${usage.days}-${linkBase.actorId ?? ''}`}
							overview={usage.overview}
							usage={usage.usage}
							apis={usage.apis}
							windowLabel={windowLabel(usage.days)}
							callsHref={monitorHref({ show: 'calls', ...linkBase })}
							failedHref={monitorHref({
								show: 'calls',
								status: 'failed',
								...linkBase,
							})}
						/>
					)}

					<div className="grid grid-cols-1 items-start gap-4 xl:grid-cols-[minmax(0,1fr)_minmax(340px,26rem)]">
						<MonitorOverview
							state={usage}
							linkBase={linkBase}
							hasActor={!!filters.actorId}
						/>
						<ActivityStreamPanel
							// Sticks 1rem under the 3rem top bar and stops 1rem above the
							// viewport's bottom edge; the log fills whatever's left.
							className="xl:sticky xl:top-4 xl:h-[calc(100dvh-5rem)]"
							logClassName="max-h-[480px] xl:h-full xl:max-h-none"
							actions={
								<Button
									variant="ghost"
									size="sm"
									className="h-8 w-8 p-0"
									aria-label="Expand activity to the full log"
									title="Expand to the full log"
									onClick={() => setExpanded(true)}
								>
									<Maximize2 className="h-4 w-4" aria-hidden="true" />
								</Button>
							}
							footer={
								<CardFooter className="py-2">
									<button
										type="button"
										onClick={() => setExpanded(true)}
										className="text-primary text-sm font-medium hover:underline"
									>
										Open the full log →
									</button>
								</CardFooter>
							}
						/>
					</div>
				</div>
			)}
		</PageShell>
	);
}
