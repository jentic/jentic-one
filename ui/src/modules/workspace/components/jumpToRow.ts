/**
 * jumpToRow — scroll-and-focus a cross-linked list row (revision ⇄ overlay).
 *
 * The Revisions and Overlays sections cross-link each other's rows via
 * `data-revision-id` / `data-overlay-id` attributes. A bare
 * `scrollIntoView` is a poor jump: keyboard focus stays behind on the link,
 * screen readers hear nothing, reduced-motion users get an animated scroll,
 * and a missing target (paginated out / other section errored) is a silent
 * no-op that reads as a broken button. This helper:
 *
 * - returns `false` when the target row isn't in the DOM, so the caller can
 *   surface a toast instead of doing nothing;
 * - respects `prefers-reduced-motion` (auto vs smooth scrolling);
 * - moves keyboard/SR focus to the row (rows carry `tabIndex={-1}`), which
 *   also announces the row's content;
 * - flashes a ring so sighted users can tell which row they landed on (the
 *   focus outline is the non-color-only signal).
 */
export function jumpToRow(selector: string): boolean {
	const row = document.querySelector<HTMLElement>(selector);
	if (!row) return false;

	const reducedMotion =
		typeof window.matchMedia === 'function' &&
		window.matchMedia('(prefers-reduced-motion: reduce)').matches;
	row.scrollIntoView({ behavior: reducedMotion ? 'auto' : 'smooth', block: 'center' });
	row.focus({ preventScroll: true });

	const highlight = ['ring-2', 'ring-primary/60', 'rounded-md'];
	row.classList.add(...highlight);
	window.setTimeout(() => row.classList.remove(...highlight), 2000);
	return true;
}

/** Jump to a revision row; false when it isn't rendered. */
export function jumpToRevision(revisionId: string): boolean {
	return jumpToRow(`[data-revision-id="${CSS.escape(revisionId)}"]`);
}

/** Jump to an overlay row; false when it isn't rendered. */
export function jumpToOverlay(overlayId: string): boolean {
	return jumpToRow(`[data-overlay-id="${CSS.escape(overlayId)}"]`);
}
