/**
 * What covers the viewport's right edge right now — the agent rail, an open
 * right-hand sheet — so an overlay pinned to that corner (the toast region)
 * can sit beside it instead of on top of its controls.
 *
 * Each cover reports its live width; the inset is the widest one, because
 * covers stack against the same edge.
 */
import { useEffect, useSyncExternalStore, type RefObject } from 'react';

const widths = new Map<symbol, number>();
const listeners = new Set<() => void>();
let inset = 0;

function publish(): void {
	const next = Math.max(0, ...widths.values());
	if (next === inset) return;
	inset = next;
	for (const listener of listeners) listener();
}

function subscribe(listener: () => void): () => void {
	listeners.add(listener);
	return () => {
		listeners.delete(listener);
	};
}

function getInset(): number {
	return inset;
}

/** Width in px of the widest thing covering the right edge; 0 when nothing does. */
export function useRightEdgeInset(): number {
	return useSyncExternalStore(subscribe, getInset, getInset);
}

/**
 * Report `ref`'s element as covering the right edge while `active`, tracking its
 * width as it resizes. A `display: none` element measures 0, so a cover hidden
 * at the current breakpoint takes no room.
 */
export function useCoversRightEdge(ref: RefObject<HTMLElement | null>, active: boolean): void {
	useEffect(() => {
		const el = ref.current;
		if (!active || !el) return undefined;
		const key = Symbol('right-edge-cover');
		const measure = (): void => {
			widths.set(key, el.getBoundingClientRect().width);
			publish();
		};
		measure();
		const observer = new ResizeObserver(measure);
		observer.observe(el);
		return () => {
			observer.disconnect();
			widths.delete(key);
			publish();
		};
	}, [ref, active]);
}
