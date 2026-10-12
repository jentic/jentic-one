/**
 * The agent card's pinned-header plumbing: whether the card has scrolled into
 * its pinned place (an IntersectionObserver on a sentinel, never a per-frame
 * scroll handler), and the natural height of a block that folds shut.
 */
import { useCallback, useEffect, useState, type RefObject } from 'react';
import { shellScrollRoot, shellScroller } from '@/shared/lib/shellScroll';

/** How far past its resting place the card scrolls before it folds — the
 * sentinel's offset below the card's top. */
export const STICK_AFTER_PX = 16;

/** The sentinel's height: the hysteresis band. The card pins once the band has
 * left the top entirely and unpins only once it is wholly back in view, so a
 * scroll that hovers on the line never flickers between the two. */
export const STICK_BAND_PX = 8;

/**
 * The fold's motion. Folding in, the height starts collapsing into the lip at
 * once and settles gently (an ease-out, no long tail), while the content fades
 * and drifts up over the first part of it, so the stats read as tucking under
 * the lip rather than vanishing from an empty panel. Unfolding uses the same
 * curve, the content fading in once the height is under way. One timing for
 * every folding block and the spacer that holds their room, so they stay in
 * step frame for frame.
 */
export const FOLD_MOTION = {
	/** Ease-out: moves at once, settles softly, no overshoot. */
	ease: 'cubic-bezier(0.32, 0.72, 0, 1)',
	/** The height collapse, folding in. */
	foldMs: 320,
	/** The collapse's wait for the content to start going (none). */
	foldDelayMs: 0,
	/** The content's exit: a fade and a small upward drift, with the height. */
	contentOutMs: 140,
	contentLiftPx: 4,
	/** The height growing back, unfolding. */
	unfoldMs: 300,
	/** The content's return, a beat after the height starts. */
	contentInMs: 180,
	contentInDelayMs: 60,
} as const;

/** `prop`'s transition toward `folded` (folding in) or back out; `none` under
 * reduced motion. */
export function foldTransition(prop: string, folded: boolean, reduced: boolean): string {
	if (reduced) return 'none';
	const { ease, foldMs, foldDelayMs, unfoldMs } = FOLD_MOTION;
	if (!folded) return `${prop} ${unfoldMs}ms ${ease}`;
	return foldDelayMs > 0
		? `${prop} ${foldMs}ms ${ease} ${foldDelayMs}ms`
		: `${prop} ${foldMs}ms ${ease}`;
}

/** The folding content's own fade (and lift): out from t=0, in after the
 * height has started. */
export function contentTransition(props: string[], folded: boolean, reduced: boolean): string {
	if (reduced) return 'none';
	const { ease, contentOutMs, contentInMs, contentInDelayMs } = FOLD_MOTION;
	return props
		.map((p) =>
			folded
				? `${p} ${contentOutMs}ms ${ease}`
				: `${p} ${contentInMs}ms ${ease} ${contentInDelayMs}ms`,
		)
		.join(', ');
}

/**
 * True once `sentinel` has scrolled above the line `top` px under the shell
 * scroller's top edge (where the agent strip's bottom sits), false once it is
 * wholly back below it; a partial overlap keeps the last answer.
 */
export function useStuck(
	sentinel: RefObject<HTMLElement | null>,
	top: number,
	enabled: boolean,
): boolean {
	const [stuck, setStuck] = useState(false);
	useEffect(() => {
		const el = sentinel.current;
		if (!el || !enabled || typeof IntersectionObserver === 'undefined') return;
		const obs = new IntersectionObserver(
			(entries) => {
				const entry = entries[entries.length - 1];
				if (!entry) return;
				const lineTop = entry.rootBounds?.top ?? top;
				if (entry.intersectionRatio >= 1) setStuck(false);
				else if (!entry.isIntersecting && entry.boundingClientRect.bottom <= lineTop + 0.5)
					setStuck(true);
			},
			{
				root: shellScrollRoot(),
				rootMargin: `-${Math.round(top)}px 0px 0px 0px`,
				threshold: [0, 1],
			},
		);
		obs.observe(el);
		return () => obs.disconnect();
	}, [sentinel, top, enabled]);
	return enabled && stuck;
}

/** The border-box height of the element the returned callback ref lands on,
 * kept current as it resizes; `undefined` until measured. Sub-pixel exact (not
 * `offsetHeight`'s rounding), so a fold sized from it ends on the content's own
 * bottom edge. A callback ref, so a block that mounts later (the stats once an
 * agent is approved) is measured too, and one that unmounts (the approval
 * banner once approved) drops back to `undefined`. React 19 runs the returned
 * cleanup on detach instead of calling the ref with `null`, so the reset lives
 * there. */
export function useMeasuredHeight(): [(el: HTMLElement | null) => void, number | undefined] {
	const [height, setHeight] = useState<number | undefined>(undefined);
	const ref = useCallback((el: HTMLElement | null) => {
		if (!el) return;
		// Layout height: transforms (the figures' drift) don't count.
		const read = () => parseFloat(getComputedStyle(el).height) || el.offsetHeight;
		setHeight(read());
		const obs =
			typeof ResizeObserver === 'undefined'
				? null
				: new ResizeObserver(() => setHeight(read()));
		obs?.observe(el);
		return () => {
			obs?.disconnect();
			setHeight(undefined);
		};
	}, []);
	return [ref, height];
}

/**
 * Calls `onDismiss` on Escape, or once the page has scrolled more than
 * `distance` px from where it was when `active` turned on. Listens only while
 * active; the scroll listener is passive and reads one number.
 */
export function useDismissPeek(active: boolean, onDismiss: () => void, distance = 64): void {
	useEffect(() => {
		if (!active) return;
		const scroller = shellScroller();
		const read = () => (scroller instanceof Window ? scroller.scrollY : scroller.scrollTop);
		const start = read();
		function onScroll() {
			if (Math.abs(read() - start) > distance) onDismiss();
		}
		function onKey(event: KeyboardEvent) {
			if (event.key === 'Escape' && !event.defaultPrevented) onDismiss();
		}
		scroller.addEventListener('scroll', onScroll, { passive: true });
		document.addEventListener('keydown', onKey);
		return () => {
			scroller.removeEventListener('scroll', onScroll);
			document.removeEventListener('keydown', onKey);
		};
	}, [active, onDismiss, distance]);
}
