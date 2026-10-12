/**
 * The "Can call" area's list ⇄ cards lens: the motion between the two layouts.
 *
 * A switch is a short, choreographed swap rather than a bare crossfade:
 *
 * 1. the outgoing layout fades out with a slight lift (~120ms, easeIn);
 * 2. the incoming one enters — cards fade up as a block with each card
 *    staggered in; the list grows its tree: the trunk draws down from the card,
 *    each row rises in and its elbow wipes out to meet it, top to bottom, with
 *    "+ Add APIs" joining last (`lib/apiViewMotion`, `TreeBranch`);
 * 3. meanwhile the container's height tweens from the old layout's height to
 *    the new one's, so the page below never jumps.
 *
 * The height is pinned and tweened ONLY while a switch is in flight; at rest
 * it is `auto`, so a row's hover reveal or a new binding resizes instantly.
 * Reduced motion (the app's `MotionConfig reducedMotion="user"`) swaps the
 * layout instantly: no fade, no stagger, no height tween.
 */
import { useEffect, useLayoutEffect, useRef, useState, type ReactNode } from 'react';
import { AnimatePresence, motion, useReducedMotionConfig } from 'framer-motion';
import type { ApiView } from '@/modules/agents/lib/apiView';
import { LENS_HEIGHT_S, lensLayoutVariants } from '@/modules/agents/lib/apiViewMotion';

interface ApiViewSwitchProps {
	view: ApiView;
	className?: string;
	children: ReactNode;
}

export function ApiViewSwitch({ view, className, children }: ApiViewSwitchProps) {
	const reduced = useReducedMotionConfig() ?? false;
	const innerRef = useRef<HTMLDivElement>(null);
	const lastView = useRef(view);
	// Read by the ResizeObserver callback, which can run after the switch ends.
	const switchingRef = useRef(false);
	const [switching, setSwitching] = useState(false);
	const [height, setHeight] = useState<number | 'auto'>('auto');

	// A switch pins the current height before the browser paints the change,
	// then follows the incoming layout's height (below) until it has settled.
	useLayoutEffect(() => {
		if (lastView.current === view) return;
		lastView.current = view;
		if (reduced || !innerRef.current) return;
		switchingRef.current = true;
		setHeight(innerRef.current.offsetHeight);
		setSwitching(true);
	}, [view, reduced]);

	useEffect(() => {
		const el = innerRef.current;
		if (!switching || !el || typeof ResizeObserver === 'undefined') return;
		const ro = new ResizeObserver(() => {
			if (switchingRef.current) setHeight(el.offsetHeight);
		});
		ro.observe(el);
		return () => ro.disconnect();
	}, [switching]);

	const settle = () => {
		switchingRef.current = false;
		setSwitching(false);
		setHeight('auto');
	};

	const variants = lensLayoutVariants(view);

	return (
		<motion.div
			data-testid="api-view-switch"
			data-switching={switching || undefined}
			initial={false}
			animate={{ height }}
			transition={{ duration: switching ? LENS_HEIGHT_S : 0, ease: 'easeOut' }}
			// Clip only while the height tweens; at rest card shadows/lifts show.
			style={{ overflow: switching ? 'hidden' : undefined }}
			className={className}
		>
			<div ref={innerRef}>
				{reduced ? (
					<motion.div
						key={view}
						data-view={view}
						initial={false}
						animate="visible"
						variants={variants}
					>
						{children}
					</motion.div>
				) : (
					<AnimatePresence initial={false} mode="wait">
						<motion.div
							key={view}
							data-view={view}
							variants={variants}
							initial="hidden"
							animate="visible"
							exit="exit"
							onAnimationComplete={(definition) => {
								// Fires once the layout AND every item in it have landed.
								if (definition === 'visible') settle();
							}}
						>
							{children}
						</motion.div>
					</AnimatePresence>
				)}
			</div>
		</motion.div>
	);
}
