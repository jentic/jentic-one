import { Navigate, Outlet } from 'react-router-dom';
import { usePermission } from '@/shared/auth/usePermission';
import { ROUTES } from '@/shared/app/routes';

/**
 * Route guard for permission-gated areas. Renders its children only when the
 * current user holds `permission`; otherwise redirects to the app home. Runs
 * INSIDE the authenticated shell (below `AuthGuard`), so a user is always
 * present — this checks authorization, not authentication.
 *
 * Cosmetic nav-hiding (see `visibleNavItems`) keeps the entry out of sight; this
 * guard is the matching defense-in-depth for a deep-link/hard-refresh to the
 * path. The backend still enforces the real boundary (403 on the data calls).
 *
 * Use either as a wrapper element (`<RequirePermission permission="…"><Page/>…`)
 * or as a layout route (renders `<Outlet/>` when no children are passed).
 */
export function RequirePermission({
	permission,
	children,
	redirectTo = ROUTES.app,
}: {
	permission: string;
	children?: React.ReactNode;
	redirectTo?: string;
}) {
	const allowed = usePermission(permission);
	if (!allowed) return <Navigate to={redirectTo} replace />;
	return <>{children ?? <Outlet />}</>;
}
