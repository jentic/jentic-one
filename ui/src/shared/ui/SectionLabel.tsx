import type { ElementType, HTMLAttributes, ReactNode } from 'react';
import { cn } from '@/shared/lib/utils';

interface SectionLabelProps extends HTMLAttributes<HTMLElement> {
	/** Rendered element — a heading level when the label titles a region. Default `p`. */
	as?: ElementType;
	/** Optional count shown after a `·`. */
	count?: ReactNode;
	children: ReactNode;
}

/**
 * SectionLabel — the small uppercase label that names a block inside a panel
 * ("NEEDS ATTENTION · 5", "YOUR APIS"). Bold 10.5px tracking-wide caps in the
 * faint tier: quiet enough not to compete with the rows, and bold + caps so it
 * stays legible at that size.
 */
export function SectionLabel({
	as: Tag = 'p',
	count,
	children,
	className,
	...props
}: SectionLabelProps) {
	return (
		<Tag
			className={cn(
				'text-foreground-faint m-0 font-sans text-[10.5px] font-bold tracking-[0.09em] uppercase',
				className,
			)}
			{...props}
		>
			{children}
			{count != null && (
				<>
					<span aria-hidden="true"> · </span>
					<span className="sr-only">, </span>
					{count}
				</>
			)}
		</Tag>
	);
}
