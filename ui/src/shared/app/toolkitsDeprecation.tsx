/**
 * DEPRECATION-WINDOW ALIAS for the retired Toolkits module — DELETE IN 6b.
 *
 * Theme-5 phase 5b removed the toolkits UI module (routes, nav, pages), but
 * bookmarks and agent-emitted links to `/app/toolkits` and
 * `/app/toolkits/{tk_id}` (± `?tab=` variants) are still in the wild. For the
 * deprecation window (phase 5d → 6b) those paths land here instead of the
 * router's not-found catch-all: they redirect to the Agents page — the
 * binding-management home that replaced toolkits — and surface a one-time
 * dismissible toast pointing at the per-agent Access tab.
 *
 * The `tk_…` id on detail deep links is NOT resolved (the toolkit endpoints
 * were deleted in 5b; there is nothing to look it up against), and the query
 * string is intentionally dropped by the redirect — `?tab=` spoke toolkit-era
 * vocabulary. Phase 6b deletes this file plus its one spread in `App.tsx`.
 */
import { useEffect } from 'react';
import { Navigate, type RouteObject } from 'react-router';
import { ROUTES } from '@/shared/app/routes';
import { toast } from '@/shared/ui';

/**
 * Once per page load: repeated hits on stale links within one SPA session
 * shouldn't stack (or resurface a dismissed) notice. Deliberately NOT
 * persisted — a later visit in a fresh tab may genuinely need the reminder.
 */
let noticeShown = false;

/** Test-only: re-arm the one-time notice so cases stay isolated. */
export function resetToolkitsDeprecationNotice(): void {
	noticeShown = false;
}

function ToolkitsRetiredRedirect() {
	useEffect(() => {
		if (noticeShown) return;
		noticeShown = true;
		toast({
			// Stable id: even if two alias routes mount in quick succession
			// (e.g. a redirect race), the store dedups to a single toast.
			id: 'toolkits-retired',
			title: 'Toolkits were retired',
			description:
				'Access is now managed per agent — open an agent and use its Access tab to manage bound credentials.',
			durationMs: 10000,
		});
	}, []);
	return <Navigate to={ROUTES.agents} replace />;
}

/**
 * Mounted inside the authenticated Layout (see `App.tsx`), so the alias only
 * exists where the old toolkits pages lived. Splat covers `/toolkits/{tk_id}`
 * and any deeper toolkit-era path.
 */
export const toolkitsDeprecationRoutes: RouteObject[] = [
	{ path: 'toolkits', element: <ToolkitsRetiredRedirect /> },
	{ path: 'toolkits/*', element: <ToolkitsRetiredRedirect /> },
];
