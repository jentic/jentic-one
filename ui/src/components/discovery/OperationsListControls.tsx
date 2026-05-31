import { Loader2 } from 'lucide-react';
import { Button } from '@/components/ui/Button';
import { SearchInput } from '@/components/ui/SearchInput';

/**
 * Operation row shape consumed by the list helpers below. Both
 * `/apis/{api_id}/operations` (workspace) and `/catalog/{api_id}/operations`
 * (directory) rows project onto this shared shape after a tiny adapter at
 * the call site.
 */
export interface OpRow {
	key: string;
	method?: string;
	path?: string;
	label: string;
	tags: string[];
}

export const OPS_PAGE_SIZE = 25;
export const TAG_CHIP_LIMIT = 8;

/**
 * Cap the visible tag chips to the most-frequent N. Stable order:
 * descending by frequency, then alphabetical. Returns the de-duplicated,
 * sorted list — caller decides how many to render.
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
 * Apply the inline filter (case-insensitive substring over label, path,
 * method) and the tag chip filter to the accumulated rows. Returns a new
 * array — kept pure so `useMemo` can cache against the inputs.
 */
export function filterOps(rows: OpRow[], filter: string, tag: string | null): OpRow[] {
	const q = filter.trim().toLowerCase();
	return rows.filter((row) => {
		if (tag && !row.tags.some((t) => t.toLowerCase() === tag.toLowerCase())) {
			return false;
		}
		if (!q) return true;
		const hay =
			(row.label ?? '').toLowerCase() +
			' ' +
			(row.path ?? '').toLowerCase() +
			' ' +
			(row.method ?? '').toLowerCase();
		return hay.includes(q);
	});
}

/**
 * Inline filter input + tag chip bar above the operations list.
 */
export function OperationsListToolbar({
	filter,
	onFilterChange,
	onFilterFocus,
	tags,
	activeTag,
	onTagChange,
	totalOps,
	isLoadingAll,
}: {
	filter: string;
	onFilterChange: (next: string) => void;
	onFilterFocus?: () => void;
	tags: string[];
	activeTag: string | null;
	onTagChange: (next: string | null) => void;
	totalOps: number;
	isLoadingAll?: boolean;
}) {
	const showInput = totalOps > 5;
	const visibleTags = tags.slice(0, TAG_CHIP_LIMIT);
	const showTags = visibleTags.length >= 2;

	if (!showInput && !showTags) return null;

	return (
		<div className="mb-3 space-y-2">
			{showInput && (
				<SearchInput
					value={filter}
					onValueChange={onFilterChange}
					onFocus={onFilterFocus}
					placeholder={`Search ${totalOps} operations…`}
					aria-label="Search operations"
					size="sm"
					loading={isLoadingAll}
					data-testid="ops-filter-input"
				/>
			)}
			{showTags && (
				<div className="flex flex-wrap gap-1" data-testid="ops-tag-bar">
					<button
						type="button"
						onClick={() => onTagChange(null)}
						aria-pressed={activeTag === null}
						className={
							'rounded-full px-2 py-0.5 text-[10px] font-medium transition-colors ' +
							(activeTag === null
								? 'bg-primary/15 text-foreground ring-primary/30 ring-1'
								: 'bg-muted/60 text-muted-foreground hover:bg-muted')
						}
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
								className={
									'rounded-full px-2 py-0.5 text-[10px] font-medium transition-colors ' +
									(active
										? 'bg-primary/15 text-foreground ring-primary/30 ring-1'
										: 'bg-muted/60 text-muted-foreground hover:bg-muted')
								}
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
 * Footer row beneath the operations list. Renders one of three states:
 *   1. nothing (everything loaded, no filter activity)
 *   2. "Showing N of M" + Load more button (more pages available)
 *   3. "Showing N of M (filtered)" — purely informational when filters trim
 *      the visible list below the loaded count.
 */
export function OperationsListFooter({
	visible,
	loaded,
	total,
	hasMore,
	isFetchingMore,
	onLoadMore,
}: {
	visible: number;
	loaded: number;
	total: number;
	hasMore: boolean;
	isFetchingMore: boolean;
	onLoadMore: () => void;
}) {
	if (!hasMore && visible === loaded) return null;

	const remaining = total - loaded;
	const nextBatch = Math.min(remaining, OPS_PAGE_SIZE);
	const filtered = visible !== loaded;

	return (
		<div className="border-border/40 mt-2 flex items-center justify-between gap-3 border-t pt-3 text-xs">
			<span className="text-muted-foreground">
				Showing {visible} of {total}
				{filtered && <span className="text-muted-foreground/70"> (filtered)</span>}
			</span>
			{hasMore && (
				<Button
					variant="ghost"
					size="sm"
					onClick={onLoadMore}
					disabled={isFetchingMore}
					data-testid="ops-load-more"
				>
					{isFetchingMore ? (
						<>
							<Loader2 className="mr-1 h-3 w-3 animate-spin" /> Loading…
						</>
					) : (
						<>Load {nextBatch} more</>
					)}
				</Button>
			)}
		</div>
	);
}
