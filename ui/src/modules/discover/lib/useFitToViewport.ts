/**
 * Keeps a sticky side panel's bottom edge on the viewport: its height is the
 * distance from where its top rests on screen to the shell scroller's bottom
 * (less `gap`).
 *
 * A sticky dock sized `calc(100dvh - …)` is only right once it has stuck —
 * at rest, below the page header, its footer hangs under the fold. Measuring
 * on scroll / resize keeps the header and footer on screen in both states
 * while the body scrolls inside. The CSS height stays as the fallback (no JS
 * layout, tests); this only overrides it while mounted.
 *
 * The top used is never above the panel's sticky line (`top` in CSS): at the
 * end of the page the sticky box is pushed up by its container's bottom edge,
 * and measuring that raw (negative) top made the panel taller → the grid
 * taller → more scroll → taller again, a feedback loop that left a void
 * under the list and scrolled the panel's header away.
 *
 * `until` (the main column beside the panel) also caps the bottom: at the end
 * of the page the panel ends with the list instead of being pushed up under
 * the top bar. The cap applies only while that column is taller than the
 * viewport — a short column (no matches, still loading) never shrinks the
 * panel, which keeps its full viewport height.
 */
import { useLayoutEffect, type RefObject } from 'react';
import { shellScroller } from '@/shared/lib';

export function useFitToViewport(
	ref: RefObject<HTMLElement | null>,
	{
		enabled = true,
		gap = 16,
		min = 320,
		until,
	}: {
		enabled?: boolean;
		gap?: number;
		min?: number;
		until?: RefObject<HTMLElement | null>;
	} = {},
): void {
	useLayoutEffect(() => {
		const el = ref.current;
		if (!enabled || !el) return;
		const scroller = shellScroller();
		let frame = 0;
		const fit = () => {
			frame = 0;
			const box =
				scroller instanceof Window
					? { top: 0, bottom: window.innerHeight }
					: scroller.getBoundingClientRect();
			const stickyTop = parseFloat(getComputedStyle(el).top);
			const restTop = box.top + (Number.isFinite(stickyTop) ? stickyTop : 0);
			const top = Math.max(el.getBoundingClientRect().top, restTop);
			let bottom = box.bottom - gap;
			const column = until?.current?.getBoundingClientRect();
			const columnOverflows = column != null && column.height > box.bottom - box.top;
			if (columnOverflows && column.bottom - top >= min) {
				bottom = Math.min(bottom, column.bottom);
			}
			const height = Math.max(min, Math.floor(bottom - top));
			el.style.height = `${height}px`;
		};
		const schedule = () => {
			if (!frame) frame = requestAnimationFrame(fit);
		};
		fit();
		scroller.addEventListener('scroll', schedule, { passive: true });
		window.addEventListener('resize', schedule);
		// The main column grows as pages load — re-fit without a scroll.
		const grows = until?.current ? new ResizeObserver(schedule) : null;
		if (grows && until?.current) grows.observe(until.current);
		return () => {
			grows?.disconnect();
			if (frame) cancelAnimationFrame(frame);
			scroller.removeEventListener('scroll', schedule);
			window.removeEventListener('resize', schedule);
			el.style.height = '';
		};
	}, [ref, enabled, gap, min, until]);
}
