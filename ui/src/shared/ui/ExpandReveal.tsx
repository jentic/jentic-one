/**
 * ExpandReveal — a block that grows open and folds shut in place, with one
 * motion model for both directions.
 *
 * The height runs on the CSS grid-track technique (`grid-template-rows:
 * 0fr → 1fr` over a `min-height: 0` child), so it is never measured: the
 * track follows the content as it is, every frame, and a body that changes
 * while it moves (a skeleton swapped for data) never jumps at the end. The
 * content fades and settles in (opacity 0 → 1, 4px → 0) a beat after the
 * height starts, and leaves a little quicker than it came. Only the track,
 * opacity and transform move — no shadow, no filter.
 *
 *  - Opening mounts the content collapsed and paints it once before the height
 *    starts, so the cost of a first render lands in a frame where nothing
 *    visibly moves — not in the animation's first frame.
 *  - Closing waits the same two frames, so one reveal folding as another
 *    opens (a row-to-row handoff) moves in step — and keeps the content
 *    mounted until the fold has finished, then drops it (so its reads stop
 *    with it).
 *  - Reversing mid-way runs back from where it is.
 *  - Reduced motion (`MotionConfig reducedMotion="user"`) is instant.
 */
import {
	useEffect,
	useLayoutEffect,
	useRef,
	useState,
	type CSSProperties,
	type ReactNode,
	type Ref,
	type TransitionEvent,
} from 'react';
import { flushSync } from 'react-dom';
import { useReducedMotionConfig } from 'framer-motion';

/** The reveal's timings and curve, in one place. */
export const REVEAL_MOTION = {
	/** Height, opening. */
	openMs: 240,
	/** Height, closing — a little quicker than it came. */
	closeMs: 180,
	/** How far into the height the content starts to fade and settle in. */
	contentDelayMs: 40,
	/** Material "standard": eases in briefly, then settles softly — an even
	 * velocity, without the hard first-frame jump of a pure ease-out. */
	ease: 'cubic-bezier(0.4, 0, 0.2, 1)',
} as const;

/** `mounting` and `leaving` hold still for two frames before the height
 * moves, so a reveal opening and a sibling's folding start in one frame. */
type Phase = 'closed' | 'mounting' | 'open' | 'leaving' | 'closing';

export interface ExpandRevealProps {
	open: boolean;
	children: ReactNode;
	/** On the outer (clipping, height-animating) element. */
	className?: string;
	/** Fires when the height has finished opening. */
	onOpened?: () => void;
	/** Fires once the fold has finished and the content is gone. */
	onClosed?: () => void;
	ref?: Ref<HTMLDivElement>;
	/** On the outer element — what a disclosure's `aria-controls` names. */
	id?: string;
	'data-testid'?: string;
}

export function ExpandReveal({
	open,
	children,
	className,
	onOpened,
	onClosed,
	ref,
	id,
	'data-testid': testId,
}: ExpandRevealProps) {
	const reduced = useReducedMotionConfig() ?? false;
	const [phase, setPhase] = useState<Phase>(open ? 'open' : 'closed');
	const fallback = useRef<ReturnType<typeof setTimeout> | null>(null);
	const callbacks = useRef({ onOpened, onClosed });
	useLayoutEffect(() => {
		callbacks.current = { onOpened, onClosed };
	});

	// Follow `open` before paint, so a change is never one frame late.
	useLayoutEffect(() => {
		if (open) {
			if (phase === 'closed') setPhase(reduced ? 'open' : 'mounting');
			else if (phase === 'closing' || phase === 'leaving') setPhase('open');
		} else if (phase === 'mounting') {
			setPhase('closed');
		} else if (phase === 'open') {
			setPhase(reduced ? 'closed' : 'leaving');
		}
	}, [open, phase, reduced]);

	// Mounted collapsed (or about to fold): let a frame paint as it is, then
	// start the height.
	useEffect(() => {
		if (phase !== 'mounting' && phase !== 'leaving') return;
		const next = phase === 'mounting' ? 'open' : 'closing';
		let second = 0;
		const first = requestAnimationFrame(() => {
			second = requestAnimationFrame(() => flushSync(() => setPhase(next)));
		});
		return () => {
			cancelAnimationFrame(first);
			cancelAnimationFrame(second);
		};
	}, [phase]);

	// Settle even if a transitionend never comes (a hidden tab, a zero-size box).
	useEffect(() => {
		if (fallback.current) clearTimeout(fallback.current);
		fallback.current = null;
		if (phase === 'closing') {
			fallback.current = setTimeout(() => setPhase('closed'), REVEAL_MOTION.closeMs + 100);
		}
		return () => {
			if (fallback.current) clearTimeout(fallback.current);
		};
	}, [phase]);

	const wasPhase = useRef(phase);
	useEffect(() => {
		const before = wasPhase.current;
		wasPhase.current = phase;
		if (phase === 'closed' && before !== 'closed') callbacks.current.onClosed?.();
		// Reduced motion has no transition to end: it is open as soon as it is.
		if (phase === 'open' && reduced && before !== 'open') callbacks.current.onOpened?.();
	}, [phase, reduced]);

	function onTransitionEnd(event: TransitionEvent<HTMLDivElement>) {
		if (event.target !== event.currentTarget || event.propertyName !== 'grid-template-rows') {
			return;
		}
		if (phase === 'closing') setPhase('closed');
		else if (phase === 'open') callbacks.current.onOpened?.();
	}

	if (phase === 'closed') return null;

	const expanded = phase === 'open' || phase === 'leaving';
	const { openMs, closeMs, contentDelayMs, ease } = REVEAL_MOTION;
	const outer: CSSProperties = {
		display: 'grid',
		gridTemplateRows: expanded ? '1fr' : '0fr',
		transition: reduced
			? 'none'
			: `grid-template-rows ${expanded ? openMs : closeMs}ms ${ease}`,
	};
	const content: CSSProperties = {
		opacity: expanded ? 1 : 0,
		transform: expanded ? 'none' : 'translateY(4px)',
		transition: reduced
			? 'none'
			: expanded
				? `opacity ${openMs - contentDelayMs}ms ${ease} ${contentDelayMs}ms, transform ${openMs}ms ${ease} ${contentDelayMs}ms`
				: `opacity ${Math.round(closeMs * 0.7)}ms ${ease}, transform ${closeMs}ms ${ease}`,
	};

	return (
		<div
			ref={ref}
			id={id}
			data-testid={testId}
			data-state={phase}
			style={outer}
			className={className}
			onTransitionEnd={onTransitionEnd}
		>
			<div
				className="min-h-0 overflow-hidden"
				inert={phase === 'closing' || phase === 'leaving' || undefined}
			>
				<div style={content}>{children}</div>
			</div>
		</div>
	);
}
