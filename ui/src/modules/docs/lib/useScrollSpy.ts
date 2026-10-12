/**
 * useScrollSpy — track which section heading is currently "active" (in view) so
 * the docs sidebars can highlight, and auto-scroll to, the matching entry.
 *
 * Rather than only reporting elements whose top edge sits inside a narrow
 * IntersectionObserver band (which is direction-sensitive and breaks for
 * sections taller than the band — the active item flickers or drops out when
 * scrolling fast, especially bottom→top), this computes the active id
 * *deterministically* from scroll position: the active section is the last one
 * whose top has passed a fixed offset line near the top of the viewport. That
 * always yields a stable answer regardless of scroll direction or section size.
 *
 * IntersectionObserver is used only as a cheap "something changed, recompute"
 * trigger; the actual decision is a position scan, rAF-throttled.
 *
 * `defaultFirst` controls the value when nothing has passed the line yet:
 * top-level sections default to the first id (something is always active);
 * sub-anchor spies (e.g. CLI binaries/commands) pass `false` so they report
 * `null` until the reader actually reaches the first sub-anchor.
 */
import { useEffect, useState } from 'react';

/**
 * Distance from the top of the viewport that marks the "active" line (px).
 *
 * Must sit *below* where a clicked target lands (its `scroll-margin-top`), so a
 * freshly-clicked section/operation immediately counts as active. Operations and
 * tag groups use `scroll-mt-28` (112px); main sections use `scroll-mt-20` (80px).
 * 120 clears the largest of those, so any clicked target resolves as active.
 */
const ACTIVE_LINE_OFFSET = 120;

export function useScrollSpy(ids: string[], defaultFirst = true): string | null {
	const [active, setActive] = useState<string | null>(defaultFirst ? (ids[0] ?? null) : null);

	useEffect(() => {
		if (ids.length === 0) {
			setActive(null);
			return;
		}

		const getEls = () =>
			ids
				.map((id) => document.getElementById(id))
				.filter((el): el is HTMLElement => el !== null);

		const compute = () => {
			const els = getEls();
			if (els.length === 0) return;

			// Walk in document order; the active section is the last one whose top
			// has scrolled above the active line. This is monotonic and stable in
			// both directions.
			let current: string | null = defaultFirst ? els[0].id : null;
			let passedAny = false;
			for (const el of els) {
				const top = el.getBoundingClientRect().top;
				if (top - ACTIVE_LINE_OFFSET <= 1) {
					current = el.id;
					passedAny = true;
				} else {
					break;
				}
			}

			// Sub-anchor spies report null until the first anchor is reached, and
			// again once the reader scrolls back above all of them.
			if (!defaultFirst && !passedAny) current = null;

			setActive((prev) => (prev === current ? prev : current));
		};

		let frame = 0;
		const schedule = () => {
			if (frame) return;
			frame = requestAnimationFrame(() => {
				frame = 0;
				compute();
			});
		};

		// Recompute on scroll/resize (the authoritative signal)…
		window.addEventListener('scroll', schedule, { passive: true });
		window.addEventListener('resize', schedule);

		// …and use IO purely as an extra nudge when section boundaries cross, so
		// programmatic jumps that don't emit scroll still settle correctly.
		const observer = new IntersectionObserver(schedule, {
			rootMargin: '0px',
			threshold: [0, 1],
		});
		getEls().forEach((el) => observer.observe(el));

		compute();

		return () => {
			window.removeEventListener('scroll', schedule);
			window.removeEventListener('resize', schedule);
			observer.disconnect();
			if (frame) cancelAnimationFrame(frame);
		};
	}, [ids, defaultFirst]);

	return active;
}
