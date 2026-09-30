/**
 * The app shell's scroll container. The shell scrolls `<main>`, not the window,
 * so the scrollbar starts below the fixed TopNavbar instead of running behind it.
 * Anything that reads or drives the page's scroll position goes through here.
 */

/** The `id` of the shell's scrolling `<main>`. */
export const SHELL_SCROLL_ID = 'app-scroll';

/** The shell's scroller — or the window where a surface renders without the
 * shell (unit tests, embedded pages), which is what scrolls there. */
export function shellScroller(): HTMLElement | Window {
	return document.getElementById(SHELL_SCROLL_ID) ?? window;
}

/** How far the page is scrolled from its top. */
export function shellScrollTop(): number {
	const scroller = shellScroller();
	return scroller instanceof Window ? scroller.scrollY : scroller.scrollTop;
}

/** The `root` for an IntersectionObserver watching page content: the shell's
 * scroller, or `null` (the viewport) where there is no shell. */
export function shellScrollRoot(): HTMLElement | null {
	const scroller = shellScroller();
	return scroller instanceof Window ? null : scroller;
}
