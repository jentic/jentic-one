/**
 * Docs module route. Path is RELATIVE to the `/app` shell, so this mounts at
 * `/app/docs`. Registered additively in `@/shared/app/routes.ts`.
 *
 * The page is lazy-loaded: the docs portal pulls in the OpenAPI/CLI rendering
 * stack (Markdown + schema trees over the full spec), which is large and only
 * needed on this route. `React.lazy` + a `Suspense` boundary code-split it onto
 * its own chunk that loads only when a user navigates to /app/docs.
 */
import { Suspense, lazy } from 'react';
import type { RouteObject } from 'react-router-dom';
import { LoadingState, ErrorBoundary } from '@/shared/ui';

const DocsPage = lazy(() => import('@/modules/docs/pages/DocsPage'));

/**
 * The page is wrapped in an ErrorBoundary as a final safety net: the reference
 * is untyped JSON and the renderer walks the full OpenAPI spec, so a malformed
 * payload that slips past the per-field normalization degrades to a friendly
 * fallback instead of a blank route. `Suspense` handles the lazy chunk load.
 */
const docsElement = (
	<ErrorBoundary>
		<Suspense fallback={<LoadingState message="Loading the API reference…" />}>
			<DocsPage />
		</Suspense>
	</ErrorBoundary>
);

export const docsRoutes: RouteObject[] = [
	{
		path: 'docs',
		element: docsElement,
	},
];

/**
 * Public, unauthenticated docs route — API reference is public-by-norm (Stripe/
 * GitHub/etc.). Registered in `App.tsx` OUTSIDE `AuthGuard`/`Layout` (which both
 * assume a live session via the agent-stream + user menu). Standalone for now;
 * a fuller public shell/chrome is a follow-up decision.
 */
export const publicDocsRoutes: RouteObject[] = [
	{
		path: '/docs',
		element: docsElement,
	},
];
