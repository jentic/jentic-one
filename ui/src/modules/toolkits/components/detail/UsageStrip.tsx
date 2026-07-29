import type { ReactNode } from 'react';
import { Activity, Boxes, CheckCircle2, Gauge } from 'lucide-react';
import { Skeleton } from '@/shared/ui';
import { cn } from '@/shared/lib/utils';
import { useToolkitAgents, useToolkitUsage } from '@/modules/toolkits/api';
import type { Toolkit } from '@/modules/toolkits/api/types';

/**
 * KPI strip under the detail header — the toolkit's 7-day vitals from
 * `GET /monitoring/usage?toolkit_id=…`, plus the relationship counts.
 * Deliberately the same tile grammar as the dashboard's `StatCard`
 * (mono uppercase label, accent icon medallion, bold tabular value) so the
 * detail page reads as part of the same product.
 *
 * Admin-gated by data shape: the repository maps 401/403 to `null`, and a
 * `null` usage hides the whole strip (non-admins lose nothing they could see
 * elsewhere — counts live on the tabs). While loading it renders a skeleton
 * with the same footprint so the tab bar doesn't jump.
 */

function formatPercent(success: number, total: number): string {
	if (total === 0) return '—';
	return `${((success / total) * 100).toFixed(1).replace(/\.0$/, '')}%`;
}

function formatLatency(ms: number | null | undefined): string {
	if (ms == null) return '—';
	if (ms >= 1000) return `${(ms / 1000).toFixed(1)}s`;
	return `${Math.round(ms)}ms`;
}

type Accent = 'blue' | 'green' | 'orange' | 'primary' | 'danger';

const ACCENT_MEDALLION: Record<Accent, string> = {
	blue: 'bg-accent-blue/12 text-accent-blue ring-accent-blue/20',
	green: 'bg-accent-green/12 text-accent-green ring-accent-green/20',
	orange: 'bg-accent-orange/12 text-accent-orange ring-accent-orange/20',
	primary: 'bg-primary/12 text-primary ring-primary/20',
	danger: 'bg-danger/12 text-danger ring-danger/20',
};

interface StatTileProps {
	label: string;
	value: string;
	caption?: string;
	icon: ReactNode;
	accent: Accent;
	valueClassName?: string;
}

function StatTile({ label, value, caption, icon, accent, valueClassName }: StatTileProps) {
	return (
		<div className="border-border/60 bg-card flex flex-col gap-2 rounded-xl border p-4">
			<div className="flex items-start justify-between gap-2">
				<span className="text-muted-foreground font-mono text-[11px] leading-none font-medium tracking-wider uppercase">
					{label}
				</span>
				<span
					aria-hidden="true"
					className={cn(
						'-mt-1 flex h-7 w-7 shrink-0 items-center justify-center rounded-lg ring-1',
						ACCENT_MEDALLION[accent],
					)}
				>
					{icon}
				</span>
			</div>
			<div className="flex items-baseline gap-2">
				<span
					className={cn(
						'font-heading text-foreground text-2xl leading-none font-bold whitespace-nowrap tabular-nums',
						valueClassName,
					)}
				>
					{value}
				</span>
				{caption && (
					<span className="text-muted-foreground truncate text-xs">{caption}</span>
				)}
			</div>
		</div>
	);
}

export function UsageStrip({ toolkit }: { toolkit: Toolkit }) {
	const { data: usage, isLoading } = useToolkitUsage(toolkit.toolkit_id);
	const { data: agents = [] } = useToolkitAgents(toolkit.toolkit_id);

	if (isLoading) {
		return (
			<div
				className="grid grid-cols-2 gap-3 lg:grid-cols-4"
				data-testid="usage-strip-loading"
				aria-hidden="true"
			>
				{Array.from({ length: 4 }).map((_, i) => (
					<div
						key={`usage-skel-${i}`}
						className="border-border/60 bg-card space-y-3 rounded-xl border p-4"
					>
						<Skeleton className="h-3 w-20" />
						<Skeleton className="h-7 w-14" />
					</div>
				))}
			</div>
		);
	}

	// Non-admin (401/403 → null): hide the strip entirely.
	if (!usage) return null;

	const { total, success, failed } = usage.stats;
	const healthy = total === 0 || success / total >= 0.95;

	return (
		<div className="grid grid-cols-2 gap-3 lg:grid-cols-4" data-testid="usage-strip">
			<StatTile
				label="Executions"
				value={total.toLocaleString()}
				caption="last 7 days"
				icon={<Activity className="h-4 w-4" />}
				accent="blue"
			/>
			<StatTile
				label="Success rate"
				value={formatPercent(success, total)}
				caption={total > 0 && failed > 0 ? `${failed.toLocaleString()} failed` : undefined}
				icon={<CheckCircle2 className="h-4 w-4" />}
				accent={healthy ? 'green' : 'danger'}
				valueClassName={total === 0 ? undefined : healthy ? 'text-success' : 'text-danger'}
			/>
			<StatTile
				label="p95 latency"
				value={formatLatency(usage.stats.p95_ms)}
				caption="last 7 days"
				icon={<Gauge className="h-4 w-4" />}
				accent="orange"
			/>
			<StatTile
				label="Wired up"
				value={`${agents.length} / ${toolkit.credential_count} / ${toolkit.key_count}`}
				caption="agents / creds / keys"
				icon={<Boxes className="h-4 w-4" />}
				accent="primary"
			/>
		</div>
	);
}
