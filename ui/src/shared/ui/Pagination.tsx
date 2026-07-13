import { ChevronLeft, ChevronRight } from 'lucide-react';
import { Button } from '@/shared/ui/Button';
import { cn } from '@/shared/lib/utils';

interface PaginationProps {
	page: number;
	totalPages: number;
	onPageChange: (page: number) => void;
	className?: string;
	/**
	 * When both `totalCount` and `pageSize` are provided, the component
	 * renders the richer "X–Y of N" range summary on the left with
	 * chevron-only buttons + "Page X of Y" on the right. Without these
	 * props the component falls back to a compact chevron pill with
	 * "Page X of Y" only.
	 */
	totalCount?: number;
	pageSize?: number;
}

function ChevronPill({
	page,
	totalPages,
	onPageChange,
	pageLabel,
}: {
	page: number;
	totalPages: number;
	onPageChange: (page: number) => void;
	pageLabel: string;
}) {
	const segmentClasses = 'h-8 w-8 rounded-none p-0 transition-colors disabled:cursor-not-allowed';

	return (
		<div
			className={cn(
				'border-border bg-card/40 inline-flex items-stretch overflow-hidden rounded-lg border',
				'divide-border/80 divide-x',
			)}
		>
			<Button
				variant="ghost"
				aria-label="Previous page"
				disabled={page <= 1}
				onClick={() => onPageChange(page - 1)}
				className={cn(segmentClasses, 'rounded-l-lg')}
			>
				<ChevronLeft className="h-4 w-4" />
			</Button>
			<span
				className="text-muted-foreground flex items-center px-3 text-xs font-medium tabular-nums"
				aria-live="polite"
			>
				{pageLabel}
			</span>
			<Button
				variant="ghost"
				aria-label="Next page"
				disabled={page >= totalPages}
				onClick={() => onPageChange(page + 1)}
				className={cn(segmentClasses, 'rounded-r-lg')}
			>
				<ChevronRight className="h-4 w-4" />
			</Button>
		</div>
	);
}

export function Pagination({
	page,
	totalPages,
	onPageChange,
	className,
	totalCount,
	pageSize,
}: PaginationProps) {
	if (totalPages <= 0) return null;

	const showRange = totalCount != null && pageSize != null && totalCount > 0;
	const startIndex = showRange ? (page - 1) * (pageSize ?? 0) + 1 : 0;
	const endIndex = showRange ? Math.min(page * (pageSize ?? 0), totalCount ?? 0) : 0;
	const pageLabel = `Page ${page} of ${totalPages}`;

	if (showRange) {
		return (
			<nav
				aria-label="Pagination"
				className={cn(
					'border-border/60 flex items-center justify-between gap-3 border-t px-4 py-2.5',
					className,
				)}
			>
				<span className="text-muted-foreground text-xs tabular-nums">
					{startIndex.toLocaleString()}–{endIndex.toLocaleString()} of{' '}
					{(totalCount as number).toLocaleString()}
				</span>
				<ChevronPill
					page={page}
					totalPages={totalPages}
					onPageChange={onPageChange}
					pageLabel={pageLabel}
				/>
			</nav>
		);
	}

	return (
		<nav aria-label="Pagination" className={cn('flex items-center justify-end', className)}>
			<ChevronPill
				page={page}
				totalPages={totalPages}
				onPageChange={onPageChange}
				pageLabel={pageLabel}
			/>
		</nav>
	);
}
