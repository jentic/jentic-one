import { ChevronLeft, ChevronRight } from 'lucide-react';
import { Button } from './Button';
import { cn } from '@/lib/utils';

interface PaginationProps {
	page: number;
	totalPages: number;
	onPageChange: (page: number) => void;
	className?: string;
	/**
	 * When both `totalCount` and `pageSize` are provided, the component
	 * renders the richer "X–Y of N" range summary on the left with
	 * chevron-only buttons + "Page X of Y" on the right — mirroring the
	 * footer pattern used in jentic-webapp's ExecutionTable. Without these
	 * props the component falls back to the simpler "[Previous] Page X of Y
	 * [Next]" layout used by the trace/job tables.
	 */
	totalCount?: number;
	pageSize?: number;
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

	if (showRange) {
		return (
			<nav
				aria-label="Pagination"
				className={cn(
					'border-border/60 flex items-center justify-between border-t px-4 py-2.5',
					className,
				)}
			>
				<span className="text-muted-foreground text-xs tabular-nums">
					{startIndex}–{endIndex} of {(totalCount as number).toLocaleString()}
				</span>
				<div className="flex items-center gap-1">
					<Button
						variant="ghost"
						size="icon"
						aria-label="Previous page"
						disabled={page <= 1}
						onClick={() => onPageChange(page - 1)}
						className="h-8 w-8"
					>
						<ChevronLeft className="h-4 w-4" />
					</Button>
					<span
						className="text-muted-foreground px-2 text-xs tabular-nums"
						aria-live="polite"
					>
						Page {page} of {totalPages}
					</span>
					<Button
						variant="ghost"
						size="icon"
						aria-label="Next page"
						disabled={page >= totalPages}
						onClick={() => onPageChange(page + 1)}
						className="h-8 w-8"
					>
						<ChevronRight className="h-4 w-4" />
					</Button>
				</div>
			</nav>
		);
	}

	return (
		<nav aria-label="Pagination" className={cn('flex items-center justify-between', className)}>
			<Button
				variant="secondary"
				size="sm"
				disabled={page <= 1}
				onClick={() => onPageChange(page - 1)}
			>
				<ChevronLeft className="h-4 w-4" /> Previous
			</Button>
			<span className="text-muted-foreground text-sm" aria-live="polite">
				Page {page} of {totalPages}
			</span>
			<Button
				variant="secondary"
				size="sm"
				disabled={page >= totalPages}
				onClick={() => onPageChange(page + 1)}
			>
				Next <ChevronRight className="h-4 w-4" />
			</Button>
		</nav>
	);
}
