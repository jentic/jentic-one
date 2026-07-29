/**
 * KpiStrip — the at-a-glance numbers row under the detail page's identity
 * header: executions (7d), success share, last activity, bound toolkits.
 *
 * Usage stats come from the admin-gated `/monitoring/usage` aggregate; when
 * the viewer isn't an admin (`usage === null`) or the query is still in
 * flight (`undefined`), those cells render an em-dash rather than an error —
 * the strip is enrichment, identity is the page's primary data.
 */
import { cn, formatTimestamp, timeAgo } from '@/shared/lib/utils';
import type { ActorUsageDetail } from '@/modules/agents/api';

function successShare(usage: ActorUsageDetail | null | undefined): string {
	if (!usage || usage.total === 0) return '—';
	return `${((usage.success / usage.total) * 100).toFixed(1).replace(/\.0$/, '')}%`;
}

interface KpiCellProps {
	label: string;
	value: string;
	title?: string;
	tone?: 'default' | 'success' | 'danger';
}

function KpiCell({ label, value, title, tone = 'default' }: KpiCellProps) {
	return (
		<div className="min-w-0">
			<div
				className={cn(
					'font-heading truncate text-xl font-semibold tabular-nums',
					tone === 'success' && 'text-success',
					tone === 'danger' && 'text-danger',
				)}
				title={title}
			>
				{value}
			</div>
			<div className="text-muted-foreground/70 mt-0.5 text-[10px] tracking-wider uppercase">
				{label}
			</div>
		</div>
	);
}

interface KpiStripProps {
	/** 7-day usage stats; `null` = admin-gated (403), `undefined` = loading. */
	usage: ActorUsageDetail | null | undefined;
	/** ISO timestamp of the most recent execution, if the feed loaded any. */
	lastActivityAt: string | null | undefined;
	/** Bound-toolkit count; omit for actors without toolkit bindings (SAs). */
	toolkitCount?: number;
}

export function KpiStrip({ usage, lastActivityAt, toolkitCount }: KpiStripProps) {
	const share = successShare(usage);
	// Tone the success share only when there's real traffic to judge.
	const shareTone =
		!usage || usage.total === 0
			? 'default'
			: usage.success / usage.total >= 0.95
				? 'success'
				: usage.success / usage.total < 0.8
					? 'danger'
					: 'default';
	return (
		<div
			role="group"
			className="border-border/60 grid grid-cols-2 gap-x-4 gap-y-3 border-t pt-4 sm:grid-cols-4"
			aria-label="Key metrics"
		>
			<KpiCell label="Executions (7d)" value={usage ? usage.total.toLocaleString() : '—'} />
			<KpiCell label="Success rate" value={share} tone={shareTone} />
			<KpiCell
				label="Last activity"
				value={lastActivityAt ? timeAgo(lastActivityAt) : '—'}
				title={lastActivityAt ? formatTimestamp(lastActivityAt) : undefined}
			/>
			{toolkitCount != null && (
				<KpiCell label="Bound toolkits" value={String(toolkitCount)} />
			)}
		</div>
	);
}
