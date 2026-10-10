/**
 * useStripLinkage — which agents of the strip's scrolling tabs sit off-screen,
 * on which side, as the rail scrolls.
 *
 * One IntersectionObserver watches every tab against the scroller, its root
 * inset by the two raised ends (the pinned tab on the left, the circle stack on
 * the right), so "in the bar" means clear of both. A tab counts as in once
 * {@link VISIBLE_RATIO} of it shows. Callbacks are batched to one state write
 * per animation frame, and only a changed answer re-renders.
 *
 * The observer reports threshold crossings only, so a long jump (scrolled to the
 * end in one go) can leave a tab's last-seen side stale. The row is linear, so
 * the answer is re-derived from order: everything before the first visible tab
 * is behind the pin, everything after the last one is to the right.
 */
import { useEffect, useRef, useState, type RefObject } from 'react';

/** The share of a tab that must show for it to count as in the bar. */
const VISIBLE_RATIO = 0.6;

/** The attribute every observed tab carries, holding its agent id (`data-strip-tab`). */
const STRIP_TAB_ATTR = 'data-strip-tab';

type Side = 'in' | 'L' | 'R';

export interface StripLinkage {
	/** Off-screen to the right, in strip order. */
	right: readonly string[];
	/** How many sit scrolled off behind the pin. */
	left: number;
}

const EMPTY: StripLinkage = { right: [], left: 0 };

/** The side of every tab from the observer's last word on each (pure). */
function deriveLinkage(order: readonly string[], seen: ReadonlyMap<string, Side>): StripLinkage {
	const ins: number[] = [];
	order.forEach((id, i) => {
		if (seen.get(id) === 'in') ins.push(i);
	});
	const first = ins[0];
	const last = ins[ins.length - 1];
	const right: string[] = [];
	let left = 0;
	order.forEach((id, i) => {
		const side: Side =
			first == null ? (seen.get(id) ?? 'R') : i < first ? 'L' : i > last ? 'R' : 'in';
		if (side === 'R') right.push(id);
		else if (side === 'L') left++;
	});
	return { right, left };
}

function sameLinkage(a: StripLinkage, b: StripLinkage): boolean {
	return (
		a.left === b.left &&
		a.right.length === b.right.length &&
		a.right.every((id, i) => id === b.right[i])
	);
}

export function useStripLinkage({
	scrollerRef,
	order,
	startInset,
	endInset,
	paused = false,
}: {
	scrollerRef: RefObject<HTMLElement | null>;
	/** The scrolling tabs' agent ids, in DOM order. */
	order: readonly string[];
	/** The pinned end's width, px. */
	startInset: number;
	/** The circle stack's width, px. */
	endInset: number;
	/** Mid-switch (the tabs still sliding): hold the last answer, then observe
	 * afresh once the tabs have settled, so nothing is read off a moving tab. */
	paused?: boolean;
}): StripLinkage {
	const [linkage, setLinkage] = useState<StripLinkage>(EMPTY);
	const orderRef = useRef(order);
	orderRef.current = order;
	const orderKey = order.join('\u0000');

	useEffect(() => {
		if (paused) return;
		const root = scrollerRef.current;
		if (orderRef.current.length === 0) {
			setLinkage((prev) => (sameLinkage(prev, EMPTY) ? prev : EMPTY));
			return;
		}
		if (!root || typeof IntersectionObserver === 'undefined') return;
		const seen = new Map<string, Side>();
		let raf = 0;
		const flush = () => {
			raf = 0;
			const next = deriveLinkage(orderRef.current, seen);
			setLinkage((prev) => (sameLinkage(prev, next) ? prev : next));
		};
		const io = new IntersectionObserver(
			(entries) => {
				for (const en of entries) {
					const id = (en.target as HTMLElement).getAttribute(STRIP_TAB_ATTR);
					if (!id) continue;
					if (en.intersectionRatio >= VISIBLE_RATIO) {
						seen.set(id, 'in');
						continue;
					}
					const box = en.boundingClientRect;
					const bounds = en.rootBounds ?? root.getBoundingClientRect();
					const centre = box.left + box.width / 2;
					seen.set(id, centre < (bounds.left + bounds.right) / 2 ? 'L' : 'R');
				}
				if (!raf) raf = requestAnimationFrame(flush);
			},
			{
				root,
				rootMargin: `0px -${Math.round(endInset)}px 0px -${Math.round(startInset)}px`,
				threshold: [0, VISIBLE_RATIO, 1],
			},
		);
		root.querySelectorAll(`[${STRIP_TAB_ATTR}]`).forEach((el) => io.observe(el));
		return () => {
			io.disconnect();
			if (raf) cancelAnimationFrame(raf);
		};
		// `orderKey` re-observes when the tab set changes; the ref carries the order.
	}, [scrollerRef, orderKey, startInset, endInset, paused]);

	return linkage;
}
