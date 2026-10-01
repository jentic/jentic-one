/**
 * Keeps a sticky side panel's bottom edge on the viewport: its height is the
 * distance from its current top to the shell scroller's bottom (less `gap`).
 *
 * A sticky dock sized `calc(100dvh - …)` is only right once it has stuck —
 * at rest, below the page header, its footer hangs under the fold. Measuring
 * on scroll / resize keeps the header and footer on screen in both states
 * while the body scrolls inside. The CSS height stays as the fallback (no JS
 * layout, tests); this only overrides it while mounted.
 */
import { useLayoutEffect, type RefObject } from 'react';
import { shellScroller } from '@/shared/lib';

export function useFitToViewport(
	ref: RefObject<HTMLElement | null>,
	{ enabled = true, gap = 16, min = 320 }: { enabled?: boolean; gap?: number; min?: number } = {},
): void {
	useLayoutEffect(() => {
		const el = ref.current;
		if (!enabled || !el) return;
		const scroller = shellScroller();
		let frame = 0;
		const fit = () => {
			frame = 0;
			const bottom =
				scroller instanceof Window
					? window.innerHeight
					: scroller.getBoundingClientRect().bottom;
			const height = Math.max(min, Math.floor(bottom - el.getBoundingClientRect().top - gap));
			el.style.height = `${height}px`;
		};
		const schedule = () => {
			if (!frame) frame = requestAnimationFrame(fit);
		};
		fit();
		scroller.addEventListener('scroll', schedule, { passive: true });
		window.addEventListener('resize', schedule);
		return () => {
			if (frame) cancelAnimationFrame(frame);
			scroller.removeEventListener('scroll', schedule);
			window.removeEventListener('resize', schedule);
			el.style.height = '';
		};
	}, [ref, enabled, gap, min]);
}
