import { useEffect, useLayoutEffect, useRef, type RefObject } from 'react';
import { useLocation, useNavigationType } from 'react-router';

/**
 * How long a Back/Forward restore keeps retrying while the page it returns to
 * is still too short to reach its old position (data loading, lazy sections).
 */
const RESTORE_TIMEOUT_MS = 1500;

/** User input that takes over from a pending restore. */
const USER_SCROLL_EVENTS = ['wheel', 'touchstart', 'keydown', 'pointerdown'] as const;

/**
 * Page-to-page behaviour for the shell's scroller, which the browser no longer
 * handles now that `<main>` scrolls instead of the document:
 *
 *  - a new page starts at its top, while Back/Forward returns to where that
 *    history entry was left (the browser only restores the document's scroll);
 *  - `<main>` takes keyboard focus unless the operator is already working in
 *    the page or a dialog, so Space/PageDown/arrow keys scroll it.
 *
 * Keyed on the pathname, so a filter or tab change in the query string keeps
 * its place.
 */
export function useShellNavigation(mainRef: RefObject<HTMLElement | null>): void {
	const { key, pathname } = useLocation();
	const navigationType = useNavigationType();
	// Scroll offset per history entry.
	const positions = useRef(new Map<string, number>());
	const keyRef = useRef(key);
	const navigationTypeRef = useRef(navigationType);

	useLayoutEffect(() => {
		keyRef.current = key;
		navigationTypeRef.current = navigationType;
	}, [key, navigationType]);

	// Recorded as the page scrolls. By the time a navigation commits, the old
	// page is gone and `<main>` may already have clamped to the new, shorter one.
	useEffect(() => {
		const main = mainRef.current;
		if (!main) return;
		const onScroll = (): void => {
			positions.current.set(keyRef.current, main.scrollTop);
		};
		main.addEventListener('scroll', onScroll, { passive: true });
		return () => main.removeEventListener('scroll', onScroll);
	}, [mainRef]);

	useLayoutEffect(() => {
		const main = mainRef.current;
		if (!main) return;

		const active = document.activeElement;
		const keepFocus =
			active &&
			active !== document.body &&
			(main.contains(active) || active.closest('dialog, [role="dialog"]'));
		if (!keepFocus) main.focus({ preventScroll: true });

		const saved =
			navigationTypeRef.current === 'POP' ? positions.current.get(keyRef.current) : undefined;
		if (!saved) {
			main.scrollTo({ top: 0, left: 0 });
			return;
		}

		let frame = 0;
		const deadline = performance.now() + RESTORE_TIMEOUT_MS;
		const stop = (): void => {
			cancelAnimationFrame(frame);
			for (const type of USER_SCROLL_EVENTS) main.removeEventListener(type, stop);
		};
		const attempt = (): void => {
			main.scrollTop = saved;
			if (Math.abs(main.scrollTop - saved) < 1 || performance.now() > deadline) {
				stop();
				return;
			}
			frame = requestAnimationFrame(attempt);
		};
		for (const type of USER_SCROLL_EVENTS) {
			main.addEventListener(type, stop, { passive: true });
		}
		attempt();
		return stop;
	}, [mainRef, pathname]);
}
