/**
 * Dashboard module route. Dashboard owns the `/app` INDEX (the landing page),
 * not a child path — so it exports an index route the shell mounts as
 * `{ index: true }` inside the `/app` layout, replacing the foundation's
 * `DashboardPlaceholder`.
 *
 * This is the one documented exception to the pure-additive `moduleRoutes`
 * registry: the index slot lives in `App.tsx` (the shell owns it), so this PR
 * swaps that single line there rather than spreading into `moduleRoutes`. See
 * STATUS.md (coord-with-shell note) + COLLABORATION.md §3.
 */
import type { RouteObject } from 'react-router';
import { Navigate } from 'react-router';
import DashboardPage from '@/modules/dashboard/pages/DashboardPage';

export const dashboardIndexRoute: RouteObject = { index: true, element: <DashboardPage /> };

/**
 * Dashboard's non-index child routes. `/app/access-requests` (the retired
 * access-request queue — theme 7, epic jentic/jentic-one#1372) redirects to
 * the dashboard so stale bookmarks and old `approve_url` deep links land
 * somewhere useful instead of a 404.
 */
export const dashboardRoutes: RouteObject[] = [
	{ path: 'access-requests', element: <Navigate to="/" replace /> },
];
