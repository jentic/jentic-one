import { useEffect, useLayoutEffect, useRef, type RefObject } from 'react';
import { useLocation, useNavigationType } from 'react-router';

/**
 * How long a Back/Forward restore keeps retrying while the page it returns to
 * is still too short to reach its old position (data loading, lazy sections).
 */
const RESTORE_TIMEOUT_MS = 1500;

/** The element a location `#hash` points at, if it is rendered. */
function anchorTarget(hash: string): HTMLElement | null {
	if (hash.length < 2) return null;
	let id = hash.slice(1);
	try {
		id = decodeURIComponent(id);
	} catch {
		// A malformed escape is looked up verbatim.
	}
	return document.getElementById(id);
}

/** User input that takes over from a pending restore. */
const USER_SCROLL_EVENTS = ['wheel', 'touchstart', 'keydown', 'pointerdown'] as const;

/**
 * Page-to-page behaviour for the shell's scroller, which the browser no longer
 * handles now that `<main>` scrolls instead of the document:
 *
 *  - a new page starts at its top, while Back/Forward returns to where that
 *    history entry was left (the browser only restores the document's scroll);
 *  - a new page reached through a `#hash` link opens at that anchor instead
 *    (when it is already rendered — a page that mounts it later, like Docs,
 *    scrolls there itself), so deep links still land;
 *  - `<main>` takes keyboard focus unless the operator is already working in
 *    the page or a dialog, so Space/PageDown/arrow keys scroll it.
 *
 * Keyed on the pathname, so a filter or tab change in the query string keeps
 * its place.
 */
export function useShellNavigation(mainRef: RefObject<HTMLElement | null>): void {
	const { key, pathname, hash } = useLocation();
	const navigationType = useNavigationType();
	// Scroll offset per history entry.
	const positions = useRef(new Map<string, number>());
	const keyRef = useRef(key);
	const navigationTypeRef = useRef(navigationType);
	const pathnameRef = useRef(pathname);
	const hashRef = useRef(hash);
	// Set while a restore is in flight, so its clamped attempts aren't recorded
	// over the position it is restoring.
	const restoringRef = useRef(false);

	useLayoutEffect(() => {
		// A query-only navigation (a filter, a selected agent) is a new history
		// entry that doesn't scroll, so no scroll event ever records it.
		const main = mainRef.current;
		if (main && pathname === pathnameRef.current && !positions.current.has(key)) {
			positions.current.set(key, main.scrollTop);
		}
		pathnameRef.current = pathname;
		hashRef.current = hash;
		keyRef.current = key;
		navigationTypeRef.current = navigationType;
	}, [mainRef, key, navigationType, pathname, hash]);

	// Recorded as the page scrolls. By the time a navigation commits, the old
	// page is gone and `<main>` may already have clamped to the new, shorter one.
	useEffect(() => {
		const main = mainRef.current;
		if (!main) return;
		const onScroll = (): void => {
			if (restoringRef.current) return;
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
			const anchor = anchorTarget(hashRef.current);
			if (anchor && main.contains(anchor)) anchor.scrollIntoView({ block: 'start' });
			else main.scrollTo({ top: 0, left: 0 });
			return;
		}

		let frame = 0;
		const deadline = performance.now() + RESTORE_TIMEOUT_MS;
		const stop = (): void => {
			restoringRef.current = false;
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
		restoringRef.current = true;
		attempt();
		return stop;
	}, [mainRef, pathname]);
}
