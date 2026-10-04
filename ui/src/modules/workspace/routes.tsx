/**
 * Workspace module routes — the API hubs. Paths are RELATIVE to the `/app`
 * shell, so a hub mounts at `/app/library/workspace/:vendor/:name/:version`.
 * Registered additively into `@/shared/app/routes.ts`.
 *
 * There is no workspace list page: the workspace lives in the panel docked
 * beside the Library's catalog. `/app/library/workspace` (the retired full
 * view) and the older `/app/workspace` redirect to `/app/library` with the
 * search string and hash kept — the panel reads the same `?q=` / `?status=`
 * filter params the page did, and `?import=1` opens the import dialog there.
 *
 * The hub route spreads the API's `(vendor, name, version)` identity triple
 * across three path segments — the same shape the backend uses
 * (`/apis/{vendor}/{name}/{version}`) — so the URL is human-readable and no
 * opaque id encoding is needed. Links are built with
 * `ROUTE_PATHS.workspaceApiHub` (which percent-encodes each segment); the page
 * reads them back from `useParams`.
 *
 * The retired `/app/workspace/:vendor/:name/:version` redirects to its hub
 * with the search string and hash preserved (so `?tab=` deep links keep
 * working).
 */
import type { RouteObject } from 'react-router';
import { Navigate, useLocation, useParams } from 'react-router';
import { ROUTES, ROUTE_PATHS } from '@/shared/app';
import ApiDetailPage from '@/modules/workspace/pages/ApiDetailPage';

// Components (not inline elements) so `ROUTES` is read at render time — see
// the TDZ note in the agents module's routes.
function RetiredWorkspaceRedirect() {
	const { search, hash } = useLocation();
	return <Navigate to={`${ROUTES.library}${search}${hash}`} replace />;
}

function RetiredWorkspaceApiRedirect() {
	const { vendor = '', name = '', version = '' } = useParams();
	const { search, hash } = useLocation();
	return (
		<Navigate
			to={`${ROUTE_PATHS.workspaceApiHub({ vendor, name, version })}${search}${hash}`}
			replace
		/>
	);
}

export const workspaceRoutes: RouteObject[] = [
	{ path: 'library/workspace', element: <RetiredWorkspaceRedirect /> },
	{ path: 'library/workspace/:vendor/:name/:version', element: <ApiDetailPage /> },
	{ path: 'workspace', element: <RetiredWorkspaceRedirect /> },
	{ path: 'workspace/:vendor/:name/:version', element: <RetiredWorkspaceApiRedirect /> },
];
