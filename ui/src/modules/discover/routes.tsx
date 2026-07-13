/**
 * Discover module routes. Path is RELATIVE to the `/app` shell, so this mounts
 * at `/app/discover`. Registered additively into `@/shared/app/routes.ts`.
 */
import type { RouteObject } from 'react-router-dom';
import DiscoverPage from '@/modules/discover/pages/DiscoverPage';

export const discoverRoutes: RouteObject[] = [{ path: 'discover', element: <DiscoverPage /> }];
