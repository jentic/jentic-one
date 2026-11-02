/**
 * Monitor filter controls — the shared time-window + actor (+ origin) pickers.
 *
 * Renders inline controls only (no container): the Activity toolbar folds
 * them in beside the source switch, and Usage mounts them in its own row.
 * They write `?days` / `?actor_id` / `?actor_type` / `?origin` via
 * {@link useMonitorFilters}, which every view folds into its query.
 *
 * View-aware:
 *   usage  no unbounded window (the aggregate's bucket tiers need a finite
 *          range), so "All" is not offered and a carried-over `days=all`
 *          reads as 30d. The aggregate scopes by actor via `agent_id`.
 *   jobs   the actor Select renders disabled (the jobs endpoint has no actor
 *          parameter — backend gap), keeping the row positionally stable.
 *   calls  adds the Origin picker (the one list with an `origin` param).
 *
 * Free-text search is intentionally absent: no Monitor list endpoint supports
 * server-side search yet (tracked in jentic-one#624). What the calls lens does
 * take is an ID — the agent id an agent's Settings show, or a call's trace id —
 * since those are the ids the rest of the app prints; a pasted agent id becomes
 * the actor filter, anything else the trace filter.
 */
import { useEffect, useState } from 'react';
import { Route, Users } from 'lucide-react';
import { Input, SegmentedToggle, type SegmentedToggleOption } from '@/shared/ui';
import { cn } from '@/shared/lib/utils';
import { useActors, type ActivitySource } from '@/modules/monitor/api';
import { FilterSelect } from '@/modules/monitor/components/FilterSelect';
import {
	useMonitorFilters,
	ORIGIN_OPTIONS,
	WINDOW_OPTIONS,
	type WindowValue,
} from '@/modules/monitor/lib/useMonitorFilters';

export type MonitorFilterView = ActivitySource | 'usage';

interface MonitorFilterBarProps {
	view: MonitorFilterView;
}

/** Encode actor id + type into a single Select value (and back). */
const ACTOR_SEP = '\u0001';
const encodeActor = (id: string, type: string) => `${id}${ACTOR_SEP}${type}`;
const decodeActor = (value: string): { id: string; type: string } | null => {
	if (!value) return null;
	const [id, type] = value.split(ACTOR_SEP);
	return id ? { id, type: type ?? '' } : null;
};

/** An agent id, as the platform mints them (`agnt_…`). */
const AGENT_ID = /^agnt_[a-z0-9]+$/i;
/** A user id (`usr_…`). */
const USER_ID = /^usr_[a-z0-9]+$/i;

/**
 * The ID box: one field for the ids the app shows elsewhere. An agent or user
 * id becomes the actor filter and empties the box; anything else filters by
 * trace. Clearing the box drops the trace filter.
 */
function IdFilter() {
	const filters = useMonitorFilters();
	const applied = filters.traceId ?? '';
	const [draft, setDraft] = useState(applied);
	// Follow the URL (a deep link, a Reset) while the operator isn't typing.
	useEffect(() => {
		setDraft(applied);
	}, [applied]);
	const commit = (): void => {
		const value = draft.trim();
		if (!value) {
			filters.setTraceId(null);
			return;
		}
		if (AGENT_ID.test(value) || USER_ID.test(value)) {
			filters.setTraceId(null);
			filters.setActor(value, AGENT_ID.test(value) ? 'agent' : 'user');
			setDraft('');
			return;
		}
		filters.setTraceId(value);
	};
	return (
		<Input
			size="sm"
			aria-label="Filter by ID"
			title="An agent id (agnt_…) filters by that agent; a trace id filters to one call."
			placeholder="Agent or trace ID"
			value={draft}
			onChange={(e) => setDraft(e.target.value)}
			onBlur={commit}
			onKeyDown={(e) => {
				if (e.key === 'Enter') {
					e.preventDefault();
					commit();
				}
			}}
			spellCheck={false}
			autoComplete="off"
			className={cn(
				'h-[1.875rem] w-52 font-mono text-xs',
				applied && 'bg-primary/15 text-foreground',
			)}
			data-testid="monitor-id-filter"
		/>
	);
}

export function MonitorFilterBar({ view }: MonitorFilterBarProps) {
	const filters = useMonitorFilters();
	const actorsQuery = useActors();
	const actors = actorsQuery.data?.data ?? [];

	const actorDisabled = view === 'jobs';
	const selectValue =
		filters.actorId && filters.actorType ? encodeActor(filters.actorId, filters.actorType) : '';

	const isUsage = view === 'usage';
	const windowOptions: SegmentedToggleOption<WindowValue>[] = isUsage
		? WINDOW_OPTIONS.filter((o) => o.value !== 'all')
		: WINDOW_OPTIONS;
	const windowValue: WindowValue = isUsage && filters.window === 'all' ? '30' : filters.window;

	return (
		<>
			<SegmentedToggle
				options={windowOptions}
				value={windowValue}
				onChange={filters.setWindow}
				ariaLabel="Time window"
			/>

			<FilterSelect
				icon={<Users className="h-3.5 w-3.5" />}
				// On the Overview's phone toolbar the picker takes its own row, so
				// the window and the refresh control share the first.
				className={cn(
					'min-w-[9rem] flex-1 sm:w-48 sm:flex-none',
					isUsage && 'max-sm:order-last max-sm:basis-full',
				)}
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
			</FilterSelect>

			{/* Origin scope — API calls only (local-MCP 2-E2): a picker, not a
			    chip, since origins are a small closed set worth browsing. */}
			{view === 'calls' && (
				<FilterSelect
					icon={<Route className="h-3.5 w-3.5" />}
					className="w-36"
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
				</FilterSelect>
			)}
			{view === 'calls' && <IdFilter />}
		</>
	);
}
