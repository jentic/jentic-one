/**
 * What covers the viewport's right edge right now — the agent rail, an open
 * right-hand sheet, a page's docked side column — so an overlay pinned to that
 * corner (the toast region) can sit beside it instead of on top of its controls.
 *
 * Each cover reports how far in from the right edge it reaches; the inset is
 * the largest, because covers stack against the same edge.
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

/** Tailwind's `xl` — where pages dock a sticky side column beside their content. */
const XL_QUERY = '(min-width: 80rem)';

export interface ReportRightDockOptions {
	/** Measure `ref`'s last element child instead of `ref` itself — for a page
	 * that owns the grid but not the docked column component (which may not
	 * take a ref, and can't be wrapped without breaking `position: sticky`). */
	lastChild?: boolean;
	/** Only report while this media query matches — where the column is
	 * actually docked beside the content rather than stacked under it. */
	when?: string;
	/** Report only while true — for a column that isn't always rendered. */
	active?: boolean;
}

/**
 * Report a page's docked side column (Library's "Your workspace" panel,
 * Monitor's Live activity panel) as covering the right edge. Unlike a
 * `useCoversRightEdge` cover it isn't flush with the viewport edge — it sits
 * left of the rail — so it reports the distance from its left edge to the
 * viewport's right edge, which already includes the rail beside it.
 *
 * Re-measures when the column or its container resizes (the rail collapsing
 * moves the column without resizing it), on window resize, and when the
 * container's children change (a column that mounts only at `xl`). Stops
 * reporting when `when` stops matching and on unmount.
 */
export function useReportRightDock(
	ref: RefObject<HTMLElement | null>,
	{ lastChild = false, when = XL_QUERY, active = true }: ReportRightDockOptions = {},
): void {
	useEffect(() => {
		const host = ref.current;
		if (!active || !host) return undefined;
		const key = Symbol('right-dock');
		const media = window.matchMedia(when);
		const target = (): Element | null => (lastChild ? host.lastElementChild : host);
		const measure = (): void => {
			const el = target();
			const rect = el?.getBoundingClientRect();
			if (!media.matches || !rect || rect.width === 0) {
				widths.delete(key);
			} else {
				const viewport = document.documentElement.clientWidth;
				widths.set(key, Math.max(0, viewport - rect.left));
			}
			publish();
		};

		const resize = new ResizeObserver(measure);
		let observed: Element | null = null;
		const observe = (): void => {
			resize.disconnect();
			resize.observe(host);
			if (host.parentElement) resize.observe(host.parentElement);
			observed = target();
			if (observed && observed !== host) resize.observe(observed);
		};
		observe();
		const children = new MutationObserver(() => {
			if (target() !== observed) observe();
			measure();
		});
		if (lastChild) children.observe(host, { childList: true });
		media.addEventListener('change', measure);
		window.addEventListener('resize', measure);
		measure();
		return () => {
			resize.disconnect();
			children.disconnect();
			media.removeEventListener('change', measure);
			window.removeEventListener('resize', measure);
			widths.delete(key);
			publish();
		};
	}, [ref, lastChild, when, active]);
}
