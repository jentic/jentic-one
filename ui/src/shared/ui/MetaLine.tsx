import { Fragment, type ReactNode } from 'react';
import { cn } from '@/shared/lib/utils';

/** One item on a meta line (`Live · 🤖 0 · ⚡ 21 · 🔑 2`). */
export interface MetaLineItem {
	key: string;
	/** Decorative glyph before the value (hidden from assistive tech). */
	icon?: ReactNode;
	value: ReactNode;
	/**
	 * Spoken form of the item ("21 operations"). When set, the visible value
	 * is hidden from assistive tech and this is read instead — so a bare
	 * number next to an icon still has a meaning.
	 */
	label?: string;
	/** Hover title. */
	title?: string;
	/** Tone class for the whole item (word + glyph). Prefer `iconTone`. */
	tone?: string;
	/** Tone class for the glyph only (e.g. `text-caution`): the warm states
	 * tint the glyph and leave the word neutral. Drops the glyph's dimming. */
	iconTone?: string;
	testId?: string;
}

/**
 * MetaLine — a row's quiet secondary line: short items (a state word, icon +
 * count figures) joined by faint `·` separators. Wraps to a tidy second line
 * at narrow widths instead of overflowing. Inherits its colour and size from
 * the caller (`text-foreground-faint text-xs` on the workspace rows); an item
 * can override its own tone.
 */
export function MetaLine({ items, className }: { items: MetaLineItem[]; className?: string }) {
	return (
		<span className={cn('flex min-w-0 flex-wrap items-center gap-x-1.5 gap-y-0.5', className)}>
			{items.map((item, i) => (
				<Fragment key={item.key}>
					{i > 0 && (
						<span aria-hidden="true" className="text-meta-separator">
							·
						</span>
					)}
					<span
						className={cn('inline-flex min-w-0 items-center gap-1', item.tone)}
						title={item.title}
						data-testid={item.testId}
					>
						{item.icon && (
							<span
								aria-hidden="true"
								className={cn(
									'inline-flex shrink-0 [&_svg]:h-3 [&_svg]:w-3',
									item.iconTone ?? 'opacity-80',
								)}
							>
								{item.icon}
							</span>
						)}
						{item.label ? (
							<>
								<span aria-hidden="true">{item.value}</span>
								<span className="sr-only">{item.label}</span>
							</>
						) : (
							<span className="truncate">{item.value}</span>
						)}
					</span>
				</Fragment>
			))}
		</span>
	);
}
