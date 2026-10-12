import React from 'react';
import { LoadingState } from '@/shared/ui/LoadingState';
import { useMediaQuery } from '@/shared/hooks/useMediaQuery';
import { cn } from '@/shared/lib/utils';

export type Column<T> = {
	key: keyof T | string;
	header: string;
	render?: (row: T) => React.ReactNode;
	className?: string;
};

interface DataTableProps<T> {
	columns: Column<T>[];
	data: T[];
	getRowKey: (row: T) => string;
	emptyMessage?: string;
	isLoading?: boolean;
	className?: string;
	onRowClick?: (row: T) => void;
	/**
	 * Accessible name for the horizontally-scrollable region wrapping the table.
	 * The wrapper is keyboard-focusable (so keyboard users can scroll it), which
	 * axe requires to carry a name. Defaults to a generic label.
	 */
	ariaLabel?: string;
	/**
	 * Accessible name for a clickable row. Only used when `onRowClick` is set —
	 * clickable rows are exposed as `role="button"` and keyboard-activatable, so
	 * they need a name (e.g. "View execution POST /v1/charges").
	 */
	getRowLabel?: (row: T) => string;
	/**
	 * Optional mobile renderer. When provided, the data table renders a stacked
	 * card list below the `sm` breakpoint and the regular table at `sm` and up.
	 * Each card receives the full row; the component handles the clickable
	 * wrapper (role/tabindex/aria-label/keyboard) so the card body stays purely
	 * presentational. Omit it to keep the table-only behaviour (the table just
	 * horizontally scrolls on small screens).
	 */
	renderCard?: (row: T) => React.ReactNode;
}

export function DataTable<T>({
	columns,
	data,
	getRowKey,
	emptyMessage = 'No data found.',
	isLoading,
	className,
	onRowClick,
	ariaLabel = 'Scrollable table',
	getRowLabel,
	renderCard,
}: DataTableProps<T>) {
	// Render genuinely different DOM for phones (card list) vs. desktop (table)
	// rather than mounting both and toggling with CSS — that would duplicate the
	// content in the accessibility tree. Tailwind's `sm` breakpoint is 640px.
	const isMobile = useMediaQuery('(max-width: 639px)');

	if (isLoading) {
		return <LoadingState />;
	}

	if (data.length === 0) {
		return <p className="text-muted-foreground py-8 text-center text-sm">{emptyMessage}</p>;
	}

	// Mobile: stacked cards. Only when a card renderer is supplied; otherwise we
	// fall through to the table (which horizontally scrolls on small screens).
	if (renderCard && isMobile) {
		return (
			<ul className="space-y-2" aria-label={ariaLabel}>
				{data.map((row) => {
					const card = (
						<div className="bg-surface-1 rounded-lg p-3 transition-colors duration-[140ms]">
							{renderCard(row)}
						</div>
					);
					return (
						<li key={getRowKey(row)}>
							{onRowClick ? (
								<button
									type="button"
									onClick={() => onRowClick(row)}
									aria-label={getRowLabel?.(row)}
									className="focus-visible:ring-ring [&:hover>div]:bg-surface-1-hover block w-full rounded-lg text-left outline-none focus-visible:ring-2"
								>
									{card}
								</button>
							) : (
								card
							)}
						</li>
					);
				})}
			</ul>
		);
	}

	return (
		<div
			className={cn(
				'focus-visible:ring-ring overflow-x-auto rounded outline-none focus-visible:ring-2',
				className,
			)}
			role="region"
			aria-label={ariaLabel}
			tabIndex={0}
		>
			<table className="w-full border-collapse">
				<thead>
					<tr className="text-left shadow-[0_1px_0_hsl(var(--hairline-head))]">
						{columns.map((col) => (
							<th
								key={String(col.key)}
								className={cn(
									'text-foreground-faint h-9 px-4 text-[10.5px] font-bold tracking-[0.08em] uppercase',
									col.className,
								)}
							>
								{col.header}
							</th>
						))}
					</tr>
				</thead>
				<tbody>
					{data.map((row) => (
						<tr
							key={getRowKey(row)}
							onClick={onRowClick ? () => onRowClick(row) : undefined}
							onKeyDown={
								onRowClick
									? (e) => {
											if (e.key === 'Enter' || e.key === ' ') {
												e.preventDefault();
												onRowClick(row);
											}
										}
									: undefined
							}
							role={onRowClick ? 'button' : undefined}
							tabIndex={onRowClick ? 0 : undefined}
							aria-label={onRowClick ? getRowLabel?.(row) : undefined}
							className={cn(
								'border-hairline-row hover:bg-surface-1-hover border-b transition-colors duration-[140ms] last:border-0',
								onRowClick &&
									'focus-visible:bg-surface-1-hover cursor-pointer outline-none focus-visible:shadow-[inset_0_0_0_1px_hsl(var(--primary)/0.35)]',
							)}
						>
							{columns.map((col) => (
								<td
									key={String(col.key)}
									className={cn('px-4 py-3 text-sm', col.className)}
								>
									{col.render
										? col.render(row)
										: String(
												(row as Record<string, unknown>)[String(col.key)] ??
													'',
											)}
								</td>
							))}
						</tr>
					))}
				</tbody>
			</table>
		</div>
	);
}
