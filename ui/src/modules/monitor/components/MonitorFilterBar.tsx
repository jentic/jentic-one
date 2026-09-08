/**
 * Monitor global filter bar — the shared time-window + actor picker mounted
 * between the lens tabs and the active tabpanel.
 *
 * It writes `?days` / `?actor_id` / `?actor_type` (via {@link useMonitorFilters})
 * which every list tab folds into its query. The bar is hidden on Overview
 * (which owns its own window selector and the aggregation endpoint takes no
 * actor filter) and renders the actor Select disabled on Jobs (the jobs
 * endpoint has no actor parameter — backend gap).
 *
 * Free-text search is intentionally absent: no Monitor list endpoint supports
 * server-side search yet (tracked in jentic-one#624). The bar leaves room for
 * it once the backend lands.
 */
import { X } from 'lucide-react';
import { Button, Select, type SegmentedToggleOption } from '@/shared/ui';
import { SegmentedToggle } from '@/shared/ui';
import { useActors, type MonitorTab } from '@/modules/monitor/api';
import {
	useMonitorFilters,
	ORIGIN_OPTIONS,
	WINDOW_OPTIONS,
	type WindowValue,
} from '@/modules/monitor/lib/useMonitorFilters';

interface MonitorFilterBarProps {
	tab: MonitorTab;
}

/** Encode actor id + type into a single Select value (and back). */
const ACTOR_SEP = '\u0001';
const encodeActor = (id: string, type: string) => `${id}${ACTOR_SEP}${type}`;
const decodeActor = (value: string): { id: string; type: string } | null => {
	if (!value) return null;
	const [id, type] = value.split(ACTOR_SEP);
	return id ? { id, type: type ?? '' } : null;
};

export function MonitorFilterBar({ tab }: MonitorFilterBarProps) {
	const filters = useMonitorFilters();
	const actorsQuery = useActors();
	const actors = actorsQuery.data?.data ?? [];

	// Jobs has no actor filter on the backend; render the control disabled with a
	// hint rather than hiding it, so the bar stays positionally stable.
	const actorDisabled = tab === 'jobs';
	const selectValue =
		filters.actorId && filters.actorType ? encodeActor(filters.actorId, filters.actorType) : '';

	const windowOptions: SegmentedToggleOption<WindowValue>[] = WINDOW_OPTIONS;

	return (
		<div className="border-border/60 flex flex-wrap items-center gap-3 rounded-lg border border-dashed px-3 py-2">
			{/* TODO: mount a debounced free-text search input here once the
			    Monitor list endpoints support a `search` query param. The list
			    param interfaces in client.ts are ready to thread it through. */}
			<div className="flex items-center gap-2">
				<span className="text-muted-foreground text-xs font-medium">Window</span>
				<SegmentedToggle
					options={windowOptions}
					value={filters.window}
					onChange={filters.setWindow}
					ariaLabel="Time window"
				/>
			</div>

			<div className="flex min-w-[14rem] flex-1 items-center gap-2">
				<span className="text-muted-foreground text-xs font-medium">Actor</span>
				<Select
					aria-label="Filter by actor"
					value={actorDisabled ? '' : selectValue}
					disabled={actorDisabled}
					title={actorDisabled ? "Actor filter isn't available for jobs." : undefined}
					onChange={(e) => {
						const decoded = decodeActor(e.target.value);
						filters.setActor(decoded?.id ?? null, decoded?.type ?? null);
					}}
				>
					<option value="">All actors</option>
					{actors.map((actor) => (
						<option key={actor.id} value={encodeActor(actor.id, actor.actor_type)}>
							{actor.name} ({actor.actor_type})
						</option>
					))}
				</Select>
			</div>

			{/* Origin scope — executions-only (local-MCP 2-E2): the executions
			    endpoint is the one list with an `origin` query param. A picker
			    (not a chip): origins are a small closed set worth browsing —
			    "show me everything that arrived over MCP" is the headline ask. */}
			{tab === 'executions' && (
				<div className="flex items-center gap-2">
					<span className="text-muted-foreground text-xs font-medium">Origin</span>
					<Select
						aria-label="Filter by origin"
						value={filters.origin ?? ''}
						onChange={(e) => filters.setOrigin(e.target.value || null)}
					>
						<option value="">All origins</option>
						{ORIGIN_OPTIONS.map((o) => (
							<option key={o.value} value={o.value}>
								{o.label}
							</option>
						))}
					</Select>
				</div>
			)}

			{/* Toolkit scope — an executions-only deep-link filter (written by the
			    toolkit detail's "Open in Monitor" link). Rendered as a removable
			    chip rather than a picker: there's no in-Monitor toolkit selector
			    yet, so the chip's job is to make the active scope visible and
			    dismissible. */}
			{tab === 'executions' && filters.toolkitId && (
				<span className="border-primary/30 bg-primary/5 inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs">
					<span className="text-muted-foreground">Toolkit</span>
					<span className="text-foreground font-mono">{filters.toolkitId}</span>
					<Button
						variant="ghost"
						size="sm"
						aria-label="Clear toolkit filter"
						className="h-4 w-4 p-0"
						onClick={() => filters.setToolkit(null)}
					>
						<X className="h-3 w-3" aria-hidden="true" />
					</Button>
				</span>
			)}
		</div>
	);
}
