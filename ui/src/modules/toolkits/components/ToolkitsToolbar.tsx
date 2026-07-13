/**
 * ToolkitsToolbar — sticky filter + status filter + refresh for the toolkit
 * list.
 *
 * Mirrors the Credentials/Discover toolbar treatment for cross-page parity: the
 * bar pins below the fixed `h-12` TopNavbar (`sticky top-12`), bleeds to the
 * page gutter edges (`-mx-page-gutter px-page-gutter`) so its backdrop-blur
 * covers the full width, and drops a hairline shadow only once it has actually
 * stuck (a zero-height sentinel + IntersectionObserver flips `data-scrolled`).
 *
 * Note: this is a *filter* over the toolkits already fetched from the backend
 * `GET /toolkits` list (itself identity-scoped), not a separate backend search —
 * the page narrows its in-memory list by name/description client-side, and the
 * status segments narrow on the toolkit's `active` flag (All / Active /
 * Suspended). Copy reflects that ("Filter…").
 *
 * Layout (matching Credentials): a full-width text filter takes the remaining
 * width, with the status filter + refresh pinned to the right.
 */
import { useEffect, useRef } from 'react';
import { Filter } from 'lucide-react';
import { RefreshButton, SearchInput, SegmentedToggle } from '@/shared/ui';

/** `all` plus the two derived states of a toolkit's `active` flag. */
export type ToolkitStatusFilter = 'all' | 'active' | 'suspended';

interface ToolkitsToolbarProps {
	query: string;
	onQueryChange: (value: string) => void;
	filter: ToolkitStatusFilter;
	onFilterChange: (value: ToolkitStatusFilter) => void;
	onRefresh: () => void;
	/** Keeps the refresh glyph spinning while a refetch is in flight. */
	refreshing?: boolean;
}

const FILTER_OPTIONS: { value: ToolkitStatusFilter; label: string }[] = [
	{ value: 'all', label: 'All' },
	{ value: 'active', label: 'Active' },
	{ value: 'suspended', label: 'Suspended' },
];

export function ToolkitsToolbar({
	query,
	onQueryChange,
	filter,
	onFilterChange,
	onRefresh,
	refreshing,
}: ToolkitsToolbarProps) {
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

	return (
		<div
			ref={toolbarRef}
			data-scrolled="false"
			className="-mx-page-gutter px-page-gutter border-border/40 bg-background/85 sticky top-12 z-20 border-b py-3 backdrop-blur transition-shadow data-[scrolled=true]:shadow-[0_1px_0_0_rgb(0_0_0_/0.04)]"
			data-testid="toolkits-toolbar"
		>
			<div ref={sentinelRef} aria-hidden="true" className="absolute top-0 h-px w-full" />
			<div className="flex flex-col gap-3 sm:flex-row sm:items-center">
				<div className="flex-1">
					<SearchInput
						value={query}
						onValueChange={onQueryChange}
						placeholder="Filter by name or description…"
						aria-label="Filter toolkits"
						icon={<Filter className="h-3.5 w-3.5" />}
					/>
				</div>
				<div className="flex items-center gap-2">
					<SegmentedToggle
						layoutId="toolkits-filter"
						options={FILTER_OPTIONS}
						value={filter}
						onChange={onFilterChange}
					/>
					<RefreshButton
						onRefresh={onRefresh}
						pending={refreshing}
						title="Refresh toolkits"
						testId="toolkits-refresh"
					/>
				</div>
			</div>
		</div>
	);
}
