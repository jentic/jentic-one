/**
 * Dev-only routes — internal tools, never shipped to production. Every route
 * here is gated by `import.meta.env.DEV`, so the array is empty (and the page
 * is tree-shaken) in a production build.
 */
import type { RouteObject } from 'react-router-dom';
import AccessRequestShowcasePage from '@/modules/dev/pages/AccessRequestShowcasePage';

export const devRoutes: RouteObject[] = import.meta.env.DEV
	? [{ path: 'dev/access-requests', element: <AccessRequestShowcasePage /> }]
	: [];
