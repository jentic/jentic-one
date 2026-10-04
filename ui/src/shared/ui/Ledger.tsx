/**
 * Ledger — a dense, tonal list-table for long catalogues (the Library's API
 * catalog first; credentials / logs later).
 *
 * One `surface-1` table body; 38px rows with zebra striping; a quiet caps
 * header; big thin group headings (letters, "In your workspace"); row
 * actions that fade in on hover / keyboard focus / selection (always shown
 * on touch). No custom key handling: rows are reached with Tab through
 * their own real controls (a row's primary button, its action buttons).
 *
 * ARIA: `role="table"` → `row` → `cell` / `columnheader`. Group headings are
 * rows with a single cell, so the structure stays a valid table.
 *
 * Columns are a CSS grid template shared by the head and every row, set
 * through `--ledger-cols` (callers pass Tailwind arbitrary-property classes
 * so the template can change per breakpoint).
 */
import { forwardRef, type HTMLAttributes, type ReactNode } from 'react';
import { cn } from '@/shared/lib/utils';

/** Shared grid: every row lays its cells on the table's column template. */
const GRID = 'grid grid-cols-[var(--ledger-cols)] items-center gap-x-3.5';

interface LedgerProps extends HTMLAttributes<HTMLDivElement> {
	/** Accessible name for the table ("API catalog"). */
	label: string;
	/** Classes that set `--ledger-cols` (may be responsive). */
	columnsClassName: string;
	children: ReactNode;
}

export const Ledger = forwardRef<HTMLDivElement, LedgerProps>(function Ledger(
	{ label, columnsClassName, children, className, ...props },
	ref,
) {
	return (
		<div
			ref={ref}
			role="table"
			aria-label={label}
			className={cn('bg-surface-1 rounded-lg px-2 pt-0.5 pb-2', columnsClassName, className)}
			{...props}
		>
			{children}
		</div>
	);
});

/** The caps header row. Pass `columnheader` cells (use `LedgerHeadCell`). */
export function LedgerHead({ children, className }: { children: ReactNode; className?: string }) {
	return (
		<div
			role="row"
			className={cn(
				GRID,
				'text-foreground-faint h-9 px-2.5 text-[10.5px] font-bold tracking-[0.08em] uppercase shadow-[0_1px_0_hsl(185_20%_60%/0.08)]',
				className,
			)}
		>
			{children}
		</div>
	);
}

export function LedgerHeadCell({
	children,
	sorted,
	className,
}: {
	children: ReactNode;
	/** The column the list is ordered by — shown brighter with a ↓. */
	sorted?: boolean;
	className?: string;
}) {
	return (
		<span
			role="columnheader"
			aria-sort={sorted ? 'ascending' : undefined}
			className={cn(sorted && 'text-muted-foreground', className)}
		>
			{children}
			{sorted && <span aria-hidden="true"> ↓</span>}
		</span>
	);
}

/** A big thin group heading ("In your workspace", "A", "0–9"). */
export const LedgerGroupHeading = forwardRef<
	HTMLDivElement,
	{ label: ReactNode; detail?: ReactNode; className?: string; id?: string }
>(function LedgerGroupHeading({ label, detail, className, id }, ref) {
	return (
		<div ref={ref} role="row" id={id} className={cn('scroll-mt-[76px]', className)}>
			<div
				role="cell"
				className="font-heading text-foreground-group flex h-[34px] items-end px-2.5 pb-1.5 text-[17px] font-light tracking-[-0.01em]"
			>
				{label}
				{detail && (
					<small className="text-foreground-faint ml-2.5 font-sans text-[11px] font-semibold tracking-normal">
						{detail}
					</small>
				)}
			</div>
		</div>
	);
});

interface LedgerRowProps extends HTMLAttributes<HTMLDivElement> {
	/** Odd row: the zebra stripe. */
	zebra?: boolean;
	/** The row whose preview is open (accent bar). */
	selected?: boolean;
	/** Clickable (hover tint; a click anywhere runs `onActivate`). Default true. */
	interactive?: boolean;
	/**
	 * The row's pointer action. Keyboard users reach the same action through
	 * the row's own primary button (Tab → Enter/Space), so the row itself is
	 * not focusable.
	 */
	onActivate?: () => void;
	/** Row actions (rendered over the row's right edge — use `LedgerRowActions`). */
	actions?: ReactNode;
	children: ReactNode;
}

export const LedgerRow = forwardRef<HTMLDivElement, LedgerRowProps>(function LedgerRow(
	{
		zebra = false,
		selected = false,
		interactive = true,
		onActivate,
		actions,
		children,
		className,
		onClick,
		...props
	},
	ref,
) {
	return (
		<div
			ref={ref}
			role="row"
			data-selected={selected || undefined}
			onClick={onClick ?? (interactive && onActivate ? () => onActivate() : undefined)}
			className={cn(
				GRID,
				'group/row ease-out-soft relative h-[38px] rounded-md px-2.5 transition-colors duration-[140ms]',
				zebra && 'bg-surface-zebra',
				interactive &&
					'hover:bg-surface-1-hover focus-within:bg-surface-1-hover cursor-default',
				selected &&
					'bg-surface-selected hover:bg-surface-selected shadow-[inset_2px_0_0_hsl(var(--primary))]',
				className,
			)}
			{...props}
		>
			{children}
			{actions}
		</div>
	);
});

/**
 * Row actions, pinned over the row's right edge on a short fade so long names
 * slide under them. Hidden until the row is hovered, focused or selected —
 * still in the tab order (focus-within reveals them) — and always visible on
 * touch, where there's no hover.
 */
export function LedgerRowActions({
	children,
	className,
}: {
	children: ReactNode;
	className?: string;
}) {
	return (
		<div
			role="cell"
			data-nodrag=""
			className={cn(
				'absolute top-1/2 right-1.5 flex -translate-y-1/2 items-center justify-end gap-1 rounded-r-md pl-7',
				'bg-[linear-gradient(90deg,transparent,hsl(var(--surface-1-hover))_26px)] group-data-[selected=true]/row:bg-[linear-gradient(90deg,transparent,hsl(var(--surface-selected))_26px)]',
				'pointer-events-none opacity-0 transition-opacity duration-[140ms]',
				'group-focus-within/row:pointer-events-auto group-focus-within/row:opacity-100 group-hover/row:pointer-events-auto group-hover/row:opacity-100 group-data-[selected=true]/row:pointer-events-auto group-data-[selected=true]/row:opacity-100',
				'[@media(hover:none)]:pointer-events-auto [@media(hover:none)]:opacity-100',
				className,
			)}
		>
			{children}
		</div>
	);
}
