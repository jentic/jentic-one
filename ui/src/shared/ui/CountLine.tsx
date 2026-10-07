import { Fragment, type HTMLAttributes, type ReactNode } from 'react';
import { cn } from '@/shared/lib/utils';

export interface CountLineProps extends HTMLAttributes<HTMLParagraphElement> {
	/** The headline figure, set big and thin. */
	value: ReactNode;
	/** What the figure counts, right after it ("APIs in the catalog …"). */
	label: ReactNode;
	/** Further facts, joined with `·` separators. Falsy entries are skipped. */
	details?: ReactNode[];
}

/**
 * CountLine — a page's headline count as one calm sentence: a big thin figure
 * followed by what it counts and a few `·`-separated facts. Emphasis inside
 * the facts is the caller's (`<b>` renders in the body tone; tone words such
 * as "in your workspace" carry their own colour *and* say it in words).
 */
export function CountLine({ value, label, details = [], className, ...props }: CountLineProps) {
	const parts = details.filter(Boolean);
	return (
		<p
			className={cn(
				'text-muted-foreground [&_b]:text-foreground-lighter flex flex-wrap items-baseline gap-x-1.5 gap-y-0.5 text-sm [&_b]:font-semibold',
				className,
			)}
			{...props}
		>
			<span className="font-heading text-foreground mr-0.5 text-[30px] leading-none font-extralight tracking-[-0.02em]">
				{value}
			</span>
			<span>{label}</span>
			{parts.map((part, i) => (
				<Fragment key={i}>
					<span aria-hidden="true">·</span>
					{part}
				</Fragment>
			))}
		</p>
	);
}
