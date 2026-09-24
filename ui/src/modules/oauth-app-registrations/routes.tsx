import type { RouteObject } from 'react-router';
import { RequirePermission } from '@/shared/auth';
import { OAuthAppRegistrationsPage } from './pages/OAuthAppRegistrationsPage';

/**
 * OAuth App Registrations module routes — mounted under the `/app` shell.
 * Admin-only: admin mutations gate on `org:admin`, reads on
 * `credentials:read`; we choose the stricter permission at the route level
 * to keep read access aligned with who can operate the surface, mirroring
 * the settings module's own admin gate.
 * Registered in `shared/app/routes.ts` (append-only registry).
 */
export const oauthAppRegistrationsRoutes: RouteObject[] = [
	{
		path: 'admin/oauth-app-registrations',
		element: (
			<RequirePermission permission="org:admin">
				<OAuthAppRegistrationsPage />
			</RequirePermission>
		),
	},
];
