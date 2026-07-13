/**
 * OperationsListControls — search + tag-chip toolbar and a "Load more" footer
 * for the operations list inside the API detail sheet.
 *
 * Filtering is SERVER-SIDE: the toolbar is controlled (the sheet owns the
 * search text + active tag and forwards them to the preview query as `q`/`tag`),
 * so a search covers every operation in the spec — not just the loaded page.
 * Presentational only.
 */
import { Button, SearchInput } from '@/shared/ui';

/**
 * Row shape the list renders. The sheet projects each
 * `PreviewOperationResponse` onto this.
 */
export interface OpRow {
	key: string;
	method?: string;
	path?: string;
	label: string;
	tags: string[];
}

export const TAG_CHIP_LIMIT = 8;

/**
 * Most-frequent tags first, then alphabetical; de-duplicated. The caller
 * decides how many chips to actually render (see TAG_CHIP_LIMIT).
 */
export function topTags(tags: string[]): string[] {
	const freq = new Map<string, number>();
	for (const t of tags) {
		const k = t.trim();
		if (!k) continue;
		freq.set(k, (freq.get(k) ?? 0) + 1);
	}
	return Array.from(freq.entries())
		.sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
		.map(([k]) => k);
}

/**
 * Search input + tag chip bar above the operations list. Always renders the
 * search box (it filters the whole spec server-side, so it's useful even when
 * few ops are loaded yet); the tag bar only shows when ≥2 tags are known from
 * the loaded operations.
 */
export function OperationsListToolbar({
	filter,
	onFilterChange,
	tags,
	activeTag,
	onTagChange,
	totalOps,
}: {
	filter: string;
	onFilterChange: (next: string) => void;
	tags: string[];
	activeTag: string | null;
	onTagChange: (next: string | null) => void;
	/** Full (filtered) operation count in the spec — shown in the placeholder. */
	totalOps: number;
}) {
	const visibleTags = tags.slice(0, TAG_CHIP_LIMIT);
	const showTags = visibleTags.length >= 2 || activeTag !== null;

	const chipClass = (active: boolean) =>
		'rounded-full px-2 py-0.5 text-[10px] font-medium transition-colors ' +
		(active
			? 'bg-primary/15 text-foreground ring-primary/30 ring-1'
			: 'bg-muted/60 text-muted-foreground hover:bg-muted');

	return (
		<div className="mb-3 space-y-2">
			<SearchInput
				value={filter}
				onValueChange={onFilterChange}
				placeholder={totalOps > 0 ? `Search ${totalOps} operations…` : 'Search operations…'}
				aria-label="Search operations"
				size="sm"
				data-testid="ops-filter-input"
			/>
			{showTags && (
				<div className="flex flex-wrap gap-1" data-testid="ops-tag-bar">
					<button
						type="button"
						onClick={() => onTagChange(null)}
						aria-pressed={activeTag === null}
						className={chipClass(activeTag === null)}
					>
						All
					</button>
					{visibleTags.map((tag) => {
						const active = activeTag === tag;
						return (
							<button
								type="button"
								key={tag}
								onClick={() => onTagChange(active ? null : tag)}
								aria-pressed={active}
								data-testid="ops-tag-chip"
								className={chipClass(active)}
							>
								{tag}
							</button>
						);
					})}
				</div>
			)}
		</div>
	);
}

/**
 * Footer beneath the operations list: shows how many of the (filtered) total
 * are loaded and a "Load more" button to page in the next 25.
 */
export function OperationsListFooter({
	loaded,
	total,
	hasNextPage,
	isFetchingNextPage,
	onLoadMore,
}: {
	loaded: number;
	total: number;
	hasNextPage: boolean;
	isFetchingNextPage: boolean;
	onLoadMore: () => void;
}) {
	if (total === 0) return null;
	return (
		<div className="border-border/40 mt-3 border-t pt-2">
			<p className="text-muted-foreground text-xs">
				Showing {loaded} of {total}
			</p>
			{hasNextPage && (
				<div className="mt-2 flex justify-center">
					<Button
						variant="ghost"
						size="sm"
						loading={isFetchingNextPage}
						onClick={onLoadMore}
						data-testid="ops-load-more"
					>
						{isFetchingNextPage ? 'Loading…' : 'Load more'}
					</Button>
				</div>
			)}
		</div>
	);
}
