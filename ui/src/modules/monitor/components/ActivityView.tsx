/**
 * ActivityView — Monitor's default view: one toolbar over one list.
 *
 * The toolbar picks WHAT you're looking at (the source) and narrows it:
 *
 *   Source   Everything | API calls | Jobs | Audit log (org admins)   ?show=
 *   Status   per-source chips (see lib/statusFilters)                ?status=
 *   Window · Actor (· Origin for API calls)                           ?days= …
 *
 * From lg the toolbar sticks under the top bar (on phones it wraps to several
 * rows — too tall to pin). Its measured height is published as `--log-top`,
 * the line everything else sticky in the log (day headers, the new-events
 * pill, the docked detail pane, j/k scroll margins) sits beneath.
 *
 * Everything is the live event feed; the other sources are the focused,
 * columned logs behind it. Switching source drops the params only the old
 * source understood (status, open sheets, deep-link filters) and keeps the
 * window + actor (and a brushed time range), so you never lose the slice you
 * were looking at.
 */
import { useEffect, useRef, useState, type CSSProperties, type ReactNode } from 'react';
import { useSearchParams } from 'react-router';
import { ArrowLeftRight, Layers, Radio, ScrollText } from 'lucide-react';
import { SegmentedToggle } from '@/shared/ui';
import { cn } from '@/shared/lib/utils';
import type { ActivitySource } from '@/modules/monitor/api';
import { ActivityFeed } from '@/modules/monitor/components/ActivityFeed';
import { ExecutionsTab } from '@/modules/monitor/components/ExecutionsTab';
import { JobsTab } from '@/modules/monitor/components/JobsTab';
import { AuditTab } from '@/modules/monitor/components/AuditTab';
import { MonitorFilterBar } from '@/modules/monitor/components/MonitorFilterBar';
import { LogTimeline } from '@/modules/monitor/components/LogTimeline';
import { usePermission, ORG_ADMIN } from '@/modules/monitor/lib/usePermission';
import { STATUS_OPTIONS, useStatusFilter } from '@/modules/monitor/lib/statusFilters';

const SOURCE_LABEL: Record<ActivitySource, string> = {
	all: 'Everything',
	calls: 'API calls',
	jobs: 'Jobs',
	audit: 'Audit log',
};

const SOURCE_ICON: Record<ActivitySource, ReactNode> = {
	all: <Radio className="h-3.5 w-3.5" />,
	calls: <ArrowLeftRight className="h-3.5 w-3.5" />,
	jobs: <Layers className="h-3.5 w-3.5" />,
	audit: <ScrollText className="h-3.5 w-3.5" />,
};

/** Each status chip carries the tone of the rows it keeps. */
const STATUS_DOT: Record<string, string> = {
	failed: 'bg-danger',
	action: 'bg-warning',
	completed: 'bg-success',
	active: 'bg-primary',
};

function StatusDot({ tone }: { tone: string }) {
	return <span className={cn('h-1.5 w-1.5 rounded-full', tone)} />;
}

/** The app's top bar — the toolbar pins directly beneath it. */
const TOP_BAR_PX = 48;

/**
 * Pins the toolbar and reports its height and whether it's currently stuck
 * (the stuck state only changes the look: a backdrop and a hairline).
 */
function useStickyBar() {
	const ref = useRef<HTMLDivElement>(null);
	const [height, setHeight] = useState(0);
	const [stuck, setStuck] = useState(false);
	useEffect(() => {
		const el = ref.current;
		if (!el) return;
		const ro = new ResizeObserver(() => setHeight(el.offsetHeight));
		ro.observe(el);
		// Pinned at the top bar's edge, the bar pokes 1px above a root inset by
		// one more pixel — so "not fully visible" means "stuck".
		const io = new IntersectionObserver(
			([entry]) =>
				setStuck(entry.intersectionRatio < 1 && entry.boundingClientRect.top <= TOP_BAR_PX),
			{ rootMargin: `-${TOP_BAR_PX + 1}px 0px 0px 0px`, threshold: [1] },
		);
		io.observe(el);
		return () => {
			ro.disconnect();
			io.disconnect();
		};
	}, []);
	return { ref, height, stuck };
}

