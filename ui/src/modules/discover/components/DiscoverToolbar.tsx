/**
 * DiscoverToolbar — sticky search field + registration filter + refresh.
 *
 * Built entirely from shared primitives (SearchInput, SegmentedToggle,
 * RefreshButton). The filter maps onto the catalog query:
 * All (no flag) / Imported (`registered_only`) / Available (`unregistered_only`).
 *
 * Sticky-on-scroll (jentic-mini parity): the bar pins below the fixed `h-12`
 * TopNavbar (`sticky top-12`) and bleeds to the page gutter edges
 * (`-mx-page-gutter px-page-gutter`) so its backdrop-blur covers the full width.
 * A zero-height sentinel + IntersectionObserver flips `data-scrolled` so we can
 * drop a hairline shadow only once the bar has actually stuck.
 */
import { useEffect, useRef } from 'react';
import { SearchInput, SegmentedToggle, RefreshButton } from '@/shared/ui';
import type { CatalogFilter } from '@/modules/discover/api';

interface DiscoverToolbarProps {
	query: string;
	onQueryChange: (value: string) => void;
	filter: CatalogFilter;
	onFilterChange: (value: CatalogFilter) => void;
	onRefresh: () => void;
	loading?: boolean;
	/** Keeps the refresh glyph spinning while the backend rebuild is in flight. */
	refreshing?: boolean;
}

const FILTER_OPTIONS: { value: CatalogFilter; label: string }[] = [
	{ value: 'all', label: 'All' },
	{ value: 'registered', label: 'Imported' },
	{ value: 'unregistered', label: 'Available' },
];

export function DiscoverToolbar({
	query,
	onQueryChange,
	filter,
	onFilterChange,
	onRefresh,
	loading,
	refreshing,
}: DiscoverToolbarProps) {
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
			data-testid="discover-toolbar"
		>
			<div ref={sentinelRef} aria-hidden="true" className="absolute top-0 h-px w-full" />
			<div className="flex flex-col gap-3 sm:flex-row sm:items-center">
				<div className="flex-1">
					<SearchInput
						value={query}
						onValueChange={onQueryChange}
						placeholder="Search APIs by name or vendor…"
						loading={loading}
						aria-label="Search APIs"
					/>
				</div>
				<div className="flex items-center gap-2">
					<SegmentedToggle
						layoutId="discover-filter"
						options={FILTER_OPTIONS}
						value={filter}
						onChange={onFilterChange}
					/>
					<RefreshButton
						onRefresh={onRefresh}
						pending={refreshing}
						title="Refresh the public catalog"
						testId="discover-refresh"
					/>
				</div>
			</div>
		</div>
	);
}
