/**
 * KpiStrip — the console's 7-day vitals between the identity header and the
 * tab bar: executions, success share, last activity (+ bound toolkits for
 * agents). Rendered with the shared `StatCard` grid — the same tile grammar
 * the dashboard and the toolkit console use — so the detail pages read as
 * one product.
 *
 * Admin-gated by data shape (same contract as the toolkit console's
 * `UsageStrip`): `undefined` = loading → a skeleton with the final footprint
 * so the tab bar doesn't jump; `null` = 403 → the strip hides entirely
 * (non-admins lose nothing they could see elsewhere — the toolkit count
 * lives on the Overview tab).
 */
import { Activity, Boxes, CheckCircle2, Clock } from 'lucide-react';
import { Skeleton, StatCard } from '@/shared/ui';
import { timeAgo } from '@/shared/lib/utils';
import type { ActorUsageDetail } from '@/modules/agents/api';
import { successShare } from '@/modules/agents/components/detail/shared';

interface KpiStripProps {
	/** 7-day usage stats; `null` = admin-gated (403), `undefined` = loading. */
	usage: ActorUsageDetail | null | undefined;
	/** ISO timestamp of the most recent execution, if the feed loaded any. */
	lastActivityAt: string | null | undefined;
	/** Bound-toolkit count; omit for actors without toolkit bindings (SAs). */
	toolkitCount?: number;
}

export function KpiStrip({ usage, lastActivityAt, toolkitCount }: KpiStripProps) {
	const tiles = toolkitCount != null ? 4 : 3;
	const gridClass =
		tiles === 4
			? 'grid grid-cols-2 gap-3 lg:grid-cols-4'
			: 'grid grid-cols-2 gap-3 lg:grid-cols-3';

	if (usage === undefined) {
		return (
			<div className={gridClass} data-testid="kpi-strip-loading" aria-hidden="true">
				{Array.from({ length: tiles }).map((_, i) => (
					<div
						key={`kpi-skel-${i}`}
						className="border-border/60 bg-card space-y-3 rounded-xl border p-4"
					>
						<Skeleton className="h-3 w-20" />
						<Skeleton className="h-7 w-14" />
					</div>
				))}
			</div>
		);
	}

	// Non-admin (403 → null): hide the strip entirely, like the toolkit console.
	if (usage === null) return null;

	const { total, success, failed } = usage;
	const healthy = total === 0 || success / total >= 0.95;

	return (
		<div role="group" aria-label="Key metrics" className={gridClass}>
			<StatCard
				label="Executions"
				value={total.toLocaleString()}
				caption="last 7 days"
				icon={<Activity className="h-4 w-4" />}
				accent="blue"
			/>
			<StatCard
				label="Success rate"
				value={successShare(success, total)}
				caption={total > 0 && failed > 0 ? `${failed.toLocaleString()} failed` : undefined}
				icon={<CheckCircle2 className="h-4 w-4" />}
				accent={healthy ? 'green' : 'danger'}
				valueClassName={total === 0 ? undefined : healthy ? 'text-success' : 'text-danger'}
			/>
			<StatCard
				label="Last activity"
				value={lastActivityAt ? timeAgo(lastActivityAt) : '—'}
				icon={<Clock className="h-4 w-4" />}
				accent="orange"
			/>
			{toolkitCount != null && (
				<StatCard
					label="Bound toolkits"
					value={String(toolkitCount)}
					icon={<Boxes className="h-4 w-4" />}
					accent="primary"
				/>
			)}
		</div>
	);
}
