/**
 * Docs module route — the public API reference at `/app/docs`.
 *
 * API reference is public-by-norm (Stripe/GitHub/etc.), so the route is
 * registered in `App.tsx` OUTSIDE `AuthGuard`/`Layout` (which both assume a
 * live session via the agent-stream + user menu) and wins for `/app/docs`
 * whether or not a session exists. Standalone for now; a fuller public
 * shell/chrome is a follow-up decision.
 *
 * The page is lazy-loaded: the docs portal pulls in the OpenAPI/CLI rendering
 * stack (Markdown + schema trees over the full spec), which is large and only
 * needed on this route. `React.lazy` + a `Suspense` boundary code-split it onto
 * its own chunk that loads only when a user navigates to /app/docs.
 */
import { Suspense, lazy } from 'react';
import type { RouteObject } from 'react-router';
import { LoadingState, ErrorBoundary } from '@/shared/ui';

const DocsPage = lazy(() => import('@/modules/docs/pages/DocsPage'));

/**
 * The page is wrapped in an ErrorBoundary as a final safety net: the reference
 * is untyped JSON and the renderer walks the full OpenAPI spec, so a malformed
 * payload that slips past the per-field normalization degrades to a friendly
 * fallback instead of a blank route. `Suspense` handles the lazy chunk load.
 */
export const publicDocsRoutes: RouteObject[] = [
	{
		path: '/docs',
		element: (
			<ErrorBoundary>
				<Suspense fallback={<LoadingState message="Loading the API reference…" />}>
					<DocsPage />
				</Suspense>
			</ErrorBoundary>
		),
	},
];
