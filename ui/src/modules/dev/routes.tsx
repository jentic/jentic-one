/**
 * Dev-only routes — internal tools, never shipped to production. Every route
 * here is gated by `import.meta.env.DEV`, so the array is empty (and the pages
 * are tree-shaken) in a production build.
 */
import type { RouteObject } from 'react-router-dom';

export const devRoutes: RouteObject[] = import.meta.env.DEV
	? [
			{
				path: 'dev/access-requests',
				lazy: async () => {
					const mod = await import('@/modules/dev/pages/AccessRequestShowcasePage');
					return { Component: mod.default };
				},
			},
		]
	: [];
