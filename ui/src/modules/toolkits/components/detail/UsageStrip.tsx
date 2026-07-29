import { useToolkitAgents, useToolkitUsage } from '@/modules/toolkits/api';
import type { Toolkit } from '@/modules/toolkits/api/types';

/**
 * KPI strip under the detail chrome — the toolkit's 7-day vitals from
 * `GET /monitoring/usage?toolkit_id=…`, plus the relationship counts.
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

interface StatTileProps {
	label: string;
	value: string;
	tone?: 'default' | 'success' | 'danger';
}

function StatTile({ label, value, tone = 'default' }: StatTileProps) {
	const valueClass =
		tone === 'success' ? 'text-success' : tone === 'danger' ? 'text-danger' : 'text-foreground';
	return (
		<div className="border-border/60 bg-card rounded-xl border px-4 py-3">
			<p className="text-muted-foreground font-mono text-xs tracking-wider uppercase">
				{label}
			</p>
			<p className={`font-heading mt-1 text-xl font-semibold ${valueClass}`}>{value}</p>
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
						className="border-border/60 bg-card space-y-2 rounded-xl border px-4 py-3"
					>
						<div className="bg-muted h-3 w-20 animate-pulse rounded" />
						<div className="bg-muted h-6 w-14 animate-pulse rounded" />
					</div>
				))}
			</div>
		);
	}

	// Non-admin (401/403 → null): hide the strip entirely.
	if (!usage) return null;

	const { total, success } = usage.stats;
	const successTone: StatTileProps['tone'] =
		total === 0 ? 'default' : success / total >= 0.95 ? 'success' : 'danger';

	return (
		<div className="grid grid-cols-2 gap-3 lg:grid-cols-4" data-testid="usage-strip">
			<StatTile label="Executions · 7d" value={total.toLocaleString()} />
			<StatTile
				label="Success rate"
				value={formatPercent(success, total)}
				tone={successTone}
			/>
			<StatTile label="p95 latency" value={formatLatency(usage.stats.p95_ms)} />
			<StatTile
				label="Agents / Creds / Keys"
				value={`${agents.length} / ${toolkit.credential_count} / ${toolkit.key_count}`}
			/>
		</div>
	);
}
