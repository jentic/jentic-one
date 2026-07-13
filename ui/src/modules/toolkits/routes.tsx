import type { RouteObject } from 'react-router-dom';
import { ToolkitsPage } from '@/modules/toolkits/pages/ToolkitsPage';
import { ToolkitDetailPage } from '@/modules/toolkits/pages/ToolkitDetailPage';

/**
 * Toolkits module routes, RELATIVE to the `/app` shell (no leading slash) — see
 * `shared/app/routes.ts`. Registered there with two additive lines (an import
 * and a `...toolkitRoutes` spread).
 */
export const toolkitRoutes: RouteObject[] = [
	{ path: 'toolkits', element: <ToolkitsPage /> },
	{ path: 'toolkits/:toolkitId', element: <ToolkitDetailPage /> },
];
