/**
 * Anchor ↔ endpoint bridge for the native API reference.
 *
 * The renderer assigns each operation/model block a stable DOM id
 * (`operationAnchorId` / `modelAnchorId`); the sidebar, global search, and
 * in-page links all jump to those ids via `scrollToAnchor`. Our reference
 * payload keys endpoints by `(method, path)`, so `indexReference` builds the
 * `(method, path)` → `ReferenceEndpoint` lookup the renderer uses to enrich
 * each operation with its scope/actor data.
 */
import type { ReferenceEndpoint, ReferencePayload } from '@/modules/docs/api/types';

/** Canonical join key shared by anchors and reference rows: `"GET /path"`. */
export function lookupKey(method: string, path: string): string {
	const normalizedPath = path.startsWith('/') ? path : `/${path}`;
	return `${method.toUpperCase()} ${normalizedPath}`;
}

/**
 * DOM anchor id for an operation block in the native API reference. Shared by
 * the reference renderer (which assigns the id) and the global search (which
 * jumps to it), so a search hit lands exactly on its operation. Must stay in
 * sync on both sides — hence one definition here.
 */
export function operationAnchorId(method: string, path: string): string {
	return `op-${method.toUpperCase()}-${path}`.replace(/[^a-zA-Z0-9_-]+/g, '-');
}

/** DOM anchor id for a model (component schema) block in the API reference. */
export function modelAnchorId(name: string): string {
	return `model-${name}`.replace(/[^a-zA-Z0-9_-]+/g, '-');
}

/**
 * Scroll to an anchor and *keep* it pinned while lazily-mounted blocks above it
 * expand.
 *
 * The reference renders most operations/models as fixed-height placeholders
 * (see `LazyMount`) that grow when they mount. A single `scrollIntoView` aims at
 * the pre-mount layout, so as earlier blocks grow the target drifts away. This
 * re-scrolls on a short rAF loop until the element's top stops moving (or a
 * deadline passes), which lets the IntersectionObserver mount the intervening
 * blocks and settles exactly on the target — the fix for "clicking navigation
 * doesn't land in the right place".
 *
 * Once settled it moves keyboard focus to the target (making it programmatically
 * focusable if needed) so keyboard and screen-reader users continue from the
 * destination, not the link they activated. `preventScroll` keeps the focus
 * call from re-scrolling and fighting the pin.
 */
export function scrollToAnchor(id: string, opts: { settleMs?: number } = {}): void {
	if (typeof document === 'undefined') return;
	const settleMs = opts.settleMs ?? 2500;
	const start = performance.now();
	let lastTop = Number.NaN;
	let stableFrames = 0;

	const focusTarget = (el: HTMLElement) => {
		// Headings/sections aren't focusable by default; make them focusable
		// without adding them to the tab order, then focus without scrolling.
		if (!el.hasAttribute('tabindex')) el.setAttribute('tabindex', '-1');
		el.focus({ preventScroll: true });
	};

	const step = () => {
		const el = document.getElementById(id);
		if (!el) {
			// Target not in the DOM yet (e.g. a section not mounted) — keep trying
			// until the deadline so late-mounting content still gets pinned.
			if (performance.now() - start < settleMs) requestAnimationFrame(step);
			return;
		}
		el.scrollIntoView({ block: 'start' });
		const top = Math.round(el.getBoundingClientRect().top);
		// Stop only once the position has held steady for many frames — long
		// enough for lazily-mounted blocks above to mount, grow, and re-settle.
		// (A short hold can fire while intervening placeholders are still
		// expanding, which is what made far jumps land thousands of px off.)
		if (Math.abs(top - lastTop) <= 1) {
			stableFrames += 1;
			if (stableFrames >= 8) {
				focusTarget(el);
				return;
			}
		} else {
			stableFrames = 0;
		}
		lastTop = top;
		if (performance.now() - start < settleMs) requestAnimationFrame(step);
		else focusTarget(el);
	};
	requestAnimationFrame(step);
}

/**
 * Coerce one reference row into a shape the renderers can trust.
 *
 * The reference is fetched as untyped JSON. A malformed-but-200 payload (an old
 * or buggy server) can omit array/object fields the components iterate over;
 * `required_scopes.map(...)` or `Object.entries(implied_scopes)` would then
 * throw and blank the whole route. Normalizing once at the boundary means every
 * downstream consumer (ScopePanel, AuthChip, scope tree) gets safe defaults
 * without scattering `?? []` everywhere — a bad field degrades to "empty"
 * instead of crashing.
 */
function normalizeEndpoint(endpoint: ReferenceEndpoint): ReferenceEndpoint {
	return {
		...endpoint,
		actor_types: Array.isArray(endpoint.actor_types) ? endpoint.actor_types : [],
		required_scopes: Array.isArray(endpoint.required_scopes) ? endpoint.required_scopes : [],
		implied_scopes:
			endpoint.implied_scopes && typeof endpoint.implied_scopes === 'object'
				? endpoint.implied_scopes
				: {},
	};
}

/** Build the `(method, path)` → endpoint index the renderer uses to enrich operations. */
export function indexReference(payload: ReferencePayload): Map<string, ReferenceEndpoint> {
	const index = new Map<string, ReferenceEndpoint>();
	for (const endpoint of payload.endpoints ?? []) {
		index.set(lookupKey(endpoint.method, endpoint.path), normalizeEndpoint(endpoint));
	}
	return index;
}
