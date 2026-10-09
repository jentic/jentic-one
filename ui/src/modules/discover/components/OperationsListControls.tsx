/**
 * OperationsListControls — search + tag-chip toolbar and a "Load more" footer
 * for the operations list inside the API detail sheet.
 *
 * Filtering is SERVER-SIDE: the toolbar is controlled (the sheet owns the
 * search text + active tag and forwards them to the preview query as `q`/`tag`),
 * so a search covers every operation in the spec — not just the loaded page.
 * Presentational only.
 */
import type React from 'react';
import { Filter } from 'lucide-react';
import { Button, SearchInput } from '@/shared/ui';
import { cn } from '@/shared/lib/utils';
import { OPERATION_PREVIEW_PAGE_SIZE } from '@/modules/discover/api';

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

const TAG_CHIP_LIMIT = 8;

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
 * Filter field + tag chip bar above the operations list. The filter is always
 * there (it searches the whole spec server-side, so it's useful even when few
 * ops are loaded yet); the tag bar only shows when ≥2 tags are known from the
 * loaded operations.
 */
export function OperationsListToolbar({
	filter,
	onFilterChange,
	tags,
	activeTag,
	onTagChange,
}: {
	filter: string;
	onFilterChange: (next: string) => void;
	tags: string[];
	activeTag: string | null;
	onTagChange: (next: string | null) => void;
}) {
	const visibleTags = tags.slice(0, TAG_CHIP_LIMIT);
	const showTags = visibleTags.length >= 2 || activeTag !== null;

	return (
		<div className="mb-3">
			<SearchInput
				value={filter}
				onValueChange={onFilterChange}
				placeholder="Filter operations"
				aria-label="Filter operations"
				tone="inset"
				icon={<Filter className="h-3.5 w-3.5" aria-hidden="true" />}
				data-testid="ops-filter-input"
			/>
			{showTags && (
				<div className="mt-2.5 flex flex-wrap gap-1.5" data-testid="ops-tag-bar">
					<TagChip active={activeTag === null} onClick={() => onTagChange(null)}>
						All
					</TagChip>
					{visibleTags.map((tag) => {
						const active = activeTag === tag;
						return (
							<TagChip
								key={tag}
								active={active}
								onClick={() => onTagChange(active ? null : tag)}
								data-testid="ops-tag-chip"
							>
								{tag}
							</TagChip>
						);
					})}
				</div>
			)}
		</div>
	);
}

/** A toggle pill in the tag bar; `aria-pressed` carries the selection. */
function TagChip({
	active,
	onClick,
	children,
	...rest
}: {
	active: boolean;
	onClick: () => void;
	children: React.ReactNode;
	'data-testid'?: string;
}) {
	return (
		<Button
			variant="ghost"
			onClick={onClick}
			aria-pressed={active}
			className={cn(
				'h-auto rounded-full px-2.5 py-[3px] text-xs font-semibold active:scale-100',
				active
					? 'bg-surface-chip-active hover:bg-surface-chip-active text-foreground hover:text-foreground'
					: 'bg-surface-field text-muted-foreground hover:bg-surface-chip hover:text-foreground-lighter',
			)}
			{...rest}
		>
			{children}
		</Button>
	);
}

/**
 * Footer beneath the operations list: how many of the (filtered) total are
 * loaded, and a "Load N more" control that pages in the next batch.
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
	const nextBatch = Math.min(OPERATION_PREVIEW_PAGE_SIZE, Math.max(0, total - loaded));
	return (
		<div className="mt-2 flex items-center justify-between gap-3">
			<p className="text-foreground-faint text-xs">
				Showing {loaded} of {total}
			</p>
			{hasNextPage && (
				<Button
					variant="ghost"
					size="sm"
					loading={isFetchingNextPage}
					onClick={onLoadMore}
					className="text-muted-foreground hover:bg-tint-2 rounded-field hover:text-foreground text-[13px] font-semibold"
					data-testid="ops-load-more"
				>
					{isFetchingNextPage ? 'Loading…' : `Load ${nextBatch} more`}
				</Button>
			)}
		</div>
	);
}
