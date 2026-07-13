/**
 * Workspace module routes. Paths are RELATIVE to the `/app` shell, so these
 * mount at `/app/workspace` (list) and `/app/workspace/:vendor/:name/:version`
 * (detail). Registered additively into `@/shared/app/routes.ts`.
 *
 * The detail route spreads the API's `(vendor, name, version)` identity triple
 * across three path segments — the same shape the backend uses
 * (`/apis/{vendor}/{name}/{version}`) — so the URL is human-readable and no
 * opaque id encoding is needed. `ApiCard` builds these links via `encodeApiId`
 * (which percent-encodes each segment); the page reads them back from
 * `useParams`.
 */
import type { RouteObject } from 'react-router-dom';
import WorkspacePage from '@/modules/workspace/pages/WorkspacePage';
import ApiDetailPage from '@/modules/workspace/pages/ApiDetailPage';

export const workspaceRoutes: RouteObject[] = [
	{ path: 'workspace', element: <WorkspacePage /> },
	{ path: 'workspace/:vendor/:name/:version', element: <ApiDetailPage /> },
];
