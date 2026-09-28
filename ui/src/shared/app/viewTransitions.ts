/**
 * View transitions — motion for state changes that move the activity stream.
 *
 * On Monitor the live activity stream docks as a side panel beside the
 * Overview charts and expands into the full-width log. Both carry the same
 * `view-transition-name`, so the expand/fold toggle morphs the stream from
 * where it was to where it lands instead of cutting (see index.css).
 *
 * {@link withViewTransition} wraps any state change that moves the stream.
 * Browsers without the API, and users who prefer reduced motion, get the plain
 * instant update.
 */
import { flushSync } from 'react-dom';

/** The `view-transition-name` every docking of the activity stream shares. */
export const ACTIVITY_STREAM_VT = 'activity-stream';

/** Spread onto the element that currently hosts the stream. */
export const activityStreamVtStyle = { viewTransitionName: ACTIVITY_STREAM_VT } as const;

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
