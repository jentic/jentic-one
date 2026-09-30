/**
 * View transitions — the shell's page-to-page motion.
 *
 * The live activity stream is one thing that docks in different places: the
 * right-hand rail on most pages, the side panel (or the full-width log) on
 * Monitor. Each of those carries the same `view-transition-name`, so when a
 * navigation or an expand toggle moves the stream, the browser morphs it from
 * where it was to where it lands instead of cutting. Everything else fades
 * through quickly (see index.css). The rail's own open/close is a width
 * animation, not a view transition (see AgentRail).
 *
 * The app mounts a declarative `<BrowserRouter>`, whose `<Link viewTransition>`
 * is a no-op (that flag is data-router only), so the shell drives the API
 * itself: {@link useLinkViewTransitions} intercepts ordinary in-app link
 * clicks and {@link withViewTransition} wraps any state change that moves the
 * stream. Browsers without the API, and users who prefer reduced motion, get
 * the plain instant update.
 */
import { useEffect, useLayoutEffect, useRef } from 'react';
import { flushSync } from 'react-dom';
import { useLocation, useNavigate } from 'react-router';

/** The `view-transition-name` every docking of the activity stream shares. */
export const ACTIVITY_STREAM_VT = 'activity-stream';

/** Spread onto the element that currently hosts the stream. */
export const activityStreamVtStyle = { viewTransitionName: ACTIVITY_STREAM_VT } as const;

const ROUTER_BASENAME = import.meta.env.BASE_URL.replace(/\/$/, '');

function canAnimate(): boolean {
	return (
		typeof document !== 'undefined' &&
		typeof document.startViewTransition === 'function' &&
		!window.matchMedia?.('(prefers-reduced-motion: reduce)').matches
	);
}

/**
 * Run a state update as a view transition. The update is flushed
 * synchronously inside the transition callback so the browser snapshots the
 * finished DOM, not a half-rendered one.
 */
export function withViewTransition(update: () => void): void {
	if (!canAnimate()) {
		update();
		return;
	}
	const transition = document.startViewTransition(() => {
		flushSync(update);
	});
	// A transition started while another runs skips the first (its `ready`
	// rejects with AbortError). The update itself still applied — nothing to
	// handle, just don't let it surface as an unhandled rejection.
	transition.ready.catch(() => {});
}

/** The router-relative target of an in-app anchor click, or null to let it through. */
function transitionTarget(event: MouseEvent): string | null {
	if (event.defaultPrevented || event.button !== 0) return null;
	if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return null;
	const anchor = (event.target as Element | null)?.closest?.('a[href]');
	if (!(anchor instanceof HTMLAnchorElement)) return null;
	if (anchor.target && anchor.target !== '_self') return null;
	if (anchor.hasAttribute('download') || anchor.dataset.noTransition != null) return null;
	const url = new URL(anchor.href, window.location.href);
	if (url.origin !== window.location.origin) return null;
	if (ROUTER_BASENAME && !url.pathname.startsWith(ROUTER_BASENAME)) return null;
	const here = window.location;
	// Hash-only jumps and links to the page you're on don't move anything.
	if (url.pathname === here.pathname && url.search === here.search) return null;
	const path = url.pathname.slice(ROUTER_BASENAME.length) || '/';
	return `${path}${url.search}${url.hash}`;
}

/**
 * The longest a navigation may hold the page frozen waiting for its route to
 * render. Past this the transition captures whatever is on screen.
 */
const NAVIGATION_COMMIT_TIMEOUT_MS = 500;

/**
 * Mount once, in the shell: turns in-app link clicks into view-transitioned
 * navigations. Listens in the capture phase so it can claim the click before
 * react-router's `<Link>` does (Link skips an event that's already
 * `defaultPrevented`); the link's own `onClick` — closing a menu, say — still
 * runs.
 *
 * `<BrowserRouter>` applies every location change inside
 * `React.startTransition`, which `flushSync` can't force — so a plain
 * {@link withViewTransition} would snapshot the OLD page as the "new" state
 * and nothing would move. Instead the transition's update resolves when the
 * shell commits the new location (a layout effect, before paint).
 */
export function useLinkViewTransitions(): void {
	const navigate = useNavigate();
	const location = useLocation();
	const committed = useRef<(() => void) | null>(null);

	useLayoutEffect(() => {
		committed.current?.();
		committed.current = null;
	}, [location]);

	useEffect(() => {
		if (!canAnimate()) return;
		const onClick = (event: MouseEvent) => {
			const to = transitionTarget(event);
			if (!to) return;
			event.preventDefault();
			const transition = document.startViewTransition(
				() =>
					new Promise<void>((resolve) => {
						committed.current?.();
						committed.current = resolve;
						setTimeout(resolve, NAVIGATION_COMMIT_TIMEOUT_MS);
						void navigate(to);
					}),
			);
			transition.ready.catch(() => {});
		};
		document.addEventListener('click', onClick, true);
		return () => document.removeEventListener('click', onClick, true);
	}, [navigate]);
}
