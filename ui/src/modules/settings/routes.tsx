import type { RouteObject } from 'react-router';
import { SettingsPage } from './pages/SettingsPage';

/**
 * Settings module routes — mounted under the `/app` shell.
 * Registered in `shared/app/routes.ts` (append-only registry).
 */
export const settingsRoutes: RouteObject[] = [
	{ path: 'settings', element: <SettingsPage /> },
	{ path: 'settings/developer', element: <SettingsPage /> },
];
