import { Navigate, useRoutes, type RouteObject } from 'react-router-dom';
import { AuthGuard } from '@/shared/auth/AuthGuard';
import { SetupGate } from '@/shared/auth/SetupGate';
import { LoginPage } from '@/shared/auth/LoginPage';
import { SetupPage } from '@/shared/auth/SetupPage';
import { ChangePasswordPage } from '@/shared/auth/ChangePasswordPage';
import { RedeemInvitePage } from '@/shared/auth/RedeemInvitePage';
import { OAuthPopupReturn } from '@/shared/auth/OAuthPopupReturn';
import { Layout } from '@/shared/app/Layout';
import { moduleRoutes, ROUTES } from '@/shared/app/routes';
import { PlaceholderPage } from '@/shared/app/placeholders';
import { sortedNavItems } from '@/shared/app/nav';
// [ui-dashboard] Dashboard owns the /app index — replaces DashboardPlaceholder.
import { dashboardIndexRoute } from '@/modules/dashboard/routes';
import { publicDocsRoutes } from '@/modules/docs/routes';

/**
 * Route tree. All paths are relative to the router `basename` (`/app`, set in
 * `main.tsx` from Vite's `base`), so a path like `/login` resolves to
 * `/app/login` in the browser and `/credentials` to `/app/credentials`. The
 * SPA owns the entire `/app` namespace; nothing here is served outside it.
 *
 *   /login, /setup                   → SetupGate steers by setup_required
 *                                      (→ /app/login, /app/setup)
 *   /change-password                 → outside the Layout, reachable pre-session
 *                                      (→ /app/change-password)
 *   /redeem-invite                    → public invite-redemption landing
 *                                      (→ /app/redeem-invite?token=…); outside
 *                                      the AuthGuard, auto-logs in on success
 *   /oauth/connected                  → public OAuth popup landing (self-closes)
 *                                      (→ /app/oauth/connected; the backend
 *                                      callback redirects the popup here)
 *   / (basename index) + children    → AuthGuard → Layout → feature pages
 *                                      (uniformly authenticated; no exceptions)
 *
 * Feature PRs register real pages in `shared/app/routes.ts` (moduleRoutes);
 * those win over the per-slot placeholders below (their path is "claimed").
 *
 * Because the bundle is served under `/app`, bare API prefixes (`/credentials`,
 * `/agents`, …) can never collide with an SPA route on hard refresh — they live
 * in a different namespace from the SPA entirely.
 *
 * `extraRoutes` is the SPA extension seam (parallel to the CLI's
 * `AppContainer.ExtraCommands`): a downstream build passes additional
 * `RouteObject`s that are appended into the authenticated shell (and claim their
 * nav-slot path, suppressing that slot's placeholder), so it can ship its own
 * SPA (OSS shell + built-in routes + its extras) without editing this repo.
 * Omitted (the default) for the OSS binary.
 */
export function App({ extraRoutes = [] }: { extraRoutes?: RouteObject[] } = {}) {
	return useRoutes(buildRoutes(extraRoutes));
}

function buildRoutes(extraRoutes: RouteObject[] = []): RouteObject[] {
	// A nav slot gets a placeholder until a real route claims its path. Both
	// moduleRoutes AND extraRoutes count as "claimed" so a downstream route at a
	// nav-slot path suppresses that slot's placeholder (no duplicate entry).
	const claimed = new Set([...moduleRoutes, ...extraRoutes].map((r) => r.path).filter(Boolean));
	const placeholderRoutes: RouteObject[] = sortedNavItems()
		.filter((item) => item.to !== ROUTES.app && !claimed.has(relativeToApp(item.to)))
		.map((item) => ({
			path: relativeToApp(item.to),
			element: <PlaceholderPage title={item.label} />,
		}));

	return [
		{
			// SetupGate steers between sign-in and first-run setup based on the
			// server's setup_required flag; both live outside the Layout.
			element: <SetupGate />,
			children: [
				{ path: '/login', element: <LoginPage /> },
				{ path: '/setup', element: <SetupPage /> },
			],
		},
		{ path: '/change-password', element: <ChangePasswordPage /> },
		// Public invite-redemption landing. An admin invites a user (POST /users
		// → invite_token) and sends them here (→ /app/redeem-invite?token=…) to set
		// a password and finish account creation. Outside the AuthGuard — the
		// invitee has no session yet (redeem returns a JWT and auto-logs them in).
		{ path: '/redeem-invite', element: <RedeemInvitePage /> },
		// Public landing for the OAuth connect popup. The backend callback
		// redirects the popup here (→ /app/oauth/connected?status=ok|error); it
		// self-closes. Outside the AuthGuard (the popup has no guaranteed
		// session). Registered before the '*' catch-all below.
		{ path: '/oauth/connected', element: <OAuthPopupReturn /> },
		// Public API reference — API docs are public-by-norm; lives outside the
		// AuthGuard/Layout. Matched before the authenticated shell so it wins for
		// `/app/docs` whether or not a session exists.
		...publicDocsRoutes,
		{
			element: <AuthGuard />,
			children: [
				{
					// Basename index: the authenticated app shell home (`/app`).
					path: '/',
					element: <Layout />,
					// `moduleRoutes` is the append-only route registry; it is
					// spread here (not inlined) so a consumer can compose
					// `[...moduleRoutes, ...extraRoutes]` at this single point
					// without editing the registry. Order is load-bearing —
					// placeholders stay last so a real module route wins.
					children: [
						dashboardIndexRoute,
						...moduleRoutes,
						...extraRoutes,
						...placeholderRoutes,
					],
				},
			],
		},
		// Unknown in-app path → the shell home. (A bare host hit at `/`, before
		// the basename, is redirected to `/app/` by the backend.)
		{ path: '*', element: <Navigate to="/" replace /> },
	];
}

/**
 * A nav item's `to` is a root-relative client path (e.g. `/credentials`). The
 * module route registry mounts pages relative to the basename index, so strip
 * the leading slash to get the route `path` (`credentials`). The dashboard
 * index (`ROUTES.app` === `/`) is filtered out before this is called.
 */
function relativeToApp(path: string): string {
	return path.startsWith('/') ? path.slice(1) : path;
}
