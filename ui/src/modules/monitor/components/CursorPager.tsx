/**
 * CursorPager — a "Newer / Older" pager for the Monitor list tabs.
 *
 * The Monitor list endpoints are forward-only cursor APIs (no totals, no page
 * numbers), so we offer a two-button stepper instead of numbered pagination:
 *   - "Older" advances to the next page (disabled when `!hasMore`).
 *   - "Newer" steps back (disabled on the first page).
 *
 * The page indicator is `aria-live` so screen readers announce the page change.
 * The whole control is hidden when there's nothing to page through (first page
 * AND no more results).
 */
import { ChevronLeft, ChevronRight } from 'lucide-react';
import { Button } from '@/shared/ui';

interface CursorPagerProps {
	/** The current response says there's another page after this one. */
	hasMore: boolean;
	/** True when not on the first page. */
	hasPrev: boolean;
	onOlder: () => void;
	onNewer: () => void;
	/** 1-based index of the current page (for the indicator). */
	page: number;
	loading?: boolean;
}

export function CursorPager({
	hasMore,
	hasPrev,
	onOlder,
	onNewer,
	page,
	loading = false,
}: CursorPagerProps) {
	// Nothing to page: single page of results.
	if (!hasMore && !hasPrev) return null;

	return (
		<nav aria-label="Pagination" className="flex items-center justify-end gap-3 pt-1">
			<span className="text-muted-foreground text-xs" aria-live="polite">
				Page {page}
			</span>
			<div className="flex items-center gap-1">
				<Button
					variant="secondary"
					size="sm"
					onClick={onNewer}
					disabled={!hasPrev || loading}
					aria-label="Newer results"
				>
					<ChevronLeft className="h-4 w-4" />
					Newer
				</Button>
				<Button
					variant="secondary"
					size="sm"
					onClick={onOlder}
					disabled={!hasMore || loading}
					aria-label="Older results"
				>
					Older
					<ChevronRight className="h-4 w-4" />
				</Button>
			</div>
		</nav>
	);
}
