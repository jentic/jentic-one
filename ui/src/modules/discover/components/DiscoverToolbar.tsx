/**
 * DiscoverToolbar — sticky search field + registration filter + refresh.
 *
 * Built entirely from shared primitives (SearchInput, SegmentedToggle,
 * RefreshButton). The filter maps onto the catalog query:
 * All (no flag) / Available (`unregistered_only`) / Updates (`outdated_only`,
 * registered entries with an upstream update). What's already in your
 * workspace is the docked panel's job, and each row's status.
 *
 * Sticky-on-scroll: the bar pins below the fixed `h-12` TopNavbar
 * (`sticky top-0` of the shell's scroller). It stays inside its column (no
 * gutter bleed) because it sits beside the Library's docked workspace panel,
 * and a full-width backdrop would spill across that neighbouring column.
 * A 1px sentinel in normal flow just above the bar, observed against the
 * shell scroller, flips `data-scrolled` once it leaves the top edge — i.e.
 * once the bar has actually stuck — to drop the header's hairline under it.
 * The bar itself is page-tinted glass (90% + blur) with borderless tonal
 * controls — no rule at rest; the catalog's tonal table already separates
 * the two.
 */
import { useEffect, useRef } from 'react';
import { SearchInput, SegmentedToggle, RefreshButton } from '@/shared/ui';
import { shellScrollRoot } from '@/shared/lib';
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
	// The ledger shows no marker for these rows: a blank status is "available".
	{ value: 'unregistered', label: 'Available' },
	{ value: 'outdated', label: 'Update available' },
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
		// The sentinel sits in normal flow right above the bar, so it leaves the
		// scroller's top edge exactly when the bar (`top-0`) pins there. Observed
		// against the shell scroller itself — the viewport root would be offset
		// by the fixed navbar the scroller already starts under.
		const obs = new IntersectionObserver(
			([entry]) => {
				if (!entry) return;
				// Only "scrolled past the top" counts — not "below the fold".
				const pinned =
					!entry.isIntersecting &&
					entry.boundingClientRect.top < (entry.rootBounds?.top ?? 0);
				toolbar.dataset.scrolled = pinned ? 'true' : 'false';
			},
			{ root: shellScrollRoot(), threshold: 0 },
		);
		obs.observe(sentinel);
		return () => obs.disconnect();
	}, []);

	return (
		<>
			{/* 1px tall, pulled back out of the layout so the page doesn't shift. */}
			<div
				ref={sentinelRef}
				aria-hidden="true"
				className="-mb-px h-px"
				data-testid="discover-toolbar-sentinel"
			/>
			<div
				ref={toolbarRef}
				data-scrolled="false"
				className="bg-toolbar-glass sticky top-0 z-20 -mx-1 mb-3 px-1 py-3 backdrop-blur-[8px] transition-shadow data-[scrolled=true]:shadow-[0_1px_0_0_var(--color-hairline)]"
				data-testid="discover-toolbar"
			>
				<div className="flex flex-col gap-3 sm:flex-row sm:items-center">
					<div className="flex-1">
						<SearchInput
							value={query}
							onValueChange={onQueryChange}
							placeholder="Search APIs by name or vendor…"
							loading={loading}
							aria-label="Search APIs"
							tone="surface"
						/>
					</div>
					<div className="flex items-center gap-2">
						<SegmentedToggle
							layoutId="discover-filter"
							options={FILTER_OPTIONS}
							value={filter}
							onChange={onFilterChange}
							tone="surface"
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
		</>
	);
}
