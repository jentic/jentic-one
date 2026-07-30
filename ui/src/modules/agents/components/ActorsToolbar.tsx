/**
 * ActorsToolbar — filter + status segments + refresh for the agents /
 * service-accounts fleet table.
 *
 * Mirrors the Toolkits/Credentials toolbar treatment for cross-page parity:
 * sticky below the fixed `h-12` TopNavbar, bleeding to the page gutter edges
 * so its backdrop-blur covers the full width, with a hairline shadow once
 * stuck (zero-height sentinel + IntersectionObserver flips `data-scrolled`).
 *
 * This is a *filter* over rows already fetched from the cursor-paginated
 * backend list, not a backend search — hence the `<Filter />` affordance and
 * "Filter…" copy. The status segments narrow on the closed `ActorStatus`
 * vocabulary and carry live counts so the fleet's shape is readable at a
 * glance without a separate badge strip.
 */
import { useEffect, useRef } from 'react';
import { Filter } from 'lucide-react';
import { RefreshButton, SearchInput, SegmentedToggle } from '@/shared/ui';
import { ACTOR_STATUSES, STATUS_LABELS, type ActorStatus } from '@/modules/agents/api';

/** `all` plus the closed actor-status vocabulary. */
export type ActorStatusFilter = 'all' | ActorStatus;

interface ActorsToolbarProps {
	query: string;
	onQueryChange: (value: string) => void;
	filter: ActorStatusFilter;
	onFilterChange: (value: ActorStatusFilter) => void;
	/** Loaded-row count per status, used to label the segments. */
	counts: Record<ActorStatusFilter, number>;
	/** Lower-case plural noun for placeholders/labels (e.g. "agents"). */
	nounPlural: string;
	/** Disables the filter input when there is nothing to narrow. */
	disabled?: boolean;
	onRefresh: () => void;
	/** Keeps the refresh glyph spinning while a refetch is in flight. */
	refreshing?: boolean;
}

export function ActorsToolbar({
	query,
	onQueryChange,
	filter,
	onFilterChange,
	counts,
	nounPlural,
	disabled,
	onRefresh,
	refreshing,
}: ActorsToolbarProps) {
	const sentinelRef = useRef<HTMLDivElement | null>(null);
	const toolbarRef = useRef<HTMLDivElement | null>(null);

	useEffect(() => {
		const sentinel = sentinelRef.current;
		const toolbar = toolbarRef.current;
		if (!sentinel || !toolbar || typeof IntersectionObserver === 'undefined') return;
		const obs = new IntersectionObserver(
			([entry]) => {
				toolbar.dataset.scrolled = entry.isIntersecting ? 'false' : 'true';
			},
			{ threshold: 0 },
		);
		obs.observe(sentinel);
		return () => obs.disconnect();
	}, []);

	const options: { value: ActorStatusFilter; label: string }[] = [
		{ value: 'all', label: `All ${counts.all}` },
		...ACTOR_STATUSES.map((status) => ({
			value: status,
			label: `${STATUS_LABELS[status]} ${counts[status]}`,
		})),
	];

	return (
		<div
			ref={toolbarRef}
			data-scrolled="false"
			className="-mx-page-gutter px-page-gutter border-border/40 bg-background/85 sticky top-12 z-20 border-b py-3 backdrop-blur transition-shadow data-[scrolled=true]:shadow-[0_1px_0_0_rgb(0_0_0_/0.04)]"
			data-testid="actors-toolbar"
		>
			<div ref={sentinelRef} aria-hidden="true" className="absolute top-0 h-px w-full" />
			<div className="flex flex-col gap-3 lg:flex-row lg:items-center">
				<div className="min-w-0 flex-1">
					<SearchInput
						value={query}
						onValueChange={onQueryChange}
						placeholder={`Filter ${nounPlural} by name or id…`}
						aria-label={`Filter ${nounPlural}`}
						icon={<Filter className="h-3.5 w-3.5" />}
						disabled={disabled}
					/>
				</div>
				<div className="flex min-w-0 items-center gap-2">
					<div className="min-w-0 overflow-x-auto">
						<SegmentedToggle<ActorStatusFilter>
							options={options}
							value={filter}
							onChange={onFilterChange}
							ariaLabel={`Filter ${nounPlural} by status`}
							className="w-max"
						/>
					</div>
					<RefreshButton
						onRefresh={onRefresh}
						pending={refreshing}
						title={`Refresh ${nounPlural}`}
						testId="actors-refresh"
					/>
				</div>
			</div>
		</div>
	);
}
