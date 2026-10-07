/**
 * FilterSelect — a toolbar-sized picker that sits flush beside SegmentedToggle.
 *
 * A native `<select>` (so keyboard, screen readers and mobile pickers behave
 * natively) dressed to match the toggle: same 30px height, borderless field
 * fill and radius, a leading glyph naming what it filters, and our own chevron in
 * place of the OS arrow. While a filter is applied it tints with the primary
 * colour so a narrowed view is visible at a glance.
 */
import type { ComponentProps, ReactNode } from 'react';
import { ChevronDown } from 'lucide-react';
import { cn } from '@/shared/lib/utils';

type FilterSelectProps = Omit<ComponentProps<'select'>, 'className'> & {
	icon: ReactNode;
	className?: string;
};

export function FilterSelect({ icon, className, value, disabled, ...props }: FilterSelectProps) {
	const active = value != null && value !== '' && !disabled;
	return (
		<div
			className={cn(
				'group rounded-field border-control-edge relative flex h-[1.875rem] items-center border text-xs font-medium transition-colors',
				active
					? 'bg-primary/15 text-foreground'
					: 'bg-field text-muted-foreground hover:text-foreground',
				disabled && 'hover:text-muted-foreground cursor-not-allowed opacity-50',
				'focus-within:ring-ring/60 focus-within:ring-2',
				className,
			)}
		>
			<span
				aria-hidden="true"
				className={cn(
					'pointer-events-none absolute left-2.5 inline-flex',
					active && 'text-primary',
				)}
			>
				{icon}
			</span>
			<select
				value={value}
				disabled={disabled}
				className="[&>option]:bg-popover [&>option]:text-popover-foreground rounded-field h-full w-full min-w-0 cursor-pointer appearance-none truncate bg-transparent pr-7 pl-7.5 outline-hidden disabled:cursor-not-allowed"
				{...props}
			/>
			<ChevronDown
				aria-hidden="true"
				className="pointer-events-none absolute right-2 h-3.5 w-3.5 opacity-70"
			/>
		</div>
	);
}
