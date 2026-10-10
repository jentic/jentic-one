import { useCallback, useContext } from 'react';
import { UNSAFE_NavigationContext, useLocation } from 'react-router';

/** The live location the router's history carries (browser and memory alike). */
interface HistoryWithLocation {
	location?: { pathname: string };
}

/**
 * Returns a check: is the path this component rendered with still the
 * router's current one?
 *
 * React Router commits a navigation as a transition, so the page being left
 * stays mounted — on its old location — until the destination has rendered.
 * An effect that writes the URL in that window (`setSearchParams`, a relative
 * `navigate`) resolves against the old path and replaces the navigation the
 * operator just made. Effect-driven URL write-backs call this first and skip
 * the write when it returns `false`; the page is on its way out.
 *
 * A navigator without a live `location` counts as current.
 */
export function useIsRenderedPathCurrent(): () => boolean {
	const { basename, navigator } = useContext(UNSAFE_NavigationContext);
	const { pathname } = useLocation();
	return useCallback(() => {
		const live = (navigator as HistoryWithLocation).location?.pathname;
		if (live == null) return true;
		// The browser history's pathname carries the basename; the router's
		// location does not.
		const base = basename.replace(/\/+$/, '');
		const livePath =
			base !== '' && live.toLowerCase().startsWith(base.toLowerCase())
				? live.slice(base.length) || '/'
				: live;
		return livePath === pathname;
	}, [basename, navigator, pathname]);
}
