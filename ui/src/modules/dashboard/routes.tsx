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
import type { RouteObject } from 'react-router-dom';
import DashboardPage from '@/modules/dashboard/pages/DashboardPage';
import AccessRequestsPage from '@/modules/dashboard/pages/AccessRequestsPage';

export const dashboardIndexRoute: RouteObject = { index: true, element: <DashboardPage /> };

/**
 * Dashboard's non-index child routes. Mounts at `/app/access-requests` — the
 * full, paginated access-request queue the Dashboard "Pending requests" card
 * links to via "View all". Registered additively in `@/shared/app/routes.ts`.
 */
export const dashboardRoutes: RouteObject[] = [
	{ path: 'access-requests', element: <AccessRequestsPage /> },
];
