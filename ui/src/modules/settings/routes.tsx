import type { RouteObject } from 'react-router';
import { RequirePermission } from '@/shared/auth';
import { SettingsPage } from './pages/SettingsPage';

/**
 * Settings module routes — mounted under the `/app` shell.
 * Registered in `shared/app/routes.ts` (append-only registry).
 */
export const settingsRoutes: RouteObject[] = [
	{
		path: 'settings',
		element: (
			<RequirePermission permission="org:admin">
				<SettingsPage />
			</RequirePermission>
		),
	},
	{
		path: 'settings/developer',
		element: (
			<RequirePermission permission="org:admin">
				<SettingsPage />
			</RequirePermission>
		),
	},
];
