import { Activity, ListTree, Workflow } from 'lucide-react';
import { useQuery } from '@tanstack/react-query';
import { api } from '@/api/client';
import { timeAgo } from '@/lib/time';

/**
 * Compact horizontal strip rendered just below the workflow page header.
 *
 * Mirrors the visual vocabulary of `WorkspaceStatsStrip` (label-eyebrow +
 * value, dot-spaced row) so the workflow detail surface reads as a
 * sibling of the Workspace home page rather than a different product.
 *
 * Slots, in order:
 *   • Steps count
 *   • APIs count
 *   • Last run — derived from the most recent trace where
 *     `workflow_slug` matches; "—" when there are no runs yet
 *
 * Deliberately NOT a vendor pile anymore — the Overview sidebar
 * carries the full involved-APIs list with names and links, so the
 * strip would just be redundant chrome.
 *
 * No source pill either: `GET /workflows/{slug}` doesn't return a
 * `source` field, and catalog workflows that haven't been imported
 * never reach this component (they fall through to the catalog
 * fallback). So the pill always read "Local" and was just decoration.
 *
 * The slug also doesn't live here. It sits next to the segmented
 * view-toggle below the strip, where it anchors the URL-state row
 * (`?view=`) and reads as a quiet caption rather than a stat.
 */
export interface WorkflowMetaStripProps {
	slug: string;
	stepsCount: number;
	involvedApis: string[];
	createdAt?: number | null;
}

export function WorkflowMetaStrip({
	slug,
	stepsCount,
	involvedApis,
	createdAt,
}: WorkflowMetaStripProps) {
	const lastRun = useQuery({
		queryKey: ['workflow-meta', 'last-run', slug],
		queryFn: () => api.listTraces({ limit: 20 }),
		staleTime: 15_000,
	});
	const lastRunTs = (() => {
		const traces = (
			lastRun.data as
				| { traces?: Array<{ created_at?: number; workflow_slug?: string }> }
				| undefined
		)?.traces;
		const match = traces?.find((t) => t.workflow_slug === slug);
		const ts = match?.created_at;
		return typeof ts === 'number' ? ts : null;
	})();

	return (
		<div
			className="border-border/60 bg-muted/20 text-muted-foreground flex flex-wrap items-center gap-x-6 gap-y-3 rounded-xl border px-4 py-3 text-xs"
			data-testid="workflow-meta-strip"
		>
			<MetaItem
				icon={<ListTree size={13} aria-hidden="true" />}
				label="Steps"
				value={stepsCount.toLocaleString()}
				testId="workflow-meta-steps"
			/>
			<MetaItem
				icon={<Workflow size={13} aria-hidden="true" />}
				label="APIs"
				value={involvedApis.length.toLocaleString()}
				testId="workflow-meta-apis-count"
			/>
			<MetaItem
				icon={<Activity size={13} aria-hidden="true" />}
				label="Last run"
				value={lastRunTs ? timeAgo(lastRunTs) : '—'}
				loading={lastRun.isLoading}
				testId="workflow-meta-last-run"
			/>
			{createdAt && (
				<span className="text-muted-foreground ml-auto text-xs">
					Imported{' '}
					<time dateTime={new Date(createdAt * 1000).toISOString()}>
						{timeAgo(createdAt)}
					</time>
				</span>
			)}
		</div>
	);
}

function MetaItem({
	icon,
	label,
	value,
	loading,
	testId,
}: {
	icon: React.ReactNode;
	label: string;
	value: string;
	loading?: boolean;
	testId: string;
}) {
	return (
		<span className="inline-flex min-w-0 items-baseline gap-2" data-testid={testId}>
			<span className="text-muted-foreground/70 inline-flex shrink-0 items-center gap-1.5 self-center">
				{icon}
				<span className="text-[10px] tracking-wider uppercase">{label}</span>
			</span>
			<span className="text-foreground font-mono text-sm font-medium">
				{loading ? '…' : value}
			</span>
		</span>
	);
}
