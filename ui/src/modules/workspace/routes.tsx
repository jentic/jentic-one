/**
 * Workspace module routes — the Library's Workspace view and API hubs. Paths
 * are RELATIVE to the `/app` shell, so these mount at `/app/library/workspace`
 * (the full workspace) and `/app/library/workspace/:vendor/:name/:version`
 * (one API's hub). Registered additively into `@/shared/app/routes.ts`.
 *
 * The hub route spreads the API's `(vendor, name, version)` identity triple
 * across three path segments — the same shape the backend uses
 * (`/apis/{vendor}/{name}/{version}`) — so the URL is human-readable and no
 * opaque id encoding is needed. `ApiCard` builds these links via `encodeApiId`
 * (which percent-encodes each segment); the page reads them back from
 * `useParams`.
 *
 * The retired `/app/workspace` and `/app/workspace/:vendor/:name/:version`
 * URLs redirect to their Library equivalents with the search string and hash
 * preserved (so `?import=1` and `?tab=` deep links keep working).
 */
import type { RouteObject } from 'react-router';
import { Navigate, useLocation, useParams } from 'react-router';
import { ROUTES } from '@/shared/app';
import WorkspacePage from '@/modules/workspace/pages/WorkspacePage';
import ApiDetailPage from '@/modules/workspace/pages/ApiDetailPage';

// Components (not inline elements) so `ROUTES` is read at render time — see
// the TDZ note in the agents module's routes.
function RetiredWorkspaceRedirect() {
	const { search, hash } = useLocation();
	return <Navigate to={`${ROUTES.workspace}${search}${hash}`} replace />;
}

function RetiredWorkspaceApiRedirect() {
	const { vendor = '', name = '', version = '' } = useParams();
	const { search, hash } = useLocation();
	const path = [vendor, name, version].map(encodeURIComponent).join('/');
	return <Navigate to={`${ROUTES.workspace}/${path}${search}${hash}`} replace />;
}

export const workspaceRoutes: RouteObject[] = [
	{ path: 'library/workspace', element: <WorkspacePage /> },
	{ path: 'library/workspace/:vendor/:name/:version', element: <ApiDetailPage /> },
	{ path: 'workspace', element: <RetiredWorkspaceRedirect /> },
	{ path: 'workspace/:vendor/:name/:version', element: <RetiredWorkspaceApiRedirect /> },
];
