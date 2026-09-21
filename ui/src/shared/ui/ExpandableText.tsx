import { useEffect, useId, useRef, useState, type ReactNode } from 'react';
import { motion, useReducedMotion } from 'framer-motion';
import { Button } from '@/shared/ui/Button';
import { cn } from '@/shared/lib/utils';

/** Tailwind's clamp utilities, keyed by the line budget this component offers. */
const CLAMP: Record<1 | 2 | 3, string> = {
	1: 'line-clamp-1',
	2: 'line-clamp-2',
	3: 'line-clamp-3',
};

interface ExpandableTextProps {
	children: ReactNode;
	/** Lines shown while collapsed. */
	lines?: 1 | 2 | 3;
	/** Classes for the text itself (typography, colour, spacing). */
	className?: string;
}

/**
 * Free text of unknown length, clamped by default and expanded on request.
 *
 * Text an operator typed can be a phrase or a paragraph, so laying it out at
 * its natural height hands the page's vertical rhythm to whoever filled the
 * field. It is clamped to a fixed line budget instead, and the rest is one
 * click away — the layout moves only when a reader asks it to, and it grows
 * into place rather than jumping, so the shift reads as their own doing.
 *
 * The toggle appears only when text is actually cut off, measured from live
 * layout rather than guessed from length: a `ResizeObserver` re-measures on
 * reflow, so a description that fits at desktop width grows a button when the
 * column narrows. While expanded the cut-off verdict is held, because an
 * expanded element never overflows and would otherwise retract its own toggle.
 *
 * Both heights come from the clamped paragraph itself — `clientHeight` is the
 * line budget, `scrollHeight` the whole text — so the animation has two real
 * numbers to travel between and the clamp keeps its ellipsis. They are kept
 * current by the same observer, so a reflow mid-read resizes rather than clips.
 */
export function ExpandableText({ children, lines = 2, className }: ExpandableTextProps) {
	const [expanded, setExpanded] = useState(false);
	const [overflows, setOverflows] = useState(false);
	const [heights, setHeights] = useState<{ collapsed: number; full: number } | null>(null);
	const textRef = useRef<HTMLParagraphElement | null>(null);
	const reducedMotion = useReducedMotion();
	const textId = useId();

	useEffect(() => {
		const el = textRef.current;
		if (!el) return;
		const check = (): void => {
			// Expanded, the paragraph is its own full height; clamped, it reports
			// the budget as `clientHeight` and the rest as `scrollHeight`.
			setHeights((prev) => ({
				collapsed: expanded ? (prev?.collapsed ?? el.scrollHeight) : el.clientHeight,
				full: el.scrollHeight,
			}));
			if (!expanded) setOverflows(el.scrollHeight > el.clientHeight);
		};
		check();
		const ro = new ResizeObserver(check);
		ro.observe(el);
		return () => ro.disconnect();
	}, [children, expanded]);

	return (
		<div>
			<motion.div
				className="overflow-hidden"
				initial={false}
				animate={heights ? { height: expanded ? heights.full : heights.collapsed } : {}}
				transition={reducedMotion ? { duration: 0 } : { duration: 0.2, ease: 'easeOut' }}
			>
				<p ref={textRef} id={textId} className={cn(className, !expanded && CLAMP[lines])}>
					{children}
				</p>
			</motion.div>
			{(overflows || expanded) && (
				<Button
					variant="ghost"
					size="sm"
					aria-expanded={expanded}
					aria-controls={textId}
					onClick={() => setExpanded((v) => !v)}
					className="text-muted-foreground hover:text-foreground mt-0.5 h-auto px-0 py-0 text-xs font-medium"
				>
					{expanded ? 'Show less' : 'Show more'}
				</Button>
			)}
		</div>
	);
}
