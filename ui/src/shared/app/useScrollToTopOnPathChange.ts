import { useLayoutEffect, useRef } from 'react';
import { useLocation, useNavigationType } from 'react-router';

/**
 * Land cross-page navigations at the top of the page.
 *
 * The app mounts a declarative `<BrowserRouter>`, so react-router's
 * `<ScrollRestoration>` (data-router only) isn't available, and without this
 * the window keeps the previous page's scroll offset — e.g. clicking a
 * "Recent changes" row far down the Library opened Monitor mid-page.
 *
 * Deliberately narrow:
 *  - fires on a **pathname** change only — query-param changes (Monitor's
 *    `?show=`, filters, sheets opened via params, hub `?tab=`) keep their offset;
 *  - skips `POP` (browser Back/Forward), leaving the browser's own restoration;
 *  - skips navigations carrying a `#hash`, so in-page / Docs anchors still land.
 */
export function useScrollToTopOnPathChange(): void {
	const { pathname, hash } = useLocation();
	const navigationType = useNavigationType();
	const prevPathRef = useRef(pathname);

	useLayoutEffect(() => {
		if (prevPathRef.current === pathname) return;
		prevPathRef.current = pathname;
		if (navigationType === 'POP' || hash) return;
		window.scrollTo({ top: 0, left: 0 });
	}, [pathname, hash, navigationType]);
}
