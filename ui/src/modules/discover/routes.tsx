/**
 * Discover module routes — the Library's Catalog view. Path is RELATIVE to the
 * `/app` shell, so this mounts at `/app/library`. Registered additively into
 * `@/shared/app/routes.ts`.
 *
 * The retired `/app/discover` URL redirects here (search string and hash
 * preserved, so any old deep link still lands with them).
 */
import type { RouteObject } from 'react-router';
import { Navigate, useLocation } from 'react-router';
import { ROUTES } from '@/shared/app';
import LibraryPage from '@/modules/discover/pages/LibraryPage';

/**
 * A component rather than an inline `<Navigate>` element: `@/shared/app/routes`
 * imports this module to build `moduleRoutes`, so reading `ROUTES` at module
 * evaluation would hit the import cycle's TDZ (same pattern as the agents
 * module's retired-route redirects).
 */
function RetiredDiscoverRedirect() {
	const { search, hash } = useLocation();
	return <Navigate to={`${ROUTES.library}${search}${hash}`} replace />;
}

export const discoverRoutes: RouteObject[] = [
	{ path: 'library', element: <LibraryPage /> },
	{ path: 'discover', element: <RetiredDiscoverRedirect /> },
];
