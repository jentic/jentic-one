import type { ReactNode } from 'react';

/**
 * Dashboard heading chrome. One visual grammar for the whole page:
 *
 *   - top-level SECTIONS ("Gateway health", "Recent activity") get an
 *     {@link SectionHeading} — an h2 with a monochrome icon medallion, sitting
 *     OUTSIDE the cards it introduces;
 *   - CARD TITLES inside a section (h3: "Execution volume", "Top usage", …)
 *     carry no icon — a one-line muted caption under the title does the
 *     explaining instead. Icons on every card title turned the page into a
 *     rainbow of chips; icons on only some read as an accident.
 *
 * NOTE: this file keeps its `CardRow.tsx` name (the row component it once held
 * was absorbed into `ActionInboxBell`) because the enterprise overlay imports
 * `CardHeaderIcon` from this exact path.
 */
export function CardHeaderIcon({ children }: { children: ReactNode }) {
	return (
		<span className="bg-muted text-muted-foreground ring-border flex h-7 w-7 shrink-0 items-center justify-center rounded-lg ring-1">
			{children}
		</span>
	);
}

interface SectionHeadingProps {
	/** The lucide glyph, sized by the caller (`h-4 w-4`). */
	icon: ReactNode;
	/** Section title text (plus optional inline extras, e.g. a spinner). */
	children: ReactNode;
	/** Right-aligned controls: range toggles, "View all" links, … */
	trailing?: ReactNode;
}

/** The h2 heading row every top-level dashboard section starts with. */
export function SectionHeading({ icon, children, trailing }: SectionHeadingProps) {
	return (
		<div className="flex flex-wrap items-center justify-between gap-3">
			<h2 className="font-heading text-foreground flex items-center gap-2.5 font-semibold">
				<CardHeaderIcon>{icon}</CardHeaderIcon>
				{children}
			</h2>
			{trailing}
		</div>
	);
}