/** Params scoped to one source — dropped whenever the source changes. */
export const SOURCE_SCOPED_PARAMS = [
	'status',
	'trace_id',
	'execution_id',
	'job_id',
	'audit_id',
	'target_id',
	'target_type',
	'origin',
	'api',
	'cursor',
];

export function ActivityView({
	source,
	sources,
	leading,
}: {
	source: ActivitySource;
	/** The sources this user may pick (Audit log is org-admin only). */
	sources: ActivitySource[];
	/** Rendered at the start of the toolbar (Monitor's "← Overview"). */
	leading?: ReactNode;
}) {
	const [, setSearchParams] = useSearchParams();
	const { status, setStatus } = useStatusFilter(source);
	const statusOptions = STATUS_OPTIONS[source];
	// The histogram is API traffic (the usage aggregate is admin-scoped), so it
	// heads the two sources made of calls.
	const isAdmin = usePermission(ORG_ADMIN);
	const showTimeline = isAdmin && (source === 'all' || source === 'calls');

	const bar = useStickyBar();

	const setSource = (next: ActivitySource) => {
		if (next === source) return;
		setSearchParams(
			(prev) => {
				const p = new URLSearchParams(prev);
				for (const k of SOURCE_SCOPED_PARAMS) p.delete(k);
				// Everything has no `show`; `view` keeps the log expanded on Monitor.
				if (next === 'all') {
					p.delete('show');
					p.set('view', 'activity');
				} else {
					p.set('show', next);
					p.delete('view');
				}
				return p;
			},
			{ replace: false },
		);
	};

	return (
		<div
			className="space-y-4 [--log-top:0px] lg:[--log-top:var(--log-toolbar-h,0px)]"
			style={{ '--log-toolbar-h': `${bar.height}px` } as CSSProperties}
		>
			<div
				ref={bar.ref}
				role="toolbar"
				aria-label="Activity filters"
				data-stuck={bar.stuck || undefined}
				className={cn(
					'flex flex-wrap items-center gap-x-2 gap-y-2',
					// Padded for the backdrop, pulled back by the same amount so the
					// resting layout is unchanged.
					'-mx-page-gutter px-page-gutter -mt-2 mb-2 py-2',
					'border-b border-transparent transition-[background-color,border-color,box-shadow] duration-200',
					'lg:sticky lg:top-0 lg:z-30',
					'lg:data-stuck:bg-background/85 lg:data-stuck:border-border/70 lg:data-stuck:shadow-[0_8px_16px_-12px_rgb(0_0_0/0.5)] lg:data-stuck:backdrop-blur-md',
				)}
			>
				{leading && (
					<>
						{leading}
						<span
							aria-hidden="true"
							className="bg-border mx-1 hidden h-5 w-px sm:block"
						/>
					</>
				)}
				<div className="-mx-1 max-w-full overflow-x-auto px-1">
					<SegmentedToggle<ActivitySource>
						options={sources.map((s) => ({
							value: s,
							label: SOURCE_LABEL[s],
							icon: SOURCE_ICON[s],
						}))}
						value={source}
						onChange={setSource}
						ariaLabel="Activity source"
					/>
				</div>
				{statusOptions.length > 0 && (
					<SegmentedToggle
						options={statusOptions.map((o) =>
							STATUS_DOT[o.value]
								? { ...o, icon: <StatusDot tone={STATUS_DOT[o.value]} /> }
								: o,
						)}
						value={status}
						onChange={setStatus}
						ariaLabel="Status"
					/>
				)}
				<div className="flex flex-1 flex-wrap items-center gap-2 lg:justify-end">
					<MonitorFilterBar view={source} />
				</div>
			</div>

			{showTimeline && <LogTimeline />}

			{source === 'all' && <ActivityFeed />}
			{source === 'calls' && <ExecutionsTab />}
			{source === 'jobs' && <JobsTab />}
			{source === 'audit' && <AuditTab />}
		</div>
	);
}
