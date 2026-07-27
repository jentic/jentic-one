import type { RouteObject } from 'react-router';
import { CredentialsPage } from './pages/CredentialsPage';

/**
 * Credentials module routes — mounted under the `/app` shell.
 * Registered in `shared/app/routes.ts` (append-only registry).
 */
export const credentialsRoutes: RouteObject[] = [
	{ path: 'credentials', element: <CredentialsPage /> },
];
