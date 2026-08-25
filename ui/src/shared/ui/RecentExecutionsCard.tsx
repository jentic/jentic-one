/**
 * RecentExecutionsCard — the console-standard "Recent executions" feed used
 * by toolkit, agent, and service-account detail pages. One visual grammar
 * (grown on the toolkit console): a status-dot row with the mono operation
 * label, inline HTTP status, optional error line, optional attribution slot,
 * duration, and relative time — ending in a pre-filtered "Open Monitor"
 * deep-link. Monitor owns the full history (paging, filters, trace sheets);
 * this card is deliberately a shallow feed.
 */
import type { ReactNode } from 'react';
import { Activity, ListOrdered } from 'lucide-react';
import { AppLink } from '@/shared/ui/AppLink';
import { DetailSection, EmptyRow } from '@/shared/ui/DetailSection';
import { formatTimestamp, timeAgo } from '@/shared/lib/utils';

/** Absolute-time tooltip for either wire shape (ISO string or epoch number). */
function absoluteTime(value: string | number): string {
	if (typeof value === 'string') return formatTimestamp(value);
	// Same 10-digit heuristic as the shared `timeAgo`: seconds vs milliseconds.
	const ms = value < 1e12 ? value * 1000 : value;
	return formatTimestamp(new Date(ms).toISOString());
}

export interface RecentExecutionItem {
	id: string;
	/** Execution lifecycle status (completed/failed/running/cancelled). */
	status: string;
	/** HTTP status of the upstream call, shown inline after the label. */
	httpStatus?: number | null;
	/** Mono operation label (e.g. `github.create_issue`). */
	label: string;
	/** Error detail rendered under the label for failures/denials. */
	error?: string | null;
	/** Optional attribution slot (e.g. an `ActorLabel` on the toolkit page). */
	meta?: ReactNode;
	durationMs: number | null;
	/** ISO string, epoch seconds, or epoch ms (shared `timeAgo` rules). */
	startedAt: string | number;
}

export interface RecentExecutionsCardProps {
	items: RecentExecutionItem[];
	/** Pre-filtered Monitor deep-link for the header. */
	monitorHref: string;
	isLoading?: boolean;
	emptyMessage?: string;
	/**
	 * Render the "there's more" footnote (e.g. when the API reports another
	 * page) — links back into Monitor for the full history.
	 */
	hasMore?: boolean;
}

/** "412ms" / "1.2s", or an em-dash for executions without a duration. */
function formatDuration(ms: number | null): string {
	if (ms == null) return '—';
	if (ms >= 1000) return `${(ms / 1000).toFixed(1)}s`;
	return `${Math.round(ms)}ms`;
}

/**
 * Execution lifecycle → dot colour, aligned with Monitor's vocabulary
 * (running/completed/failed/cancelled — see `ExecutionStatusPill`). Unknown
 * wire values fall through to the neutral dot rather than leaking a colour.
 */
const STATUS_DOT_CLASS: Record<string, string> = {
	completed: 'bg-success',
	failed: 'bg-danger',
	running: 'bg-accent-blue',
	cancelled: 'bg-warning',
};

function ExecutionRow({ item }: { item: RecentExecutionItem }) {
	const dotClass = STATUS_DOT_CLASS[item.status] ?? 'bg-muted-foreground';
	const failed = item.status === 'failed';
	return (
		<div
			data-testid="execution-feed-row"
			className="bg-muted/30 border-border/60 flex flex-wrap items-center gap-3 rounded-lg border px-4 py-2.5"
		>
			<span
				className={`h-2 w-2 shrink-0 rounded-full ${dotClass}`}
				aria-hidden="true"
				title={item.status}
			/>
			{/* The dot alone is invisible to AT — carry the status as text too. */}
			<span className="sr-only">{item.status}</span>
			<div className="min-w-0 flex-1 basis-48">
				<p className="text-foreground truncate font-mono text-xs">
					{item.label}
					{item.httpStatus != null && (
						<span
							className={failed ? 'text-danger ml-2' : 'text-muted-foreground ml-2'}
						>
							{item.httpStatus}
						</span>
					)}
				</p>
				{item.error && <p className="text-danger mt-0.5 truncate text-xs">{item.error}</p>}
			</div>
			{item.meta != null && (
				<span className="text-muted-foreground shrink-0 text-xs">{item.meta}</span>
			)}
			<span className="text-muted-foreground w-14 shrink-0 text-right font-mono text-xs">
				{formatDuration(item.durationMs)}
			</span>
			<span
				className="text-muted-foreground w-20 shrink-0 text-right text-xs"
				title={absoluteTime(item.startedAt)}
			>
				{timeAgo(item.startedAt)}
			</span>
		</div>
	);
}

export function RecentExecutionsCard({
	items,
	monitorHref,
	isLoading = false,
	emptyMessage = 'No recent executions.',
	hasMore = false,
}: RecentExecutionsCardProps) {
	return (
		<DetailSection
			title="Recent executions"
			icon={<ListOrdered className="h-4 w-4" />}
			trailing={
				<AppLink
					href={monitorHref}
					className="text-primary inline-flex items-center gap-1 text-xs font-medium"
				>
					{/* In-app route — the Activity glyph; ExternalLink is reserved
					    for real external targets. */}
					Open Monitor <Activity className="h-3 w-3" aria-hidden="true" />
				</AppLink>
			}
		>
			{isLoading ? (
				<div className="space-y-2" aria-hidden="true">
					<div className="bg-muted h-10 animate-pulse rounded-lg" />
					<div className="bg-muted h-10 animate-pulse rounded-lg" />
				</div>
			) : items.length === 0 ? (
				<EmptyRow icon={<Activity />}>{emptyMessage}</EmptyRow>
			) : (
				<>
					{items.map((item) => (
						<ExecutionRow key={item.id} item={item} />
					))}
					{hasMore && (
						<p className="text-muted-foreground pt-1 text-xs">
							Showing the {items.length} most recent —{' '}
							<AppLink href={monitorHref} className="text-primary font-medium">
								see the full history in Monitor
							</AppLink>
							.
						</p>
					)}
				</>
			)}
		</DetailSection>
	);
}
