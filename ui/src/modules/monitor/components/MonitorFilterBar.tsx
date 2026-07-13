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
import { Select, type SegmentedToggleOption } from '@/shared/ui';
import { SegmentedToggle } from '@/shared/ui';
import { useActors, type MonitorTab } from '@/modules/monitor/api';
import {
	useMonitorFilters,
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
			{/* TODO(#624): mount a debounced free-text search input here once the
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
		</div>
	);
}
